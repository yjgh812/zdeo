#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主线驱动选股器 v1.0（替代散点乱买，提升选股胜率）。

核心逻辑：
  1. 从归因库读取当日涨停范式，按行业聚合
  2. 涨停≥5只的行业=主线，≥10只=强主线
  3. 从主线涨停股中按"屠龙选股标准"筛选明日候选：
     - 价格≤30元（铁律）
     - 非防御蓝筹（R4排除）
     - 封板质量好（炸板<3次）
     - 可预判性≥7分
     - 分层输出：龙头/中军/弹性首板/低位补涨

用法:
    python mainline_candidate_screener.py                    # 读今日归因库
    python mainline_candidate_screener.py --date 20260813    # 指定日期
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

DB = r"D:\zcodesj\attribution\attribution.db"
EM_SECTOR_DB = r"D:\zcodesj\eastmoney_quant\sector\sector_data.db"
INST_LHB_DB = r"D:\workbuddyspace\databases\fundamental_data.db"  # 龙虎榜明细(机构买入)
REPORTS_DIR = r"D:\zcodesj\reports"

# R4 防御蓝筹关键词
DEFENSIVE_KEYWORDS = ["银行", "煤炭", "电力", "热电", "水电", "火电", "核电",
                      "铁路", "石油", "石化", "电信", "航运"]
MAX_PRICE = 30.0
SEAL_BREAK_MAX = 3       # 炸板≥3次排除
PREDICTABILITY_MIN = 7.0  # 可预判性≥7
MAINLINE_MIN = 5          # 主线最低涨停数
STRONG_MAINLINE_MIN = 10  # 强主线最低涨停数


def is_defensive(name: str, industry: str) -> bool:
    text = f"{name} {industry}"
    return any(k in text for k in DEFENSIVE_KEYWORDS)


