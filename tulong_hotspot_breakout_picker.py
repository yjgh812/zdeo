#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""热点题材+放量突破底部盘整+未涨停 选股器（C方案，2026-08-24 主人拍板新增）。

⚠️ 2026-08-29 证伪降级为观察源（_common.RETIRED_SOURCES，admit 闸门拒绝买入）：
  形态层 45 组 OAT 预筛全负期望（-0.2~-1.9%/笔）+ 主线层负增量（全集 -1.17% →
  ∩主线 -2.32%/笔）+ 市值层无影响；三独立证据链（vbt 引擎回放 + 8/26 跟踪池复盘
  + 8/24-28 实盘买入即跌）。白名单已断开收录（tulong_buy_whitelist 2026-08-29）。
  本脚本**每日照常运行**：产出 hotspot_breakout_latest.csv 仅供监控/复盘观察
  （chain_step4_probe 探针继续探测），不再作为买入信号源。
  证伪档案: reports/VBT_HOTSPOT_PRESCREEN_20260828.md

定位：把「当前热点主线 + 刚刚放量突破底部盘整 + 未涨停 + ≤20元 + 中小盘/创业板」从
人工/外部扫描收敛为系统内独立信号源 `hotspot_breakout`，产出候选并入 buy_whitelist
链路（→作战清单 → pre_trade_check 闸门 → trade_executor），彻底消除"选股各干各的"。

复用（单一真源，不另造判据）：
  - board_ok / name_ok ← tulong_buy_whitelist.py（剔除铁律唯一真源）
  - mainline_candidate_screener.screen()            ← 系统主线判定（涨停≥5=主线，≥10=强主线）
  - industry_map_ths / daily_basic（fundamental_data.db）← 本地 代码→行业/名称 / 流通市值

过滤漏斗（顺序固定）：
  1. 剔除铁律：688科创 / 8/4/92北交所 + ST（board_ok / name_ok）
  2. 价格 ≤ 20 元（--price-cap，默认20，用户硬约束）
  3. 未涨停：当日涨幅 < 涨停线（300/301→20%，其余→10%）且 当日上涨
  4. 放量突破底部盘整：底部横盘(前20日振幅≤25% 且 涨幅≤15%) + 放量(量比≥1.5)
     + 突破箱体上沿(收盘≥前20日高点×0.98 且 当日涨幅≥3%) + 上升趋势(收盘>MA20)
     （2026-08-24 修正：初版误用 classify_low_pos_breakout 的"低位pos≤0.5"，
       与"突破"语义相反，实测 8/21 筛出 0 只；改为突破箱体语义）
  6. 热点主线：一级行业 ∈ 当日主线一级行业集合（涨停≥5）
  7. 中小盘/创业板：300/301 直接放行；主板/中小板 流通市值 < 100亿（--float-mv-cap）
  8. 业绩预筛（2026-08-28 新增）：2026 中报预告 type ∈ {首亏,续亏,增亏,减亏} 剔除
     （earnings_yjyg 结构化枚举，无预告记录 fail-open 放行；闸门仍兜底）

输出：reports/hotspot_breakout_latest.csv（UTF-8-BOM，含 date 列；buy_whitelist 用 glob 取最新）。
无候选则输出空文件头 + 显式打印。纯标准库；akshare / 系统脚本为可选 import（失败显式 WARN）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

BASE = r"D:\zcodesj"
OUT_DIR = r"D:\zcodesj\reports"
CACHE_DIR = r"D:\zcodesj\_cache"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
ZT_CACHE_PATH = Path("D:/zcodesj/_cache/zt_pool_latest.json")
OUT_PATH = Path("D:/zcodesj/reports/hotspot_breakout_latest.csv")

MAX_PRICE = 20.0
FLOAT_MV_CAP = 100.0
MAINLINE_MIN = 5

sys.path.insert(0, BASE)
try:
    from tulong_buy_whitelist import board_ok, name_ok, norm6
except Exception as _e:  # pragma: no cover
    raise RuntimeError(f"无法复用 tulong_buy_whitelist 判据: {_e}")


def _latest_qfq_date() -> str:
    """qfq 库最新交易日（YYYYMMDD）。"""
    import sqlite3
    con = sqlite3.connect("file:" + QFQ_DB + "?mode=ro", uri=True, timeout=60)
    d = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    con.close()
    return str(d)


