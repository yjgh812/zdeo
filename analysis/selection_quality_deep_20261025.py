# -*- coding: utf-8 -*-
"""
selection_quality_deep_20261025.py — 选股质量层深入归因（M6/M1 规则腿, 真实逐笔, 只读）
================================================================================================
在 10-23 负α实锤（M6/M1 跑赢随机仅 28-31%）基础上深挖三层：
  A. 退出族 × α 分解 —— 负α在止损单（买进去就落后）还是连止盈单都输随机（选票整体弱）？
  B. 亏损单入场特征画像 —— 亏损单 vs 盈利单在买入日的 pos250/chg5/chg20/开盘跳空差异
  C. 月度 α 时间序列 —— 负α是全程还是集中在特定月（regime 依赖）
方法：逐笔 α_rand = pnl_pct − 同窗全市场等权 open→open（与 10-23 报告同口径可比）。
全部只读（trades CSV + qfq 库只读），零交易零写入。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
FILES = {"M6": "trades_M6_wfa_top20.csv", "M1": "trades_M1_lowconsol_main.csv"}
DB_QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\selection_quality_deep_20261025_result.json"
PANEL_FROM = 20231001

# ---- 随机基线面板（与 10-23 同口径） ----
con = sqlite3.connect(f"file:{DB_QFQ}?mode=ro", uri=True)
panel = pd.read_sql_query(
    """SELECT code, date, open, close FROM stock_daily_meta_qfq
       WHERE date>=? AND (code LIKE '6%' OR code LIKE '0%' OR code LIKE '3%')
         AND code NOT LIKE '688%' AND code NOT LIKE '689%'""", con, params=(str(PANEL_FROM),))
panel["date"] = panel["date"].astype(np.int64)
open_wide = panel.pivot_table(index="date", columns="code", values="open")


def rand_ret(d1: int, d2: int) -> float | None:
    if d1 not in open_wide.index or d2 not in open_wide.index:
        return None
    a, b = open_wide.loc[d1], open_wide.loc[d2]
    m = (a > 0) & (b > 0) & a.notna() & b.notna()
    return float((b[m] / a[m] - 1).mean()) if m.sum() >= 100 else None


RESULT = {"generated": datetime.now().isoformat(timespec="seconds"), "legs": {}}

for leg, fn in FILES.items():
    df = pd.read_csv(f"{BASE}\\{fn}")
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["buy_date"] >= 20240101].copy()  # 归因主战场 2024-2026（面板起点 2023-10）
    df["fam"] = df["reason"].astype(str).str[:2]
    df["month"] = df["buy_date"] // 100

    # ---- 逐笔 α（缓存窗口） ----
    cache = {}
    alphas = []
    for _, r in df.iterrows():
        key = (int(r["buy_date"]), int(r["sell_date"]))
        if key not in cache:
            cache[key] = rand_ret(*key)
        rr = cache[key]
        alphas.append(None if rr is None else r["pnl_pct"] * 100 - rr * 100)
    df["alpha_rand"] = alphas
    t = df.dropna(subset=["alpha_rand"]).copy()

    print("=" * 84)
    print(f"◆ {leg}: 2024以来 {len(df)} 笔, 可配对 {len(t)} 笔")

    # ---- A. 退出族 × α ----
    print("[A] 退出族 × α(vs随机) 分解")
    famA = {}
    for fam in ("止损", "止盈", "熔断", "期末"):
        s = t[t["fam"] == fam]
        if len(s) < 5:
            continue
        row = dict(n=int(len(s)), pnl_med=round(float(s["pnl_pct"].median() * 100), 2),
                   alpha_med=round(float(s["alpha_rand"].median()), 2),
                   beat_rand=round(float((s["alpha_rand"] > 0).mean()), 3))
        famA[fam] = row
        print(f"    {fam}: n={row['n']:4d} 中位pnl={row['pnl_med']:+7.2f}% "
              f"中位α={row['alpha_med']:+6.2f}% 跑赢随机={row['beat_rand']*100:5.1f}%")

    # ---- B. 入场特征画像（winners vs losers） ----
    print("[B] 入场特征画像（中位: winners vs losers）")
    codes = sorted(t["code"].unique())
    feat_cache = {}
    for c in codes:
        rows = con.execute(
            "SELECT date, open, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (c,)).fetchall()
        if rows:
            d = np.array([int(r[0]) for r in rows])
            o = np.array([float(r[1]) for r in rows])
            cl = np.array([float(r[2]) for r in rows])
            feat_cache[c] = (d, o, cl)

    def entry_feats(code, bdate):
        s = feat_cache.get(code)
        if s is None:
            return None
        d, o, cl = s
        i = np.searchsorted(d, bdate)
        if i <= 20 or i >= len(d) or d[i] != bdate:
            return None
        prev_close = cl[i - 1]
        if not (prev_close > 0) or not (o[i] > 0):
            return None
        w250 = cl[max(0, i - 250):i + 1]
        pos250 = float((w250.max() - cl[i]) / (w250.max() - w250.min()) * 100) \
            if w250.max() > w250.min() else np.nan
        return {"pos250": pos250,
                "chg5": float(cl[i] / cl[i - 6] - 1) * 100 if i >= 6 and cl[i - 6] > 0 else np.nan,
                "chg20": float(cl[i] / cl[i - 21] - 1) * 100 if i >= 21 and cl[i - 21] > 0 else np.nan,
                "gap": float(o[i] / prev_close - 1) * 100}

    feats = [entry_feats(r["code"], int(r["buy_date"])) for _, r in t.iterrows()]
    t2 = t.copy()
    for k in ("pos250", "chg5", "chg20", "gap"):
        t2[k] = [f[k] if f else np.nan for f in feats]
    t2 = t2.dropna(subset=["pos250"])
    win, lose = t2[t2["pnl_pct"] > 0], t2[t2["pnl_pct"] <= 0]
    # α 负最深 25% 的单子（毒票画像）
    q75 = t2["alpha_rand"].quantile(0.25)
    toxic = t2[t2["alpha_rand"] <= q75]
    print(f"        {'特征':10s} {'盈利单':>8s} {'亏损单':>8s} {'毒票(α最差25%)':>12s}")
    profB = {}
    for k in ("pos250", "chg5", "chg20", "gap"):
        profB[k] = {"win": round(float(win[k].median()), 1), "lose": round(float(lose[k].median()), 1),
                    "toxic": round(float(toxic[k].median()), 1)}
        print(f"        {k:10s} {win[k].median():8.1f} {lose[k].median():8.1f} {toxic[k].median():12.1f}")
    print(f"    毒票阈值 α≤{q75:.1f}%（n={len(toxic)}）, 毒票亏损单占比={float((toxic['pnl_pct']<=0).mean())*100:.0f}%")

    # ---- C. 月度 α ----
    print("[C] 月度 α 中位（负α集中度）")
    mth = t.groupby("month").agg(n=("alpha_rand", "size"), alpha=("alpha_rand", "median"),
                                 pnl=("pnl_pct", "median")).round(2)
    mth = mth[mth["n"] >= 5].reset_index()
    worst5 = mth.nsmallest(5, "alpha")
    tot_neg_alpha_months = int((mth["alpha"] < 0).sum())
    print(f"    月数={len(mth)}, 负α月={tot_neg_alpha_months} ({tot_neg_alpha_months/len(mth)*100:.0f}%)")
    print("    最差5月:")
    for _, r in worst5.iterrows():
        print(f"      {int(r['month'])}: n={int(r['n'])} α中位={r['alpha']:+.2f}% pnl中位={r['pnl']*100:+.2f}%")

    RESULT["legs"][leg] = {
        "n_paired": int(len(t)),
        "A_exit_family_alpha": famA,
        "B_entry_profile": profB,
        "toxic_threshold_alpha": round(float(q75), 2),
        "toxic_loser_share": round(float((toxic["pnl_pct"] <= 0).mean()), 3),
        "C_month_alpha": {int(r["month"]): {"n": int(r["n"]), "alpha": float(r["alpha"])}
                          for _, r in mth.iterrows()},
        "C_neg_months": f"{tot_neg_alpha_months}/{len(mth)}",
    }

con.close()
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(RESULT, f, ensure_ascii=False, indent=2)
print("=" * 84)
print(f"证据已落盘: {OUT}")