def enrich_with_eastmoney_sectors(mainlines: list) -> list:
    """用 eastmoney 板块数据丰富主线信息（涨跌幅、资金流向等）"""
    if not os.path.exists(EM_SECTOR_DB):
        return mainlines
    try:
        conn = sqlite3.connect(EM_SECTOR_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # 获取今日板块行情
        sector_data = {}
        for row in cur.execute(
            "SELECT name, change_pct, amount FROM sector_basic ORDER BY change_pct DESC"
        ).fetchall():
            sector_data[row["name"]] = {
                "change_pct": row["change_pct"],
                "amount": row["amount"],
            }
        conn.close()
    except Exception:
        return mainlines

    for ml in mainlines:
        sector_name = ml["sector"]
        # 尝试精确匹配
        if sector_name in sector_data:
            ml["em_change_pct"] = sector_data[sector_name]["change_pct"]
            ml["em_amount"] = sector_data[sector_name]["amount"]
        else:
            # 模糊匹配：取关键词含相同字的板块
            hits = []
            for em_name, data in sector_data.items():
                # 取最短的共同子串判断
                common = set(sector_name) & set(em_name)
                if len(common) >= 2 and (sector_name[:2] in em_name or em_name[:2] in sector_name):
                    hits.append((em_name, data))
            if hits:
                best = max(hits, key=lambda x: len(set(x[0]) & set(sector_name)))
                ml["em_change_pct"] = best[1]["change_pct"]
                ml["em_amount"] = best[1]["amount"]
                ml["em_sector_matched"] = best[0]
            else:
                ml["em_change_pct"] = None
                ml["em_amount"] = None
    return mainlines


def _institution_buy_map() -> dict:
    """读最近交易日龙虎榜明细，解析'X家机构买入' → {6位code: {"n": 家数, "net": 净买额(元)}}。

    2026-08-23 复盘线索落地：机构龙虎榜买入=资金确认。数据源 fundamental_data.db
    longhubang_daily（akshare 东财龙虎榜，8/23 已一次性补拉到 8/21）。
    """
    import re
    out = {}
    if not os.path.exists(INST_LHB_DB):
        return out
    try:
        conn = sqlite3.connect(f"file:{INST_LHB_DB}?mode=ro", uri=True, timeout=10)
        row = conn.execute("SELECT MAX(上榜日) FROM longhubang_daily").fetchone()
        if row and row[0]:
            d = row[0]
            for code, jd, net in conn.execute(
                "SELECT 代码,解读,龙虎榜净买额 FROM longhubang_daily "
                "WHERE 上榜日=? AND 解读 LIKE '%机构买入%'", (d,)).fetchall():
                m = re.search(r"(\d+)家机构买入", jd or "")
                if not m:
                    continue
                c = str(code or "").zfill(6)
                n = int(m.group(1))
                if c and (c not in out or out[c]["n"] < n):
                    out[c] = {"n": n, "net": net or 0}
        conn.close()
    except Exception:
        return out
    return out


def enrich_institution_buy(candidates: dict) -> dict:
    """给候选叠加机构龙虎榜买入标注（inst_buy_n 家数 / inst_buy_net 净买额元）。"""
    inst = _institution_buy_map()
    if not inst:
        return candidates
    for key in candidates:
        for x in candidates[key]:
            c = (x.get("code") or "").lower()
            if c.startswith(("sh", "sz", "bj")):
                c = c[2:]
            c = c.zfill(6)
            if c in inst:
                x["inst_buy_n"] = inst[c]["n"]
                x["inst_buy_net"] = inst[c]["net"]
    return candidates


# 业绩预告正向类型（中报/年报业绩因子，2026-08-23 复盘线索③落地）
POSITIVE_EARN_TYPES = {"预增", "略增", "扭亏", "续盈"}
# 业绩预告时效窗口: 只取近4个报告期(2025中报~至今)内预告, 排除陈旧预告(如601177仅到2021年报)
EARN_MIN_END_DATE = 20250630


def _earnings_map() -> dict:
    """读时效窗口内最新一期业绩预告（fundamental_data.db forecast），判'业绩增长/扭亏'。

    2026-08-23 复盘线索③落地：中报季业绩因子——业绩预增/扭亏=基本面确认。
    数据源 fundamental_data.db forecast（akshare 东财业绩预告）；对每只取 EARN_MIN_END_DATE
    时效窗口内最新报告期，仅保留正向类型（预增/略增/扭亏/续盈），返回
    {6位code: {"type", "pct"(变动上限%), "period"(报告期)}}。
    注：forecast 当前最新期为 20251231（2025 年报预告），2026 中报预告待 8/31 披露季/补拉后入库；
    覆盖不均的陈旧预告（如仅到 2021 年）被时效窗口排除，避免把历史预增误标为当前信号。
    """
    out = {}
    if not os.path.exists(INST_LHB_DB):
        return out
    try:
        conn = sqlite3.connect(f"file:{INST_LHB_DB}?mode=ro", uri=True, timeout=10)
        # 1) 优先 2026 中报预告（真实中报因子，8/23 akshare 一次性补拉 earnings_yjyg）
        try:
            for code, typ, pct in conn.execute(
                "SELECT code, type, pct_change FROM earnings_yjyg WHERE report_date='20260630'"
            ).fetchall():
                c = str(code or "").zfill(6)
                if c and typ in POSITIVE_EARN_TYPES and c not in out:
                    out[c] = {"type": typ, "pct": pct, "period": "20260630"}
        except Exception:
            pass
        # 2) fallback 历史预告（forecast 时效窗口内最新报告期）
        for code, t, pmin, pmax, end_date in conn.execute(
            "SELECT f.ts_code, f.type, f.p_change_min, f.p_change_max, f.end_date FROM forecast f "
            "WHERE f.end_date=(SELECT MAX(end_date) FROM forecast f2 "
            "                     WHERE f2.ts_code=f.ts_code AND f2.end_date>=?)",
            (EARN_MIN_END_DATE,)
        ).fetchall():
            c = str(code or "").split(".")[0].zfill(6)
            if not c or c in out or t not in POSITIVE_EARN_TYPES:
                continue
            pct = pmax if pmax is not None else (pmin if pmin is not None else None)
            out[c] = {"type": t, "pct": pct, "period": end_date}
        conn.close()
    except Exception:
        return out
    return out


def enrich_earnings(candidates: dict) -> dict:
    """给候选叠加业绩预告标注（earn_type 预增/扭亏等 / earn_pct 变动上限% / earn_period 报告期）。"""
    emap = _earnings_map()
    if not emap:
        return candidates
    for key in candidates:
        for x in candidates[key]:
            c = (x.get("code") or "").lower()
            if c.startswith(("sh", "sz", "bj")):
                c = c[2:]
            c = c.zfill(6)
            if c in emap:
                x["earn_type"] = emap[c]["type"]
                x["earn_pct"] = emap[c]["pct"]
                x["earn_period"] = emap[c]["period"]
    return candidates


def screen(date_str: str | None = None) -> dict:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    if date_str:
        cur.execute(
            "SELECT code, name, industry, board, seal_status, turnover, "
            "primary_attribution, predictability_score, pattern_type, "
            "feature_tags, strategy_value FROM limit_up_patterns WHERE trade_date=?",
            (date_str,))
    else:
        cur.execute(
            "SELECT code, name, industry, board, seal_status, turnover, "
            "primary_attribution, predictability_score, pattern_type, "
            "feature_tags, strategy_value FROM limit_up_patterns "
            "WHERE trade_date=(SELECT MAX(trade_date) FROM limit_up_patterns)")
        # 获取日期
        cur.execute("SELECT MAX(trade_date) FROM limit_up_patterns")
        date_str = cur.fetchone()[0]
        # 重新查询
        cur.execute(
            "SELECT code, name, industry, board, seal_status, turnover, "
            "primary_attribution, predictability_score, pattern_type, "
            "feature_tags, strategy_value FROM limit_up_patterns WHERE trade_date=?",
            (date_str,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {"error": f"无 {date_str} 涨停数据"}

    # === Step 1: 按行业聚合 ===
    sector_count = defaultdict(list)
    for r in rows:
        code, name, industry, board, seal, turnover, attr, pred, ptype, tags, strategy = r
        # 已验证涨停特征(2026-08-18): 封单比≥3x→次日高开≥6%占88%; 午后板(≥13:00首封)晋级仅7~9.5%
        seal_ratio = None
        afternoon = False
        tag_industry = None
        try:
            _t = json.loads(tags) if tags else {}
            if isinstance(_t, dict):
                seal_ratio = _t.get("seal_amt_ratio")
                afternoon = bool(_t.get("afternoon_board"))
                tag_industry = _t.get("industry")
        except Exception:
            pass
        # 2026-08-23 修复：主线聚合优先用粗粒度产业(tags.industry,如"医药生物")，
        # fallback 申万细分(industry)。此前用申万细分导致跨行业产业主线(8/21 创新药/mRNA
        # 横跨化学制药+中药+生物制品+医疗服务)被分散漏识别，8/24 清单"主线雷达候选0只"根因。
        agg_industry = tag_industry or industry
        sector_count[agg_industry].append({
            "code": code, "name": name, "industry": agg_industry,
            "sw_industry": industry, "board": board, "seal_status": seal, "turnover": turnover,
            "attribution": attr, "predictability": pred or 0,
            "pattern_type": ptype, "tags": tags, "strategy": strategy,
            "seal_ratio": seal_ratio, "afternoon": afternoon,
        })

    # === Step 2: 识别主线 ===
    mainlines = []
    for sector, stocks in sorted(sector_count.items(), key=lambda x: -len(x[1])):
        cnt = len(stocks)
        if cnt >= MAINLINE_MIN:
            tier = "强主线" if cnt >= STRONG_MAINLINE_MIN else "主线"
            mainlines.append({
                "sector": sector, "count": cnt, "tier": tier,
                "stocks": stocks,
            })

    # Step 2.5: eastmoney 板块数据丰富主线信息
    mainlines = enrich_with_eastmoney_sectors(mainlines)

    # === Step 3: 从主线中筛选明日候选 ===
    # R1_LAUNCH_FIRSTBOARD: 新增"首板启动"层——底部首板+天量(换手>10%)=主升浪启动信号
    candidates = {"dragon": [], "center": [], "elastic": [], "launch": [], "catch_up": []}

    for ml in mainlines:
        for s in ml["stocks"]:
            # 基础过滤
            name = s["name"] or ""
            industry = s["industry"] or ""
            if is_defensive(name, industry):
                continue
            # 屠龙口径：沪深主板+创业板，剔除科创板(688)/北交所(4/8/92开头)/ST
            # 2026-08-23 修复：此前未过滤，8/20 候选混入 688105诺唯赞/bj920344三元基因
            s_code = (s["code"] or "").lower()
            if s_code.startswith(("sh", "sz", "bj")):
                s_code = s_code[2:]
            if s_code.startswith(("688", "4", "8", "92")):
                continue
            if "ST" in name.upper():
                continue

            # 解析封板信息
            seal_str = s["seal_status"] or ""
            break_count = 0
            if "炸" in seal_str:
                for part in seal_str.split():
                    if "炸" in part and part[0].isdigit():
                        break_count = int(part[0])
            if "烂板" in seal_str or "开" in seal_str:
                for part in seal_str.split():
                    if part[0].isdigit():
                        break_count = max(break_count, int(part[0]))

            # 封板质量过滤
            if break_count >= SEAL_BREAK_MAX:
                continue

            pred = s["predictability"]
            if pred < PREDICTABILITY_MIN:
                continue

            board = s["board"]
            if isinstance(board, str):
                # 提取数字部分（如 "4" 或 "前7连板跌停" → 7）
                digits = "".join(c for c in board if c.isdigit())
                board = int(digits) if digits else 1
            else:
                board = int(board) if board else 1
            entry = {
                **s,
                "sector_tier": ml["tier"],
                "sector_count": ml["count"],
                "break_count": break_count,
            }

            # 分层（含R1_LAUNCH_FIRSTBOARD底部首板启动层）
            if board >= 4:
                candidates["dragon"].append(entry)      # 龙头（≥4板）
            elif board >= 2:
                candidates["center"].append(entry)       # 中军（2-3板）
            elif "20cm" in seal_str or "20cm" in (s.get("tags") or ""):
                candidates["elastic"].append(entry)      # 弹性（20cm首板）
            elif board == 1 and pred >= 7.5:
                # R1_LAUNCH_FIRSTBOARD: 首板+换手>10%=主升浪启动信号
                turnover = s.get("turnover", 0) or 0
                if isinstance(turnover, str):
                    try:
                        turnover = float(turnover.rstrip('%'))
                    except (ValueError, AttributeError):
                        turnover = 0
                if turnover > 10:
                    candidates["launch"].append({**entry, "turnover": turnover})  # 底部首板启动（天量首板=主升浪）
                else:
                    candidates["catch_up"].append(entry)     # 低位补涨（高分首板）

    # ── 已验证特征调整分（2026-08-18 主人指令「接」授权接入评分权重）──
    # 依据 verify_report_limitup_features_20260818.md 全市场验证：
    #   封单比≥3x → 次日高开≥6%占88%/晋级70.6% → +1.0；1~3x → 高开≥6%占59.6% → +0.3
    #   午后板(首封≥13:00) → 晋级率仅7~9.5% vs 早盘板19~35% → -0.5
    # 库内 predictability_score 不动，仅排序与展示用调整分（透明可追溯）
    def _adj_score(x):
        adj = 0.0
        sr = x.get("seal_ratio")
        if sr is not None and sr >= 3:
            adj += 1.0
        elif sr is not None and sr >= 1:
            adj += 0.3
        if x.get("afternoon"):
            adj -= 0.5
        # 2026-08-23 复盘线索：主线板块归属倾斜——候选优先靠"强主线/主线板块"
        # 8/21 实证：涨停集中在少数主线（医药/贵金属/算力），主线内候选资金共振更强；
        # 用现成 sector_tier/sector_count 数据做排序加分，不改变筛选集合（透明可追溯）
        tier = x.get("sector_tier")
        if tier == "强主线":
            adj += 0.5
        elif tier == "主线":
            adj += 0.2
        # 2026-08-23 复盘线索：机构龙虎榜买入=资金确认 → +0.5（透明可追溯，同主线倾斜）
        if x.get("inst_buy_n"):
            adj += 0.5
        # 2026-08-23 复盘线索③：中报季业绩因子——业绩预增/扭亏=基本面确认 → +0.5
        if x.get("earn_type"):
            adj += 0.5
        return adj

    # 机构龙虎榜买入标注（2026-08-23 复盘线索落地：读最近交易日龙虎榜'X家机构买入'）
    candidates = enrich_institution_buy(candidates)
    # 中报业绩因子标注（2026-08-23 复盘线索③：业绩预增/扭亏=基本面确认）
    candidates = enrich_earnings(candidates)
    for key in candidates:
        for x in candidates[key]:
            delta = _adj_score(x)
            x["score_adj"] = round(delta, 2)
            x["adjusted_score"] = round(x["predictability"] + delta, 2)
        candidates[key].sort(key=lambda x: -x["adjusted_score"])

    return {
        "date": date_str,
        "total_limit_up": len(rows),
"sectors": [{"sector": m["sector"], "count": m["count"], "tier": m["tier"],
                "em_change_pct": m.get("em_change_pct"),
                "em_amount": m.get("em_amount")}
             for m in mainlines],
        "mainlines": mainlines,
        "candidates": candidates,
        "summary": {
            "dragon_count": len(candidates["dragon"]),
            "center_count": len(candidates["center"]),
            "elastic_count": len(candidates["elastic"]),
            "launch_count": len(candidates["launch"]),
            "catch_up_count": len(candidates["catch_up"]),
            "total_candidates": sum(len(v) for v in candidates.values()),
        },
    }


def print_report(result: dict):
    print("=" * 65)
    print(f"  主线驱动选股器 — {result['date']}")
    print(f"  当日涨停总数: {result['total_limit_up']}")
    print("=" * 65)

    # 主线
    print(f"\n📊 主线识别:")
    if not result["sectors"]:
        print("  （无涨停≥5的行业，无明确主线）")
    for s in result["sectors"]:
        marker = "🔥" if s["tier"] == "强主线" else "📈"
        chg = s.get("em_change_pct", "")
        amt = s.get("em_amount", "")
        em_info = ""
        if chg is not None:
            try:
                em_info = f" | 板块涨跌:{float(chg):+.2f}%"
            except (ValueError, TypeError):
                pass
        print(f"  {marker} {s['sector']}: {s['count']}只涨停 ({s['tier']}){em_info}")

    # 候选
    cands = result["candidates"]
    total = result["summary"]["total_candidates"]
    print(f"\n📋 明日候选标的: 共{total}只（已过屠龙选股标准过滤）")

    for layer, label, icon in [
        ("dragon", "龙头层（≥4板）", "👑"),
        ("center", "中军层（2-3板）", "🛡️"),
        ("elastic", "弹性层（20cm首板）", "⚡"),
        ("launch", "首板启动层（天量首板）", "🚀"),
        ("catch_up", "补涨层（高分首板）", "🌱"),
    ]:
        items = cands[layer]
        if not items:
            continue
        print(f"\n  {icon} {label} ({len(items)}只):")
        for i, s in enumerate(items[:5], 1):
            price_note = ""
            tags = s.get("tags", "") or ""
            seal = s.get("seal_status", "") or ""
            # 已验证涨停特征标注: 封单比≥3x=加分(🔥), 午后板=降权(⚠️)
            extra = ""
            sr = s.get("seal_ratio")
            if sr is not None:
                extra += f" | 封单比{sr}x" + ("🔥" if sr >= 3 else "")
            if s.get("afternoon"):
                extra += " | ⚠️午后板"
            if s.get("inst_buy_n"):
                extra += f" | 🏛️机构{s['inst_buy_n']}家买入"
            if s.get("earn_type"):
                pct_txt = f"+{s['earn_pct']:.0f}%" if s.get("earn_pct") is not None else ""
                extra += f" | 📈{s['earn_type']}{pct_txt}"
            # 调整分展示: 8.5→9.5(+1.0调整)
            score_txt = f"{s['predictability']:.1f}"
            if s.get("score_adj"):
                sign = "+" if s["score_adj"] > 0 else ""
                score_txt += f"→{s['adjusted_score']:.1f}({sign}{s['score_adj']}调整)"
            print(f"    {i}. {s['code']} {s['name']} | {s['industry']} | "
                  f"{s['board']} | 可预判:{score_txt} | "
                  f"封板:{seal[:15]}{extra}")
            print(f"       策略: {s.get('strategy', '')[:60]}")

    # 操作建议
    print(f"\n{'─' * 65}")
    print("📝 操作建议:")
    if result["sectors"]:
        top_sector = result["sectors"][0]
        print(f"  1. 主线方向: {top_sector['sector']}（{top_sector['count']}只涨停）")
    print(f"  2. 优先关注: 中军层缩量加速票 > 补涨层高分首板")
    print(f"  3. 规避: 炸板≥3次/R4防御蓝筹/价格>30元/可预判<7分/R21复牌顶部风险")
    print(f"  4. 🚀 首板启动层关注: 天量首板(换手>10%)=主升浪启动信号（R1_LAUNCH_FIRSTBOARD）")
    print(f"  5. 买入前必过 pre_trade_check_v2.py 闸门（R1-R21全链）")
    print(f"  6. 卖出前必过 pre_sell_check.py 五检查（防卖飞+C5复牌顶部预警）")


def flat_candidates(result: dict) -> list[dict]:
    """把分层候选展平为 list[dict]（daily_trade_plan read_mainline_candidates 消费格式）。

    2026-08-23 修复：此前无任何脚本生成 mainline_candidates_*.json，
    daily_trade_plan 读到空 → 作战清单"主线雷达候选0只"。研究提示层输出，幂等。
    2026-09-10：name 统一经 stock_name_lookup 唯一名称接口归一（上游归因库半角 A
    如「深纺织A」与库名全角「深纺织Ａ」不一致，verify_stock_names 发布闸 000045 实锤）。
    """
    out = []
    for layer in ("dragon", "center", "elastic", "launch", "catch_up"):
        for x in result.get("candidates", {}).get(layer, []):
            out.append({
                "code": x.get("code"),
                "name": _canonical_name(x.get("code")) or x.get("name"),
                "industry": x.get("industry"), "sector_tier": x.get("sector_tier"),
                # daily_trade_plan 展示字段对齐：theme=主线方向，predictability=调整后分
                "theme": x.get("sector_tier"),
                "sector_count": x.get("sector_count"), "board": x.get("board"),
                "predictability": x.get("adjusted_score") or x.get("predictability"),
                "catalyst_strength": "-",  # 无催化强度数据源，展示占位
                "inst_buy_n": x.get("inst_buy_n"),
                "earn_type": x.get("earn_type"), "earn_pct": x.get("earn_pct"),
                "earn_period": x.get("earn_period"),
                "adjusted_score": x.get("adjusted_score"),
                "layer": layer,
            })
    return out


def _canonical_name(code: str | None) -> str | None:
    """名称唯一接口归一（2026-09-10）：stock_name_lookup 为全仓唯一名称真源。"""
    if not code:
        return None
    try:
        from stock_name_lookup import get_name
        return get_name(str(code)[-6:]) or None
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser(description="主线驱动选股器")
    p.add_argument("--date", default=None, help="日期 YYYYMMDD（默认最新）")
    p.add_argument("--json", action="store_true", help="输出JSON")
    a = p.parse_args()

    result = screen(a.date)
    if "error" in result:
        print(f"错误: {result['error']}")
        return 1

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