def load_ind_name() -> tuple[dict[str, str], dict[str, str]]:
    """全市场 代码→(一级行业, 名称) 映射（industry_map_ths 本地表，失败→空 dict）。

    返回 (ind1_map, name_map)：行业用于主线过滤，名称用于 ST 剔除与输出，均本地无网络。"""
    ind1: dict[str, str] = {}
    names: dict[str, str] = {}
    try:
        import sqlite3
        con = sqlite3.connect("file:" + FUND_DB + "?mode=ro", uri=True, timeout=60)
        for code, name, ind in con.execute(
                "SELECT code, name, industry FROM industry_map_ths"):
            c6 = str(code).zfill(6)
            if name:
                names[c6] = str(name).strip()
            ind_a = (ind or "").split("-")[0].strip()
            if ind_a:
                ind1[c6] = ind_a
        con.close()
    except Exception as e:
        print(f"[WARN] industry_map_ths 读取失败({type(e).__name__}) → 行业/名称映射缺失")
    return ind1, names


def load_bad_earnings(report_period: str = "20260630") -> set[str]:
    """2026 中报预告亏损集合（业绩雷预筛，item6/8/28）。

    读 fundamental_data.earnings_yjyg 结构化 type 枚举，亏损类（首亏/续亏/增亏/减亏）
    一律剔除——"减亏"仍为负净利，对突破追涨候选同样是雷。无记录/读库失败 → 空集
    （fail-open，闸门兜底），显式打印条数便于核对。"""
    bad: set[str] = set()
    loss_types = {"首亏", "续亏", "增亏", "减亏"}
    try:
        import sqlite3
        con = sqlite3.connect("file:" + FUND_DB + "?mode=ro", uri=True, timeout=60)
        ph = ",".join("?" * len(loss_types))
        rows = con.execute(
            "SELECT code FROM earnings_yjyg WHERE report_date=? AND type IN (" + ph + ")",
            (report_period, *sorted(loss_types))).fetchall()
        bad = {str(r[0]).zfill(6) for r in rows}
        con.close()
        print(f"[info] 业绩预筛({report_period}): 亏损预告 {len(bad)} 只（首亏/续亏/增亏/减亏，无记录 fail-open）")
    except Exception as e:
        print(f"[WARN] earnings_yjyg 业绩预筛读取失败({type(e).__name__}) → 预筛放行(闸门兜底)")
    return bad


def load_circ_mv(codes: set[str], date_str: str) -> dict[str, float]:
    """从 daily_basic 读指定日各股流通市值（万元→亿；失败→{}，中小盘过滤 fail-open）。"""
    out: dict[str, float] = {}
    if not codes:
        return out
    try:
        import sqlite3
        con = sqlite3.connect("file:" + FUND_DB + "?mode=ro", uri=True, timeout=60)
        # daily_basic.ts_code 带交易所后缀（如 000931.SZ），6位码需加后缀匹配
        def _ts(c6: str) -> str:
            return c6 + (".SH" if c6.startswith(("6", "9")) else ".SZ")
        ts_codes = sorted(_ts(c) for c in codes)
        ph = ",".join("?" * len(ts_codes))
        rows = con.execute(
            "SELECT ts_code, circ_mv FROM daily_basic WHERE trade_date=? AND ts_code IN (" + ph + ")",
            (date_str, *ts_codes)).fetchall()
        for ts, mv in rows:
            if mv is not None:
                out[str(ts).split(".")[0].zfill(6)] = float(mv) / 1e4  # 万元 → 亿
        con.close()
    except Exception as e:
        print(f"[WARN] daily_basic 流通市值读取失败({type(e).__name__}) → 中小盘过滤放行(闸门兜底)")
    return out


