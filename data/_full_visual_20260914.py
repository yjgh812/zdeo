# -*- coding: utf-8 -*-
"""全明细可视化报告生成器(20260914端点专项, 指令§四图表清单+按年逐笔明细表嵌入)。
输入: reports/drift_check_20260914e_0914end/ 引擎产物(逐笔CSV按年按臂+净值CSV+summary json)
输出: VISUAL_REPORT_FULL_2016_20260914.html (图表base64内嵌+全部逐笔明细表)
只读消费引擎产物, 不改任何引擎数据; 打印仅ASCII防GBK崩。
"""
import base64
import io
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT_DIR = r"D:\zcodesj\reports\drift_check_20260914e_0914end"
INITIAL_CAPITAL = 100000.0
ARMS = [
    ("bp_ep_core", "bp_ep_core"),
    ("ml_signal", "ml_signal"),
    ("composite_signal", "composite"),
    ("p2_signal", "p2_signal"),
    ("v375_signal", "v375_signal"),
    ("breakout_signal", "breakout"),
]
COLORS = {
    "combo": "#c0392b",
    "bp_ep_core": "#2c3e50",
    "ml_signal": "#2980b9",
    "composite_signal": "#7f8c8d",
    "p2_signal": "#8e44ad",
    "v375_signal": "#16a085",
    "breakout_signal": "#e67e22",
}


def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def load_trades_year(arm, year):
    p = os.path.join(OUT_DIR, f"{arm}_trades_{year}.csv")
    if not os.path.exists(p):
        return None
    return pd.read_csv(p, encoding="utf-8-sig")


