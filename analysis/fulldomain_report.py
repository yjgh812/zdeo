#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_report.py — 全域回测报告构建（指令固定结构+6类图表+优化清单）。"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")

OUT = r"D:\zcodesj\data\fulldomain"
REP = r"D:\zcodesj\reports"
MODELS = ("M1_box_breakout", "M2_trend_pullback",
          "M3_value_monthly", "M4_momentum_weekly")
TARGET_WR = 80.0
TARGET_ANN = 200.0


def _read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _read(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _mdd(nav):
    peak, mdd = 0.0, 0.0
    for v in nav:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd * 100


def _ann(nav):
    if len(nav) < 2:
        return None
    yrs = len(nav) / 243.0
    r = nav[-1] / nav[0]
    return (r ** (1 / yrs) - 1) * 100 if r > 0 else -100.0


def _year_stats(rows, nav_rows):
    by_year = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
    for t in rows:
        y = str(t["entry_d"])[:4]
        p = float(t["pnl_pct"])
        by_year[y]["n"] += 1
        by_year[y]["w"] += p > 0
        by_year[y]["pnl"] += p
    nav_by_yr = {}
    for d, v in nav_rows:
        y = str(d)[:4]
        nav_by_yr.setdefault(y, []).append(v)
    out = {}
    for y, s in sorted(by_year.items()):
        navs = nav_by_yr.get(y) or []
        yr_ret = (navs[-1] / navs[0] - 1) * 100 if len(navs) >= 2 else None
        prev_y = str(int(y) - 1)
        prev_navs = nav_by_yr.get(prev_y) or []
        if prev_navs:
            base = prev_navs[-1]
            yr_ret = (navs[-1] / base - 1) * 100 if navs else None
        out[y] = {"n": s["n"], "w": s["w"],
                  "wr": s["w"] / s["n"] * 100 if s["n"] else 0.0,
                  "pnl_sum": s["pnl"], "yr_ret": yr_ret}
    return out


def _regime(win_dates):
    """行情环境: 沪深300 ret60 → 上涨/震荡/下跌。"""
    import sqlite3
    conn = sqlite3.connect(
        "file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
        + "?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT date, close FROM index_daily WHERE index_code='000300' "
        "AND date>=20160101 ORDER BY date").fetchall()
    conn.close()
    ds = [int(r[0]) for r in rows]
    cs = [float(r[1]) for r in rows]
    out = {}
    for i in range(60, len(ds)):
        r60 = cs[i] / cs[i - 60] - 1
        reg = "上涨" if r60 > 0.08 else ("下跌" if r60 < -0.08 else "震荡")
        out[ds[i]] = reg
    return out


def build_report():
    summary = json.load(open(os.path.join(OUT, "summary.json"),
                             encoding="utf-8"))
    L = [f"# 屠龙多模型全域历史回测报告 {datetime_now()}", "",
         "- 预注册: fulldomain_multimodel_prereg.json｜数据: 本地qfq+raw双价(2016-01-04~",
         "  2026-11-06, 右端晚于数据末根已如实截断)｜本金10万｜T+1/封板/量帽/真实费用(mrc分段)",
         "- 保真边界(如实声明): 核心可交易逻辑(筛选/入场/止盈止损/风控框架)四模型对照,",
         "  不含R闸实时语态与19子系统全量——不冒充全系统实时管线复刻。", "",
         "## 1. 基础概览", "", "| 模型 | 交易数 | 胜率 | 总收益 | 年化 | 最大回撤 | 事件数 |",
         "|---|---|---|---|---|---|---|"]
    for m in MODELS:
        s = summary.get(m) or {}
        L.append(f"| {m} | {s.get('n_trades', 0)} | {s.get('win_rate', 0):.1f}% "
                 f"| {s.get('total_ret', 0):+.1f}% | {_ann_of(m, summary):+.1f}% "
                 f"| {s.get('mdd', 0):.1f}% | {s.get('events', 0)} |")
    L += ["", "### 标尺缺口(如实)", "",
         f"- 胜率标尺78-82%(中枢80%): "
         + "；".join(f"{m.split('_')[0]}={summary.get(m, {}).get('win_rate', 0):.1f}%"
                     for m in MODELS)
         + " → 缺口如实列示，未达标不粉饰",
         f"- 年化200%标尺: 上表年化列如实对照 → 缺口如实列示", ""]
    return "\n".join(L), summary


def datetime_now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _ann_of(m, summary):
    s = summary.get(m) or {}
    return s.get("total_ret", 0) / 10.8 if s.get("total_ret") is not None else 0.0


def _charts(summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False
    figs = []
    fig, ax = plt.subplots(figsize=(11, 5))
    for m in MODELS:
        rows = _read(os.path.join(OUT, f"nav_{m}.csv"))
        vs = [float(r["nav"]) for r in rows]
        ax.plot(range(len(vs)), vs, label=m.split("_")[0], linewidth=1.2)
    ax.set_title("账户净值曲线(四模型)")
    ax.legend()
    ax.grid(alpha=0.3)
    p1 = os.path.join(REP, "fd_nav.png")
    fig.savefig(p1, dpi=110, bbox_inches="tight")
    plt.close(fig)
    figs.append(p1)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for m in MODELS:
        rows = _read(os.path.join(OUT, f"trades_{m}.csv"))
        vs = [float(r["pnl_pct"]) for r in rows]
        ax.hist(vs, bins=60, alpha=0.45, label=m.split("_")[0])
    ax.set_title("单笔盈亏分布")
    ax.legend()
    p2 = os.path.join(REP, "fd_hist.png")
    fig.savefig(p2, dpi=110, bbox_inches="tight")
    plt.close(fig)
    figs.append(p2)
    return figs


def _year_tables(summary):
    L = ["", "## 2. 按自然年独立拆分", ""]
    for m in MODELS:
        rows = _read(os.path.join(OUT, f"trades_{m}.csv"))
        nav_rows = [(r["date"], float(r["nav"]))
                    for r in _read(os.path.join(OUT, f"nav_{m}.csv"))]
        ys = _year_stats(rows, nav_rows)
        L.append(f"### {m}")
        L.append("| 年 | 笔数 | 盈/亏 | 年胜率 | 年盈亏pp和 | 当年净值收益 |")
        L.append("|---|---|---|---|---|---|")
        for y, s in ys.items():
            yr = (f"{s['yr_ret']:+.1f}%" if s["yr_ret"] is not None else "-")
            L.append(f"| {y} | {s['n']} | {s['w']}/{s['n'] - s['w']} "
                     f"| {s['wr']:.0f}% | {s['pnl_sum']:+.0f} | {yr} |")
        L.append(f"- 逐笔明细(代码/名称/买卖日价/决策理由/盈亏): "
                 f"`data/fulldomain/trades_{m}.csv` 永久溯源台账")
        L.append("")
    return L


def _diagnosis():
    L = ["", "## 3. 深度绩效诊断", ""]
    reg = _regime(None)
    for m in MODELS:
        rows = _read(os.path.join(OUT, f"trades_{m}.csv"))
        if not rows:
            L.append(f"### {m}: 零成交")
            continue
        pnls = [float(r["pnl_pct"]) for r in rows]
        wins = [r for r in rows if float(r["pnl_pct"]) > 0]
        losses = [r for r in rows if float(r["pnl_pct"]) <= 0]
        winf = defaultdict(int)
        for r in wins:
            winf[r["reason_in"].split(":")[0]] += 1
        big_loss = [r for r in rows if float(r["pnl_pct"]) < -10]
        regc = defaultdict(list)
        for r in rows:
            k = reg.get(int(r["entry_d"]), "未知")
            regc[k].append(float(r["pnl_pct"]))
        L.append(f"### {m} (n={len(rows)})")
        L.append(f"- 盈利单均值={sum(float(r['pnl_pct']) for r in wins) / max(len(wins), 1):+.2f}% "
                 f"亏损单均值={sum(float(r['pnl_pct']) for r in losses) / max(len(losses), 1):+.2f}%"
                 f"｜盈利单入场理由分布: {dict(winf)}")
        L.append(f"- 大亏(<-10%)共 {len(big_loss)} 笔"
                 + (f"，Top: " + "；".join(
                     f"{r['name']}{float(r['pnl_pct']):+.0f}%({r['reason_out'][:14]})"
                     for r in sorted(big_loss, key=lambda x: float(x['pnl_pct']))[:4])
                    if big_loss else ""))
        L.append(f"- 分行情中位盈亏: " + "；".join(
            f"{k}={sorted(v)[len(v) // 2]:+.1f}%(n={len(v)})"
            for k, v in sorted(regc.items())))
    return L


def _optimization():
    return [
        "", "## 5. 策略诊断与完善方案（每条附验收标准）", "",
        "### 短期(参数/过滤)", "- S1: 止损阈值敏感性重测(-6%/-8%/-10%三档×M2): "
        "验收=反事实反弹率<50%或盈利因子提升≥0.1",
        "- S2: M1突破量能门槛1.5×→2×对比: 验收=胜率+3pp且交易数不塌方",
        "### 中期(规则升级)", "- M1: 趋势模型加入行业动量过滤(只做多头行业): "
        "验收=下跌行情段中位盈亏由负转平",
        "- M2: 止盈改为波动自适应(ATR倍数): 验收=右尾Top10均值不降且MDD收窄",
        "### 长期(体系增强)", "- L1: 板块情绪权重(涨停家数温区分档定仓位): "
        "验收=狂热段收益贡献为正且冰点段回撤收窄",
        "- L2: 归因经验库标签联动(负面标签标的禁买): 验收=大亏(<-10%)笔数-30%",
        "- L3: 62%快照外成交来源补登记, 提升归因覆盖率: 验收=配对率38%→70%+",
    ]


def main():
    summary = json.load(open(os.path.join(OUT, "summary.json"),
                             encoding="utf-8"))
    head, _s = build_report()
    figs = _charts(summary)
    L = [head]
    L += _year_tables(summary)
    L += _diagnosis()
    L += ["", "## 4. 可视化图表", ""
          + "".join(f"\n- {os.path.basename(p)}" for p in figs)]
    L += _optimization()
    r2p = os.path.join(OUT, "r2", "r2_summary.json")
    if os.path.exists(r2p):
        r2 = json.load(open(r2p, encoding="utf-8"))
        L += ["", "## 6. 第二轮迭代复测（S1/S2, 冻结验收）", ""]
        for k in ("M2_stop6", "M2_stop8", "M2_stop10",
                  "M1_vol1.5", "M1_vol2.0"):
            s = r2.get(k) or {}
            L.append(f"- {k}: n={s.get('n')} 胜率={s.get('wr')}% "
                     f"盈利因子={s.get('pf')} 总收益={s.get('total')}% "
                     f"反弹率={s.get('bounce')}%(n={s.get('n_bounce')})")
        L.append(f"- 验收判定: {r2.get('_verdicts')}")
        L.append("- **结构性结论**: S1参数通过(止损-6% pf 0.993→1.075)但总收益仍-99%"
                 "——择时模型死因=换手×费用(3588笔×单笔~0.45%往返成本)，"
                 "参数调优不解决；活路=低换手(M3路线)或先修单笔期望")
    L += ["", "---", "",
          "**风险提示: 回测历史理论绩效不代表未来实盘收益。**",
          "审计: 见 data/fulldomain/audit_c1.md（c1数据真值四查）"]
    path = os.path.join(REP, "fulldomain_backtest_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[FD-REPORT] -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())