def get_mainline_sectors(date_str: str, ind1: dict[str, str]) -> tuple[set[str], dict[str, dict]]:
    """主线一级行业集合 + 详情。

    主路径：复用 mainline_candidate_screener.screen() 主线判定（涨停≥5=主线），
    对主线内涨停股用 industry_map_ths 反查一级行业 → 与候选股同口径。
    fallback：akshare 涨停池按一级行业聚合（涨停≥5）。全部失败 → 空（显式 WARN）。"""
    sectors: set[str] = set()
    detail: dict[str, dict] = {}
    try:
        import mainline_candidate_screener as mcs
        res = mcs.screen(date_str)
        for ml in res.get("mainlines", []):
            tier, cnt = ml.get("tier", "主线"), ml.get("count", 0)
            for s in ml.get("stocks", []):
                i1 = ind1.get(norm6(s.get("code", "")))
                if i1:
                    sectors.add(i1)
                    detail.setdefault(i1, {"tier": tier, "count": cnt})
        if sectors:
            print(f"[info] 主线一级行业({len(sectors)}): " + ", ".join(sorted(sectors)))
            return sectors, detail
        print(f"[WARN] mainline screen 无主线（attribution.db 无 {date_str} 范式？）→ 尝试 akshare")
    except Exception as e:
        print(f"[WARN] mainline_candidate_screener.screen 失败({type(e).__name__}) → 尝试 akshare")

    zt_rows = _fetch_zt_pool(date_str)
    if zt_rows:
        cnt: Counter = Counter()
        for code in zt_rows:
            i1 = ind1.get(code)
            if i1:
                cnt[i1] += 1
        sectors = {k for k, v in cnt.items() if v >= MAINLINE_MIN}
        if sectors:
            print(f"[info] 主线一级行业(akshare涨停池聚合 {len(sectors)}): " + ", ".join(sorted(sectors)))
            return sectors, detail
    print("[WARN] 无可用主线行业 → 候选将为空（fail-open 显式说明，不硬造）")
    return sectors, detail


def _fetch_zt_pool(date_str: str) -> list[str]:
    """拉当日涨停股代码：本地缓存优先（Path 字面量读写，内容带 date 校验）。"""
    _d = re.sub(r"[^0-9]", "", date_str or "")
    if len(_d) != 8:
        print(f"[WARN] 非法日期，跳过涨停池: {date_str!r}")
        return []
    if ZT_CACHE_PATH.exists():
        try:
            with ZT_CACHE_PATH.open("r", encoding="utf-8") as f:
                blob = json.load(f)
            if isinstance(blob, dict) and blob.get("date") == _d:
                return [norm6(c) for c in blob.get("codes", [])]
        except Exception:
            pass
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=_d)
        codes = [norm6(str(c)) for c in df["代码"]]
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with ZT_CACHE_PATH.open("w", encoding="utf-8") as f:
                json.dump({"date": _d, "codes": codes}, f, ensure_ascii=False)
        except Exception:
            pass
        print(f"[info] 涨停池 akshare: {_d} {len(codes)}只（已缓存）")
        return codes
    except Exception as e:
        print(f"[WARN] akshare 涨停池失败({type(e).__name__}) → 无涨停数据")
        return []


def scan_qfq_candidates(date_str: str, price_cap: float) -> list[dict]:
    """qfq 库全市场粗筛：价格≤cap + 当日上涨 + 未涨停（SQL 参数化）。

    2026-08-24 P2修复: date_str 参数此前完全未用(只取库最新3日), 回灌历史日(--date
    20260821)时输出 date=用户日但价格形态来自库最新日, 与 get_mainline_sectors(date_str)
    跨日错配。现以 date_str 为上界取最近两根交易日(<=date_str)。"""
    import sqlite3
    con = sqlite3.connect("file:" + QFQ_DB + "?mode=ro", uri=True, timeout=60)
    d_target = str(date_str).replace("-", "")
    rows = con.execute(
        "SELECT date FROM (SELECT DISTINCT date FROM stock_daily_meta_qfq "
        "WHERE date<=? ORDER BY date DESC LIMIT 3)", (d_target,)).fetchall()
    dates = [r[0] for r in rows]
    if len(dates) < 2:
        print("[WARN] qfq 库交易日不足 → 无候选")
        con.close()
        return []
    d_latest, d_prev = str(dates[0]), str(dates[1])
    cur_rows = con.execute(
        "SELECT code, close, volume FROM stock_daily_meta_qfq WHERE date=?", (d_latest,)).fetchall()
    prev_rows = con.execute(
        "SELECT code, close FROM stock_daily_meta_qfq WHERE date=?", (d_prev,)).fetchall()
    con.close()
    prev_close = {str(c).split(".")[0].zfill(6): float(p) for c, p in prev_rows}

    cands = []
    for code, close, _vol in cur_rows:
        c6 = str(code).split(".")[0].zfill(6)
        if not board_ok(c6):
            continue
        close = float(close)
        if not (0 < close <= price_cap):
            continue
        pc = prev_close.get(c6)
        if not pc:
            continue
        pct = (close / pc - 1) * 100
        lim_pct = 20.0 if c6[:3] in ("300", "301") else 10.0
        limit_price = round(pc * (1 + lim_pct / 100.0), 2)  # 涨停价（昨收×1.1/1.2 四舍五入到分）
        if pct <= 0 or close >= limit_price:  # 当日上涨 且 未涨停（按涨停价精确判断）
            continue
        cands.append({"code": c6, "price": close, "pct_today": round(pct, 2), "date": d_latest})
    print(f"[info] qfq 粗筛: 全市场{len(cur_rows)}只 → 价格≤{price_cap:g}+当日上涨+未涨停 {len(cands)}只")
    return cands


