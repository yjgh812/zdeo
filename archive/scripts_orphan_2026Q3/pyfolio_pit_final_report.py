# -*- coding: utf-8 -*-
"""
pyfolio_pit_final_report.py — 财务因子 PIT 终局 pyfolio 绩效报告
输入: arbitration_pit_final_20260815.py 落盘的 arbitration_pit_navs.pkl
输出: ①A_pit(终局真实成色)完整 tear sheet ②前视vs PIT vs 基准对比图 ③指标表 md
"""
from datetime import datetime
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pyfolio as pf
import empyrical

REPORT_DIR = Path(r"D:\zcodesj\reports")
navs = pd.read_pickle(REPORT_DIR / "arbitration_pit_navs.pkl")
print("nav 组:", list(navs.keys()))

A = "A_pit_WFicir_Top50"        # 终局: droe+roa PIT qfq next-bar
D = "D_asof_WFicir_Top50"       # 前视版(同引擎)
C = "C_droe_pit_Top50"          # droe 单因子 PIT
B = "B_bpep_pit_Top20"          # bp+ep 生产结构 PIT
EW = "EW_benchmark"

bench_ret = navs[EW].pct_change().dropna()
strat_ret = navs[A].pct_change().dropna()
common = strat_ret.index.intersection(bench_ret.index)
strat_ret, bench_ret = strat_ret.loc[common], bench_ret.loc[common]
print(f"策略: {A} | 基准: {EW} | {common[0].date()}~{common[-1].date()} {len(common)}日")

def full_stats(r, br):
    return {
        "年化收益": empyrical.annual_return(r), "年化波动": empyrical.annual_volatility(r),
        "Sharpe": empyrical.sharpe_ratio(r), "Sortino": empyrical.sortino_ratio(r),
        "Calmar": empyrical.calmar_ratio(r), "最大回撤": empyrical.max_drawdown(r),
        "Alpha(年)": empyrical.alpha(r, br), "Beta": empyrical.beta(r, br),
    }

cols = {k: full_stats(navs[k].pct_change().dropna(), bench_ret) for k in [A, D, C, B, EW]}
stats_df = pd.DataFrame(cols).round(4)
print("\n", stats_df.to_string())

# ── tear sheet（终局 A_pit）──
fig = pf.create_returns_tear_sheet(strat_ret, benchmark_rets=bench_ret, return_fig=True)
out_ts = REPORT_DIR / "pyfolio_pit_final_droe_roa_tearsheet.png"
fig.savefig(out_ts, dpi=110, bbox_inches="tight")
plt.close(fig)

# ── 对比图: PIT终局 vs 前视版 vs bp+ep PIT vs 基准 ──
fig2, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True,
                                gridspec_kw={"height_ratios": [2, 1]})
series = [(A, "#c0392b", 1.9, "droe+roa PIT(终局)"), (D, "#e67e22", 1.2, "droe+roa as-of(前视版)"),
          (C, "#27ae60", 1.1, "droe单因子PIT"), (B, "#8e44ad", 1.1, "bp+ep PIT(生产结构)"),
          (EW, "#7f8c8d", 1.1, "等权≤30基准")]
for key, color, lw, label in series:
    nav = navs[key].reindex(common)
    ax1.plot(nav.index, nav.values, label=label, lw=lw, color=color)
    dd = nav / nav.cummax() - 1
    ax2.fill_between(dd.index, dd.values, 0, alpha=0.35, color=color, lw=0.5)
ax1.set_yscale("log")
ax1.legend(loc="upper left", fontsize=9)
ax1.set_title(f"财务因子终局对比(全部PIT+qfq+next-bar)  {common[0].date()}~{common[-1].date()}", fontsize=12)
ax1.grid(alpha=0.3)
ax2.set_title("回撤", fontsize=11)
ax2.grid(alpha=0.3)
fig2.tight_layout()
out_cmp = REPORT_DIR / "pyfolio_pit_vs_asof_vs_benchmark.png"
fig2.savefig(out_cmp, dpi=110, bbox_inches="tight")
plt.close(fig2)

# ── md ──
fmt = lambda v: f"{v:+.2%}" if abs(v) > 1 else f"{v:+.4f}"
md = REPORT_DIR / f"PIT_FINAL_PYFOLIO_{datetime.now():%Y%m%d}.md"
lines = [
    "# 财务因子 PIT 终局 pyfolio 绩效报告",
    "",
    f"- 生成: {datetime.now().isoformat(timespec='seconds')}",
    f"- 数据: arbitration_pit_final_20260815.py（严格PIT+qfq+next-bar，前视禁令后唯一可信口径）",
    f"- 区间: {common[0].date()} ~ {common[-1].date()}（{len(common)} 交易日）",
    "",
    "## 指标对比（empyrical 252年化，Alpha/Beta 相对等权≤30基准）",
    "",
    "| 指标 | " + " | ".join(stats_df.columns) + " |",
    "|:--|" + ":--:|" * len(stats_df.columns),
]
for row in stats_df.index:
    lines.append(f"| {row} | " + " | ".join(fmt(v) for v in stats_df.loc[row]) + " |")
lines += [
    "",
    "## 要点",
    "",
    f"- 终局真实成色 droe+roa PIT Top50：年化 {fmt(stats_df.loc['年化收益', A])} / Sharpe {stats_df.loc['Sharpe', A]:.3f} / MDD {fmt(stats_df.loc['最大回撤', A])}",
    f"- 前视水分可视化：同引擎 as-of 版年化 {fmt(stats_df.loc['年化收益', D])} vs PIT {fmt(stats_df.loc['年化收益', A])}（差 {(stats_df.loc['年化收益', D]-stats_df.loc['年化收益', A])*100:.1f}pp/年）",
    f"- bp+ep 生产结构 PIT 真实：年化 {fmt(stats_df.loc['年化收益', B])}（负超额 vs 基准 {fmt(stats_df.loc['年化收益', EW])}）",
    f"- droe 单因子 PIT 与组合几乎无差（{fmt(stats_df.loc['年化收益', C])}），roa 在 PIT 下不贡献",
    "",
    "## 图表",
    "",
    f-"" if False else f"- 终局 tear sheet: reports/{out_ts.name}",
    f"- 五线对比图(前视水分): reports/{out_cmp.name}",
]
md.write_text("\n".join(lines), encoding="utf-8")
print(f"\ntear sheet : {out_ts}")
print(f"对比图     : {out_cmp}")
print(f"md 报告    : {md}")
print("PIT PYFOLIO REPORT DONE")
