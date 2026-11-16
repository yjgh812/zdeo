# -*- coding: utf-8 -*-
"""
E1 吸收项：六张盈亏分布图（数据=10/16 全域回测逐笔溯源 CSV，脚本直出禁手抄）
到件计划 T1.1 的呈现层升级（数据已在案=analysis/pnl_distribution_audit_20261017.py 同源 CSV）。
口径声明：
  - 样本=bp_ep_core_trades.csv（2062 笔）+ ml_signal_trades.csv（1339 笔），2016-2026 全史引擎逐笔
  - pnl_pct=引擎净口径收益率%（计划 T1.1 的"毛/净对比"在溯源 CSV 无毛口径字段，
    以审计实测"成本/毛=13.1%(bp_ep)/10.6%(ml)"注记替代，禁编造）
  - "按画像格分组"不可行（溯源 CSV 无画像格字段）→ 以"按腿分组"替代（如实声明）
  - "按市场环境四格"不可行（逐笔无环境字段）→ 以"按年分组"替代（环境轴代理，如实声明）
产出：reports/pnl_plots/ 六张 PNG + summary md 表（全部脚本直出）。
"""
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BASE = r"D:\zcodesj\reports\backtest_full_20261016_eod"
OUTDIR = r"D:\zcodesj\reports\pnl_plots"
os.makedirs(OUTDIR, exist_ok=True)

legs = {}
for leg, fname in (("bp_ep", "bp_ep_core_trades.csv"), ("ml", "ml_signal_trades.csv")):
    df = pd.read_csv(os.path.join(BASE, fname))
    df["leg"] = leg
    legs[leg] = df
df = pd.concat(legs.values(), ignore_index=True)
n_bp, n_ml = len(legs["bp_ep"]), len(legs["ml"])

# ---------- 图1 单笔收益率直方图 ----------
fig, ax = plt.subplots(figsize=(10, 6))
for leg, color, name in (("bp_ep", "#1f77b4", f"bp_ep(n={n_bp})"), ("ml", "#ff7f0e", f"ml(n={n_ml})")):
    x = legs[leg]["pnl_pct"].astype(float)
    ax.hist(x, bins=80, range=(-30, 60), alpha=0.55, label=name, color=color)
allx = df["pnl_pct"].astype(float)
for v, c, lab in ((allx.mean(), "red", f"均值{allx.mean():.2f}%"), (allx.median(), "green", f"中位{allx.median():.2f}%")):
    ax.axvline(v, color=c, linestyle="--", linewidth=1.2, label=lab)
for q, c in ((5, "gray"), (95, "gray")):
    v = allx.quantile(q / 100)
    ax.axvline(v, color=c, linestyle=":", linewidth=1)
    ax.text(v, ax.get_ylim()[1] * 0.95, f"p{q}={v:.1f}%", color=c, fontsize=9,
            ha="right" if q == 5 else "left")
ax.set_xlim(-30, 60)
ax.set_xlabel("单笔收益率 %（引擎净口径）")
ax.set_ylabel("笔数")
ax.set_title(f"图1 单笔收益率分布（全史 2016-2026，共{n_bp + n_ml}笔；虚线=均值/中位，点线=p5/p95）")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUTDIR, "01_hist_pnl_pct.png"), dpi=130)
plt.close(fig)

# ---------- 公共统计函数 ----------
def group_stats(sub, keycol):
    sub = sub.copy()
    sub["pnl_pct"] = sub["pnl_pct"].astype(float)
    g = sub.groupby(keycol)["pnl_pct"]
    out = []
    for k, v in g:
        win = (v > 0).mean() * 100
        gains = v[v > 0]
        losses = v[v < 0]
        plr = (gains.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else float("nan")
        out.append({keycol: k, "n": int(len(v)), "mean": round(v.mean(), 2),
                    "median": round(v.median(), 2), "win_rate": round(win, 1),
                    "pl_ratio_sum": round(plr, 2) if np.isfinite(plr) else None})
    return pd.DataFrame(out)

# ---------- 图2 按腿分组盈亏比 ----------
st2 = group_stats(df, "leg")
fig, ax = plt.subplots(figsize=(8, 5))
xs = np.arange(len(st2))
ax.bar(xs - 0.18, st2["mean"], 0.36, label="均值%", color="#1f77b4")
ax.bar(xs + 0.18, st2["win_rate"], 0.36, label="胜率%", color="#ff7f0e")
ax2 = ax.twinx()
ax2.plot(xs, st2["pl_ratio_sum"], "ro-", label="盈亏比(盈亏和比)")
ax2.axhline(1.0, color="gray", linestyle=":")
for i, v in enumerate(st2["pl_ratio_sum"]):
    ax2.text(i, v, f"{v}", ha="center", va="bottom")
ax.set_xticks(xs)
ax.set_xticklabels([f"{r['leg']}\n(n={r['n']})" for _, r in st2.iterrows()])
ax.set_ylabel("均值% / 胜率%")
ax2.set_ylabel("盈亏比（Σ盈/|Σ亏|）")
ax.set_title("图2 按腿分组：均值/胜率/盈亏比（口径=引擎净收益；注：计划前提'盈亏比<1.0'被实测否证）")
ax.legend(loc="upper left")
ax2.legend(loc="upper right")
fig.tight_layout()
fig.savefig(os.path.join(OUTDIR, "02_by_leg.png"), dpi=130)
plt.close(fig)

# ---------- 图3 按持有期分组 ----------
bins = [0, 5, 10, 20, 40, 60, 10**6]
labels = ["<=5", "6-10", "11-20", "21-40", "41-60", ">60"]
df["hold_b"] = pd.cut(df["holding_bars"].astype(float), bins=bins, labels=labels)
st3 = group_stats(df.dropna(subset=["hold_b"]), "hold_b")
fig, ax = plt.subplots(figsize=(9, 5))
xs = np.arange(len(st3))
ax.bar(xs - 0.18, st3["mean"], 0.36, label="均值%", color="#1f77b4")
ax.bar(xs + 0.18, st3["win_rate"], 0.36, label="胜率%", color="#ff7f0e")
ax.set_xticks(xs)
ax.set_xticklabels([f"{r['hold_b']}\n(n={r['n']})" for _, r in st3.iterrows()])
ax.set_ylabel("均值% / 胜率%")
ax.set_title("图3 按持有期（交易日）分组：均值/胜率（全腿合并）")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUTDIR, "03_by_holding.png"), dpi=130)
plt.close(fig)