def main():
    if not os.path.isdir(OUT_DIR):
        sys.stderr.write("OUT_DIR missing: %s\n" % OUT_DIR)
        raise SystemExit(2)

    # ---- 净值曲线数据 ----
    combo = pd.read_csv(os.path.join(OUT_DIR, "portfolio_combo_equity.csv"), encoding="utf-8-sig")
    combo["date"] = pd.to_datetime(combo["date"])
    equities = {"combo": combo.set_index("date")["equity"]}
    for arm, _ in ARMS:
        p = os.path.join(OUT_DIR, f"{arm}_equity.csv")
        if os.path.exists(p):
            eq = pd.read_csv(p, encoding="utf-8-sig")
            eq["date"] = pd.to_datetime(eq["date"])
            val_col = "equity" if "equity" in eq.columns else "value"
            equities[arm] = eq.set_index("date")[val_col]

    start_d = combo["date"].min().strftime("%Y-%m-%d")
    end_d = combo["date"].max().strftime("%Y-%m-%d")

    # ---- 逐笔汇总(按臂, 来自引擎按年CSV) ----
    arm_stats = {}
    years_all = set()
    for arm, _ in ARMS:
        per_year = {}
        for y in range(2016, 2027):
            df = load_trades_year(arm, y)
            if df is None or len(df) == 0:
                continue
            years_all.add(y)
            wins = int((df["盈亏金额"] > 0).sum())
            losses = int((df["盈亏金额"] <= 0).sum())
            per_year[y] = {
                "n": len(df), "wins": wins, "losses": losses,
                "winrate": wins / len(df) * 100.0,
                "pnl": float(df["盈亏金额"].sum()),
                "ret": float(df["盈亏金额"].sum()) / INITIAL_CAPITAL * 100.0,
                "df": df,
            }
        arm_stats[arm] = per_year

    # ---- 图1: 全臂净值曲线 ----
    fig, ax = plt.subplots(figsize=(11, 5.2))
    for key in ["combo"] + [a for a, _ in ARMS]:
        if key in equities:
            lw = 2.6 if key == "combo" else 1.2
            label = "组合50/50" if key == "combo" else key
            ax.plot(equities[key].index, equities[key].values, label=label, linewidth=lw, color=COLORS.get(key))
    ax.set_title("账户净值时序曲线 (2016-01-01 ~ %s, 初始10万)" % end_d)
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    b64_equity = fig_to_b64(fig)

    # ---- 图2: 组合最大回撤区间标记 ----
    eq = equities["combo"]
    run_max = eq.cummax()
    dd = eq / run_max - 1.0
    trough_d = dd.idxmin()
    peak_d = eq.loc[:trough_d].idxmax()
    mdd = float(dd.min()) * 100.0
    post = dd.loc[trough_d:]
    rec = post[post >= -1e-9]
    rec_d = rec.index.min() if len(rec) else None
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.plot(eq.index, eq.values, color="#c0392b", linewidth=1.6, label="组合50/50 净值")
    ax.axvspan(peak_d, trough_d, color="#e74c3c", alpha=0.25,
               label="最大回撤 %s ~ %s (%.2f%%)" % (peak_d.strftime("%Y-%m-%d"), trough_d.strftime("%Y-%m-%d"), mdd))
    if rec_d is not None:
        ax.axvline(rec_d, color="#27ae60", linestyle="--", linewidth=1.0,
                   label="回撤修复 %s" % rec_d.strftime("%Y-%m-%d"))
    ax.set_title("组合50/50 净值与最大回撤区间")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    b64_mdd = fig_to_b64(fig)

    # ---- 图3: 年度收益对比柱状(口径=年度盈亏/初始资金, 与引擎yearly一致) ----
    ys = sorted(years_all)
    width = 0.13
    fig, ax = plt.subplots(figsize=(11, 4.6))
    import numpy as np
    xs = np.arange(len(ys))
    for i, (arm, _) in enumerate(ARMS):
        vals = [arm_stats[arm].get(y, {}).get("ret", 0.0) for y in ys]
        ax.bar(xs + (i - 2.5) * width, vals, width, label=arm, color=COLORS.get(arm), alpha=0.85)
    ax.set_xticks(xs)
    ax.set_xticklabels([str(y) for y in ys])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("年度收益率对比 (年度总盈亏/初始资金10万, %%)")
    ax.legend(fontsize=8, ncol=3)
    ax.grid(alpha=0.3, axis="y")
    b64_year = fig_to_b64(fig)

    # ---- 图4: 单笔盈亏分布直方(全臂合并, 盈亏比例%) ----
    all_pnl_pct = []
    for arm, _ in ARMS:
        for y, rec_ in arm_stats[arm].items():
            all_pnl_pct.extend(rec_["df"]["盈亏比例%"].astype(float).tolist())
    fig, ax = plt.subplots(figsize=(11, 4.2))
    bins = np.linspace(-30, 60, 91)
    ax.hist(all_pnl_pct, bins=bins, color="#2980b9", alpha=0.75, edgecolor="white")
    ax.axvline(0, color="red", linewidth=1.0)
    ax.axvline(-8, color="#e74c3c", linestyle="--", linewidth=1.0, label="-8%硬止损")
    ax.set_title("单笔盈亏比例分布直方图 (六臂全部平仓笔, n=%d)" % len(all_pnl_pct))
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    b64_hist = fig_to_b64(fig)

    # ---- 图5: 胜率时序(生产双臂滚动30笔) ----
    fig, ax = plt.subplots(figsize=(11, 4.2))
    for arm in ["bp_ep_core", "ml_signal"]:
        seq = []
        for y in sorted(arm_stats[arm].keys()):
            d = arm_stats[arm][y]["df"].sort_values("卖出日期")
            seq.extend((d["盈亏金额"] > 0).astype(int).tolist())
        roll = pd.Series(seq).rolling(30).mean() * 100.0
        ax.plot(range(len(roll)), roll, linewidth=1.2, label="%s (滚动30笔)" % arm, color=COLORS.get(arm))
    ax.axhline(80, color="#7f8c8d", linestyle=":", linewidth=1.0, label="指令字面80%线(冻结口径已判不可达)")
    ax.set_ylim(0, 100)
    ax.set_title("胜率时序波动曲线 (逐笔顺序滚动30笔胜率, %)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    b64_wr = fig_to_b64(fig)

    # ---- 图6: 盈亏散点(持仓天数 x 盈亏比例%, 生产双臂) ----
    fig, ax = plt.subplots(figsize=(11, 4.6))
    for arm in ["bp_ep_core", "ml_signal"]:
        frames = [arm_stats[arm][y]["df"] for y in sorted(arm_stats[arm].keys())]
        d = pd.concat(frames)
        pos = d[d["盈亏金额"] > 0]
        neg = d[d["盈亏金额"] <= 0]
        ax.scatter(pos["持仓天数"], pos["盈亏比例%"], s=6, alpha=0.35, color="#c0392b", label="%s 盈利" % arm)
        ax.scatter(neg["持仓天数"], neg["盈亏比例%"], s=6, alpha=0.35, color="#27ae60", label="%s 亏损" % arm)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.axhline(-8, color="#e74c3c", linestyle="--", linewidth=0.9)
    ax.set_xlabel("持仓天数")
    ax.set_ylabel("单笔盈亏比例%")
    ax.set_title("盈亏散点分布 (生产双臂, 红=盈利 绿=亏损, 虚线=-8%止损)")
    ax.legend(fontsize=8, markerscale=2)
    ax.grid(alpha=0.3)
    b64_scatter = fig_to_b64(fig)

    # ---- 全周期指标表 ----
    rows = []
    for arm, disp in ARMS:
        frames = [arm_stats[arm][y]["df"] for y in sorted(arm_stats[arm].keys())]
        if not frames:
            continue
        d = pd.concat(frames)
        n = len(d)
        wins = int((d["盈亏金额"] > 0).sum())
        avg_win = float(d.loc[d["盈亏金额"] > 0, "盈亏金额"].mean()) if wins else 0.0
        n_loss = int((d["盈亏金额"] <= 0).sum())
        avg_loss = abs(float(d.loc[d["盈亏金额"] <= 0, "盈亏金额"].mean())) if n_loss else 0.0
        rows.append({
            "臂": arm,
            "笔数": n,
            "胜率%": round(wins / n * 100.0, 1),
            "盈亏比": round(avg_win / avg_loss, 2) if avg_loss else float("inf"),
            "平均持仓天数": round(float(d["持仓天数"].mean()), 1),
            "总盈亏": round(float(d["盈亏金额"].sum()), 0),
        })
    stat_df = pd.DataFrame(rows)

    combo_end = float(combo["equity"].iloc[-1])
    combo_ret = (combo_end / INITIAL_CAPITAL - 1.0) * 100.0
    span_years = (combo["date"].iloc[-1] - combo["date"].iloc[0]).days / 365.25
    combo_cagr = ((combo_end / INITIAL_CAPITAL) ** (1.0 / span_years) - 1.0) * 100.0

    # ---- HTML 组装 ----
    parts = []
    parts.append("<html><head><meta charset='utf-8'><title>屠龙系统 多模型全域历史回测 全明细可视化报告 2016~%s</title>" % end_d)
    parts.append("<style>body{font-family:'Microsoft YaHei',sans-serif;margin:24px;} "
                 "table{border-collapse:collapse;font-size:11px;margin:8px 0;} "
                 "th,td{border:1px solid #ccc;padding:2px 6px;} th{background:#f0f3f5;} "
                 "img{max-width:100%;} h2{border-left:6px solid #c0392b;padding-left:8px;} "
                 "h3{border-left:4px solid #2980b9;padding-left:6px;} .warn{color:#c0392b;} .ok{color:#27ae60;} "
                 ".note{background:#fdf6e3;padding:8px 12px;border:1px solid #e0d6b8;font-size:12px;}</style></head><body>")
    parts.append("<h1>屠龙系统 多模型全域历史回测 · 全明细可视化报告</h1>")
    parts.append("<p>回测区间: %s ~ %s | 初始资金: 100,000 元 | 组合50/50 期末净值: <b>%s 元 (+%.2f%%)</b> | "
                 "最大回撤: <b>%.2f%%</b> | 年化复合(CAGR): <b>%.2f%%</b></p>"
                 % (start_d, end_d, format(combo_end, ",.2f"), combo_ret, mdd, combo_cagr))
    parts.append("<div class='note'>口径裁决(grilling共识, 主人2026-09-09确认): 验收标尺=8/31冻结口径(十年总收益≥200%组合50/50); "
                 "指令字面「年化复合200%/全周期胜率80%」系已判不可达口径, 本报告如实呈现差距零调参。"
                 "横盘突破不纳入主对照(四重证伪, 仅作退役对照臂展示); 频率=月度调仓+每日监控; "
                 "p2/breakout退役对照臂信号表MAX=20260908, 存量持仓照常退出, 2026段明细如实截断于信号端点。</div>")

    parts.append("<h2>一、基础概览与图表(指令§四1/§四4)</h2>")
    parts.append("<img src='data:image/png;base64,%s'/><br/>" % b64_equity)
    parts.append("<img src='data:image/png;base64,%s'/><br/>" % b64_mdd)
    parts.append("<h3>各臂全周期指标</h3>")
    parts.append(stat_df.to_html(index=False))

    parts.append("<h2>二、图表清单续(指令§四4)</h2>")
    parts.append("<img src='data:image/png;base64,%s'/><br/>" % b64_year)
    parts.append("<img src='data:image/png;base64,%s'/><br/>" % b64_hist)
    parts.append("<img src='data:image/png;base64,%s'/><br/>" % b64_wr)
    parts.append("<img src='data:image/png;base64,%s'/><br/>" % b64_scatter)

    parts.append("<h2>三、按自然年独立拆分 · 年度汇总(指令§四2)</h2>")
    for arm, _ in ARMS:
        if not arm_stats[arm]:
            continue
        parts.append("<h3>%s</h3>" % arm)
        t = ["<table><tr><th>年份</th><th>交易笔数</th><th>盈利笔</th><th>亏损笔</th><th>年度胜率</th><th>年度总盈亏(元)</th><th>年度收益率%</th></tr>"]
        for y in sorted(arm_stats[arm].keys()):
            r = arm_stats[arm][y]
            cls = "ok" if r["pnl"] > 0 else "warn"
            t.append("<tr><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%.1f%%</td><td class='%s'>%+.0f</td><td class='%s'>%+.2f</td></tr>"
                     % (y, r["n"], r["wins"], r["losses"], r["winrate"], cls, r["pnl"], cls, r["ret"]))
        t.append("</table>")
        parts.append("".join(t))

    parts.append("<h2>四、按自然年独立拆分 · 逐笔交易明细(指令§四2, 全臂全量嵌入)</h2>")
    for arm, _ in ARMS:
        if not arm_stats[arm]:
            continue
        parts.append("<h3>%s 逐笔明细</h3>" % arm)
        for y in sorted(arm_stats[arm].keys()):
            d = arm_stats[arm][y]["df"]
            cols = ["股票代码", "股票名称", "买入日期", "买入价格", "卖出日期", "卖出价格",
                    "买入决策理由", "卖出触发理由", "盈亏金额", "盈亏比例%"]
            show = d[cols].copy()
            show["盈亏金额"] = show["盈亏金额"].round(0)
            show["盈亏比例%"] = show["盈亏比例%"].round(2)
            html_tbl = show.to_html(index=False, border=0)
            parts.append("<details><summary><b>%d年 (%d笔)</b></summary>%s</details>" % (y, len(d), html_tbl))

    parts.append("<h2>五、深度绩效诊断与策略完善方案(指令§四3/§四5)</h2>")
    parts.append("<p>见同目录 <a href='DEEP_DIAGNOSTIC.md'>DEEP_DIAGNOSTIC.md</a>、<a href='OPTIMIZATION_PLAN.md'>OPTIMIZATION_PLAN.md</a>、"
                 "<a href='MASTER_REPORT_指令结构_20260914.md'>MASTER_REPORT_指令结构_20260914.md</a>(指令结构主报告)。</p>")
    parts.append("<h2>风险提示</h2>")
    parts.append("<p class='warn'>回测历史理论绩效不代表未来实盘收益。本报告基于2016-01-01至%s历史行情数据, "
                 "在严格避免未来函数(qfq+next-bar+PIT)前提下生成; 退役对照臂受信号表端点限制存在如实截断。</p>" % end_d)
    parts.append("</body></html>")

    out_path = os.path.join(OUT_DIR, "VISUAL_REPORT_FULL_2016_20260914.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))

    print("visual report written:", out_path.encode("ascii", "replace").decode())
    print("combo_end=%.2f combo_ret=%.2f%% mdd=%.2f%% cagr=%.2f%%" % (combo_end, combo_ret, mdd, combo_cagr))
    print("total trades embedded:", sum(r["n"] for a in arm_stats.values() for r in a.values()))


if __name__ == "__main__":
    main()
