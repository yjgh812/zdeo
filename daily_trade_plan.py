#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屠龙系统·每日交易作战清单 v1.0（8/13全维度自省 S-6）

========== 用途 ==========
09:20 cron 执行，生成今日《交易作战清单》，包含：
1. ✅ 今日目标买入候选（bp+ep Top20 × 主线雷达交叉）
2. ✅ 今日禁止操作清单（价格>30/防御行业/R4黑名单/持仓已达上限）
3. ✅ 当前持仓止损线（-8%触发线价格）
4. ✅ 昨日复盘要点（主线方向/风险预警）
5. ✅ 今日闸门状态一览（R1-R18当前值）

========== 执行红线 ==========
盘中任何操作必须先查阅本清单。
清单上没有的标的 → 不买。
清单未列举的风险 → 先确认再操作。

========== 用法 ==========
    python daily_trade_plan.py                      # 生成今日清单
    python daily_trade_plan.py --date 20260813       # 指定日期回溯
    python daily_trade_plan.py --open-in-browser     # 生成+打开HTML版
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

REPORT_DIR = r"D:\zcodesj\reports"
PLAN_DIR = r"D:\zcodesj\trade_plans"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


# ===== 系统信号读取（兜底：当 MCP 不可用时返回模拟数据）=====

def _overlay_latest_close(picks: list[dict]) -> None:
    """把清单里的 price 列原地替换为 qfq 库最新收盘（清单股票构成仍按月度的，
    只是价格不再停留在清单生成日）。qfq 库不可用时静默保持原价。"""
    try:
        codes = [str(p.get("code", ""))[-6:] for p in picks if p.get("code")]
        if not codes:
            return
        qfq = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
        con = sqlite3.connect(f"file:{qfq}?mode=ro", uri=True, timeout=30)
        latest = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        marks = ",".join("?" * len(codes))
        rows = con.execute(
            "SELECT code, close FROM stock_daily_meta_qfq WHERE date=? AND code IN (%s)" % marks,
            [latest] + codes).fetchall()
        con.close()
        px = {r[0]: r[1] for r in rows}
        for p in picks:
            c = str(p.get("code", ""))[-6:]
            if c in px and px[c]:
                p["price"] = round(px[c], 2)
    except Exception:
        pass


