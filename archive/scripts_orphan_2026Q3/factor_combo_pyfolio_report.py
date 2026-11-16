# -*- coding: utf-8 -*-
"""
factor_combo_pyfolio_report.py — 因子组合 pyfolio 正式绩效报告
读取 factor_combo_walkforward.py 落盘的 nav pickle，输出：
  1. 策略完整 tear sheet（14图，benchmark=等权≤30元基准）
  2. 策略 vs 基准 累计净值(log)+回撤对比图
  3. empyrical 全指标对比表 -> md 报告
用法: python factor_combo_pyfolio_report.py [nav.pkl]
"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pyfolio as pf
import empyrical

REPORT_DIR = Path(r"D:\zcodesj\reports")

# ── 1. 加载 nav ──
pkl = Path(sys.argv[1]) if len(sys.argv) > 1 else REPORT_DIR / "factor_combo_navs_20160104_20260731.pkl"
navs = pd.read_pickle(pkl)
strat_key = next(k for k in navs if "组合" in k)
bench_key = next(k for k in navs if "基准" in k)
print(f"nav 源: {pkl.name}")
print(f"策略: {strat_key}  |  基准: {bench_key}")

strat_ret = navs[strat_key].pct_change().dropna()
bench_ret = navs[bench_key].pct_change().dropna()
common = strat_ret.index.intersection(bench_ret.index)
strat_ret, bench_ret = strat_ret.loc[common], bench_ret.loc[common]
print(f"区间: {common[0].date()} ~ {common[-1].date()}  {len(common)} 交易日")

# ── 2. 指标对比表（empyrical，全部组合） ──
def full_stats(r, br):
    return {
        "年化收益": empyrical.annual_return(r),
        "年化波动": empyrical.annual_volatility(r),
        "Sharpe": empyrical.sharpe_ratio(r),
        "Sortino": empyrical.sortino_ratio(r),
        "Calmar": empyrical.calmar_ratio(r),
        "Omega": empyrical.omega_ratio(r),
        "最大回撤": empyrical.max_drawdown(r),
        "尾部比率": empyrical.tail_ratio(r),
        "Alpha(年)": empyrical.alpha(r, br),
        "Beta": empyrical.beta(r, br),
    }

stats_df = pd.DataFrame({
    k: full_stats(v.pct_change().dropna().reindex(common).dropna(), bench_ret)
    for k, v in navs.items()
}).round(4)
print("\n", stats_df.to_string())

# ── 3. 策略完整 tear sheet ──
fig = pf.create_returns_tear_sheet(strat_ret, benchmark_rets=bench_ret, return_fig=True)
out_ts = REPORT_DIR / "pyfolio_droe_roa_wficir_tearsheet.png"
fig.savefig(out_ts, dpi=110, bbox_inches="tight")
plt.close(fig)

# ── 4. 对比图：累计净值(log) + 回撤（全部组合） ──
colors = ["#c0392b", "#2c3e50", "#27ae60", "#8e44ad", "#e67e22", "#16a085"]
fig2, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                gridspec_kw={"height_ratios": [2, 1]})
for i, (key, color) in enumerate(zip(navs.keys(), colors)):
    nav = navs[key].reindex(common).ffill()
    lw = 1.8 if key == strat_key else 1.1
    ax1.plot(nav.index, nav.values, label=key, lw=lw, color=color,
             alpha=1.0 if key == strat_key else 0.85)
    dd = nav / nav.cummax() - 1
    ax2.fill_between(dd.index, dd.values, 0, alpha=0.4 if key == strat_key else 0.18,
                     color=color, label=key if i < 2 else None)
ax1.set_yscale("log")
ax1.set_title(f"累计净值(log)对比  {common[0].date()}~{common[-1].date()}", fontsize=12)
ax1.legend(loc="upper left", fontsize=9)
ax1.grid(alpha=0.3)
ax2.set_title("回撤", fontsize=11)
ax2.grid(alpha=0.3)
fig2.tight_layout()
out_cmp = REPORT_DIR / "pyfolio_droe_roa_vs_benchmark.png"
fig2.savefig(out_cmp, dpi=110, bbox_inches="tight")
plt.close(fig2)

# ── 5. md 报告 ──
md = REPORT_DIR / f"FACTOR_COMBO_PYFOLIO_{datetime.now():%Y%m%d}.md"
fmt = lambda v: f"{v:+.2%}" if abs(v) > 1 else f"{v:+.4f}"
lines = [
    "# 因子组合 pyfolio 绩效报告（droe+roa WF-icir）",
    "",
    f"- 生成: {datetime.now().isoformat(timespec='seconds')}",
    f"- 数据源: {pkl.name}（factor_combo_walkforward.py 月度Top10%多头，0.1%单边成本，价格≤30非ST）",
    f"- 区间: {common[0].date()} ~ {common[-1].date()}（{len(common)} 交易日）",
    "",
    "## 指标对比（empyrical 引擎，全组合，Alpha/Beta 相对等权基准）",
    "",
    "| 指标 | " + " | ".join(stats_df.columns) + " |",
    "|:--|" + ":--:|" * len(stats_df.columns),
]
for row in stats_df.index:
    lines.append(f"| {row} | " + " | ".join(fmt(v) for v in stats_df.loc[row]) + " |")
lines += [
    "",
    "## 图表产出",
    "",
    f"- 完整 tear sheet: reports/{out_ts.name}（滚动Sharpe/Beta/月度热力图/回撤期/最差日等14图）",
    f"- 对比图: reports/{out_cmp.name}",
    "",
    "## 口径说明",
    "",
    "- 价格为原始 close（无复权），除权失真对策略/基准一致，相对结论稳健",
    "- benchmark=等权≤30元全universe，非沪深300",
    "- tear sheet 中 Beta/Alpha 均相对该等权基准",
]
md.write_text("\n".join(lines), encoding="utf-8")
print(f"\ntear sheet : {out_ts}")
print(f"对比图     : {out_cmp}")
print(f"md 报告    : {md}")
print("PYFOLIO REPORT DONE")