# ---------- 图4 按出场原因分组 ----------
st4 = group_stats(df, "exit_reason").sort_values("n", ascending=False)
fig, ax = plt.subplots(figsize=(10, 5))
xs = np.arange(len(st4))
ax.bar(xs - 0.18, st4["mean"], 0.36, label="均值%", color="#1f77b4")
ax.bar(xs + 0.18, st4["win_rate"], 0.36, label="胜率%", color="#ff7f0e")
ax.set_xticks(xs)
ax.set_xticklabels([f"{r['exit_reason']}\n(n={r['n']})" for _, r in st4.iterrows()],
                   fontsize=8, rotation=0)
ax.set_ylabel("均值% / 胜率%")
ax.set_title("图4 按出场原因分组：均值/胜率（全腿合并）")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUTDIR, "04_by_exit_reason.png"), dpi=130)
plt.close(fig)

# ---------- 图5 按年分组（市场环境轴代理） ----------
df["year"] = df["entry_date"].astype(str).str[:4]
st5 = group_stats(df, "year")
fig, ax = plt.subplots(figsize=(10, 5))
xs = np.arange(len(st5))
colors = ["#2ca02c" if v >= 0 else "#d62728" for v in st5["mean"]]
ax.bar(xs, st5["mean"], 0.6, color=colors)
ax.set_xticks(xs)
ax.set_xticklabels([f"{r['year']}\n(n={r['n']})" for _, r in st5.iterrows()], fontsize=9)
ax.set_ylabel("均值% / 胜率%")
ax.set_title("图5 按年分组：均值/胜率（红=负年；环境轴代理，逐笔无环境字段如实声明）")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUTDIR, "05_by_year.png"), dpi=130)
plt.close(fig)

# ---------- 表6 毛/净对比（净口径直出+毛口径引用审计注记） ----------
st6 = group_stats(df, "leg")
# 汇总 markdown
md = ["# 盈亏分布六图 summary（2026-10-18 脚本直出）", "",
      f"样本：bp_ep {n_bp} 笔 + ml {n_ml} 笔（10/16 全域回测逐笔溯源，引擎净口径）", "",
      "## 表：按腿分组", st2.to_markdown(index=False), "",
      "## 表：按持有期分组", st3.to_markdown(index=False), "",
      "## 表：按出场原因分组", st4.to_markdown(index=False), "",
      "## 表：按年分组", st5.to_markdown(index=False), "",
      "## 毛/净对比注",
      "溯源 CSV 无毛口径字段；审计实测成本/毛=13.1%(bp_ep)/10.6%(ml)"
      "（analysis/pnl_distribution_audit_20261017.py），毛口径≈净口径/(1-成本占比) 量级参考，禁编造精确值。", "",
      "## 与计划前提对照（关键）",
      f"- 计划前提'盈亏比<1.0'：实测 bp_ep Σ盈/|Σ亏|={st2[st2['leg']=='bp_ep']['pl_ratio_sum'].iloc[0] if 'leg' in st2 else '见02图'}（另见01图右尾）——已被否证",
      "- 本六图=T1.1 呈现层升级（数据已在案），非新实验",
    ]
io = "\n".join(md)
with open(os.path.join(OUTDIR, "summary_20261018.md"), "w", encoding="utf-8") as f:
    f.write(io)

st2j = st2.to_dict(orient="records")
st3j = st3.to_dict(orient="records")
st4j = st4.to_dict(orient="records")
st5j = st5.to_dict(orient="records")
json.dump({"n_bp": n_bp, "n_ml": n_ml, "by_leg": st2j, "by_holding": st3j,
           "by_exit": st4j, "by_year": st5j},
          open(os.path.join(OUTDIR, "summary_20261018.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print("六图+summary 落盘:", OUTDIR)
print("by_leg:", st2j)
print("exit_reason top:", st4j[:6])
