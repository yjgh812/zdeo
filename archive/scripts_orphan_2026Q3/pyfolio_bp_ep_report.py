# -*- coding: utf-8 -*-
"""
pyfolio_bp_ep_report.py — 生产策略 bp+ep 月度调仓 pyfolio 绩效报告
输入: backtester.py 落盘的 daily_nav.csv (date/nav/daily_ret) + 等权≤30元基准(pkl)
输出: 完整 tear sheet + 指标对比 + md 报告
用法: python pyfolio_bp_ep_report.py <daily_nav.csv> <benchmark_nav.pkl>
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pyfolio as pf
import empyrical

REPORT_DIR = Path(r"D:\zcodesj\reports")

nav_csv = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    REPORT_DIR.parent / "multifactor" / "reports" / "weekly_backtest" / "bp_ep_monthly_latest" / "daily_nav.csv"
bench_pkl = Path(sys.argv[2]) if len(sys.argv) > 2 else \
    REPORT_DIR / "factor_combo_navs_20160104_20260731.pkl"

# ── 1. 加载策略净值 ──
nav = pd.read_csv(nav_csv)
nav["date"] = pd.to_datetime(nav["date"].astype(int).astype(str), format="%Y%m%d")
nav = nav.set_index("date").sort_index()
ret = nav["nav"].pct_change().dropna()

# ── 2. 基准（等权≤30元） ──
bench_all = pd.read_pickle(bench_pkl)
bench_key = next(k for k in bench_all if "基准" in k)
bench_ret = bench_all[bench_key].pct_change().dropna()
common = ret.index.intersection(bench_ret.index)
ret, bench_ret = ret.loc[common], bench_ret.loc[common]
print(f"策略: {nav_csv.name} | 基准: {bench_key}")
print(f"区间: {common[0].date()} ~ {common[-1].date()}  {len(common)} 交易日")

# ── 3. 指标 ──
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
    "bp+ep月度(生产)": full_stats(ret, bench_ret),
    "等权≤30元基准": full_stats(bench_ret, bench_ret),
}).round(4)
print("\n", stats_df.to_string())

# ── 4. tear sheet ──
fig = pf.create_returns_tear_sheet(ret, benchmark_rets=bench_ret, return_fig=True)
out_ts = REPORT_DIR / "pyfolio_bp_ep_production_tearsheet.png"
fig.savefig(out_ts, dpi=110, bbox_inches="tight")
plt.close(fig)

# ── 5. 对比图 ──
fig2, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                gridspec_kw={"height_ratios": [2, 1]})
snav = nav["nav"].reindex(common)
bnav = bench_all[bench_key].reindex(common)
ax1.plot(snav.index, snav.values, label="bp+ep月度(生产)", lw=1.8, color="#c0392b")
ax1.plot(bnav.index, bnav.values, label=bench_key, lw=1.1, color="#7f8c8d")
ax1.set_yscale("log")
ax1.legend(loc="upper left")
ax1.set_title(f"bp+ep 生产策略 vs 等权基准  {common[0].date()}~{common[-1].date()}", fontsize=12)
ax1.grid(alpha=0.3)
for label, s in [("bp+ep", snav), ("基准", bnav)]:
    dd = s / s.cummax() - 1
    ax2.fill_between(dd.index, dd.values, 0, alpha=0.4 if label == "bp+ep" else 0.2, label=label)
ax2.set_title("回撤", fontsize=11)
ax2.legend(loc="lower left", fontsize=9)
ax2.grid(alpha=0.3)
fig2.tight_layout()
out_cmp = REPORT_DIR / "pyfolio_bp_ep_vs_benchmark.png"
fig2.savefig(out_cmp, dpi=110, bbox_inches="tight")
plt.close(fig2)

# ── 6. md ──
fmt = lambda v: f"{v:+.2%}" if abs(v) > 1 else f"{v:+.4f}"
md = REPORT_DIR / f"BP_EP_PYFOLIO_{datetime.now():%Y%m%d}.md"
lines = [
    "# 生产策略 bp+ep 月度调仓 pyfolio 绩效报告",
    "",
    f"- 生成: {datetime.now().isoformat(timespec='seconds')}",
    f"- 策略: multifactor 引擎 bp+ep 等权rank Top20 月度调仓（2018-2026 全周期，M-FIRST）",
    f"- 基准: 等权≤30元全universe（factor_combo 引擎口径）",
    f"- 区间: {common[0].date()} ~ {common[-1].date()}（{len(common)} 交易日）",
    "",
    "## 指标对比（empyrical 引擎，252 年化）",
    "",
    "| 指标 | " + " | ".join(stats_df.columns) + " |",
    "|:--|" + ":--:|" * len(stats_df.columns),
]
for row in stats_df.index:
    lines.append(f"| {row} | " + " | ".join(fmt(v) for v in stats_df.loc[row]) + " |")
lines += [
    "",
    "## 图表",
    "",
    f"- 完整 tear sheet: reports/{out_ts.name}",
    f"- 对比图: reports/{out_cmp.name}",
]
md.write_text("\n".join(lines), encoding="utf-8")
print(f"\ntear sheet : {out_ts}")
print(f"对比图     : {out_cmp}")
print(f"md 报告    : {md}")
print("BP+EP PYFOLIO REPORT DONE")
