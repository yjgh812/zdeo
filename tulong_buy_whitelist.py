#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买入白名单生成器（2026-08-21 交付，模拟盘改进方案v2 M1：入口端物理收口）。

定位：把"盘中无清单标的→不买"从提示词铁律升级为物理清单。每日盘前把系统验证过的
信号源（MASTER/HIST日度清单、bp+ep月名单、mass_scan/universe_scan）统一过滤后输出
「今日可买白名单」，并把「今日禁止板块」（R24高潮禁追快照）追加进作战清单。

【2026-09-24 名单制全面废除（第二道主人令）】「全面取消、废除白名单和月度TopN名单和TopN名单，
股票A股所有股票中找。排除北交所、ST、科创板」——main() 已加废除守卫：本工具拒绝生成任何名单，
无论被 09:10 链/cron/手工调用；当日(20260924)白名单产物已删除。选股宇宙=A股全市场
（仅排除北交所/ST/科创板），准入唯一依据=admit 闸门信号源清单 + R 闸实时判定。

过滤规则（与 pre_trade_check_v2 对齐，白名单为预筛，最终仍须过闸）：
  1. 代码合法且非 688 开头(科创板)、非 8/4 开头(北交所)
  2. 名称非 ST（*ST/ST）
  3. 价格 ≤ MAX_PRICE(30，若信号源自带价格可判定)
  4. 去重，保留信号源优先级：MASTER/HIST > bp+ep > mass_scan
买入端 3 条线索（2026-08-23 主人拍板全部实施）：
  A. 低位横盘突破（20日横盘+放量+低位，读本地 qfq）→ tier +0.2档（1.0x→1.2x）
  B. 机构龙虎榜标注（ftshare 原始数据 reports/raw/lhb_raw_*.json，机构专用席位净额）→ note 提示
  C. 中报预告标注（本地 earnings_yjyg 表优先 + ftshare raw 兜底，q2 中报）→ note 提示
纯标准库，无网络依赖；任何输入缺失 → 显式 WARN 后继续（不静默）。
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

import holding_horizon  # 持有周期标签唯一真源（2026-08-24 C7-R28 分层守卫配套）

BASE = r"D:\zcodesj"
SIGNAL_DIR = os.path.join(BASE, "reports", "daily_signals")
BEEP_DIR = os.path.join(BASE, "multifactor", "reports", "daily_top20")
SCAN_DIR = os.path.join(BASE, "reports")
PLAN_DIR = os.path.join(BASE, "trade_plans")
OUT_DIR = os.path.join(BASE, "reports")
MAX_PRICE = 30.0
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
# 2026-08-29 R32 高位动量禁买（O-B 改写提案，主人"继续"授权；与 pre_trade_check_v2 同判据同证据）
# 证据: reports/OB_OD_REPLAY_20260829.md —— chg20>30% 组 n=15 全负胜率 0%(早盘-3.84%/非早盘-9.16%)
# vs 对照组 +0.5~0.8%; 白名单层=候选剔除(闸门层 REJECT 为第二道防线)。T-1 时点(date<当日)无前视。
R32_CHG20_REJECT = 0.30


def _chg20_of(code6: str):
    """R32 白名单层：T-1 qfq 收盘/20 交易日前收盘 - 1；数据不足返回 None（fail-open 由调用方标注）。"""
    import sqlite3 as _s3
    try:
        con = _s3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date < ? "
            "ORDER BY date DESC LIMIT 21",
            (code6, __import__("datetime").datetime.now().strftime("%Y%m%d"))).fetchall()
        con.close()
        closes = [r[0] for r in rows if r[0] is not None]  # 新 -> 旧
        if len(closes) < 21 or not closes[20] or closes[20] <= 0:
            return None
        return closes[0] / closes[20] - 1
    except Exception:
        return None
EARN_DB = r"D:\workbuddyspace\databases\fundamental_data.db"  # earnings_yjyg 中报业绩预告（akshare stock_yjyg_em 补拉）
RAW_DIR = r"D:\zcodesj\reports\raw"  # 龙虎榜标注原始数据目录（agent 从 ftshare 拉取；中报标注已改读本地表）

SOURCE_PRIORITY = {"mainline_radar": 0.5, "master_hist": 1, "bp_ep": 2, "ml_signal": 2.5, "full_market": 2.8, "mass_scan": 3, "universe": 4}
# 2026-08-29 smc_s2_roe 前视污染退役(主人裁决A, _common.RETIRED_SOURCES): 从优先级表移除,
# 白名单不再收录; 数据链 smc 环节照跑产 CSV 仅供监控。污染档案: reports/SMC_LOOKAHEAD_BIAS_DISCOVERY_20260829.md

# 2026-08-28 分层贴现（执行率缺口 E53/E66/E76, 主人"按你推荐继续"授权落档）:
# 挂单价=信号参考价×折扣——1.2x(低位横盘突破, 强信心)→0.998 缩小贴现提高成交率;
# 1.0x→0.995 维持; 0.5x(实验源, 低信心)→0.993 更深贴现防追价(成交率损失可接受)。
# 追价防线不受影响: R27b(>5%昨收溢价 WARN)+R28(hotspot 2% REJECT)+R7(涨停临近 REJECT)。
DISCOUNT_BY_TIER = {"1.2x": 0.998, "1.0x": 0.995, "0.5x": 0.993, "0.25x": 0.990}
# 2026-08-28 R30b 组合级行业占比守卫（E79, 当前银行持仓市值占比25.4%已破线）:
# 依赖 09:10 链写入 data/positions_latest.json(westock 持仓快照); 快照缺失 → fail-open 显式标注。
POSITIONS_SNAPSHOT = os.path.join(BASE, "data", "positions_latest.json")
R30B_SECTOR_CAP = 0.25

# O10 板块资金流过滤（2026-08-26 自省 O10, 8/24 信号资金背离教训）：
# v4 信号全指向算力CPO半导体 vs 通信-140亿/生物制品-124亿净流出 → 次日兑现下跌。
# 规则 = 申万L1 行业内已采集个股的 EM 5日 + THS 5日 净流入中位数双负 → 该板块
# 候选 tier 减半。单口径负仅 note 提示不降档（8/18 日发精机教训：单口径不可信）；
# 行业样本<5 只不判定（fail-open，快照仅覆盖持仓+universe扫描清单，非全市场）。
FLOW_SNAPSHOT_DB = os.path.join(BASE, "data", "em_flow_snapshot.db")
SW1_MAP_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
SECTOR_FLOW_MIN_SAMPLES = 5


def load_sector_flow_risk() -> dict[str, str]:
    """O10：按申万L1 聚合 em_flow_snapshot 双口径5日净流入中位数。
    返回 {板块名: 描述}（双负=资金流出区）；无库/异常 → {}（fail-open）。"""
    out: dict[str, str] = {}
    out_note: dict[str, str] = {}
    try:
        import sqlite3
        import statistics
        con = sqlite3.connect(f"file:{FLOW_SNAPSHOT_DB}?mode=ro", uri=True, timeout=5)
        last = con.execute("SELECT MAX(trade_date) FROM flow_snapshot").fetchone()[0]
        if not last:
            con.close()
            return out
        rows = con.execute(
            "SELECT code, em_5d, ths_5d FROM flow_snapshot WHERE trade_date=?", (last,)).fetchall()
        con.close()
        mcon = sqlite3.connect(f"file:{SW1_MAP_DB}?mode=ro", uri=True, timeout=5)
        sw1 = {r[0]: r[1] for r in mcon.execute("SELECT code, sector_name FROM stock_sector_sw1")}
        mcon.close()
        by_sec: dict[str, list[tuple[float, float]]] = {}
        for code, em5, ths5 in rows:
            _c = str(code)[-6:]
            _pref = "sh" if _c.startswith("6") else ("bj" if _c.startswith(("8", "9", "4")) else "sz")
            sec = sw1.get(_c) or sw1.get(f"{_pref}{_c}")
            if not sec:
                continue
            try:
                emv, thv = float(em5), float(ths5)
            except (TypeError, ValueError):
                continue
            by_sec.setdefault(sec, []).append((emv, thv))
        for sec, pairs in by_sec.items():
            if len(pairs) < SECTOR_FLOW_MIN_SAMPLES:
                continue
            em_med = statistics.median(p[0] for p in pairs)
            th_med = statistics.median(p[1] for p in pairs if p[1] is not None) if any(
                p[1] is not None for p in pairs) else None
            if th_med is None:
                continue
            em_neg, th_neg = em_med < 0, th_med < 0
            if em_neg and th_neg:
                out[sec] = f"EM中位{em_med/1e8:+.1f}亿/THS中位{th_med/1e8:+.1f}亿(5日双负,n={len(pairs)})"
            elif em_neg or th_neg:
                out_note[sec] = f"EM中位{em_med/1e8:+.1f}亿/THS中位{th_med/1e8:+.1f}亿(单负,n={len(pairs)})"
        if out:
            print(f"[info] O10板块资金流出区({len(out)}个, 双口径5日中位双负): "
                  + "; ".join(f"{k}" for k in out))
        if out_note:
            print(f"[info] O10单口径负(仅提示不降档): " + "; ".join(out_note))
    except Exception as e:
        print(f"[WARN] O10板块资金流读取失败(fail-open): {e}")
    return out


# ---- 个股资金背离过滤（R-20260827-03 规则②，2026-08-28 EXPERIMENTAL 灰度接入）----
# 证据（RULE_CANDIDATES_20260827.md 附录B，298样本回测）：em_20d/流通市值 < -1% 触发组
# T+3 胜率仅 5.0%（20只19跌）vs 保留组 27.6%，收益差 T+5 +1.63pp；京东方 8/19 回放精确拦截
# 当日 15100 股买入（BT-FAIL-006 闭环）。灰度=note 标注+tier 降 0.25x，不硬剔除，积累
# 20-40 交易日（含上涨市）后复核胜率差是否稳定再转硬剔除。
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
CAP_DIVERG_THRESHOLD = 0.01   # |em_20d|/流通市值 ≥ 1% 触发（规则②阈值候选，经回测）
CAP_DIVERG_TIER = "0.25x"


def load_stock_capital_divergence() -> dict[str, str]:
    """按个股 em_20d/流通市值 < -1% 标记资金背离候选。

    返回 {code6: 描述}；em_20d=flow_snapshot 最新期 EM 20日主力净流入(元)；
    流通市值=daily_basic.circ_mv(万元, ×1e4=元)最新期。任一源缺失/无数据 → fail-open {}。
    """
    out: dict[str, str] = {}
    try:
        fcon = sqlite3.connect(f"file:{FLOW_SNAPSHOT_DB}?mode=ro", uri=True, timeout=5)
        last = fcon.execute("SELECT MAX(trade_date) FROM flow_snapshot").fetchone()[0]
        if not last:
            fcon.close()
            return out
        flow = {r[0]: r[1] for r in fcon.execute(
            "SELECT code, em_20d FROM flow_snapshot WHERE trade_date=?", (last,))
            if r[1] is not None}
        fcon.close()
        bcon = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=10)
        bl = bcon.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
        mcap = {}
        if bl:
            for ts, cm in bcon.execute(
                    "SELECT ts_code, circ_mv FROM daily_basic WHERE trade_date=?", (bl,)):
                mcap[ts.split(".")[0]] = cm
        bcon.close()
        for c6, em20 in flow.items():
            cm = mcap.get(c6)
            if not cm or float(cm) <= 0:
                continue
            ratio = float(em20) / (float(cm) * 1e4)
            if ratio < -CAP_DIVERG_THRESHOLD:
                out[c6] = (f"资金背离(em_20d={float(em20)/1e8:+.1f}亿/"
                           f"流通市值{float(cm)/1e4:,.0f}亿={ratio*100:.1f}%)")
        if out:
            print(f"[info] 个股资金背离({len(out)}只, em_20d/流通市值<-{CAP_DIVERG_THRESHOLD*100:.0f}%): "
                  + ", ".join(f"{k}({v})" for k, v in list(out.items())[:8]))
    except Exception as e:
        print(f"[WARN] 个股资金背离读取失败(fail-open): {e}")
    return out



