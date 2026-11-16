#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""firstboard_full_history.py — 全量首板晋级研究（纯本地数据，零外部依赖）。
2026-11-07 主人令「优先使用本地数据，全量测试」。

数据源：dragon_quant.db stock_daily_raw（1990-12-19 ~ 2026-11-06, 11,605 只, 2686万行）。
口径：
  涨停日 = 收盘涨幅 ≥ 板块阈-0.2pp（板块阈按 market_rules_config 分段: 主板10%,
          创业板2020-08-24前10%/后20%, 科创板20%; 北交所4/8/92前缀整段剔除）
  首板   = 涨停日且前一日非涨停；剔除每只股票前60个交易日(次新)
  晋级   = 次一交易日收盘涨停（同板阈）
  ST     = 5%板天然达不到9.8%阈, 被口径自然排除（无需额外标记）
维度：年份 / 板块(10%·20%) / 全天封死 vs 炸板回封(盘中低点是否脱离涨停价) /
     近两年(2025-01-01起) / 窗口校验段(2026-08-10~11-06, 与45日研究15.9%对表)。
限制(如实)：封单额/封单比/封板时刻/竞价维度不在日线数据内——见45日研究(limit_up_patterns)。
退出码：0正常/3数据源不可用。"""
from __future__ import annotations

import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PRICE_DB = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
            + "?mode=ro")
REPORT_DIR = r"D:\zcodesj\reports"

CNT = {"year": Counter(), "grp": Counter(), "seal": Counter(),
       "year_grp": Counter(), "grp_seal": Counter(), "temp": Counter(),
       "temp10": Counter()}
PRO = {"year": Counter(), "grp": Counter(), "seal": Counter(),
       "year_grp": Counter(), "grp_seal": Counter(), "temp": Counter(),
       "temp10": Counter()}
NXT = {"10%": [], "20%": []}   # 首板次日涨幅(原始%)——归一化续强度裁定用
STATS = {"codes": 0, "bj_skipped": 0, "events": 0, "recent": 0,
         "recent_pro": 0, "win_events": 0, "win_pro": 0, "promoted": 0}


def run():
    import numpy as np
    try:
        conn = sqlite3.connect(PRICE_DB, uri=True, check_same_thread=False)
    except Exception as e:  # noqa: BLE001
        print(f"[FB-FULL] [FAIL-CLOSED] 数据源不可用({type(e).__name__}: {e})")
        return 3
    day_lim = Counter()      # 每日全市场收盘涨停家数(市场温度)
    events = []              # (日期, 是否晋级) — 循环后按温度分桶
    from itertools import groupby
    from operator import itemgetter
    cur = conn.execute(
        "SELECT code, date, high, low, close FROM stock_daily_raw "
        "ORDER BY code, date")
    k = 0
    for code, grp in groupby(cur, key=itemgetter(0)):
        k += 1
        if k % 1500 == 0:
            print(f"[FB-FULL] 进度 {k} 事件={STATS['events']}", flush=True)
        rows = [(r[1], r[2], r[3], r[4]) for r in grp]
        if code.startswith(("4", "8", "92")):
            STATS["bj_skipped"] += 1
            continue
        n = len(rows)
        if n < 63:
            continue
        STATS["codes"] += 1
        d = [r[0] for r in rows]
        c = [float(r[3]) for r in rows]
        lo = [float(r[2]) for r in rows]
        closed = [False] * n
        thr = [0.0] * n
        p30 = code.startswith("30")
        star = code.startswith("68")
        for i in range(1, n):
            prev = c[i - 1]
            if prev <= 0:
                continue
            if star:
                t = 0.198
            elif p30:
                t = 0.198 if d[i] >= 20200824 else 0.098
            else:
                t = 0.098
            thr[i] = t
            closed[i] = (c[i] / prev - 1.0) >= t - 0.002
            if closed[i]:
                day_lim[d[i]] += 1
        for i in range(61, n - 1):
            if not (closed[i] and not closed[i - 1]):
                continue
            prev_c = c[i - 1]
            lim = prev_c * (1.0 + thr[i])
            bounce = lo[i] < lim - max(lim * 1e-3, 0.01)
            year = d[i] // 10000
            grp = "20%" if thr[i] > 0.1 else "10%"
            seal_k = "封死" if not bounce else "炸板回封"
            pro = bool(closed[i + 1])
            events.append((d[i], pro))
            if c[i] > 0:
                NXT[grp].append((c[i + 1] / c[i] - 1.0) * 100)
            STATS["events"] += 1
            STATS["promoted"] += pro
            for kk in ("year", "grp", "seal"):
                CNT[kk][({"year": year, "grp": grp, "seal": seal_k}[kk])] += 1
                if pro:
                    PRO[kk][({"year": year, "grp": grp, "seal": seal_k}[kk])] += 1
            CNT["year_grp"][(year, grp)] += 1
            if pro:
                PRO["year_grp"][(year, grp)] += 1
            CNT["grp_seal"][(grp, seal_k)] += 1
            if pro:
                PRO["grp_seal"][(grp, seal_k)] += 1
            if d[i] >= 20250101:
                STATS["recent"] += 1
                STATS["recent_pro"] += pro
            if 20260810 <= d[i] <= 20261106:
                STATS["win_events"] += 1
                STATS["win_pro"] += pro
    conn.close()
    for dd, pro in events:
        cc = day_lim.get(dd, 0)
        b = ("<10家" if cc < 10 else "10-30家" if cc < 30 else
             "30-60家" if cc < 60 else "60-100家" if cc < 100 else "≥100家")
        CNT["temp"][b] += 1
        if pro:
            PRO["temp"][b] += 1
        if dd >= 20100101:
            CNT["temp10"][b] += 1
            if pro:
                PRO["temp10"][b] += 1
    return report()


def _rate(cnt, pro, key=None):
    n = cnt[key] if key else sum(cnt.values())
    p = pro[key] if key else sum(pro.values())
    if not n:
        return "n=0"
    return f"n={n} 晋级率={p / n * 100:.1f}%"


def report():
    L = [f"# 全量首板晋级研究（纯本地数据） {datetime.now():%Y-%m-%d %H:%M}", "",
         f"- 样本域：本地原始日线 1990-12-19~2026-11-06，覆盖 {STATS['codes']} 只"
         f"（北交所剔除 {STATS['bj_skipped']} 只）；每只股票剔除前60个交易日(次新)",
         f"- 首板事件 {STATS['events']} 笔，总体晋级率 {_rate(CNT['year'], PRO['year'])}",
         "- ST 口径被阈值自然排除(5%板达不到9.8%阈)；封单/时刻/竞价维度不在日线内(见45日研究)。",
         "", "## 近两年（2025-01-01 起）", "",
         f"- {_rate_from(STATS['recent'], STATS['recent_pro'])}", "",
         "## 窗口校验段（2026-08-10~11-06，与45日研究对表）", "",
         f"- 全量口径 {_rate_from(STATS['win_events'], STATS['win_pro'])} "
         f"vs 45日样本库口径 15.9%——两管线交叉验证", "",
         "## 按年份", ""]
    for y in sorted(CNT["year"]):
        L.append(f"- {y}：{_rate(CNT['year'], PRO['year'], y)}")
    L += ["", "## 按板块（含归一化续强度裁定）", ""]
    for g in sorted(CNT["grp"]):
        vals = sorted(NXT.get(g) or [])
        if vals:
            med = vals[len(vals) // 2]
            thr_pp = 19.8 if g == "20%" else 9.8
            L.append(f"- {g}：{_rate(CNT['grp'], PRO['grp'], g)}"
                     f"｜次日涨幅中位={med:+.2f}%｜归一化续强度(次日涨幅÷板阈)中位="
                     f"{med / thr_pp:.3f}")
        else:
            L.append(f"- {g}：{_rate(CNT['grp'], PRO['grp'], g)}")
    L.append("- 裁定说明：20%板晋级需次日再涨19.8%(10%板的两倍机械难度)，晋级率不可跨板直接比较；"
             "归一化续强度(次日涨幅÷各自板阈)才是同尺度的连续性度量。")
    L += ["", "## 封死 vs 炸板回封", ""]
    for s in sorted(CNT["seal"]):
        L.append(f"- {s}：{_rate(CNT['seal'], PRO['seal'], s)}")
    L += ["", "## 板块×封死", ""]
    for key in sorted(CNT["grp_seal"]):
        L.append(f"- {key[0]}×{key[1]}：{_rate(CNT['grp_seal'], PRO['grp_seal'], key)}")
    L += ["", "## 按当日全市场涨停家数（市场温度, 36年检验「情绪只定仓位」）", ""]
    for b in ("<10家", "10-30家", "30-60家", "60-100家", "≥100家"):
        L.append(f"- 全历史 {b}：{_rate(CNT['temp'], PRO['temp'], b)}")
    L.append("")
    for b in ("<10家", "10-30家", "30-60家", "60-100家", "≥100家"):
        L.append(f"- 2010年后 {b}：{_rate(CNT['temp10'], PRO['temp10'], b)}")
    path = os.path.join(REPORT_DIR,
                        f"firstboard_full_history_{datetime.now():%Y%m%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[FB-FULL] 事件={STATS['events']} 总体晋级率="
          f"{STATS['promoted'] / max(STATS['events'], 1) * 100:.1f}% "
          f"近两年={STATS['recent_pro'] / max(STATS['recent'], 1) * 100:.1f}% "
          f"窗口校验段={STATS['win_pro'] / max(STATS['win_events'], 1) * 100:.1f}%"
          f" -> {path}")
    return 0


def _rate_from(n, p):
    return f"n={n} 晋级率={p / n * 100:.1f}%" if n else "n=0"


if __name__ == "__main__":
    raise SystemExit(run())