def read_bp_ep_top20(date_str: str | None = None) -> list[dict]:
    """读取 bp+ep 最新月度 Top20 候选。
    
    数据源优先级: MCP实时 > 本地csv > 兜底空列表
    """
    date = date_str or _today()
    
    # 尝试读本地缓存 csv
    csv_paths = [
        os.path.join(r"D:\zcodesj\multifactor\reports", f"bp_ep_top20_{date}.csv"),
        os.path.join(r"D:\zcodesj\multifactor\reports", "bp_ep_top20_latest.csv"),
        # 兼容实际生产命名（bp_ep_monthly_picks.py 写入 daily_top20/bp_ep_monthly_*.csv，utf-8-sig 带BOM）
        os.path.join(r"D:\zcodesj\multifactor\reports", "daily_top20", f"bp_ep_monthly_*_{date}.csv"),
        os.path.join(r"D:\zcodesj\multifactor\reports", "daily_top20", "bp_ep_monthly_latest.csv"),
    ]
    # 8/16全量巡检P1修复：清单文件名尾缀=生成日，非生成日精确匹配失败导致整月其余日子
    # bp+ep候选全空。追加"取目录内文件名排序最新"的fallback，且尾缀日期距今>45天视为
    # 过期弃用（月度清单超一个调仓周期未刷新属异常，宁空勿旧）。
    try:
        import glob as _g
        _all = sorted(_g.glob(os.path.join(
            r"D:\zcodesj\multifactor\reports", "daily_top20", "bp_ep_monthly_*.csv")))
        if _all:
            # 2026-09-10 修复: 8/24 起文件名加 _n{N} 尾缀, 旧逻辑 rsplit 取尾缀当日期
            # strptime("n50") 抛 ValueError → age=9999 → 文件永不入候选 → bp+ep 段自
            # 8/29 恒空。改为正则解析 factor-date(中段日期=因子截面日), 与白名单
            # _bp_ep_factor_age_warn 同口径; 旧格式文件回退尾缀解析。
            _m = re.search(r"bp_ep_monthly_(\d{8})_", os.path.basename(_all[-1]))
            if _m:
                _age = (datetime.strptime(date, "%Y%m%d")
                        - datetime.strptime(_m.group(1), "%Y%m%d")).days
            else:
                _tail = _all[-1].rsplit("_", 1)[-1].split(".")[0]
                try:
                    _age = (datetime.strptime(date, "%Y%m%d")
                            - datetime.strptime(_tail, "%Y%m%d")).days
                except ValueError:
                    _age = 9999
            if 0 <= _age <= 45:
                csv_paths.insert(2, _all[-1])
    except Exception:
        pass
    for p in csv_paths:
        if "*" in p:  # glob 匹配（写入端命名含起始日期，无法精确预知）
            import glob as _glob
            matches = sorted(_glob.glob(p))
            if not matches:
                continue
            p = matches[-1]  # 取最新一个
        if os.path.exists(p):
            try:
                import csv
                picks = []
                # 写端用 utf-8-sig 带 BOM，必须 utf-8-sig 读取否则首列名污染（8/14全链路测试P0）
                with open(p, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # 2026-09-10: n50/全量文件 fallback 命中时按 rank<=20 截断
                        # (历史 bug: 8/24-8/29 窗口 2762 行全量文件直出含 ST 萃华的
                        # 全市场前20; 此处契约=「bp+ep 月度Top20」)
                        try:
                            _rk = int(float(row.get("rank") or 0))
                        except (TypeError, ValueError):
                            _rk = 0
                        if _rk and _rk > 20:
                            continue
                        picks.append({
                            "code": row.get("code", ""),
                            "name": row.get("name", ""),
                            "rank": row.get("rank", ""),
                            # 写端列名 composite_score/close（非 score/price），兼容兜底
                            "score": row.get("composite_score", row.get("score", "")),
                            "price": row.get("close", row.get("price", "")),
                            "sector": row.get("industry", row.get("sector", "")),
                            "source": "bp_ep_top20",
                        })
                if picks:
                    _overlay_latest_close(picks)
                    return picks
            except Exception:
                pass
    
    # 尝试读 JSON
    json_path = os.path.join(r"D:\zcodesj\multifactor\reports", "bp_ep_top20_latest.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    
    return []


def read_momvol_top20(date_str: str | None = None) -> list[dict]:
    """读取 卫星增强层 mom20+vol_low@T20 最新信号.

    成色: backtrader全约束双引擎终验 2018-2026 CAGR+4.70%/Sharpe0.69/MDD-12.9%
    口径: qfq复权+只杀上行清洁+原始价≤30+流动性>2000万/日+月末调仓
    数据: momvol_enhancer_signal.py 每月生成(建议每月1日09:00 cron)
    """
    import csv as _csv
    path = os.path.join(r"D:/zcodesj/multifactor/reports", "momvol_top20_latest.csv")
    if not os.path.exists(path):
        return []
    picks = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in _csv.DictReader(f):
            picks.append({
                "code": row.get("code", ""), "name": row.get("name", ""),
                "rank": row.get("rank", ""), "sector": row.get("industry", ""),
                "price": row.get("close_raw", ""), "action": row.get("action", ""),
                "signal_date": row.get("signal_date", ""),
                "source": "momvol_top20",
            })
    return picks


def read_ml_top20(date_str: str | None = None) -> list[dict]:
    """读取 ML多因子 Ridge·PIT-D17 最新信号.

    成色/口径: factor_ml_walkforward_v3.py (全无前视PIT通道) 见 reports/FACTOR_ML_V3_20260816.md
    数据: factor_ml_signal.py 每月生成(cron每月1日09:05), fail-closed+名称铁律
    定位: 研究/战术提示层(主人8/15 C裁决), 实盘必须过 pre_trade_check 全闸门
    """
    import csv as _csv
    path = os.path.join(r"D:/zcodesj/reports", "ml_ridge_top20_latest.csv")
    if not os.path.exists(path):
        return []
    picks = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in _csv.DictReader(f):
            picks.append({
                "code": row.get("code", ""), "name": row.get("name", ""),
                "rank": row.get("rank", ""), "sector": row.get("industry", ""),
                "price": row.get("close", ""),
                "is_new": row.get("is_new", ""),
                "source": "ml_ridge_pit_d17",
            })
    return picks


def read_mainline_candidates(date_str: str | None = None) -> list[dict]:
    """读取主线雷达今日候选。
    
    数据源: mainline_radar 输出缓存
    2026-09-10: 三路径返回统一经 _norm_candidate_names 名称归一——缓存 json 可能由
    修复前 screener 写入(半角A如深纺织A), 展示前必须对齐 stock_name_lookup 库名。
    """
    date = date_str or _today()
    path = os.path.join(r"D:\zcodesj\reports", f"mainline_candidates_{date}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _norm_candidate_names(json.load(f))
        except Exception:
            pass
    
    # 兜底：尝试读最新版
    path2 = os.path.join(r"D:\zcodesj\reports", "mainline_candidates_latest.json")
    if os.path.exists(path2):
        try:
            with open(path2, "r", encoding="utf-8") as f:
                return _norm_candidate_names(json.load(f))
        except Exception:
            pass

    # 2026-08-23 修复：此前无任何脚本生成 mainline_candidates_*.json → 清单"主线雷达候选0只"。
    # 兜底：实时调用主线选股器函数取候选（内存传递，无文件依赖，读最近交易日涨停推导，
    # 不受 cron 步骤顺序影响——本步第五步早于主线选股器第六步）
    try:
        import mainline_candidate_screener as _msc
        r = _msc.screen()
        if isinstance(r, dict) and "error" not in r:
            return _norm_candidate_names(_msc.flat_candidates(r))
    except Exception:
        pass

    return []


def _norm_candidate_names(cands: list[dict]) -> list[dict]:
    """名称唯一接口归一（2026-09-10）：stock_name_lookup 全仓唯一名称真源。"""
    try:
        from stock_name_lookup import get_name as _gn
        for c in cands:
            if not isinstance(c, dict):
                continue
            code6 = str(c.get("code") or "")[-6:]
            nm = _gn(code6) if code6.isdigit() else None
            if nm:
                c["name"] = nm
    except Exception:
        pass
    return cands


def _canonical_name(code: str) -> str | None:
    """名称唯一接口归一（2026-09-10）：返回库名或 None。"""
    code6 = str(code or "")[-6:]
    if not code6.isdigit():
        return None
    try:
        from stock_name_lookup import get_name as _gn
        return _gn(code6) or None
    except Exception:
        return None


def read_firstboard_clusters() -> dict | None:
    """读取首板集群共振报告（2026-09-09 主人红棉复盘裁决接入，纯感知零交易权）。

    数据源: firstboard_cluster_probe.py（22:00 链 aux [cluster] 环节产出）。
    时序自愈（2026-09-09 补）: aux [cluster] 22:00 跑、复盘主题标签 23 点后才入库
    → latest 的主题簇会缺位 → 本函数**无条件即时重跑 probe**（幂等 0.1s）后读最新，
    消除"清单主题簇永远是前一天"的时序缺口；probe 失败 fail-open 用现有 latest。
    """
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(r"D:\zcodesj", "firstboard_cluster_probe.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=r"D:\zcodesj")
        if r.returncode:
            print(f"[WARN] 共振探测即时重跑非零退出 {r.returncode}，使用现有 latest")
    except Exception as e:
        print(f"[WARN] 共振探测即时重跑失败({type(e).__name__})，使用现有 latest")
    path = os.path.join(r"D:\zcodesj\reports", "firstboard_cluster_latest.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("trade_date") else None
    except Exception:
        return None


def read_current_positions(date_str: str | None = None) -> list[dict]:
    """读取当前持仓（含成本价）。
    
    数据源优先级: westock 实时 > trade_state > 归因数据库
    """
    date = date_str or _today()
    positions = []

    # 1) westock 实时持仓（唯一真实源，8/14全链路测试修复：原实现缺失此层，
    #    trade_state.bought_codes 仅记录当日买入、position_history 全为已平仓，
    #    导致作战清单持仓恒为 0）
    try:
        sys.path.insert(0, r"D:/workbuddyspace")
        from westock_client import WeStockClient  # noqa: E402
        ws = WeStockClient()
        data = ws.positions().get("data", {}).get("positions", [])
        for p in data:
            code = str(p.get("code", "")).lower()
            cost = float(p.get("costPrice", 0) or 0)
            qty = int(p.get("quantity", 0) or 0)
            if not code or qty <= 0:
                continue
            positions.append({
                "code": code,
                "name": str(p.get("name", "")),
                "cost_price": cost,
                "quantity": qty,
            })
        if positions:
            return positions
    except Exception:
        pass

    # 2) 尝试读 trade_state 中的当日买入快照（仅兜底，非真实持仓）
    state_path = os.path.join(r"D:\zcodesj\trade_state", f"trade_state_{date}.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            # trade_state 不保存成本价，只能获取代码列表
            codes = state.get("bought_codes", [])
            # 从归因数据库补全信息
            try:
                import sqlite3
                # 2026-08-29: position_history=冻结历史表(写入链路死亡, OPEN 行不可靠,
                # kb/rules/data-field-semantics.md §五)——本分支为三层兜底链末端死表分支,
                # westock 主源正常时不可达; 待下次触达重构(改读 trade_records 成交价)
                con = sqlite3.connect(r"D:\zcodesj\attribution\attribution.db")
                for c in codes:
                    row = con.execute(
                        "SELECT name, cost_price, quantity FROM position_history "
                        "WHERE code=? AND status='OPEN' ORDER BY updated_at DESC LIMIT 1",
                        (c,)
                    ).fetchone()
                    if row:
                        positions.append({
                            "code": c,
                            "name": row[0] or "",
                            "cost_price": row[1] if row[1] else 0,
                            "quantity": row[2] if row[2] else 0,
                        })
                con.close()
            except Exception:
                pass
        except Exception:
            pass
    
    # 如果 positions 仍为空，尝试直接从 attribution.db 拉
    if not positions:
        try:
            import sqlite3
            con = sqlite3.connect(r"D:\zcodesj\attribution\attribution.db")
            rows = con.execute(
                "SELECT code, name, cost_price, quantity FROM position_history "
                "WHERE status='OPEN'"
            ).fetchall()
            for r in rows:
                positions.append({
                    "code": r[0],
                    "name": r[1] or "",
                    "cost_price": r[2] if r[2] else 0,
                    "quantity": r[3] if r[3] else 0,
                })
            con.close()
        except Exception:
            pass
    
    return positions


def _prev_trade_date(date_str: str) -> str:
    """date_str 的前一个交易日(YYYYMMDD)。简单按日历回退, 无交易日历时用近3日探测。"""
    from datetime import datetime, timedelta
    try:
        d = datetime.strptime(date_str, "%Y%m%d")
    except (TypeError, ValueError):
        return date_str
    for back in (1, 2, 3):
        prev = (d - timedelta(days=back)).strftime("%Y%m%d")
        # 周末直接跳
        if datetime.strptime(prev, "%Y%m%d").weekday() < 5:
            return prev
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def read_yesterday_summary(date_str: str | None = None) -> dict:
    """读取昨日复盘要点。

    数据源优先级:
      1. reports/daily_review_{date}.json   (结构JSON, 理想口径, 现无脚本生成)
      2. reports/DAILY_REVIEW_{date}.md     (知微复盘SOP实际产物, 大写md)
      3. 兜底: 返回"未读取到复盘数据"

    注意: 传入的是"今天"时, 内部自动读"昨日前一交易日"的复盘文件。
    """
    date = date_str or _today()
    # 复盘文件按"当日"命名, 而这里要读的是 date 的前一交易日
    prev = _prev_trade_date(date)
    # 1. 优先读复盘 JSON (结构完整)
    path = os.path.join(r"D:\zcodesj\reports", f"daily_review_{prev}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # 2. 兜底: 从 DAILY_REVIEW_{prev}.md (知微复盘实际产物, 大写) 解析
    md = os.path.join(r"D:\zcodesj\reports", f"DAILY_REVIEW_{prev}.md")
    if os.path.exists(md):
        try:
            with open(md, "r", encoding="utf-8") as f:
                text = f.read()
            return _parse_review_md(text)
        except Exception:
            pass

    # 兜底信息
    return {
        "market_env": "未知（未读取到复盘数据）",
        "main_sectors": [],
        "risk_warnings": [],
        "space_board": None,
    }


def _parse_review_md(text: str) -> dict:
    """从知微复盘 md (DAILY_REVIEW_YYYYMMDD.md) 提取结构化要点。

    对齐 read_yesterday_summary 期望的 dict 结构:
      market_env / main_sectors / risk_warnings / space_board
    纯文本解析(正则), 无额外依赖; 任何字段解析失败 → 该字段置空/None, 不报错。
    """
    import re
    out = {"market_env": "", "main_sectors": [], "risk_warnings": [], "space_board": None}

    # 市场环境: 抓"行情周期定性"章节的结论句
    m = re.search(r"##\s*[^#\n]*定性[^#\n]*\n(.*?)(?=\n##|\Z)", text, re.S)
    if m:
        env_lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip() and not ln.startswith(">")]
        if env_lines:
            out["market_env"] = env_lines[0][:200]

    # 主线方向: 优先抓"持续性主线/绝对主线"判定, 取方向名(表格行首列或加粗词)
    m = re.search(r"\*\*([^*]{2,20}全链|[\u4e00-\u9fa5A-Za-z0-9]{2,20})\*\*[^\n]*?(?:持续性主线|绝对主线|第\s*\d+\s*日)", text)
    if m:
        out["main_sectors"].append(m.group(1).strip()[:30])
    if not out["main_sectors"]:
        # 兜底: 板块表格中标记"主线"的行首列
        m = re.search(r"\| *\*\*([^*]{2,20})\*\*[^\n]*?(?:持续性|主线|绝对)", text)
        if m:
            out["main_sectors"].append(m.group(1).strip()[:30])

    # 风险预警: 含"退潮"/"风险日"/"警戒"的句子 (跳过定性章节的第一句, 避免与market_env重复)
    for line in text.splitlines():
        if any(w in line for w in ("退潮", "系统性风险", "风险日", "警戒", "走熊", "A杀")):
            s = re.sub(r"^[#>\s|*\-\d.]+", "", line).strip()
            if s and len(s) > 6 and s not in out.get("market_env", ""):
                out["risk_warnings"].append(s[:80])
        if len(out["risk_warnings"]) >= 5:
            break

    # 空间龙高度: "空间板" / "连板梯队" 章节中最高板数
    m = re.search(r"(?:高度|空间板?|最高)[^\d]{0,20}?(\d+)\s*板", text)
    if m:
        out["space_board"] = int(m.group(1))
    else:
        m = re.search(r"(\d+)\s*板[^\n]*?(?:空间|高度|梯队|最高)", text)
        if m:
            out["space_board"] = int(m.group(1))

    return out


def read_stop_loss_triggers(date_str: str | None = None) -> list[dict]:
    """读取 trade_state 中的待执行止损指令。"""
    date = date_str or _today()
    state_path = os.path.join(r"D:\zcodesj\trade_state", f"trade_state_{date}.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            return state.get("stop_loss_instructions", [])
        except Exception:
            pass
    return []


# ===== 报告生成 =====

def read_vibe_card() -> dict | None:
    """读取最新盘感卡片 reports/vibe_card_latest.json（vibe_card.py 每天盘后/盘前生成）。
    缺失/损坏 → None，作战清单该段静默跳过，不阻断生成。"""
    p = os.path.join(r"D:\zcodesj\reports", "vibe_card_latest.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def generate_plan(
    date_str: str,
    bp_ep_picks: list[dict],
    mainline_picks: list[dict],
    positions: list[dict],
    yesterday: dict,
    stop_loss_insts: list[dict],
    momvol_picks: list[dict] | None = None,
    ml_picks: list[dict] | None = None,
) -> str:
    """生成 Markdown 格式的《交易作战清单》。"""
    
    # 交叉候选：同时在 bp+ep Top20 和主线雷达中的标的
    bp_ep_codes = {p["code"] for p in bp_ep_picks if p.get("code")}
    mainline_codes = {p["code"] for p in mainline_picks if p.get("code")}
    cross_picks = [p for p in bp_ep_picks if p["code"] in mainline_codes]
    
    lines = []
    lines.append(f"# 🎯 屠龙交易作战清单 · {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}\n")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"> 选股宇宙：**A股全市场（排除北交所/ST/科创板，2026-09-24主人令）** | 止损线触发 → 自动执行\n")
    lines.append("\n---\n")

    # ========== 盘感卡片（第〇部分，取自 vibe_card_latest.json）==========
    vibe = read_vibe_card()
    # 2026-08-24 P1修复: vibe 卡片 schema 漂移（如降级写 {"error":...}）时直接下标访问
    # KeyError 打崩 09:10 作战清单（按铁律"清单失败=全天禁买"）——入口守卫，坏卡跳过该段
    _dims = (vibe or {}).get("dimensions") if isinstance(vibe, dict) else None
    if not (isinstance(_dims, dict) and {"sentiment", "volume", "index_momentum"} <= set(_dims)
            and isinstance((vibe.get("freshness") or {}), dict)):
        vibe = None
    if vibe:
        s = vibe["dimensions"]["sentiment"]
        v = vibe["dimensions"]["volume"]
        i = vibe["dimensions"]["index_momentum"]
        warn = "".join(f" ⚠️{w}" for w in vibe["freshness"]["warn"])
        lines.append("## 🎯 今日盘感卡片\n")
        lines.append(f"> 数据日 {vibe['data_date']}（非实时报价）| 生成 {vibe['generated_at']}\n")
        lines.append("| 盘感分 | 档位 | 仓位上限 | 情绪复合 | 打板溢价 | 量能 | 指数动能 | 连板梯队 |")
        lines.append("|-------|------|----------|----------|----------|------|----------|----------|")
        prem = vibe["dimensions"].get("premium", {})
        lad = vibe["dimensions"].get("ladder", {})
        prem_s = "-" if prem.get("prem_seal") is None else f"{prem['prem_seal']:+.1f}%"
        vr = v.get("ratio")
        vr_s = "-" if vr is None else f"{vr:.2f}x"
        lad_s = "-" if not lad.get("ok") else f"{lad.get('score')}({lad.get('max_boards')}板)"
        lines.append(f"| {vibe['vibe_score']} | {vibe['stage']} | ≤{vibe['max_position']:.0%} "
                     f"| {s['score']} | {prem_s} | {v['score']}（{vr_s}） | {i['score']} "
                     f"| {lad_s} |")
        lines.append(f"> 动作：{vibe['action']}\n")
        ctx = vibe.get("context", {})
        ctx_parts = []
        if ctx.get("cycle_stage"):
            ctx_parts.append(f"情绪周期={ctx['cycle_stage']}")
        if ctx.get("percentile_250d") is not None:
            ctx_parts.append(f"近250日分位={ctx['percentile_250d']}%")
        if ctx.get("momentum", {}).get("trend_days"):
            td = ctx["momentum"]["trend_days"]
            ctx_parts.append(f"动量={'↑' if td > 0 else '↓'}{abs(td)}日")
        if ctx.get("sc001", {}).get("circuit_breaker"):
            ctx_parts.append("🔴SC001打板溢价≤0亏钱效应")
        if ctx.get("sc001", {}).get("empty_advice"):
            ctx_parts.append("🈳SC001 rolling20≤0空仓提示")
        seal = ctx.get("seal_quality", {})
        if seal.get("ok"):
            ctx_parts.append(f"真封单率{seal['true_seal_rate_pct']}%(伪封板{seal['n_fake']}/炸板{seal['n_broken']})")
        sect = ctx.get("sector_heat", {})
        if sect.get("ok"):
            tops = "、".join(f"{t['industry']}{t['n']}" for t in sect["top"][:3])
            ctx_parts.append(f"{sect['mainline_clarity']}（涨停集中:{tops}）")
        trend = ctx.get("trend", [])
        if len(trend) >= 2:
            arrow = "→".join(str(int(t["vibe_score"])) for t in trend[-5:])
            ctx_parts.append(f"近5日盘感:{arrow}（{trend[-1]['stage']}）")
        if ctx.get("divergence"):
            ctx_parts.append(f"⚠️背离:{ctx['divergence']}")
        if ctx_parts:
            lines.append(f"> 判读：{' | '.join(ctx_parts)}\n")
        if warn:
            lines.append(f"> 新鲜度提醒：{warn.strip()}\n")
        lines.append("\n---\n")

    # ========== 第一部分：今日禁止操作 ==========
    lines.append("## ⛔ 今日禁止操作\n")
    lines.append("| 禁令 | 原因 | 依据 |")
    lines.append("|------|------|------|")
    lines.append("| 无信号源的新开仓 | 8/13 50笔订单全部裸奔 | admit闸门 |")
    lines.append("| 价格 > 30 元的买入 | 中恒电气44.77案例 | E-05 |")
    lines.append("| 当日批量新开仓 | 注意仓位分散度 | 建议提示 |")
    lines.append("| 非主线方向的批量买入 | 主线雷达候选转化率0% | E-04 |")
    lines.append("| 同一分钟多笔操作 | 注意订单价格合理性 | 建议提示 |")
    lines.append("\n")
    
    # ========== 第二部分：今日目标买入候选 ==========
    lines.append("## ✅ 今日目标买入候选\n")
    
    # 交叉候选（最高优先级）
    if cross_picks:
        lines.append("### 🥇 双重信号候选（bp+ep × 主线雷达交叉）\n")
        lines.append("| 代码 | 名称 | bp+ep排名 | 行业 | 现价 | 信号来源 |")
        lines.append("|------|------|-----------|------|------|----------|")
        for p in cross_picks:
            lines.append(f"| {p['code']} | {p['name']} | {p.get('rank', '-')} | {p.get('sector', '-')} | {p.get('price', '-')} | bp+ep+主线 |")
        lines.append("\n")
    
    # bp+ep Top20
    if bp_ep_picks:
        lines.append("### 📊 bp+ep 月度Top20（参考产出,非买入约束——名单制已全面废除2026-09-24,买入须过admit闸+R闸）\n")
        lines.append("| 排名 | 代码 | 名称 | 行业 | 现价 | 是否持仓 |")
        lines.append("|------|------|------|------|------|----------|")
        pos_codes = {str(p["code"])[-6:] for p in positions}  # 2026-08-24修复: 统一纯6位（westock带sh/sz前缀）
        for i, p in enumerate(bp_ep_picks[:20], 1):
            is_held = "✅ 已持仓" if str(p["code"])[-6:] in pos_codes else "—"
            lines.append(f"| {i} | {p['code']} | {p['name']} | {p.get('sector', '-')} | {p.get('price', '-')} | {is_held} |")
        lines.append("\n")
    
    # 主线雷达候选
    if mainline_picks:
        lines.append("### 🔍 主线雷达候选\n")
        lines.append("| 代码 | 名称 | 主线方向 | 催化强度 | 可预判性 |")
        lines.append("|------|------|----------|----------|----------|")
        for p in mainline_picks:
            # 催化强度列优先显示机构龙虎榜买入（资金确认信号，2026-08-23 落地）
            cat = f"🏛️机构{p['inst_buy_n']}家买入" if p.get('inst_buy_n') else (p.get('catalyst_strength') or '-')
            # 业绩预告标注（2026-08-23 复盘线索③：业绩因子；标注报告期避免歧义）
            if p.get('earn_type'):
                pct_txt = f"+{p['earn_pct']:.0f}%" if p.get('earn_pct') is not None else ""
                per_txt = {"20260630": "中报", "20251231": "年报",
                           "20250930": "三季报", "20250331": "一季报"}.get(str(p.get('earn_period') or ''), "")
                cat += f" 📈{per_txt}{p['earn_type']}{pct_txt}"
            lines.append(f"| {p.get('code', '-')} | {p.get('name', '-')} | {p.get('theme', '-')} | {cat} | {p.get('predictability', '-')} |")
        lines.append("\n")

    # 首板集群共振（2026-09-09 主人红棉复盘裁决：数据驱动主线发现，纯感知零交易权）
    fb = read_firstboard_clusters()
    if fb:
        lines.append(f"### 🧲 首板集群共振（归因库数据日 {fb.get('data_date')}，阈值{fb.get('params_fp', '-')}）\n")
        clusters = fb.get("clusters") or []
        if clusters:
            lines.append("| 状态 | 行业 | 涨停/首板 | 昨日 | 主题关键词 | 成员（代码 名称） |")
            lines.append("|------|------|-----------|------|------------|--------------------|")
            for c in clusters:
                mem = " ".join(f"{m['code']}{m['name']}" for m in c["members"][:6])
                kw = "/".join((c.get("theme_keywords") or [])[:3]) or "-"
                mark = "🆕" if c["status"] == "新共振" else "🔁"
                lines.append(f"| {mark}{c['status']} | {c['industry']} | {c['total']}/{c['firstboards']} | {c['prev_total']} | {kw} | {mem} |")
            lines.append("\n> 判读：🆕新共振=昨日启动主线候选（结合隔夜催化判断延续性）；🔁连续共振=主线加速/分歧日，关注梯队分化。共振提示≠买入名单，候选仍须过 R1-R18 闸门。\n")
            # 主题簇（跨行业主题级扩散，2026-09-09 补层：行业口径抓不住农化横跨三级行业）
            for c in fb.get("theme_clusters") or []:
                mem = " ".join(f"{m['code']}{m['name']}" for m in c["members"][:8])
                lines.append(f"\n> 🧲 **主题[{c['industry']}] {c['total']}只（首板{c['firstboards']}）** 跨{'/'.join(c['industries_spanned'][:4])}：{mem}")
            lines.append("")
        else:
            lines.append("\n> 昨日无首板共振簇（同行业≥3涨停且首板≥2）。\n")
    
    # 卫星增强层(已验证)
    if momvol_picks:
        lines.append("### 🛰️ 卫星增强层 mom20+vol_low@T20（全约束终验+4.7%/年,MDD-12.9%）\n")
        lines.append("> 月度调仓,与趋势跟踪主线互补; 等权,主人裁决后经闸门执行\n")
        lines.append("| 排名 | 代码 | 名称 | 行业 | 现价 | 是否持仓 | 本月动作 |")
        lines.append("|------|------|------|------|------|----------|----------|")
        # 2026-08-24 P2修复: 持仓码归一到后6位（westock返回 sh600519 小写带前缀，
        # 此前 momvol/ML 段用原始码与 picks 纯6位码永不匹配 → 已持仓误标"—"误导开仓）
        pos_codes2 = {str(p["code"])[-6:] for p in positions}
        for p in momvol_picks[:20]:
            is_held = "✅ 已持仓" if str(p["code"])[-6:] in pos_codes2 else "—"
            lines.append(f"| {p['rank']} | {p['code']} | {p['name']} | {p.get('sector', '-')} | {p.get('price', '-')} | {is_held} | {p.get('action', '-')} |")
        lines.append("\n")

    # ML多因子层(PIT清洁管线)
    if ml_picks:
        lines.append("### 🤖 ML多因子 Ridge·PIT-D17（v3全清洁管线,成色见FACTOR_ML_V3报告）\n")
        lines.append("> Ridge(α=1)×17特征(PIT财务9+快照4+量价4), 月度调仓; 研究/战术提示层, 实盘必须过全闸门\n")
        lines.append("| 排名 | 代码 | 名称 | 行业 | 现价 | 是否持仓 | 新进 |")
        lines.append("|------|------|------|------|------|----------|------|")
        pos_codes3 = {str(p["code"])[-6:] for p in positions}
        for p in ml_picks[:20]:
            is_held = "✅ 已持仓" if str(p["code"])[-6:] in pos_codes3 else "—"
            lines.append(f"| {p['rank']} | {p['code']} | {p['name']} | {p.get('sector', '-')} | {p.get('price', '-')} | {is_held} | {'🆕' if str(p.get('is_new')) == '1' else ''} |")
        lines.append("\n")
    
    if not bp_ep_picks and not mainline_picks and not cross_picks:
        lines.append("⚠️ **今日无候选数据**（bp+ep Top20 / 主线雷达均未就绪）。\n")
        lines.append("建议：盘前先手动确认候选清单，避免盘中临时起意。\n\n")
    
    # ========== 第三部分：持仓止损线 ==========
    lines.append("## 🛡️ 当前持仓止损线\n")
    
    # 计算每个持仓的-8%止损触发价
    if positions:
        lines.append("| 代码 | 名称 | 成本价 | 持仓量 | -8%止损价 | 是否有在途止损指令 |")
        lines.append("|------|------|--------|--------|-----------|------------------|")
        stop_codes = {s["code"] for s in stop_loss_insts}
        for p in positions:
            cost = p.get("cost_price", 0)
            stop_price = round(cost * 0.92, 2) if cost else "—"
            has_inst = "⚠️ 待执行" if p["code"] in stop_codes else "—"
            lines.append(f"| {p['code']} | {p['name']} | {cost} | {p.get('quantity', 0)} | {stop_price} | {has_inst} |")
        lines.append("\n")
    else:
        lines.append("⚠️ 未读取到持仓数据，请通过 MCP/westock 实时获取。\n\n")
    
    # 待执行止损指令
    if stop_loss_insts:
        lines.append("### ⚠️ 待执行止损指令\n")
        lines.append("| 代码 | 名称 | 亏损% | 卖出数量 | 操作 | 原因 |")
        lines.append("|------|------|-------|----------|------|------|")
        for s in stop_loss_insts:
            lines.append(f"| {s.get('code', '-')} | {s.get('name', '-')} | {s.get('pnl_pct', '-')}% | {s.get('sell_quantity', '-')} | {s.get('action', '-')} | {s.get('reason', '-')} |")
        lines.append("\n> 💡 **14:45-14:55 尾盘窗口自动执行**。持有到止损触发但还拿着，说明 S-5 保护生效中。\n\n")
    
    # ========== 第四部分：昨日复盘要点 ==========
    lines.append("## 📋 昨日复盘要点\n")
    env = yesterday.get("market_env", "未知")
    lines.append(f"- **市场环境**: {env}\n")
    sectors = yesterday.get("main_sectors", [])
    if sectors:
        lines.append(f"- **主线方向**: {', '.join(sectors)}\n")
    risks = yesterday.get("risk_warnings", [])
    if risks:
        lines.append("- **风险预警**:\n")
        for r in risks:
            lines.append(f"  - ⚠️ {r}\n")
    space = yesterday.get("space_board")
    if space:
        lines.append(f"- **空间龙高度**: {space}板\n")
    lines.append("\n")
    
    # ========== 第五部分：闸门状态一览 ==========
    lines.append("## 🔐 今日闸门配置状态\n")
    lines.append("| 闸门 | 当前值 | 阈值 | 状态 |")
    lines.append("|------|--------|------|------|")
    lines.append("| R1 追高 | >70%+非涨停+主升末 | 硬拦截(高位接盘风险) | 🚫 REJECT |")
    lines.append("| R2 止损 | -8%硬止损 | ≤-8%自动执行 | 🔴 已硬化 S-5 |")
    lines.append("| R3 批量买 | 无硬性上限 | 注意仓位分散 | 🟢 建议性 |")
    lines.append("| R4 行业黑名单 | 已移除 | 主人8/14裁决行业不应拦截 | ❌ 已移除 |")
    lines.append("| R5 振幅 | 40日振幅>50% | 提示性(十年验证高振幅短期更强) | 🟡 WARN |")
    lines.append("| R6 收高点 | (close-low)/(high-low)≥70% | 拒单 | 🟢 已硬化 |")
    lines.append("| R7 超买 | 涨停价95%拒/80%拒 | 双阈值硬拦截 | 🚫 REJECT |")
    lines.append("| R8 分化日 | 板块宽度<20% | 上限降至2只 | 🟢 已硬化 |")
    lines.append("| R9 持仓上限 | 已废除(原≤50只) | 2026-10-26主人令「不再限定持股数量」 | ⚪ 已废除 |")
    lines.append("| R12 基本面保留 | PE<10/PB<1.5/股息>3%/业绩拐点/主力流入 | 任一命中HOLD | 🟢 已硬化 |")
    lines.append("| R13 重复买入 | bought_codes+在途单检查 | 同标的重报需--allow-add-position | 🟢 已硬化 |")
    lines.append("| R14 空间龙退潮 | 空间龙跌停+目标≥3板 | 禁追连板 | 🟢 已硬化 |")
    lines.append("| R15 封板质量 | 炸板≥3次 | 封板质量过滤 | 🟢 已硬化 |")
    lines.append("| R16 高换手 | 3板以上+换手>20% | 提示性(十年验证放量短期更强) | 🟡 WARN |")
    lines.append("| R17 退潮预警 | 最高板≥6→≤5 | 上限再降1只 | 🟢 已硬化 |")
    lines.append("| R18 极端结构 | 指数跌+涨停多+涨少+板块<5只 | 拒单 | 🟢 已硬化 |")
    lines.append("| **S-3 价格锁** | >30元拒单 | MAX_PRICE=30 | 🔴 已硬化✅ |")
    lines.append("| **卖出时段** | 不限（主人8/14裁决） | 盘中可卖 | 🟢 已解除 |")
    lines.append("| **买入时段** | 09:30-10:00可买卖（主人8/14裁决） | 闸门R1-R18质量拦截 | 🟢 已解除 |")
    lines.append("| **准则11** | 交易时段无限制（8/14裁决废除禁买时段） | 全天可买卖 | 🟢 已解除 |")
    lines.append("| **准则12** | 买前回答\"为什么不是bp+ep\" | 行为准则 | 🟡 需训练 |")
    lines.append("| **准则13** | 每日≤1只自发现票 | 行为准则 | 🟡 需训练 |")
    lines.append("| **准则14** | 每笔交易可回溯触发逻辑 | 行为准则 | 🟡 需训练 |")
    lines.append("\n---\n")
    
    return "\n".join(lines)


# 白名单段标题（与 tulong_buy_whitelist.py append_to_plan 的 marker 保持一致，
# 重生成作战清单时用于识别并保留该段，避免被整体覆盖）
_WHITELIST_MARKER = "🧾 今日买入白名单（系统生成）"


def _preserve_whitelist_section(md: str, date_str: str) -> str:
    """重生成作战清单时保留白名单段。

    tulong_buy_whitelist.py 会把「今日买入白名单+禁止板块」幂等追加到
    trade_plan_YYYYMMDD.md 末尾；若之后 daily_trade_plan.py 重新生成作战清单，
    该段会被整体覆盖。这里在落盘前从旧文件提取该段并重新追加到新内容尾部。
    """
    path = os.path.join(PLAN_DIR, f"trade_plan_{date_str}.md")
    try:
        if not os.path.exists(path):
            return md
        with open(path, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        return md
    idx = old.find("## " + _WHITELIST_MARKER)
    if idx < 0:
        return md
    section = old[idx:].rstrip()
    if not section:
        return md
    if _WHITELIST_MARKER in md:
        return md
    return md.rstrip() + "\n\n---\n\n" + section + "\n"


def save_plan(md: str, date_str: str) -> str:
    """落盘作战清单 Markdown。"""
    os.makedirs(PLAN_DIR, exist_ok=True)
    md = _preserve_whitelist_section(md, date_str)
    path = os.path.join(PLAN_DIR, f"trade_plan_{date_str}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    latest_path = os.path.join(PLAN_DIR, "trade_plan_latest.md")
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="屠龙系统·每日交易作战清单 v1.0 (S-6)")
    p.add_argument("--date", default=_today(), help="日期 YYYYMMDD（默认今日）")
    a = p.parse_args()
    
    # 2026-09-10 加固: --date 直通落盘路径(trade_plan_{date}.md), 非法值会造成路径
    # 穿越式乱写(Mimosa 全量扫描复核时对 date 面加固)
    if not re.fullmatch(r"\d{8}", str(a.date)):
        print(f"[FAIL] 非法 --date: {a.date!r}（须为 8 位数字 YYYYMMDD）", file=sys.stderr)
        return 2
    
    date_str = a.date
    
    print(f"🎯 生成交易作战清单: {date_str}")
    
    # 读取所有数据源
    # 2026-09-24 主人令「全面废除TopN名单限制」: bp_ep 月度Top20 名单不再作为作战清单候选源,
    # 传空列表——交叉候选/Top20段自动跳过; 候选源=主线雷达等全市场系统化扫描(排除北交所/ST/科创板)。
    bp_ep_picks = []
    print(f"  bp+ep Top20: 已废除(2026-09-24主人令), 不再作为候选源")
    
    mainline_picks = read_mainline_candidates(date_str)
    print(f"  主线雷达候选: {len(mainline_picks)} 只")
    
    positions = read_current_positions(date_str)
    print(f"  当前持仓: {len(positions)} 只")
    
    yesterday = read_yesterday_summary(date_str)
    print(f"  复盘数据: {'已读取' if yesterday.get('market_env') != '未知（未读取到复盘数据）' else '未就绪'}")
    
    stop_insts = read_stop_loss_triggers(date_str)
    print(f"  待执行止损: {len(stop_insts)} 条")
    
    # 生成+落盘
    momvol_picks = read_momvol_top20()
    ml_picks = read_ml_top20()
    md = generate_plan(date_str, bp_ep_picks, mainline_picks, positions, yesterday, stop_insts, momvol_picks, ml_picks)
    # 8/18: 追加新信号段（美股映射/昨涨停资金/高位核查预警/横盘基线）
    md = append_new_signals(md, date_str)
    # 9/5: 四维体检段(主线归属+风格三闸+⛔不追高+资金流) + A/B 前向登记(裁决A接线)
    _cand = []
    for pick in (bp_ep_picks + momvol_picks + ml_picks + mainline_picks):
        _c = str(pick.get("code", ""))[-6:] if isinstance(pick, dict) else str(pick)[-6:]
        if _c.isdigit() and _c not in _cand:
            _cand.append(_c)
    md = append_style_checkup(md, date_str, _cand[:60])
    path = save_plan(md, date_str)
    
    print(f"\n✅ 作战清单已生成: {path}")
    print(f"  盘中操作前请查阅此清单。清单上没有的 → 不买。")
    return 0


def append_style_checkup(md: str, date_str: str, codes: list[str]) -> str:
    """四维体检段（2026-09-05 主人裁决 A 接线；幂等：已有段则跳过）。

    对当日候选跑 mainline_context.build_checkup（主线归属+风格三闸+⛔不追高+资金流方向）,
    结果写入作战清单; 同时把 原始名单/不追高过滤后名单 双版登记 data/style_ab_track.jsonl
    (append 幂等, 9/30 首验 fwd5/fwd10 双口径)。
    体检失败 → 段内如实标注, 不阻断清单生成。
    """
    if "四维体检" in md or not codes:
        return md
    try:
        sys.path.insert(0, r"D:\zcodesj")
        from mainline_context import build_checkup  # 观测工具核心(纯只读)
        c = build_checkup(codes)
    except Exception as e:
        return md + f"\n\n## 🔎 四维体检（风格三闸+不追高+资金流）\n\n体检失败: {type(e).__name__}: {e} → 本段缺位, 人工跑 python mainline_context.py <代码串>\n"
    original, filtered, lines = [], [], [
        f"数据锚: 行业K线 {c['asof']} / daily_basic {c['style_asof']} / flow_snapshot {c['flow_asof']}",
        f"{'代码':8}{'股票':10}{'行业':10}{'mom20%':>8}{'三闸':>5}  ⛔/资金流",
    ]
    for row in c["rows"]:
        code = row["code"]
        original.append(code)
        # 2026-09-10: 名称经唯一接口归一(mainline_context 来源含半角A如深纺织A,
        # verify_stock_names 发布闸 000045 实锤拦截); 归一失败保留原名
        stock = _canonical_name(code) or (row.get("stock") or "")
        if row.get("warn"):
            lines.append(f"{code:8}{stock:10}{row.get('ind') or '':10}{'--':>8}{'--':>5}  {row['warn']}")
            continue
        g = row["gates"]
        em5, ths5 = row.get("em5"), row.get("ths5")
        flow_s = "--" if em5 is None else (
            f"EM{em5:+.1f}⚠THS{ths5:+.1f}" if row.get("flow_diverge") else f"EM{em5:+.1f}/THS{ths5:+.1f}")
        mark = "⛔不追高" if row.get("hard") else ""
        lines.append(f"{code:8}{stock:10}{row.get('ind') or '':10}"
                     f"{(row['m20'] if row.get('m20') is not None else float('nan')):>8.2f}"
                     f"{g['marks']:>5}  {mark} {flow_s}")
        if not row.get("hard"):
            filtered.append(code)
    block = (f"\n\n## 🔎 四维体检（主线归属+风格三闸+⛔不追高+资金流, 9/5裁决A观测层; "
             f"硬约束在 pre_trade R-S5 闸）\n\n"
             + "\n".join(lines)
             + f"\n\n小结: {c['n_main']}/{c['n']} 在5日主线榜前10; "
               f"A/B登记: 原始{len(original)}只/过滤后{len(filtered)}只(9/30首验)")
    # A/B 前向登记(append 幂等)
    try:
        import json as _json
        rec = {"date": date_str, "original": original, "filtered": filtered,
               "notes": "daily_trade_plan 自动登记(9/5裁决A)"}
        _path = r"D:\zcodesj\data\style_ab_track.jsonl"
        # 2026-09-10 M1: 同日去重(此前 09:10 链/白名单重生成/手动多次运行每天写 4-7 行,
        # eval「最后行胜出」被盘中更知情快照污染; 每个选股日仅首行有效)
        _seen_dates = set()
        try:
            with open(_path, encoding="utf-8") as _f:
                for _ln in _f:
                    if _ln.strip():
                        try:
                            _seen_dates.add(_json.loads(_ln).get("date"))
                        except _json.JSONDecodeError:
                            pass
        except OSError:
            pass
        if date_str not in _seen_dates:
            with open(_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 登记失败不阻断清单; 失败如实性由下次运行覆盖
    return md + block


def append_new_signals(md: str, date_str: str) -> str:
    """追加 8/18 落地的新信号段（幂等：缺失才追加，读取失败静默跳过）"""
    if "自动化信号段" in md:
        return md
    from datetime import timedelta
    # 候选日期: 当日 + 往前回溯最多7个日历日（覆盖周末/节假日——信号文件按交易日命名,
    # 周一生成时前一交易日=上周五, 若只查 date_str-1(=周日)会匹配不到, 4个信号段全'未生成'）
    candidates = [date_str]
    try:
        _d = datetime.strptime(date_str, "%Y%m%d")
        for _i in range(1, 8):
            candidates.append((_d - timedelta(days=_i)).strftime("%Y%m%d"))
    except ValueError:
        pass
    lines = []
    for tag, key_part, header in [
        ("美股映射", "us_overnight_signal", "🌙 隔夜美股映射信号（盘前选股权重输入）"),
        ("昨涨停资金", "zt_flow_leading", "💰 昨日涨停板块资金流（追涨强度先行指标）"),
        ("高位核查", "high_board_alert", "🚨 高位X天Y板核查预警"),
        ("横盘基线", "breakout_baseline", "📏 横盘突破命中率滚动基线"),
    ]:
        path = None
        for d in candidates:
            _p = os.path.join(r"D:\zcodesj\reports", f"{key_part}_{d}.json")
            if os.path.exists(_p):
                path = _p
                break
        try:
            if not path:
                lines.append(f"### {header}\n> 信号未生成（{key_part}_*.json 缺失）\n")
                continue
            data = json.load(open(path, encoding="utf-8"))
            if key_part == "us_overnight_signal":
                segs = data.get("sectors", {})
                rows = " | ".join(
                    f"{k.replace('芯片','')}={v.get('signal_pct','?')}%({v.get('strength','')})"
                    for k, v in segs.items() if v.get("signal_pct") is not None)
                lines.append(f"### {header}\n> {rows or '无匹配信号'}\n")
            elif key_part == "zt_flow_leading":
                sig = data.get("signal", {})
                lines.append(f"### {header}\n> 昨日涨停板块主力净流入: **{sig.get('zt_board_main_flow_yi')} 亿**"
                             f" → {sig.get('strength','')}（{sig.get('interpretation','')}）\n")
                if data.get("caliber_dispute"):
                    # 2026-09-23: 07b54d1 在产物侧写入口径分歧警告, 但此处此前只提取 signal
                    # 三字段 → 警告在最后一公里丢失(9/22 初值+1.39STRONG vs 修订-21.13DANGER,
                    # R22 待拆)。下游展示必须并列呈现, 禁单边采信。
                    lines.append(f"> ⚠️ **口径分歧未裁决,禁单边采信**: {data['caliber_dispute']}\n")
            elif key_part == "high_board_alert":
                alerts = data.get("alerts", [])
                if alerts:
                    names = "、".join(f"{a['name']}({a['desc'].split(',')[0]})" for a in alerts)
                    lines.append(f"### {header}\n> ⚠️ **{len(alerts)} 只触发核查预警**: {names}（禁追高）\n")
                else:
                    lines.append(f"### {header}\n> 无预警\n")
            elif key_part == "breakout_baseline":
                bl = data.get("baseline")
                if bl:
                    lines.append(f"### {header}\n> 近{bl.get('days')}日横盘放量突破占比: 均值 **{bl.get('avg_breakout_pct')}%**"
                                 f"（区间 {bl.get('min')}%~{bl.get('max')}%，std {bl.get('std')}）\n")
                else:
                    lines.append(f"### {header}\n> 基线未生成\n")
        except Exception as e:
            lines.append(f"### {header}\n> 读取失败: {str(e)[:60]}\n")
    if not lines:
        return md
    return md.rstrip() + "\n\n---\n\n## 🔀 自动化信号段（8/18 新增）\n\n" + "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())