def _tier_discount(tier: str) -> float:
    return DISCOUNT_BY_TIER.get((tier or "1.0x").strip(), 0.995)


def _load_list_health_grade() -> str:
    """P2-D: 读 reports/bp_ep_list_health_*.json 最新 health(GREEN/YELLOW/RED)。
    缺失/损坏 → 'UNKNOWN'(调用侧仅 RED 触发减半, UNKNOWN 不动作)。
    2026-09-10: 加 age 校验——健康档随月名单生成, 超过 45 天(月频上限)的旧档不再
    参与 tier 判定(WARN+UNKNOWN), 防陈旧健康档误导减半(与 _bp_ep_factor_age_warn 同锚)。"""
    try:
        import glob as _g
        _files = sorted(_g.glob(os.path.join(OUT_DIR, "bp_ep_list_health_*.json")))
        if not _files:
            return "UNKNOWN"
        fname = os.path.basename(_files[-1])
        m = re.search(r"bp_ep_list_health_(\d{8})\.json", fname)
        if m:
            try:
                hd = datetime.strptime(m.group(1), "%Y%m%d")
                if (datetime.now() - hd).days > 45:
                    print(f"[WARN] bp_ep健康档 {fname} 已陈旧({(datetime.now() - hd).days}天>45), "
                          f"tier 判定退回 UNKNOWN(不消费陈旧健康档)")
                    return "UNKNOWN"
            except ValueError:
                pass
        with open(_files[-1], encoding="utf-8") as f:
            return json.load(f).get("health", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def _load_positions_snapshot():
    """09:10 链写入的 westock 持仓快照 data/positions_latest.json → {code6:{qty,price}}。
    缺失/损坏 → None（调用侧 fail-open 显式标注，不静默）。"""
    try:
        with open(POSITIONS_SNAPSHOT, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return None
    out = {}
    for p in doc.get("positions", []):
        c6 = norm6(str(p.get("code") or ""))
        qty = int(float(p.get("quantity") or 0))
        px = float(p.get("costPrice") or p.get("price") or 0)
        if c6 and qty > 0 and px > 0:
            out[c6] = {"qty": qty, "price": px}
    return out if out else None


def _industry_exposure(positions: dict, sector: str):
    """按 stock_sector_sw1 计算某申万L1行业持仓市值占比。返回 (占比, 已知映射只数, 持仓只数)
    或 None(映射读取失败/无可映射持仓)。占比按已知映射部分计——覆盖率不足由调用侧 WARN。"""
    try:
        import sqlite3 as _sq
        _con = _sq.connect(f"file:{SW1_MAP_DB}?mode=ro", uri=True, timeout=10)
        rows = _con.execute("SELECT code, sector_name FROM stock_sector_sw1").fetchall()
        _con.close()
    except Exception:
        return None
    sec_map = {norm6(c): (s or "") for c, s in rows}
    tot = sec = 0.0
    known = 0
    for c6, pos in positions.items():
        sname = sec_map.get(norm6(c6), "")
        mv = pos["price"] * pos["qty"]
        if sname:
            tot += mv
            known += 1
            if sname == sector:
                sec += mv
    if known == 0 or tot <= 0:
        return None
    return (sec / tot, known, len(positions))


def _load_latest_em20d():
    """最新一期双口径库个股 EM 20日主力净流入(元) {code6: em_20d}。缺失→None(fail-open)。"""
    try:
        import sqlite3 as _sq
        _con = _sq.connect(f"file:{FLOW_SNAPSHOT_DB}?mode=ro", uri=True, timeout=10)
        _r = _con.execute("SELECT MAX(trade_date) FROM flow_snapshot").fetchone()
        if not _r or not _r[0]:
            _con.close()
            return None
        rows = _con.execute(
            "SELECT code, em_20d FROM flow_snapshot WHERE trade_date=?",
            (_r[0],)).fetchall()
        _con.close()
        return {norm6(str(r[0])): float(r[1] or 0) for r in rows}
    except Exception:
        return None


def _load_latest_circ_mv():
    """最新一期 daily_basic 流通市值(万元) {code6: circ_mv}。缺失→None(fail-open)。"""
    try:
        import sqlite3 as _sq
        _con = _sq.connect(f"file:{EARN_DB}?mode=ro", uri=True, timeout=10)
        _r = _con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()
        if not _r or not _r[0]:
            _con.close()
            return None
        rows = _con.execute(
            "SELECT ts_code, circ_mv FROM daily_basic WHERE trade_date=?",
            (_r[0],)).fetchall()
        _con.close()
        return {str(r[0])[:6]: float(r[1] or 0) for r in rows}
    except Exception:
        return None


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def norm6(code: str) -> str:
    c = re.sub(r"[^0-9]", "", str(code)).zfill(6)
    return c


def board_ok(code: str) -> bool:
    """主板+创业板；剔除科创板(688)/北交所(8/4开头)。"""
    if len(code) != 6 or not code.isdigit():
        return False
    if code.startswith("688"):
        return False
    # 2026-08-24 P2修复: 补 920 北交所新号段（2026 换码后 920xxx 不再匹配旧 8/4 规则）
    if code.startswith(("8", "4", "92")):
        return False
    return True


def name_ok(name: str) -> bool:
    return bool(name) and "ST" not in str(name).upper()


def _latest_dated_file(directory: str, prefix: str, max_age_days: int = 3):
    """2026-08-24 时序修复: 当日文件缺失时取最新存在文件（≤max_age_days自然日），
    返回 (path, lag_days) 或 (None, None)。修复 master_hist/universe_scan 两源因产物
    生成时点（22:00/09:26）晚于白名单生成（09:10）而永久空转的问题。"""
    import glob as _g
    import re as _re
    from datetime import datetime as _dt
    cands = []
    for f in _g.glob(os.path.join(directory, f"{prefix}*.csv")):
        m = _re.search(r"(\d{8})", os.path.basename(f))
        if not m:
            continue
        try:
            lag = (_dt.now() - _dt.strptime(m.group(1), "%Y%m%d")).days
        except ValueError:
            continue
        if 0 <= lag <= max_age_days:
            cands.append((lag, f))
    if not cands:
        return None, None
    cands.sort()
    return cands[0][1], cands[0][0]


def load_signal_csv(path: str) -> list[dict]:
    """MASTER/HIST 日度清单（兼容 v4 的 Top20 输出与早期变体）。"""
    out = []
    if not os.path.exists(path):
        # 时序修复: 信号由 22:00 十三合一生成（当日名=昨日），09:10 消费当日文件必缺 →
        # 回退最近存在文件并标注滞后天数（透明，不编造当日信号）
        alt, lag = _latest_dated_file(os.path.dirname(path), "signal_")
        if alt and lag is not None:
            print(f"[WARN] 当日信号缺失，回退 {os.path.basename(alt)}（滞后{lag}天）")
            path = alt
        else:
            print(f"[WARN] 信号清单缺失: {path}（daily_signal_production 未跑？近3日也无）")
            return out
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return out
        for row in reader:
            # 兼容 v4 输出首列无列名（文件头以逗号开头，code 在未命名首列）
            if row.get("code"):
                raw_code = row["code"]
            else:
                vals = list(row.values())
                raw_code = vals[0] if vals else ""
            code = norm6(raw_code)
            if not code or not board_ok(code):
                continue
            out.append({
                "code": code,
                "name": (row.get("name") or "").strip(),
                "source": "master_hist",
                "price": _to_float(row.get("close") or row.get("price")),
                "industry": "",
                "note": f"MASTER/HIST rank_m={row.get('rank_m','')} rank_h={row.get('rank_h','')}",
            })
    return out


# 2026-09-10: 15→20 对齐宪法 §2.4 名单制「bp+ep 最新 Top20」与 production_combo_monthly
# nlargest(20) 口径。此前 rank16-20(核心腿 25% 仓位)在白名单落不进 bp_ep 核心段,
# 被错标 full_market/universe 半仓甚至被 R26 名单制 REJECT(9/9 浙商银行实锤)。
BP_EP_TOP_N = 20
FULL_MARKET_TOP_N = 50 # 全市场覆盖层：同一 PIT 因子排名顺延至 Top50（21~50 名段，灰度 EXPERIMENTAL）


def _load_bp_ep_file(prefer_full: bool = False) -> tuple[str, str]:
    """返回 (bp_ep月名单路径, 文件名)；缺失时 ('','')。

    2026-08-24 P1修复: 核心(-n20)与全量(-n5000)产物共用同一 glob 且历史同名互相覆盖
    （picks 现已加 _n{N} 后缀）。此处按文件规模分流：核心层取最新 ≤100 行文件，
    全量层取最新 >=FULL_MARKET_TOP_N 行文件（2026-09-10 修复: 原阈值 >500 行导致 8/24
    后新产出的 n50 文件被跳过, 全市场层冻结在 8/24 旧排名 17 天——中报季基本面变化
    完全未反映; n50 已含 rank1-50, 足以支撑 21-50 段)；找不到匹配规模才回退 glob
    最新并 WARN（旧文件兼容）。另加 factor-date 陈旧 WARN(月频锁定机制保障)。
    """
    files = sorted(glob.glob(os.path.join(BEEP_DIR, "bp_ep_monthly_*.csv")))
    if not files:
        return "", ""
    for path in reversed(files):
        try:
            with open(path, encoding="utf-8-sig") as f:
                n_rows = sum(1 for _ in f) - 1
        except OSError:
            continue
        if prefer_full and n_rows >= FULL_MARKET_TOP_N:
            return path, os.path.basename(path)
        if not prefer_full and 0 < n_rows <= 100:
            return path, os.path.basename(path)
    print(f"[WARN] bp_ep文件规模分流未命中(prefer_full={prefer_full})，回退最新文件"
          f"（rank截断仍生效）：{os.path.basename(files[-1])}")
    return files[-1], os.path.basename(files[-1])


def _bp_ep_factor_age_warn(fname: str) -> None:
    """月频锁定机制保障(2026-09-10): 消费端静默跟随「最新」bp_ep 文件的语义 =
    任何人月中重跑 picker, 当日白名单即无声切换名单(8/29 实锤: 8 月窗口内二次重评
    当天跟随)。从文件名解析 factor-date, 超过 45 天(月频+半月宽限)打 WARN 提醒
    主人重跑 picker, 不拦截(名单仍可用, 仅可见性)。"""
    m = re.search(r"bp_ep_monthly_(\d{8})_", fname)
    if not m:
        print(f"[WARN] bp_ep文件名无法解析factor-date: {fname}")
        return
    try:
        fd = datetime.strptime(m.group(1), "%Y%m%d")
        age = (datetime.now() - fd).days
        if age > 45:
            print(f"[WARN] bp_ep名单factor-date={m.group(1)} 已 {age} 天(>45天月频上限), "
                  f"建议重跑 picker 生成当月名单")
    except ValueError:
        pass


def load_bp_ep_csv() -> list[dict]:
    """bp+ep 月名单核心段（最新一期，仅 rank<=BP_EP_TOP_N）。

    2026-08-24 起全量文件（-n 5000 产出）也落同名 glob，必须按 rank 截断防误吞全量。
    """
    out = []
    path, fname = _load_bp_ep_file()
    if not path:
        print(f"[WARN] bp+ep月名单缺失: {BEEP_DIR}/bp_ep_monthly_*.csv")
        return out
    _bp_ep_factor_age_warn(fname)
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rank = int(float(row.get("rank") or 0))
            except (TypeError, ValueError):
                rank = 0
            if rank < 1 or rank > BP_EP_TOP_N:
                continue
            code = norm6(row.get("code", ""))
            if not code or not board_ok(code):
                continue
            out.append({
                "code": code,
                "name": (row.get("name") or "").strip(),
                "source": "bp_ep",
                "price": _to_float(row.get("close")),
                "industry": (row.get("industry") or "").strip(),
                "l3": (row.get("l3") or "").strip(),
                "note": f"bp+ep月名单(文件{fname}) rank={rank}",
            })
    print(f"[info] bp+ep月名单核心段: {fname} rank1-{BP_EP_TOP_N} → {len(out)}只")
    return out


def load_full_market_csv() -> list[dict]:
    """全市场覆盖层（2026-08-24 主人裁决"白名单要覆盖全部A股"）。

    与 bp_ep 同一 PIT 因子（factor_pit merge_asof）对全市场约 5200 只的完整排名，
    取 rank 16~FULL_MARKET_TOP_N 顺延段。覆盖=全市场逐只打分（picks -n 5000 产出），
    名单=最优名次段。source=full_market（EXPERIMENTAL 灰度：因子同源已验证，
    16~50 名段组合表现未经回测）。
    """
    out = []
    path, fname = _load_bp_ep_file(prefer_full=True)
    if not path:
        return out
    _bp_ep_factor_age_warn(fname)
    total = 0
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            try:
                rank = int(float(row.get("rank") or 0))
            except (TypeError, ValueError):
                continue
            if rank < BP_EP_TOP_N + 1 or rank > FULL_MARKET_TOP_N:
                continue
            code = norm6(row.get("code", ""))
            if not code or not board_ok(code):
                continue
            out.append({
                "code": code,
                "name": (row.get("name") or "").strip(),
                "source": "full_market",
                "price": _to_float(row.get("close")),
                "industry": (row.get("industry") or "").strip(),
                "l3": (row.get("l3") or "").strip(),
                "note": f"全市场覆盖层(文件{fname}) rank={rank}",
            })
    print(f"[info] 全市场覆盖层: {fname} 覆盖{total}只 → rank{BP_EP_TOP_N + 1}-{FULL_MARKET_TOP_N} 命中{len(out)}只")
    return out


ML_SCORES_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
# 2026-09-08 主人裁决 Top20→Top50: Top20内ST占一半(过滤后仅10只有效供给), 扩容后
# 非ST有效候选翻倍(≈25只); ST/科创/北交所由主流程board_ok+name_ok统一过滤。
ML_TOP_N = 50


def load_ml_scores(date_str: str) -> list[dict]:
    """ml_v22 模型源（2026-09-08 主人裁决 P0-c 接入；source=ml_signal 已在 _common
    PROVEN 登记——2026-09-04 M-3 复活 bp60/ml40 生产组合的 ml 腿，trailing10/hold20
    参数由守卫携带，2026-11-30 回退复核点在册，故通道接入不新增实验源枚举）。

    读 stock_ml_scores_v22 最新分区 Top20；名称经 stock_name_lookup 唯一接口解析，
    ST/科创板/北交所由主流程 board_ok+name_ok 统一过滤；price 缺失由 main() 的
    qfq 价格刷新补齐。此前 ml 仅参与 production_combo 打分、从未接入买入白名单，
    导致模型分最高的非ST股（如 9/7 粤海饲料 prob 第14位）无通道载体。
    """
    from stock_name_lookup import get_name as _get_name
    out = []
    try:
        # 2026-09-10: 改只读打开——原读写模式在库缺失时会静默创建 0 字节占位文件,
        # 污染后续加载方(其余 loader 均 mode=ro 范式)
        con = sqlite3.connect(f"file:{ML_SCORES_DB}?mode=ro", uri=True)
        mx = con.execute("select max(date) from stock_ml_scores_v22").fetchone()[0]
        if not mx:
            print("[WARN] ml_scores_v22 空表 → ml_signal 源缺位")
            return out
        # 2026-09-10: 新鲜度守卫接入日频路径(_common 单一真源)。此前 L138 型「静默读
        # MAX(date)」坑在 combo 侧已修但白名单日频侧无 lag 阈值: v22 停更时陈旧分以
        # tier 1.0x 照常喂入每日买入名单(8/14-9/4 停更史实)。fail(>21d)=该源缺位;
        # warn(>7d)留痕继续供给, 月度调仓侧由 production_combo fail-closed 把关。
        from _common import ml_scores_staleness_guard as _ml_guard
        _lvl, _msg = _ml_guard(mx)
        if _lvl == "fail":
            print(f"[WARN] ml_signal 源缺位: {_msg}")
            con.close()
            return out
        if _lvl == "warn":
            print(f"[WARN] {_msg} (日频继续供给, 月度调仓侧 fail-closed)")
        lag = (datetime.now() - datetime.strptime(str(mx), "%Y%m%d")).days
        rows = con.execute(
            "select code, ml_prob from stock_ml_scores_v22 where date=? order by ml_prob desc limit ?",
            (str(mx), ML_TOP_N)).fetchall()
        con.close()
    except Exception as e:
        print(f"[WARN] ml_scores_v22 读取失败({type(e).__name__}: {e}) → ml_signal 源缺位")
        return out
    for code, prob in rows:
        code6 = norm6(str(code))
        if not code6:
            continue
        out.append({
            "code": code6,
            "name": _get_name(code6) or "",
            "source": "ml_signal",
            "price": None,
            "industry": "",
            "note": f"ml_v22({mx},lag{lag}d) prob={float(prob):.4f}",
        })
    print(f"[info] ml_v22 模型源: max_date={mx}(lag {lag}d) Top{ML_TOP_N} → 候选{len(out)}只")
    return out


UNIV_MIN_ADV = 2e8          # 静态宇宙: 20日日均成交额下限(业界500万~5000万区间取上限档, 百万级资金进出零冲击)
UNIV_MIN_LIST_BARS = 60     # 次新过滤: 上市>=60交易日(宪法<60日不碰同口径)
UNIV_DYN_MIN_AMT = 5e7      # 动态池保底: 当日成交额>=5000万(强势股当日流动性天然爆发)
UNIV_RAW_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"


def load_universe(date_str: str) -> list[dict]:
    """可投宇宙资格层（2026-09-08 主人裁决「从选股机制入手改变+数量至少500~1000家」）。

    业界两层结构（公募基础池/量化 universe，全网调研 20260908）：宇宙只做资格筛选——
    非 ST / 非科创北交 / 上市>=60交易日 / <=30元 / 20日日均成交额>=2亿；动态池
    （当日涨停、创60日新高，当日成交额>=5000万保底）强制并入，覆盖流动性门槛，
    保证"市场在买什么"的强势股不因静态均额被拦。买入资格仍由 pre_trade R 闸实时
    判定（R-S5/解禁/板块高潮/价偏离等不变）。source=universe（_common PROVEN 登记）。
    """
    import collections
    hist = collections.defaultdict(dict)
    amts = collections.defaultdict(dict)
    try:
        q = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=30)
        # 残缺截面防御(2026-09-18): 非交易日脏行/半日截面会毒害 60 日窗口——20260627(周六)90行
        # 停牌占位行事故实锤: 窗口左端滚到该日时全市场被"次新过滤"误杀, universe 1109→1。
        # 防御=取 80 个候选日后按当日行数>=1000 只过滤残缺日, 再取最近 66 个完整交易日。
        _cand = [r[0] for r in q.execute(
            "select distinct date from stock_daily_meta_qfq order by date desc limit 80")]
        _cnt = {d: n for d, n in q.execute(
            "select date, count(*) from stock_daily_meta_qfq where date>=? group by date", (min(_cand),))}
        _full = [d for d in _cand if _cnt.get(d, 0) >= 1000]
        _dropped = len(_cand) - len(_full)
        if _dropped:
            print(f"[info] universe 残缺截面防御: 剔除{_dropped}个不完整交易日(行数<1000)")
        dates = sorted(_full)
        if len(dates) < UNIV_MIN_LIST_BARS:
            print(f"[WARN] qfq 完整交易日不足({len(dates)}) → universe 缺位")
            return []
        T, d_prev, d_win = dates[-1], dates[-2], dates[-UNIV_MIN_LIST_BARS]
        for code, close, d in q.execute(
                "select code,close,date from stock_daily_meta_qfq where date>=?", (d_win,)):
            c6 = code[-6:] if isinstance(code, str) else ""
            if c6.isdigit():
                hist[c6][d] = close
        prev_map = {code[-6:]: c for code, c in q.execute(
            "select code,close from stock_daily_meta_qfq where date=?", (d_prev,))}
        q.close()
        r = sqlite3.connect(f"file:{UNIV_RAW_DB}?mode=ro", uri=True, timeout=30)
        for code, a, d in r.execute(
                "select code,amt,date from stock_daily_raw where date>=?", (dates[-21],)):
            c6 = code[-6:] if isinstance(code, str) else ""
            if c6.isdigit() and a:
                # raw.date=INTEGER, qfq.date=TEXT(已知坑: str/int 字典键恒不等) → 统一 str 键
                amts[c6][str(d)] = float(a)
        r.close()
    except Exception as e:
        print(f"[WARN] universe 数据读取失败({type(e).__name__}: {e}) → 缺位")
        return []

    from stock_name_lookup import get_name as _gn
    out, n_static, n_dyn = [], 0, 0
    for c6, dm in hist.items():
        if c6.startswith(("688", "8", "4", "92")) or T not in dm:
            continue
        # 次新过滤(宪法<60交易日不碰): 首行晚于窗口起点=60交易日内才上市=次新。
        # (2026-09-08体检补漏: 初版漏此判定致 N华润/惠科两只次新漏网; 不用"窗口满行数"
        #  判定——qfq 窗口端点天然缺行会误杀全市场, 首行日期法对停牌/缺行稳健)
        if min(dm) > d_win:
            continue
        nm = _gn(c6) or ""
        if "ST" in nm.upper():
            continue
        px = float(dm[T])
        if px > MAX_PRICE:
            continue
        seq = [dm[d] for d in sorted(dm)]
        a20 = [amts[c6][d] for d in sorted(amts.get(c6, {}))]
        adv = (sum(a20) / len(a20)) if len(a20) >= 20 else 0
        limit_up = False
        base = prev_map.get(c6)
        if base:
            chg = px / float(base) - 1
            limit_up = chg >= (0.195 if c6.startswith("30") else 0.098)
        high60 = px >= max(seq) * 0.999 if len(seq) >= UNIV_MIN_LIST_BARS else False
        dyn_amt = amts.get(c6, {}).get(str(T), 0)
        tags = []
        if adv >= UNIV_MIN_ADV:
            tags.append("静态")
            n_static += 1
        if (limit_up or high60) and dyn_amt >= UNIV_DYN_MIN_AMT:
            tags.append(("涨停" if limit_up else "") + ("新高60" if high60 else ""))
            n_dyn += 1
        if not tags:
            continue
        out.append({
            "code": c6, "name": nm, "source": "universe", "price": px,
            "industry": "", "note": f"宇宙[{'+'.join(t for t in tags if t)}] adv20={adv/1e8:.1f}亿",
        })
    print(f"[info] 可投宇宙: 静态资格{n_static} + 动态池{n_dyn} → {len(out)}只 (截面{T})")
    return out


def load_recent_sell_block(days: int = 20) -> set:
    """近期卖出禁入（2026-09-08 体检改进2落地）：近 N 自然日内 executor 有 SELL 记录的标的。

    语义：卖出必有理由（-8% 止损/趋势走坏/主人指令）——刚卖出的票短期不再进入候选视野，
    防止"刚走坏又回池"的信号污染；与执行层 attribution_feedback 重亏拦截互补。
    主人确要买回 → manual_confirmed 逐笔特批（A 臂）/ab_freeroute（B 臂实验）不受影响。
    读 executor_orders.jsonl 尾部（SELL 行，唯一真源）；解析失败 fail-open 返回空集。
    """
    cut = datetime.now().timestamp() - days * 86400
    blocked = set()
    try:
        with open(os.path.join(BASE, "data", "executor_orders.jsonl"), encoding="utf-8") as f:
            lines = f.readlines()[-400:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("action") != "SELL":
                continue
            ts = r.get("submitted_at", "")
            try:
                t = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
            except (ValueError, TypeError):
                continue
            if t >= cut:
                blocked.add(str(r.get("code", "")).zfill(6))
    except Exception as e:
        print(f"[WARN] 卖出禁入读取失败({type(e).__name__}: {e}) → fail-open不禁入")
        return set()
    if blocked:
        print(f"[info] 近{days}日卖出禁入: {len(blocked)}只 {sorted(blocked)[:6]}…")
    return blocked


def load_scan_csv(path: str) -> list[dict]:
    """mass_scan/universe_scan 输出（有则纳入，无则跳过）。"""
    out = []
    if not os.path.exists(path):
        # 时序修复: 扫描器 09:26 跑，晚于 09:10 白名单 → 当日必缺，回退昨日扫描并标注
        alt, lag = _latest_dated_file(os.path.dirname(path), "universe_scan_")
        if alt and lag is not None and lag >= 1:
            print(f"[WARN] 当日扫描未生成(09:26晚于白名单)，回退 {os.path.basename(alt)}（滞后{lag}天）")
            path = alt
        else:
            return out
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = norm6(row.get("code", ""))
            if not code or not board_ok(code):
                continue
            out.append({
                "code": code,
                "name": (row.get("name") or "").strip(),
                "source": "mass_scan",
                "price": _to_float(row.get("price")),
                "industry": "",
                "note": "mass_scan/universe_scan候选",
            })
    return out


def load_mainline_radar(date_str: str) -> list[dict]:
    """主线雷达候选（mainline_candidate_screener 产出，PROVEN 源，优先级最高）。

    2026-08-25 修复：此前 buy_whitelist 未收录主线雷达，导致 pre_trade 认可的 PROVEN 源
    被 R26 名单制拦死，"追强（第一批）"路径无法落地。现直接复用 screen() + flat_candidates()，
    与 daily_trade_plan 消费口径一致；price 缺失由 main() 的 qfq 价格富化补齐。"""
    out = []
    try:
        import mainline_candidate_screener as mcs
        res = mcs.screen(date_str)
        for x in mcs.flat_candidates(res):
            code = norm6(x.get("code", ""))
            if not code or not board_ok(code):
                continue
            out.append({
                "code": code,
                "name": (x.get("name") or "").strip(),
                "source": "mainline_radar",
                "price": _to_float(x.get("price")),
                "industry": (x.get("industry") or "").strip(),
                "note": f"主线雷达[{x.get('layer','')}] 预判{x.get('predictability','')}",
            })
        if out:
            print(f"[info] 主线雷达候选: {len(out)}只（{date_str}，PROVEN源）")
    except Exception as e:
        print(f"[WARN] mainline_radar 读取失败({type(e).__name__}) → 跳过")
    return out


def load_blocked_sectors() -> list[dict]:
    """读取最新 R24 板块高潮快照（用于标记+追加禁止板块）。"""
    files = sorted(glob.glob(os.path.join(OUT_DIR, "sector_block_snapshot_*.json")))
    if not files:
        return []
    try:
        with open(files[-1], encoding="utf-8") as f:
            snap = json.load(f)
        return snap.get("blocked_sectors") or []
    except Exception:
        return []


def load_factor_health() -> dict:
    """读取最新 M7 因子健康快照（bp_ep 来源注释用；无则空）。"""
    files = sorted(glob.glob(os.path.join(OUT_DIR, "factor_health_*.json")))
    if not files:
        return {}
    try:
        with open(files[-1], encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_attribution_risk() -> dict:
    """归因经验库联动（2026-08-24 落地，修复"归因库有数据但选股未自动读"缺口）：
    读取 attribution.db 近期风险记录（loss_case/failure_scene 经验库 + 跌停风险库），
    返回 {code6: [风险标签]}；命中标的降半仓 0.5x。无库/异常 → {}（优雅降级）。"""
    out: dict = {}
    _db = r"D:\zcodesj\attribution\attribution.db"
    if not os.path.exists(_db):
        return out
    try:
        import sqlite3 as _sq
        _conn = _sq.connect(f"file:{_db}?mode=ro", uri=True)
        _rows = _conn.execute(
            # 2026-08-24 修复: 落地既定"60日冷却期"语义(此前无窗口, 历史风险永久降半仓无限膨胀)
            "SELECT code, library_type, scenario_tags FROM experience_library "
            "WHERE library_type IN ('loss_case','failure_scene') "
            "AND created_at >= datetime('now','-60 day')").fetchall()
        for _code, _ltype, _tags in _rows:
            _c6 = norm6(_code)
            if len(_c6) == 6 and _c6 != "000000":  # 归因库存在脏代码(拼接/000000), 仅收合法6位
                out.setdefault(_c6, []).append(f"{_ltype}({(_tags or '')[:40]})")
        _rows = _conn.execute(
            "SELECT code, risk_tags FROM limit_down_risks "
            "WHERE trade_date >= strftime('%Y%m%d','now','-60 day')").fetchall()
        for _code, _tags in _rows:
            _c6 = norm6(_code)
            if len(_c6) == 6 and _c6 != "000000":
                out.setdefault(_c6, []).append(f"跌停风险({(_tags or '')[:40]})")
        _conn.close()
    except Exception as _e:
        print(f"[WARN] 归因联动读取失败: {_e}")
    if out:
        print(f"[info] 归因经验库联动: {len(out)} 只命中近期风险/失败记录")
    return out


def load_lhb_annotation() -> dict:
    """读取最新 ftshare 龙虎榜原始数据，聚合「机构专用」席位净额（买入端线索②，提示性标注）。

    输入 reports/raw/lhb_raw_*.json（09:10 agent 从 ftshare ft_abnormal_trading_details 拉取）。
    返回 {code6: {"inst_net": 万元(正=净买/负=净卖), "inst_seats": 机构席位数}}；无数据 → {}（优雅降级）。"""
    files = sorted(glob.glob(os.path.join(RAW_DIR, "lhb_raw_*.json")))
    if not files:
        return {}
    out: dict[str, dict] = {}
    try:
        with open(files[-1], encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw or []:
            code = norm6(str(item.get("symbol", "")).split(".")[0])
            if not code:
                continue
            inst_net = 0.0
            n_seats = 0
            for side in ("top_buyers", "top_sellers"):
                for seat in item.get(side) or []:
                    if "机构专用" in str(seat.get("name", "")):
                        inst_net += _to_float(seat.get("net")) or 0.0
                        n_seats += 1
            out[code] = {"inst_net": round(inst_net / 1e4, 2), "inst_seats": n_seats}
    except Exception as e:
        print(f"[WARN] 机构龙虎榜标注解析失败({type(e).__name__}) → 跳过标注")
    return out


def load_review_theme_annotation() -> tuple[dict, dict]:
    """读取最近复盘日的涨停归因主题标签（线索⑤，2026-09-09 主人裁决口径）。

    主人口径：白名单主线/次主线 = 每日复盘后根据市场情况判断（财联社收盘复盘页
    cls.cn/depth?id=1003 验证）——其结构化载体 = 归因库 limit_up_patterns.core_driver
    的「XX主线 / XX支线」段（T 日盘后人工复盘入库，T+1 晨白名单消费，时序无前视）。
    非机械聚类产物（firstboard_cluster_probe 的共振簇是感知层提示，与此互不替代）。

    返回 ({code6: "主线[农业农化]"/"次主线[化工油气地缘]"}, {"ok","date","n"})；
    归因库无数据/解析失败 → ({}, {"ok":False,...})（优雅降级，零拦截零降档）。"""
    meta = {"ok": False, "date": None, "n": 0}
    try:
        con = sqlite3.connect(r"file:D:/zcodesj/attribution/attribution.db?mode=ro", uri=True)
        try:
            # 取最近一个「复盘口径」交易日——排除 engine_v2 行（2026-09-09 实锚:
            # backfill aux 日更后引擎行先占 MAX(trade_date), 全表 MAX 会取到无主题
            # 标签的引擎日 → 标签 0 只白名单降级; 复盘标签恒晚引擎一拍）
            row = con.execute("SELECT MAX(trade_date) FROM limit_up_patterns "
                              "WHERE pattern_type != 'engine_v2'").fetchone()
            if not row or not row[0]:
                return {}, meta
            rev_date = int(row[0])
            rows = con.execute(
                "SELECT code, core_driver FROM limit_up_patterns WHERE trade_date=?",
                (rev_date,)).fetchall()
        finally:
            con.close()
        out: dict[str, str] = {}
        for code, driver in rows:
            c6 = norm6(str(code))
            if not c6 or not driver:
                continue
            main_t, sub_t = None, None
            for seg in str(driver).replace("/", "+").replace("｜", "+").split("+"):
                seg = seg.strip()
                if not main_t and seg.endswith("主线"):
                    main_t = seg[:-2]
                elif not sub_t and seg.endswith("支线"):
                    sub_t = seg[:-2]
            if main_t:
                out[c6] = f"主线[{main_t}]"
            elif sub_t:
                out[c6] = f"次主线[{sub_t}]"
        meta = {"ok": True, "date": rev_date, "n": len(out)}
        # 复盘断供提醒（2026-09-09 供给闭环B: 复盘标签滞后>=3天=当晚复盘未入库, 明示降级原因）
        try:
            qcon = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
            qmx = qcon.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
            qcon.close()
            # 2026-09-10 B5 修复: 原 (qmx-rev_date)//10000 整数差同月内恒 0(20260910-20260905
            # =5→0), WARN 实质永不触发; 改 strptime 日历差(与 limitup_review_cli check 同构)
            gap_days = (datetime.strptime(str(qmx), "%Y%m%d")
                        - datetime.strptime(str(rev_date), "%Y%m%d")).days
            if gap_days >= 3:
                print(f"[WARN] 复盘主线标签滞后{gap_days}天(复盘日{rev_date} vs qfq{qmx})——"
                      f"当晚复盘未入库(limitup_review_cli.py append), 白名单仅能标注到旧复盘日")
                meta["lag_days"] = gap_days
        except Exception:
            pass
        return out, meta
    except Exception as e:
        print(f"[WARN] 复盘主线标注解析失败({type(e).__name__}: {e}) → 跳过标注")
        return {}, meta


def _refresh_portrait_feed_if_stale() -> None:
    """快照缺失/落后于最新画像截面时自动重建（幂等 fail-open；不依赖 cron 改动，2026-09-05 对接）。"""
    feed_f = os.path.join(BASE, "data", "portrait_feed.json")
    snaps = sorted(glob.glob(os.path.join(BASE, "portrait_agent", "reports", "portrait_????????.json")))
    need = True
    if os.path.exists(feed_f) and snaps:
        try:
            with open(feed_f, encoding="utf-8") as f:
                s = json.load(f)
            with open(snaps[-1], encoding="utf-8") as f:
                newest = json.load(f).get("date")
            if str(s.get("snapshot_date")) == str(newest) and not s.get("unavailable"):
                need = False
        except Exception:
            need = True
    if need:
        try:
            subprocess.run([sys.executable, os.path.join(BASE, "portrait_feed.py"), "build"],
                           capture_output=True, timeout=120)
        except Exception as e:
            print(f"[WARN] 画像快照自动刷新失败({e}) → fail-open")


def load_portrait_annotation() -> tuple[dict, dict]:
    """读取画像标注快照（买入端线索④，2026-09-05 主人"全面对接"裁决；提示性 note）。

    快照唯一来源 data/portrait_feed.json（portrait_feed.py = 画像→屠龙唯一对接 owner）。
    返回 (标注dict {code: {prob, pct}}, 元信息 dict)；快照缺失/超龄/不可用 → ({}, {"ok": False, ...})
    fail-open：画像无拦截权/零降档——未过三闸+A/B前向（portrait_ab_track 首验2026-09-30）前
    不得拥有信号权（宪法§10 + 9/3名单制裁决）。"""
    _refresh_portrait_feed_if_stale()
    meta: dict = {"ok": False, "note": ""}
    feed_f = os.path.join(BASE, "data", "portrait_feed.json")
    if not os.path.exists(feed_f):
        meta["note"] = "快照不存在(先跑 portrait_feed.py build)"
        return {}, meta
    try:
        with open(feed_f, encoding="utf-8") as f:
            snap = json.load(f)
        if snap.get("unavailable"):
            meta["note"] = f"快照不可用({snap['unavailable']})"
            return {}, meta
        scores = snap.get("scores_short") or {}
        meta = {"ok": True, "snapshot_date": snap.get("snapshot_date"),
                "lag_days": (snap.get("freshness") or {}).get("lag_days"),
                "phase": (snap.get("shadow") or {}).get("phase"),
                "weight": (snap.get("shadow") or {}).get("weight"),
                "n_scores": len(scores),
                # 名单宽度Top500(20260913主人裁决); 双读过渡: 新字段topn_short优先, 旧快照回退top20_short
                "topn_short": snap.get("topn_short") or snap.get("top20_short") or []}
        return scores, meta
    except Exception as e:
        meta["note"] = f"快照解析失败({type(e).__name__}: {e})"
        return {}, meta


def load_forecast_annotation() -> dict:
    """读取中报业绩预告标注（买入端线索③，提示性标注），映射 code → 预告类型+同比。

    2026-08-23 统一数据源：优先读本地 fundamental_data.db `earnings_yjyg`（akshare
    stock_yjyg_em 补拉，report_date=20260630 中报），与 mainline_candidate_screener 同源、
    不依赖 09:10 agent 拉取时序；fallback 读 ftshare raw reports/raw/forecast_raw_*.json。
    无数据 → {}（优雅降级）。"""
    out: dict[str, dict] = {}
    # 1) 优先本地 earnings_yjyg 表（中报 report_date=20260630，结构化：code/type/pct_change）
    try:
        import sqlite3 as _sq
        con = _sq.connect(f"file:{EARN_DB}?mode=ro", uri=True, timeout=10)
        for code, typ, pct in con.execute(
            "SELECT code, type, pct_change FROM earnings_yjyg WHERE report_date=20260630"
        ).fetchall():
            c = norm6(str(code or "").split(".")[0])
            if not c:
                continue
            pct_s = f"{pct:.0f}" if pct is not None else ""
            out[c] = {"type": str(typ or "?"), "yoy_min": pct_s, "yoy_max": pct_s}
        con.close()
        if out:
            print(f"[info] 中报预告标注加载 {len(out)} 只（本地 earnings_yjyg 表）")
            return out
        print("[WARN] earnings_yjyg 20260630 无数据 → 降级 ftshare raw")
    except Exception as e:
        print(f"[WARN] earnings_yjyg 读取失败({type(e).__name__}) → 降级 ftshare raw")
    # 2) fallback ftshare raw（原路径）
    files = sorted(glob.glob(os.path.join(RAW_DIR, "forecast_raw_*.json")))
    if not files:
        return {}
    try:
        with open(files[-1], encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw or []:
            if str(item.get("report_type_cn", "")) != "中报":
                continue
            code = norm6(str(item.get("stock_code", "")).split(".")[0])
            if not code:
                continue
            out[code] = {
                "type": str(item.get("forecast_type") or "?"),
                "yoy_min": str(item.get("yoy_change_min") or ""),
                "yoy_max": str(item.get("yoy_change_max") or ""),
            }
    except Exception as e:
        print(f"[WARN] 中报预告标注解析失败({type(e).__name__}) → 跳过标注")
    return out


def classify_low_pos_breakout(code: str, asof: str = "") -> bool:
    """20日低位横盘+放量突破判定（买入端线索①，复用 breakout_hitrate_baseline 口径并加低位条件）。

    横盘 = 20日振幅≤25% 且 涨幅≤15%；放量 = 当日量/20日均量≥1.5；
    突破 = 当日收盘≥前20日高点×0.98（2026-08-24 与 tulong_hotspot_breakout_picker 判据对齐，
    替换与突破语义相反的 pos≤0.5 旧判据）。数据读本地 qfq 库，失败返回 False（不倾斜）。"""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
        rows = con.execute(
            "SELECT date, open, close, high, low, volume FROM stock_daily_meta_qfq "
            "WHERE code=? ORDER BY date DESC LIMIT 40", (code,)).fetchall()
        con.close()
        if not rows:
            return False
        kl = [list(r) for r in reversed(rows)]
        if asof:
            kl = [k for k in kl if str(k[0]) <= asof]
        if len(kl) < 22:
            return False
        prev = kl[-22:-1]
        closes = [float(k[2]) for k in prev]
        highs = [float(k[3]) for k in prev]
        lows = [float(k[4]) for k in prev]
        vols = [float(k[5]) for k in prev]
        if min(lows) <= 0 or closes[0] <= 0:
            return False
        rng = (max(highs) - min(lows)) / min(lows) * 100
        chg = (closes[-1] - closes[0]) / closes[0] * 100
        avg_vol = sum(vols) / len(vols)
        vr = float(kl[-1][5]) / avg_vol if avg_vol > 0 else 0
        hz = rng <= 25 and chg <= 15
        bo = hz and vr >= 1.5
        hi20, lo20 = max(highs), min(lows)
        # 2026-08-24 P1修复: 旧判据 pos≤0.5（收盘在区间下半部）与"突破"语义相反，
        # 实测给放量滞涨/出货形态加仓档；对齐 picker 真源判据=突破箱体上沿(收盘≥hi20*0.98)
        breakout = float(kl[-1][2]) >= hi20 * 0.98 if hi20 > 0 else False
        return bool(bo and breakout)
    except Exception:
        return False


def load_qfq_prices(codes: set[str]) -> dict[str, float]:
    """从本地qfq库取各代码最新收盘价（离线，用于价格≤30富化过滤）。"""
    out: dict[str, float] = {}
    if not codes:
        return out
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
        cur = con.execute(
            "SELECT code, close FROM stock_daily_meta_qfq "
            "WHERE date=(SELECT MAX(date) FROM stock_daily_meta_qfq) AND code IN (%s)"
            % ",".join("?" * len(codes)), tuple(sorted(codes)))
        for c, p in cur.fetchall():
            # round 到 2 位：qfq 复权价含浮点尾数（如 1.9729579…），白名单价格列应干净（8/25 修复）
            out.setdefault(str(c).split(".")[0].zfill(6), round(float(p), 2) if p is not None else None)
        con.close()
    except Exception as e:
        print(f"[WARN] qfq价格富化失败({type(e).__name__}) → 缺失价格留给闸门终审")
    return out


# ===== K3 个股偏离度（2026-08-28 立项）=====
# 证据: 2016-2026 qfq 全史 1064万截面观测——ext20>15% → +5d -0.67%; >20% → +5d -1.00%(胜率37%)。
# 深跌桶(ext20≤-10%)为均值回归反弹的另一逻辑, 本项不碰。
# 计划层语义: ext20>15% → tier 减半（与 gate R31 REJECT@20% 互补: 白名单=计划层, gate=执行层当日追价）。
R31_EXT_HALF = 0.15


def load_ma20_map(codes: set[str]) -> dict[str, float]:
    """K3: 各代码最近20交易日 MA20（qfq 库, 离线只读）。
    数据异常 → {}（fail-open，不阻断名单生成）。返回 {code6: ma20}。
    """
    out: dict[str, float] = {}
    if not codes:
        return out
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
        for c in sorted(codes):
            rows = con.execute(
                "SELECT close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 20",
                (c.zfill(6),)).fetchall()
            closes = [r[0] for r in rows if r[0] is not None]
            if len(closes) < 15:
                continue
            ma20 = sum(closes) / len(closes)
            if ma20 > 0:
                out[c.zfill(6)] = ma20
        con.close()
    except Exception as e:
        print(f"[WARN] K3偏离度MA20计算跳过({type(e).__name__}) → fail-open")
    return out


SW_DB = r"D:\workbuddyspace\databases\sw_industry.db"  # 申万行业指数（L1 板块追高检查用）


def load_hot_sectors() -> dict[str, str]:
    """胜率计划1.2：申万L1最新两日涨幅 → 返回 {板块名: 描述}。

    当日>5% 或 2日累计>8% 视为板块追高区（8/18农业链顶部接入教训，-1.2万实证）。
    数据缺失/异常 → 空dict（fail-open，不阻断名单生成）。
    """
    out: dict[str, str] = {}
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True, timeout=5)
        rows = con.execute(
            "SELECT name, trade_date, close FROM industry_kline_sw WHERE level='L1' "
            "ORDER BY trade_date DESC LIMIT 93").fetchall()  # 31只L1 × 3日
        con.close()
        by_name: dict[str, list[float]] = {}
        for name, _d, close in rows:
            if close:
                by_name.setdefault(name, []).append(float(close))
        for name, closes in by_name.items():
            # rows 按 date DESC → closes[0]=最新日, [1]=昨日, [2]=前日
            if len(closes) < 2 or closes[1] <= 0:
                continue
            d1 = (closes[0] / closes[1] - 1) * 100
            d2 = ((closes[0] / closes[2] - 1) * 100) if len(closes) >= 3 and closes[2] > 0 else 0.0
            if d1 > 5 or d2 > 8:
                out[name] = f"当日{d1:+.1f}%/2日{d2:+.1f}%"
        return out
    except Exception as e:
        print(f"[WARN] 板块追高检查跳过({type(e).__name__}: {e}) → fail-open")
        return out


def detect_recent_board_break(codes: set[str], lookback: int = 5) -> dict[str, str]:
    """胜率计划1.3：近N交易日炸板（high触及涨停幅度但收盘未封）→ {code: 炸板日}。

    依据：19个月149个炸板样本次日开盘卖-1.37%/胜率25.5%（qfq自构造2025-01起全量验证）。
    涨停幅度：创业板300/301=19.85%，其余主板=9.85%（科创/北交已在board_ok剔除）。
    失败 → 空dict（fail-open）。
    """
    out: dict[str, str] = {}
    if not codes:
        return out
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=15)
        for c in sorted(codes):
            rows = con.execute(
                "SELECT date, close, high FROM stock_daily_meta_qfq WHERE code=? "
                "ORDER BY date DESC LIMIT ?", (c, lookback + 1)).fetchall()
            # rows 为 date DESC，reverse 成 ASC 后逐日用昨收判涨停幅度
            seq = list(reversed(rows))
            for i in range(1, len(seq)):
                d, close, high = seq[i]
                prev = float(seq[i - 1][1]) if seq[i - 1][1] else 0.0
                if prev <= 0 or not close or not high:
                    continue
                lim = 1.1985 if c.startswith(("300", "301")) else 1.0985
                if float(high) >= prev * lim and float(close) < prev * lim:
                    out[c] = str(d)
                    break
        con.close()
        return out
    except Exception as e:
        print(f"[WARN] 炸板检查跳过({type(e).__name__}: {e}) → fail-open")
        return out


def half_tier(tier: str) -> str:
    """档位减半：≥1.0x → 0.5x；<1.0x → 0.25x。非法输入回 0.5x。"""
    try:
        v = float(str(tier).replace("x", ""))
    except (TypeError, ValueError):
        return "0.5x"
    return "0.5x" if v >= 1.0 else "0.25x"


def safe_out_path(candidate: str, allowed_root: str) -> str:
    """写入路径守卫（2026-08-24 Mimosa 误拦根治，按 hook 建议的真实加固而非绕过）。

    realpath 规范化 + commonpath 限制在允许目录内，禁止 ../ 越界写。
    越界抛 ValueError（fail-closed），本生成器所有落盘文件均须在 OUT_DIR 内。
    """
    rp = os.path.realpath(candidate)
    root = os.path.realpath(allowed_root)
    if os.path.commonpath([rp, root]) != root:
        raise ValueError(f"写入路径越界被拒: {candidate} 不在允许目录 {root} 内")
    return rp


def append_to_plan(md_path: str, date_str: str, whitelist: list[dict],
                   blocked: list[dict]) -> bool:
    """幂等地把白名单+禁止板块追加进作战清单。已含标记则跳过。"""
    if not os.path.exists(md_path):
        return False
    marker = "🧾 今日买入白名单（系统生成）"
    with open(md_path, encoding="utf-8") as f:
        content = f.read()
    if marker in content:
        return False
    lines = [f"\n\n## {marker}（{date_str}，清单外禁止买入）\n"]
    if blocked:
        lines.append("### 🔴 今日禁止板块（R24高潮禁追，已过闸后仍禁止）\n")
        for b in blocked:
            lines.append(f"- **{b.get('name')}**（单日+{b.get('pct')}%，数据日{b.get('trade_date')}）")
        lines.append("")
    if whitelist:
        lines.append("### ✅ 今日可买白名单（仍需过 pre_trade_check_v2 全闸）\n")
        lines.append("| 代码 | 名称 | 来源 | 持有周期 | 价格 | 挂单价 | 档位 | 备注 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for w in whitelist:
            # 2026-08-28 修正: 此段漏改平铺0.995 → 与CSV分层贴现不一致(冒烟后复核发现)
            lp = round(w["price"] * _tier_discount(w.get("tier", "1.0x")), 2) if w.get("price") else "-"
            lines.append(
                f"| {w['code']} | {w['name'] or '-'} | {w['source']} | "
                f"{holding_horizon.horizon_label(w['source'])} | "
                f"{w['price'] if w['price'] is not None else '-'} | "
                f"{lp} | "
                f"{w.get('tier', '1.0x')} | {w['note']} |")
        lines.append("")
        lines.append("> 执行纪律（胜率计划 2026-08-24 + 2026-08-28 分层贴现）：① 挂单价=参考价×按tier折扣（1.2x→0.998/1.0x→0.995/0.5x→0.993）限价挂单，**不追价**，当日不成交即放弃；"
                     "② 持有周期规则（C7-R28）：长线票<10天禁趋势卖出（仅-8%或月度调仓换股）；短线票<5天卖出需额外确认；"
                     "③ 0.5x/0.25x 档位=仓位减半/减四分之一，标注板块追高/炸板/归因风险的票谨慎对待。\n")
    else:
        lines.append("（今日无白名单候选——即无清单，禁止买入）\n")
    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


def main() -> int:
    # 2026-09-24 主人令「全面取消、废除白名单和月度TopN名单和TopN名单，股票A股所有股票中找。
    # 排除北交所、ST、科创板」——白名单生成永久停机: 本工具拒绝产出任何名单文件,
    # 无论被 09:10 链/cron/手工调用。选股宇宙=A股全市场(仅排除北交所/ST/科创板),
    # 准入唯一依据=admit 闸门信号源清单 + pre_trade_check_v2 R 闸实时判定。
    print("[ABOLISHED 2026-09-24] 主人令: 白名单/月度TopN/TopN 名单已全面废除, 本工具停止生成。")
    print("  选股宇宙=A股全市场(排除北交所/ST/科创板); 准入=admit信号源闸+R闸实时判定。")
    return 0
    ap = argparse.ArgumentParser(description="屠龙买入白名单生成器(M1)")
    ap.add_argument("--date", default=_today(), help="日期 YYYYMMDD（默认今日）")
    ap.add_argument("--max-price", type=float, default=MAX_PRICE)
    ap.add_argument("--no-plan-append", action="store_true", help="不追加作战清单")
    a = ap.parse_args()

    # 2026-09-10 加固: --date 直通多处落盘路径(信号/扫描/白名单/作战清单), 非法值
    # 会造成路径穿越式乱写(Mimosa 全量扫描 1681 项复核时对 date 面加固)
    if not re.fullmatch(r"\d{8}", str(a.date)):
        print(f"[FAIL] 非法 --date: {a.date!r}（须为 8 位数字 YYYYMMDD）", file=sys.stderr)
        return 2

    date_str = a.date
    signal_path = os.path.join(SIGNAL_DIR, f"signal_{date_str}.csv")
    scan_path = os.path.join(SCAN_DIR, f"universe_scan_{date_str}.csv")
    plan_path = safe_out_path(os.path.join(PLAN_DIR, f"trade_plan_{date_str}.md"), PLAN_DIR)

    blocked = load_blocked_sectors()
    blocked_names = {b.get("name") for b in blocked}
    fhealth = load_factor_health()
    decayed = set(fhealth.get("decayed") or [])
    fstatus = {k: (v or {}).get("status") for k, v in (fhealth.get("factors") or {}).items()}

    # 轨A因子健康动态权重（主人2026-08-22批准）：bp/ep/bp_ep critical→剔除；warn→0.5x；healthy→1.0x
    def _bp_ep_tier() -> str:
        st = {fstatus.get(f) for f in ("bp", "ep", "bp_ep") if fstatus.get(f)}
        if "critical" in st:
            return "exclude"
        if "warn" in st:
            return "0.5x"
        return "1.0x"

    cands: dict[str, dict] = {}
    # 2026-08-29: hotspot_breakout 证伪退役(_common RETIRED_SOURCES)，白名单不再收录；
    # 观察源语义: picker 照跑产 CSV 仅供监控(chain_step4_probe 探针)，admit 闸门拒绝买入。
    # 证伪档案: reports/VBT_HOTSPOT_PRESCREEN_20260828.md §7
    # 2026-08-29: smc_s2_roe 前视污染退役(主人裁决A)——smartmoneyconcepts swing_highs_lows
    # 含 shift(-8) 未来函数，白名单断接线(load 函数已删)，数据链 smc 环节照跑仅供监控。
    # 污染档案: reports/SMC_LOOKAHEAD_BIAS_DISCOVERY_20260829.md
    src_lists = {
        "mainline_radar": load_mainline_radar(date_str),
        "master_hist": load_signal_csv(signal_path),
        "bp_ep": load_bp_ep_csv(),
        "ml_signal": load_ml_scores(date_str),
        "full_market": load_full_market_csv(),
        "mass_scan": load_scan_csv(scan_path),
        "universe": load_universe(date_str),
    }

    # ---- P0-b 源级健康断言（2026-09-08 主人裁决: PROVEN 源缺失禁止静默缩容）----
    # 8/23 后 v4 管线调度断链, 9/8 master_hist 因 signal 文件超 3 天兜底窗静默归零
    # (16→0只, 白名单照常出, 山金国际隔日掉名单)——此教训固化为: 源计数落盘 meta json,
    # PROVEN 源为 0 时 notify error 告警; 白名单 CSV 本身仍照常产出(阻断生成会让
    # 整个买入通道瘫痪, 标注+告警即可让下游感知缺位)。
    src_counts = {k: len(v) for k, v in src_lists.items()}
    # mainline_radar 的主线资格层已被 MAINLINE-STAGE2 证伪停用(reports/MAINLINE_STAGE2_RESULT_20260904.md,
    # 断链不修复是刻意决策)——缺位属预期, 不进告警清单, 仅在 meta 中标注 disabled;
    # S2B"主线内低位补涨"影子期(prereg 20260908)过 wfa_gate 后才作为其替代层回接。
    proven_alert_list = ("master_hist", "bp_ep", "ml_signal", "mass_scan", "universe")
    missing_proven = [s for s in proven_alert_list if src_counts.get(s, 0) == 0]
    meta = {
        "date": date_str,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_counts": src_counts,
        "missing_proven_sources": missing_proven,
        "mainline_radar_disabled_by_falsification": True,
    }
    if missing_proven:
        msg = f"白名单{date_str} PROVEN源缺位: {','.join(missing_proven)} (counts={src_counts})"
        print(f"[ALERT] {msg}")
        meta["alert_sent"] = True
        try:
            subprocess.run([sys.executable, os.path.join(BASE, "notify.py"), "send",
                            "--title", "白名单源缺位", "--content", msg, "--level", "error"],
                           cwd=BASE, capture_output=True, text=True, timeout=30)
        except Exception as _e:
            print(f"[WARN] 源缺位告警发送失败: {type(_e).__name__} {_e}")
    try:
        with open(os.path.join(OUT_DIR, f"buy_whitelist_{date_str}_meta.json"), "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[WARN] meta json 写盘失败: {e}")

    for item in (src_lists["mainline_radar"] + src_lists["master_hist"] + src_lists["bp_ep"]
                 + src_lists["ml_signal"] + src_lists["full_market"]
                 + src_lists["mass_scan"] + src_lists["universe"]):
        c = item["code"]
        if not board_ok(c) or not name_ok(item.get("name")):
            continue
        # 2026-08-26 数据质量修复: 不在加载循环内按源文件旧价预筛 30 元铁律——
        # bp_ep/full_market 参考价来自月度名单文件(8/13-8/15 时点), 旧价预筛会误剔
        # "旧价>30但现价≤30"的候选; 30 元终筛统一在下方 qfq 价格刷新后执行。
        old = cands.get(c)
        # 保留优先级更高的来源；价格/行业信息不足时用更低优先级源补齐
        if old is None or SOURCE_PRIORITY[item["source"]] < SOURCE_PRIORITY[old["source"]]:
            cands[c] = item
        else:
            if old.get("price") is None and item.get("price") is not None:
                old["price"] = item["price"]
            if not old.get("industry") and item.get("industry"):
                old["industry"] = item["industry"]

    # ---- qfq 价格刷新（2026-08-26 数据质量修复）----
    # bp_ep/full_market 参考价来自月度名单文件（8/13-8/15 时点）而非最新收盘，直接作
    # limit_price 会让限价偏离真实市价（科森科技名单价16.65 vs 8/25收盘20.05，挂单21%
    # 够不着）、R27 价偏离闸门失去意义。这两源参考价一律以本地 qfq 库最新收盘价覆盖
    # （qfq=系统唯一价格真源）；其余源缺失价格仍按原逻辑补全。30 元铁律终筛在下方用
    # 刷新后价格执行。
    refresh_codes = {c for c, v in cands.items()
                     if v["source"] in ("bp_ep", "full_market") or v.get("price") is None}
    if refresh_codes:
        qfq_px = load_qfq_prices(refresh_codes)
        n_upd = 0
        for c in refresh_codes:
            p = qfq_px.get(c)
            if p:
                cands[c]["price"] = p
                n_upd += 1
        print(f"[info] qfq最新收盘价刷新 {n_upd}/{len(refresh_codes)}只（bp_ep/full_market旧价覆盖+缺失价补全）")
    excluded_price: list[str] = []
    excluded_factor: list[str] = []  # 2026-08-24: 与30元剔除拆分, 修文案混列
    excluded_chgmom: list[str] = []  # 2026-08-29 R32 高位动量禁买(O-B): chg20≥30% 剔除
    chgmom_failopen = 0
    for c in list(cands):
        if cands[c].get("price") is not None and cands[c]["price"] > a.max_price:
            excluded_price.append(f"{c}{cands[c]['name']}({cands[c]['price']:.2f}元)")
            del cands[c]
            continue
        # R32 高位动量(2026-09-08 主人裁决「涨停的拦在外面说明我们还是不对」): 剔除→纯标注。
        # 原语义(2026-08-29, 证据n=15全负)已由 9/8 全样本复核更新(R32_RECHECK_20260908.md:
        # 26万点=胜率40.2%劣势但均值+5.77%反超=低胜率高尾部), 强势主线股(涨停/连板)不再
        # 因涨得多被拦; 尾部风险经 note 留痕 + 执行层 R-S5 WARN 承接。
        chg20 = _chg20_of(c)
        if chg20 is None:
            chgmom_failopen += 1
        elif chg20 >= R32_CHG20_REJECT:
            cands[c]["note"] = (cands[c].get("note", "") + f" | 追高组(chg20={chg20*100:+.0f}%,胜率劣势留痕)").strip()
        if cands[c]["source"] == "bp_ep" and _bp_ep_tier() == "exclude":
            # 轨A因子critical → 直接剔除该因子源候选（fail-close，主人批准）
            excluded_factor.append(f"{c}{cands[c]['name']}")
            del cands[c]
        elif cands[c]["source"] in ("bp_ep", "full_market"):
            # full_market 与 bp_ep 同一PIT因子族(2026-08-24 P1修复: 此前漏接L3降权，
            # rank16-50段的价值陷阱/反弹追高票以1.0x档进名单，与回测L3降权口径不一致)
            # 2026-08-24 胜率计划1.4: full_market 基础档 1.0x→0.5x
            # （回测 rank16-50 胜率48.9% vs 核心段56.4%，收益+142.8%保留、敞口减半，主人批准）
            t = _bp_ep_tier() if cands[c]["source"] == "bp_ep" else "0.5x"
            # L3 失败形态降权: bp_ep→0.25x(2026-08-28 P3采纳, 冰封窗OOS确认v1通过) / full_market→0.5x(维持, 未独立OOS验证)
            l3 = cands[c].get("l3", "")
            if l3:
                if cands[c]["source"] == "bp_ep":
                    t = "0.25x"
                    cands[c]["note"] = (cands[c].get("note", "") + f" | L3:{l3}降四分之一仓").strip()
                else:
                    t = "0.5x"
                    cands[c]["note"] = (cands[c].get("note", "") + f" | L3:{l3}降半仓").strip()
            cands[c]["tier"] = t
        else:
            cands[c]["tier"] = "1.0x"

    # 归因经验库联动（2026-08-24 落地）：近期 loss_case/failure_scene/跌停风险命中 → 0.5x 降半仓
    attr_risk = load_attribution_risk()
    for c in list(cands):
        x = cands[c]
        if c in attr_risk:
            x["tier"] = "0.5x"
            x["note"] = (x.get("note", "") + f" | 归因风险({'/'.join(attr_risk[c][:2])})降半仓").strip()

    # ---- 近期卖出禁入（2026-09-08 体检改进2落地）----
    # 卖出必有理由(止损/趋势走坏/主人指令)——刚卖出的票不再占候选视野, 防"刚走坏又回池"。
    sell_blocked = load_recent_sell_block(days=20)
    if sell_blocked:
        _sb = [c for c in cands if c in sell_blocked]
        for c in _sb:
            del cands[c]
        if _sb:
            print(f"[info] 卖出禁入剔除 {_sb}")

    # ---- 胜率改进计划阶段1（2026-08-24 主人拍板"执行"，依据三轮独立验证）----
    # 1.2 板块追高禁买：申万L1 当日>5% 或 2日累计>8% → tier 减半（8/18农业链顶部接入-1.2万教训）
    hot_sectors = load_hot_sectors()
    if hot_sectors:
        print(f"[info] 板块追高区({len(hot_sectors)}个): " + "; ".join(f"{k}{v}" for k, v in hot_sectors.items()))
        for c in list(cands):
            x = cands[c]
            ind = (x.get("industry") or "").strip()
            if not ind:
                continue
            hit = next((h for h in hot_sectors if h in ind or ind in h), None)
            if hit:
                x["tier"] = half_tier(x.get("tier", "1.0x"))
                x["note"] = (x.get("note", "") + f" | 板块追高({hit}{hot_sectors[hit]})降半仓").strip()

    # 1.4 板块资金流双负降半仓（2026-08-26 O10, 8/24 信号资金背离教训）：
    # 申万L1 双口径5日净流入中位数双负 → 该板块候选 tier 减半（单口径负不降档）
    flow_risk_sectors = load_sector_flow_risk()
    if flow_risk_sectors:
        for c in list(cands):
            x = cands[c]
            ind = (x.get("industry") or "").strip()
            if not ind:
                continue
            hit = next((h for h in flow_risk_sectors if h in ind or ind in h), None)
            if hit:
                x["tier"] = half_tier(x.get("tier", "1.0x"))
                x["note"] = (x.get("note", "")
                             + f" | 板块资金流出({flow_risk_sectors[hit]})降半仓").strip()

    # 1.4b K3 个股偏离度降半仓（2026-08-28 立项，ext20>15% → tier 减半）：
    # 证据: 2016-2026 qfq 全史 1064万截面——ext20>15% → +5d -0.67%; >20% → +5d -1.00%(胜率37%)。
    # 深跌桶(ext20≤-10%)为均值回归反弹另一逻辑, 不碰; 与 gate R31 REJECT@20% 互补(计划层vs执行层)。
    try:
        _ma20_map = load_ma20_map({norm6(c) for c in cands})
    except Exception:
        _ma20_map = {}
    _n_ext_halved = 0
    for c in list(cands):
        x = cands[c]
        _ma = _ma20_map.get(norm6(c))
        _px = x.get("price")
        if _ma and _px and _ma > 0:
            _ext20 = (_px - _ma) / _ma
            if _ext20 >= R31_EXT_HALF:
                x["tier"] = half_tier(x.get("tier", "1.0x"))
                x["note"] = (x.get("note", "")
                             + f" | 偏离度ext20={_ext20*100:.0f}%降半仓(K3)").strip()
                x["ext20"] = round(_ext20, 4)
                _n_ext_halved += 1
    if _n_ext_halved:
        print(f"[info] K3偏离度降半仓 {_n_ext_halved} 只")

    # 1.5 个股资金背离 → 0.25x（R-20260827-03 规则②，2026-08-28 EXPERIMENTAL 灰度；
    # 298样本回测触发组T+3胜率5.0% vs 保留27.6%，京东方回放精确拦截；灰度积累后复核）
    cap_diverg = load_stock_capital_divergence()
    if cap_diverg:
        for c in list(cands):
            if c in cap_diverg:
                x = cands[c]
                x["tier"] = CAP_DIVERG_TIER
                x["note"] = (x.get("note", "")
                             + f" | {cap_diverg[c]}降{CAP_DIVERG_TIER}(灰度)").strip()

    # 1.3 近5日炸板票 → 0.25x（19个月149样本次日开盘-1.37%/胜率25.5%大样本实证）
    broke_boards = detect_recent_board_break(set(cands))
    if broke_boards:
        print(f"[info] 近5日炸板候选 {len(broke_boards)} 只 → 0.25x: " + ", ".join(broke_boards))
        for c, bd in broke_boards.items():
            x = cands.get(c)
            if x:
                x["tier"] = "0.25x"
                x["note"] = (x.get("note", "") + f" | 近5日炸板({bd})禁追").strip()

    # 买入端 3 条线索（2026-08-23 主人拍板全部实施）：①低位横盘突破+0.2档 ②机构龙虎榜标注 ③中报预告标注
    lhb_ann = load_lhb_annotation()
    fc_ann = load_forecast_annotation()
    if lhb_ann:
        print(f"[info] 机构龙虎榜标注加载 {len(lhb_ann)} 只（reports/raw/lhb_raw_*.json）")
    if fc_ann:
        print(f"[info] 中报预告标注就绪 {len(fc_ann)} 只")
    for c in list(cands):
        x = cands[c]
        if x.get("tier") == "1.0x" and classify_low_pos_breakout(c):
            x["tier"] = "1.2x"
            x["note"] = (x["note"] + " | 低位横盘放量突破+0.2档").strip()
        if c in lhb_ann:
            v = lhb_ann[c]
            if v["inst_net"] > 0:
                x["note"] += f" | 机构龙虎榜净买{v['inst_net']:.0f}万({v['inst_seats']}席)"
            elif v["inst_net"] < 0:
                x["note"] += f" | 机构龙虎榜净卖{abs(v['inst_net']):.0f}万"
            else:
                x["note"] += " | 机构龙虎榜上榜(机构净平)"
        if c in fc_ann:
            fc = fc_ann[c]
            if fc["yoy_min"] and fc["yoy_max"] and fc["yoy_min"] != fc["yoy_max"]:
                x["note"] += f" | 中报预告{fc['type']}({fc['yoy_min']}%~{fc['yoy_max']}%)"
            elif fc["yoy_min"]:
                x["note"] += f" | 中报预告{fc['type']}({fc['yoy_min']}%)"
            else:
                x["note"] += f" | 中报预告{fc['type']}"

    # 买入端线索④：画像标注（2026-09-05 主人"全面对接"裁决；提示性 note，零拦截权/零降档——
    # 画像未过三闸+A/B前向（portrait_ab_track，首验2026-09-30）前不得拥有信号权，宪法§10纪律）。
    portrait_ann, portrait_meta = load_portrait_annotation()
    if portrait_meta.get("ok"):
        print(f"[info] 画像标注就绪: 截面{portrait_meta['snapshot_date']}({portrait_meta['lag_days']}d前) "
              f"影子={portrait_meta['phase']}({portrait_meta['weight']}) short分数{portrait_meta['n_scores']}只")
        _p_hit = 0
        for _c, x in cands.items():
            _v = portrait_ann.get(_c)
            if not _v:
                continue
            _p_hit += 1
            _tag = "画像Top500" if _c in portrait_meta.get("topn_short") or _c in portrait_meta.get("top20_short", []) else "画像"
            x["note"] = (x.get("note", "") + f" | {_tag}分{_v['prob']:.3f}(前{_v['pct']}%)").strip()
        print(f"[info] 画像标注命中白名单候选 {_p_hit} 只")
    else:
        print(f"[info] 画像标注不可用({portrait_meta.get('note', '无快照')}) → 无画像列(fail-open)")

    # 买入端线索⑤：复盘主线/次主线标注（2026-09-09 主人裁决口径：主线=每日复盘人工判断
    # （财联社收盘复盘页验证），结构化载体=归因库 core_driver「XX主线/XX支线」段；
    # 纯提示性 note，零拦截权/零降档——不属 M-N2 冻结期闸门语义（不拦截/不降级/不调参））。
    theme_ann, theme_meta = load_review_theme_annotation()
    if theme_meta.get("ok"):
        print(f"[info] 复盘主线标注就绪: 复盘日{theme_meta['date']} 标签{theme_meta['n']}只")
        _t_hit = 0
        for _c, x in cands.items():
            _v = theme_ann.get(norm6(_c))
            if not _v:
                continue
            _t_hit += 1
            x["note"] = (x.get("note", "") + f" | {_v}").strip()
        print(f"[info] 复盘主线标注命中白名单候选 {_t_hit} 只")
    else:
        print("[info] 复盘主线标注不可用(归因库无复盘数据) → 无主线标注(fail-open)")

    # ---- 资金背离灰度（R-20260827-03, 2026-08-28 落档）----
    # em_20d(元) / 流通市值(万元×1e4) < -1% → tier×0.25 + note 标注；缺数据 fail-open 显式。
    # 依据 8/24 复盘"信号与资金背离次日禁追" + 298样本回测(触发组T+3胜率5.0% vs 保留组27.6%)。
    _em20 = _load_latest_em20d()
    _mv = _load_latest_circ_mv()
    _div_cnt = 0
    if _em20 and _mv:
        for _c in cands.values():
            _f = _em20.get(norm6(_c["code"]))
            _m = _mv.get(norm6(_c["code"]))
            if _f is None or not _m:
                continue
            _ratio = _f / (_m * 10000.0)
            if _ratio < -0.01:
                _old = _c.get("tier", "1.0x") or "1.0x"
                try:
                    # 档位下限 clamp 0.25x(2026-09-08 体检改进1: 多层叠加曾产生0.06x=实质踢出,
                    # 与"标注化不暗踢"的宇宙化语义冲突; 0.25x 已是深罚档)
                    _mul = max(float(_old.rstrip("x")) * 0.25, 0.25)
                    _c["tier"] = f"{_mul:.2f}x" if _mul < float(_old.rstrip("x")) else _old
                except ValueError:
                    pass
                _c["note"] = (f"{_c.get('note','')} | 资金背离{abs(_ratio)*100:.1f}%"
                              f"(em20d/流通) R-20260827-03").strip(" |")
                _div_cnt += 1
        print(f"[资金背离] em_20d/流通市值<-1% 降档 {_div_cnt} 只")
    else:
        print("[资金背离] 数据缺失(em_flow_snapshot/daily_basic) → 灰度未启用(fail-open显式标注)")

    # ---- R30b 组合级行业占比守卫（2026-08-28, E79）----
    # 依赖 09:10 链写入 data/positions_latest.json(westock 持仓快照); 缺快照/映射覆盖率低 → 显式标注。
    _pos = _load_positions_snapshot()
    _r30b_blocked = []
    # 豁免行业(2026-09-08 主人裁决「不减，可以超配」): data/r30b_sector_exempt.json
    try:
        with open(os.path.join(BASE, "data", "r30b_sector_exempt.json"), encoding="utf-8") as _ef:
            _r30b_exempt = set(json.load(_ef).get("exempt_sectors") or [])
    except Exception:
        _r30b_exempt = set()
    if _pos:
        for _c in list(cands.values()):
            _sname = (str(_c.get("industry") or "")).strip()
            if not _sname:
                try:
                    import sqlite3 as _sq
                    _con = _sq.connect(f"file:{SW1_MAP_DB}?mode=ro", uri=True, timeout=10)
                    _row = _con.execute(
                        "SELECT sector_name FROM stock_sector_sw1 WHERE substr(code,-6)=?",
                        (norm6(_c["code"]),)).fetchone()
                    _con.close()
                    _sname = _row[0] if _row else ""
                except Exception:
                    _sname = ""
            if not _sname:
                continue
            _expo = _industry_exposure(_pos, _sname)
            if _expo is None:
                continue
            _share, _known, _total = _expo
            if _share >= R30B_SECTOR_CAP:
                if _sname in _r30b_exempt:
                    continue  # 主人裁决豁免行业(如电力设备 20260908「不减，可以超配」)
                _r30b_blocked.append(f"{_c['code']} {_c.get('name','')}({_sname} {_share*100:.1f}%, 映射{_known}/{_total})")
                del cands[_c["code"]]
        if _r30b_blocked:
            print(f"[R30b] 行业持仓占比≥{R30B_SECTOR_CAP*100:.0f}% 剔除 {len(_r30b_blocked)} 只: "
                  + "; ".join(_r30b_blocked[:8]))
    else:
        print("[R30b] 无持仓快照(data/positions_latest.json) → 组合占比守卫未启用(fail-open显式标注, 由09:10链写入)")

    whitelist = sorted(cands.values(), key=lambda x: (SOURCE_PRIORITY[x["source"]], x["code"]))

    # ---- P2-D 信号健康度仓位联动(v1 备好默认关, WINRATE_UPGRADE_PLAN_20260829 §P2-D)----
    # RED(最近4期已满5日窗名单模拟超额均值≤-2pp) → 全名单 tier 再减半(资金管理层, 不改选股/频率/行业)。
    # 开关 TULONG_HEALTH_TIER=1 激活; 四周试点(开关期 vs 全额期实际买入超额)数据裁决前保持关(prereg 纪律)。
    if os.environ.get("TULONG_HEALTH_TIER") == "1":
        _grade = _load_list_health_grade()
        if _grade == "RED":
            for _c in cands.values():
                _c["tier"] = half_tier(_c.get("tier", "1.0x"))
                _c["note"] = (f"{_c.get('note', '')} | P2-D健康度RED全名单减半").strip(" |")
            print(f"[P2-D] 名单健康度 RED → 全名单 {len(cands)} 只 tier 减半")
        else:
            print(f"[P2-D] 名单健康度 {_grade} → 不动(仅RED触发)")
    else:
        print("[P2-D] 未激活(TULONG_HEALTH_TIER!=1, 四周试点数据裁决前保持关闭)")

    # 2026-08-24 P0修复补落(此前因hook误拦只定义未应用): 写出路径一律过 safe_out_path
    # 守卫，禁止 --date 拼接 ../ 越界写。
    out_path = safe_out_path(os.path.join(OUT_DIR, f"buy_whitelist_{date_str}.csv"), OUT_DIR)
    # 防覆盖留档（2026-08-24 溢价核对配套）：当日已有版本先备份，保住执行时基准
    if os.path.exists(out_path):
        import shutil
        _n = 1
        _bak = f"{out_path}.v{_n}"
        while os.path.exists(_bak):
            _n += 1
            _bak = f"{out_path}.v{_n}"
        shutil.copy2(out_path, _bak)
        print(f"[info] 当日白名单已存在，旧版备份 → {os.path.basename(_bak)}")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "source", "holding_horizon", "price", "limit_price", "industry", "tier", "note"])
        for x in whitelist:
            hz_label = holding_horizon.horizon_label(x["source"])
            # 胜率计划1.1 + 2026-08-28 分层贴现：挂单价=信号参考价×按tier折扣（1.2x→0.998/1.0x→0.995/0.5x→0.993），不追价，不成交即放弃
            lp = round(x["price"] * _tier_discount(x.get("tier", "1.0x")), 2) if x.get("price") else ""
            w.writerow([x["code"], x["name"], x["source"], hz_label,
                        x["price"] if x["price"] is not None else "",
                        lp,
                        x["industry"], x.get("tier", "1.0x"), x["note"]])

    # ---- forward 绩效台账自动登记（2026-09-08 主人裁决 P2 接线; fail-open 零影响主链）----
    # 白名单落盘后登记当日名单进 signal_forward_log.csv, 由 daily_signal_cron 每日
    # --backfill 回填 T+1/3/5/20 收益——白名单选股质量从此可被度量(此前 forward log
    # 仅 9/3 手动 4 行且无回填调度, 绩效闭环断裂)。record 同日去重幂等。
    try:
        import signal_forward_tracker as _sft
        _sft.record(date_str)
    except Exception as _e:
        print(f"[WARN] forward 台账登记失败(不阻断): {type(_e).__name__} {_e}")

    # ---- 画像 A/B 台账自动登记（2026-09-05 接线, 为 9/30 首验积累每日数据; fail-open 零影响白名单主链）----
    # A=白名单最终输出序(csv 行序=单一真源), B=portrait_ab_track 内部按画像分重排, K=20。
    # 同日已登记时 append 自行拒绝(无 --force), 链内重跑幂等安全。
    try:
        import subprocess as _sp
        with open(out_path, encoding="utf-8-sig") as _f:
            _codes = [row["code"] for row in csv.DictReader(_f) if row.get("code")]
        if len(_codes) >= 20:
            _r = _sp.run([sys.executable, os.path.join(BASE, "portrait_ab_track.py"), "append",
                          "--date", date_str, "--list", ",".join(_codes), "--k", "20"],
                         capture_output=True, text=True, timeout=120)
            _tail = (_r.stdout.strip().splitlines() or [""])[-1]
            print(f"[info] 画像A/B台账: {_tail or ('exit=' + str(_r.returncode))}")
        else:
            print(f"[info] 画像A/B台账跳过: 白名单{len(_codes)}只<20")
    except Exception as _e:
        print(f"[WARN] 画像A/B台账登记失败({_e}) → fail-open")

    # ---- pa 画像标注列同步标注（2026-09-10 B-M7 接线: 消除跨任务时序依赖）----
    # 原 pa_ 列由 schtasks Annotate_0935 单独跑, 白名单任何重生成(手动/链内 21 点后)
    # 都会抹掉 pa 列直到次日 09:35——"935 只命中"承诺依赖跨任务时序。现白名单落盘后
    # 即时同步调 annotate_whitelist(幂等: 已有 pa_ 列则刷新值, .pa_bak 保留上一版)。
    try:
        _pa = subprocess.run([sys.executable, os.path.join(BASE, "portrait_agent",
                                                           "annotate_whitelist.py")],
                             capture_output=True, text=True, timeout=120, encoding="utf-8",
                             env=dict(os.environ, PYTHONUTF8="1"))
        _pa_tail = (_pa.stdout.strip().splitlines() or [""])[-1]
        print(f"[info] pa列标注: {_pa_tail or ('exit=' + str(_pa.returncode))}")
    except Exception as _e:
        print(f"[WARN] pa列标注失败({_e}) → fail-open(次日 09:35 任务兜底)")

    # 2026-08-24 P2修复(verify_stock_names 程序化接线): 落盘后强制跑名称校验，
    # ❌ 即代码↔名称错配(改名/陈旧meta)时显著标注+notify(宪法铁律: ❌ 禁发布)。
    # 此前校验仅存在于 SOP 文字，无机器消费方。不自动删 CSV(留待人工复核)。
    try:
        _vr = subprocess.run([sys.executable, r"D:\zcodesj\verify_stock_names.py", out_path],
                             capture_output=True, text=True, timeout=60, encoding="utf-8",
                             env=dict(os.environ, PYTHONUTF8="1"))
        if _vr.returncode != 0:
            print(f"⛔⛔ 名称校验 FAIL(❌禁发布): 退出码={_vr.returncode}")
            print(f"   {_vr.stdout.strip()[-400:] or _vr.stderr.strip()[-400:]}")
            subprocess.run([sys.executable, r"D:\zcodesj\notify.py", "send",
                            "--title", "白名单名称校验失败",
                            "--content", f"buy_whitelist_{date_str}.csv 名称校验❌，禁止据名单下单",
                            "--level", "error"], timeout=60)
        else:
            print("[info] 名称校验 ✅ 通过")
    except Exception as _ve:
        print(f"[WARN] 名称校验未执行({_ve})——人工补跑 verify_stock_names.py {out_path}")

    print(f"=== 买入白名单 {date_str} ===")
    print(f"候选总数: {len(whitelist)} 只（价格>30元剔除 {len(excluded_price)} 只 / 因子critical剔除 {len(excluded_factor)} 只 / R32高位动量剔除 {len(excluded_chgmom)} 只[fail-open {chgmom_failopen}]）")
    if excluded_price:
        print(f"  已按30元铁律剔除: {', '.join(excluded_price[:15])}")
    if excluded_chgmom:
        print(f"  R32高位动量(前20日涨幅≥30%)剔除: {', '.join(excluded_chgmom[:15])}")
    if blocked:
        print(f"🔴 今日禁止板块: {', '.join(sorted(blocked_names))}")
    for x in whitelist:
        tag = " 🔴禁买板块" if x.get("industry") in blocked_names else ""
        tier = x.get("tier", "1.0x")
        if tier != "1.0x":
            tag += f" ⚠️档位{tier}(因子健康)"
        hz = holding_horizon.horizon_of(x["source"])
        tag += f" [{'长线' if hz == 'long' else '短线'}]"
        print(f"  {x['code']} {x['name']:<8} [{x['source']}] {x['price'] if x['price'] else '-'} {tier}{tag}")
    if not whitelist:
        print("  （无候选 —— 无清单即禁买）")
    print(f"→ {out_path}")

    if not a.no_plan_append:
        appended = append_to_plan(plan_path, date_str, whitelist, blocked)
        if appended:
            print(f"→ 已追加至作战清单 {plan_path}")
        elif not os.path.exists(plan_path):
            print(f"[WARN] 作战清单不存在: {plan_path}（跳过追加）")

    # ---- 复评定级（E76, 2026-08-28 脚本化打包：prompt 段会话可跳过 → 并入本脚本使其不可跳过）----
    try:
        import re as _re2
        import sqlite3 as _sq3
        _con = _sq3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        _prev = _con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<?", (date_str,)).fetchone()[0]
        _con.close()
        _pr_path = os.path.join(OUT_DIR, f"pending_review_{_prev}.md") if _prev else ""
        if _pr_path and os.path.exists(_pr_path):
            _expired = []
            for _line in open(_pr_path, encoding="utf-8"):
                _m = _re2.match(r"\|\s*(?:sz|sh|bj)?(\d{6})\s*\|\s*([^|]+?)\s*\|\s*([\d.]+)\s*\|", _line)
                if _m:
                    _expired.append((_m.group(1), _m.group(2).strip(), _m.group(3)))
            _wl_map = {norm6(w["code"]): w for w in whitelist}
            print(f"[复评定级] 昨日({_prev})EXPIRED_READ {len(_expired)} 只 vs 今日白名单交叉:")
            for _c6, _nm, _px in _expired:
                _w = _wl_map.get(_c6)
                if _w:
                    print(f"  ✅可定级 {_nm}({_c6}) 在名单({_w['source']}, tier={_w.get('tier','1.0x')}, "
                          f"limit={_w.get('limit_price') or round((_w['price'] or 0)*_tier_discount(_w.get('tier','1.0x')),2)})"
                          f" — 不自动续挂不追价")
                else:
                    print(f"  ⏹放弃 {_nm}({_c6}) 不在今日名单(昨日委托失效,无新信号)")
        else:
            print(f"[复评定级] 无 pending_review_{_prev or '?'}（昨日无未成交委托）")
    except Exception as _pe:
        print(f"[复评定级] 读取失败({type(_pe).__name__}: {_pe}) → fail-open")

    # ---- 自省行动核对（准则42/E71, 2026-08-28 脚本化打包）----
    try:
        _sra = os.path.join(OUT_DIR, "SELF_REVIEW_ACTIONS.md")
        _pending = [l.strip()[:130] for l in open(_sra, encoding="utf-8")
                    if l.strip().startswith("|") and any(t in l for t in ("| ⬜", "| 🟡", "| ⏸", "| 🔴"))]
        if _pending:
            print(f"[自省行动核对] 未闭环 {len(_pending)} 项（准则42: 须列盘前简报并标延期）:")
            for _p in _pending:
                print("  " + _p)
        else:
            print("[自省行动核对] 行动清单全部闭环 ✅")
    except Exception as _se:
        print(f"[自省行动核对] 读取失败({type(_se).__name__}) → fail-open")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