def _read_k40(code: str) -> list[list]:
    """读最近40根日K（qfq 库，与 classify_low_pos_breakout 同源；code 经 SQL 参数化）。"""
    import sqlite3
    con = sqlite3.connect("file:" + QFQ_DB + "?mode=ro", uri=True, timeout=60)
    rows = con.execute(
        "SELECT date, open, close, high, low, volume FROM stock_daily_meta_qfq "
        "WHERE code=? ORDER BY date DESC LIMIT 40", (code,)).fetchall()
    con.close()
    return [list(r) for r in reversed(rows)]


def breakout_metrics(k40: list[list]) -> dict | None:
    """形态指标：量比 / 20日区间 / MA20 / 20日高点（横盘与突破判据用）。

    口径：prev=前21根(不含当日)，横盘=20日振幅rng20≤25% 且 涨幅chg20≤15%，
    放量=当日量/前21日均量 vr≥1.5，突破=当日收盘≥前20日高点hi20*0.98。"""
    if len(k40) < 22:
        return None
    prev = k40[-22:-1]
    closes = [float(k[2]) for k in prev]
    highs = [float(k[3]) for k in prev]
    lows = [float(k[4]) for k in prev]
    vols = [float(k[5]) for k in prev]
    if min(lows) <= 0 or closes[0] <= 0:
        return None
    hi20, lo20 = max(highs), min(lows)
    rng20 = (hi20 - lo20) / lo20 * 100
    chg20 = (closes[-1] - closes[0]) / closes[0] * 100
    avg_vol = sum(vols) / len(vols)
    vr = float(k40[-1][5]) / avg_vol if avg_vol > 0 else 0.0
    ma20 = sum(float(k[2]) for k in k40[-20:]) / 20.0
    return {"vr": round(vr, 2), "rng20": round(rng20, 1), "chg20": round(chg20, 1),
            "hi20": round(hi20, 2), "ma20": round(ma20, 3)}


def is_breakout_box(m: dict, pct_today: float, price: float) -> bool:
    """底部横盘后放量突破箱体判据（用户"刚放量突破底部盘整"语义，2026-08-24 修正）。

    计划初版误用 classify_low_pos_breakout 的"低位 pos≤0.5"（低位吸筹识别），与"突破"
    语义相反（突破要求收盘突破箱体上沿），实测 8/21 筛出 0 只。修正为：
    横盘(前20日振幅≤25% 且 涨幅≤15%) + 放量(量比≥1.5) + 突破(收盘≥前20日高点*0.98 且 当日涨幅≥3%)
    + 上升趋势(收盘>MA20)。"""
    if not m:
        return False
    if m["rng20"] > 25.0 or m["chg20"] > 15.0:
        return False  # 非横盘
    if m["vr"] < 1.5:
        return False  # 未放量
    if price < m["hi20"] * 0.98:
        return False  # 未突破箱体上沿
    if pct_today < 3.0:
        return False  # 突破日涨幅确认
    if price <= m["ma20"]:
        return False  # 未站上MA20（非上升趋势）
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="热点题材+放量突破底部盘整+未涨停 选股器(hotspot_breakout)")
    ap.add_argument("--date", default="", help="日期 YYYYMMDD（默认 qfq 库最新交易日）")
    ap.add_argument("--price-cap", type=float, default=MAX_PRICE, help="价格上限(默认20)")
    ap.add_argument("--float-mv-cap", type=float, default=FLOAT_MV_CAP, help="中小盘流通市值上限亿(默认100)")
    a = ap.parse_args()

    date_str = a.date or _latest_qfq_date()
    _d = re.sub(r"[^0-9]", "", date_str)
    if len(_d) != 8:
        print(f"[ERROR] 非法日期（须 YYYYMMDD 8位数字）: {date_str!r}")
        return 2

    ind1, names = load_ind_name()
    mainline_sectors, mainline_detail = get_mainline_sectors(_d, ind1)
    bad_earn = load_bad_earnings("20260630")  # 业绩预筛（2026中报亏损，fail-open）

    cands = scan_qfq_candidates(_d, a.price_cap)
    if not mainline_sectors:
        print("[WARN] 主线集合为空 → 本次不产出候选（待 attribution/涨停池数据就绪后重跑）")

    shape_hit = 0
    rows_out: list[dict] = []
    for c in cands:
        k40 = _read_k40(c["code"])
        m = breakout_metrics(k40)
        if not m:
            continue
        # 形态判定：底部横盘后放量突破箱体（is_breakout_box）
        if not is_breakout_box(m, c["pct_today"], c["price"]):
            continue
        shape_hit += 1
        c.update(m)

    if shape_hit:
        hit = [c["code"] for c in cands if "vr" in c]
        circ_mv = load_circ_mv(set(hit), _d)
        for c in cands:
            if "vr" not in c:
                continue
            # 名称取自本地 industry_map_ths（无网络），ST 剔除与输出共用
            name = names.get(c["code"], "")
            if not name_ok(name):
                continue
            if c["code"] in bad_earn:
                continue  # 业绩预筛: 2026中报亏损预告（首亏/续亏/增亏/减亏）
            ind = ind1.get(c["code"], "")
            if ind not in mainline_sectors:
                continue  # 热点主线过滤
            # 中小盘/创业板：300/301 直接放行，其余流通市值<cap（市值缺失 fail-open 放行，闸门兜底）
            if c["code"][:3] not in ("300", "301"):
                fmv = circ_mv.get(c["code"])
                if fmv is not None and fmv > a.float_mv_cap:
                    continue
            d = mainline_detail.get(ind, {})
            note = f"热点主线[{ind}:{d.get('tier','主线')}/{d.get('count','?')}] 放量突破{round(c['price']/c['ma20']*100-100,1)}%"
            rows_out.append({
                "date": _d, "code": c["code"], "name": name, "industry": ind,
                "price": c["price"], "float_mv": round(circ_mv.get(c["code"]) or 0, 2),
                "is_mainline": 1, "breakout_flow_ratio": c["vr"], "rng20": c["rng20"],
                "tier": "1.0x", "note": note, "source": "hotspot_breakout",
                # BT-FAIL-005(8/27主人裁决降级使用): 提示性标签非闸门; 白名单 DictReader 按名取列不受影响
                "no_chase_hint": "勿追价;深回踩≥5%且企稳再介入(追高fwd10约-196bp)",
            })

    rows_out.sort(key=lambda x: (-x["is_mainline"], -x["breakout_flow_ratio"], x["code"]))
    # 输出：仅当 _d 为 qfq 库最新交易日才写 latest（防手动跑历史日期污染当日白名单消费）。
    # 历史日期仅打印研究输出，不覆盖 latest.csv（8/25 修复：鲁棒性测试跑 8/19 曾覆盖 latest 致白名单读到历史候选）
    _latest = _latest_qfq_date()
    if _d != _latest:
        print(f"[info] 历史日期 {_d}（qfq最新 {_latest}）→ 仅研究输出，不写 hotspot_breakout_latest.csv")
        _show(rows_out, _d, len(cands), shape_hit)
        return 0
    # 输出：Path 字面量对象（固定 reports 目录内），无任何动态拼接
    with OUT_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "date", "code", "name", "industry", "price", "float_mv", "is_mainline",
            "breakout_flow_ratio", "rng20", "tier", "note", "source", "no_chase_hint"])
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

    print(f"=== 热点突破选股 {_d} ===")
    print(f"粗筛{len(cands)} → 形态命中{shape_hit} → 最终候选{len(rows_out)}只")
    if not rows_out:
        print("[WARN] 今日无热点突破候选（无主线 或 无形态命中 或 无主线内未涨停标的）——不静默")
    for r in rows_out:
        print(f"  {r['code']} {r['name']:<8} [{r['industry']}] {r['price']} 量比{r['breakout_flow_ratio']} "
              f"rng{r['rng20']}% 流通{r['float_mv']}亿 | {r['note']}")
    print(f"→ {OUT_DIR}/hotspot_breakout_latest.csv")
    return 0


def _show(rows: list[dict], date_str: str, n_coarse: int, n_shape: int) -> None:
    """打印候选清单（历史日期研究输出共用，不写文件）。"""
    print(f"=== 热点突破选股（研究） {date_str} ===")
    print(f"粗筛{n_coarse} → 形态命中{n_shape} → 候选{len(rows)}只")
    for r in rows:
        print(f"  {r['code']} {r['name']:<8} [{r['industry']}] {r['price']} 量比{r['breakout_flow_ratio']} "
              f"rng{r['rng20']}% 流通{r['float_mv']}亿 | {r['note']}")


if __name__ == "__main__":
    raise SystemExit(main())
