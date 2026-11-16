# -*- coding: utf-8 -*-
"""highwin cohort DSR/PBO统计稳健性检验(主人协议阶段五) 20261026 — 探索性, 零交易权。
DSR(Bailey-Lopez de Prado): 校正多重检验后的夏普显著性; 输入=部署格事件收益(每事件1单位).
PBO(CSCV-lite): 敏感性18格月度批收益, 分半秩相关→样本内优选的样本外失效率.
诚实试验计数: 本战役条件格总数≈270(frontier147+sensitivity18+typology54+attribution~50),
  高相关格的有效独立试验远低于此, 呈报N=50/270/1000三档敏感性.
输出: analysis/dsr_pbo_highwin_20261026_result.json
"""
import json
import sqlite3
import time
from math import e, sqrt
from statistics import NormalDist

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = "analysis/dsr_pbo_highwin_20261026_result.json"
ND = NormalDist()
GAMMA = 0.5772156649015329  # Euler-Mascheroni


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>=20190101 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
mkt = df.groupby("d").agg(m=("close", "mean")).sort_index()
mkt20 = mkt["m"] / mkt["m"].shift(20) - 1
print(f"[1/3] qfq行={len(df)} ({time.time()-t0:.0f}s)")

rows = []
monthly = {}  # (pos_cap, v_th, m_th, ym) -> list(ret) for PBO
for code, gg in df.groupby("code6", sort=False):
    gg = gg.sort_values("d")
    o = gg["open"].to_numpy(float)
    c = gg["close"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 280:
        continue
    days = np.arange(1, n + 1)
    s = pd.Series(c)
    mn = s.rolling(250, min_periods=120).min().to_numpy()
    mx = s.rolling(250, min_periods=120).max().to_numpy()
    pos = np.where(mx > mn, (c - mn) / (mx - mn) * 100, np.nan)
    chg5 = s.pct_change(5).to_numpy() * 100
    vol20 = s.pct_change().rolling(20, min_periods=20).std().shift(1).to_numpy() * np.sqrt(250)
    is_A = (pos < 30) & (np.abs(chg5) < 3) & (c <= 30) & (days > 60)
    last = -99
    for i in np.nonzero(is_A)[0]:
        if i - last <= 10:
            continue
        last = i
        e2 = i + 1
        if e2 + 19 >= n or not np.isfinite(vol20[i]) or d[i] < 20200104:
            continue
        m20 = float(mkt20.get(int(d[i]), np.nan))
        if not np.isfinite(m20):
            continue
        entry = o[e2]
        if entry <= 0:
            continue
        r = c[e2 + 19] / entry - 1
        # DSR样本: 部署格 test窗(2023+)
        if d[i] >= 20230101 and vol20[i] > 0.45 and m20 <= -0.05:
            rows.append(r)
        # PBO样本: 18格月度批
        ym = int(d[i]) // 100
        for pc in (10, 30):
            for vt in (0.40, 0.45, 0.50):
                for mt in (-0.03, -0.05, -0.08):
                    if pos[i] < pc and vol20[i] > vt and m20 <= mt:
                        monthly.setdefault((pc, vt, mt, ym), []).append(r)
print(f"[2/3] 部署格事件={len(rows)} 月度格批={len(monthly)} ({time.time()-t0:.0f}s)")

# ---- DSR ----
x = np.array(rows)
n = len(x)
mu, sd = float(x.mean()), float(x.std(ddof=1))
skew = float(((x - mu) ** 3).mean() / sd ** 3)
kurt = float(((x - mu) ** 4).mean() / sd ** 4)
sr = mu / sd  # 每事件夏普(非年化)
# 试验Sharpes方差: 用18敏感格的月度批Sharpe方差近似
cell_sharpes = []
ym_all = sorted({k[3] for k in monthly})
mat = {}
for key, lsts in monthly.items():
    pass
for pc in (10, 30):
    for vt in (0.40, 0.45, 0.50):
        for mt in (-0.03, -0.05, -0.08):
            rets = [np.mean(monthly[(pc, vt, mt, ym)]) for ym in ym_all
                    if (pc, vt, mt, ym) in monthly]
            if len(rets) >= 40:
                cell_sharpes.append(np.mean(rets) / np.std(rets, ddof=1))
v_sr = float(np.var(cell_sharpes, ddof=1)) if len(cell_sharpes) > 2 else 0.01

dsr_table = {}
for N in (50, 270, 1000):
    if N == 1:
        continue
    z_star = sqrt(v_sr) * ((1 - GAMMA) * ND.inv_cdf(1 - 1.0 / N) + GAMMA * ND.inv_cdf(1 - 1.0 / (N * e)))
    denom = sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4.0 * sr * sr))
    dsr = ND.cdf(((sr - z_star) * sqrt(n - 1)) / denom)
    dsr_table[f"N={N}"] = round(dsr, 4)

# ---- PBO (分半CSCV-lite: 16次随机半分, 样本内最优格的样本外排名分位) ----
rng = np.random.default_rng(42)
ym_arr = np.array(ym_all)
fails = []
for trial in range(16):
    perm = rng.permutation(len(ym_arr))
    half = len(ym_arr) // 2
    in_ym, out_ym = ym_arr[perm[:half]], ym_arr[perm[half:]]
    perf = {}
    for pc in (10, 30):
        for vt in (0.40, 0.45, 0.50):
            for mt in (-0.03, -0.05, -0.08):
                rin = [np.mean(monthly[(pc, vt, mt, y)]) for y in in_ym if (pc, vt, mt, y) in monthly]
                rout = [np.mean(monthly[(pc, vt, mt, y)]) for y in out_ym if (pc, vt, mt, y) in monthly]
                if len(rin) >= 15 and len(rout) >= 15:
                    perf[(pc, vt, mt)] = (np.mean(rin), np.mean(rout))
    if len(perf) < 10:
        continue
    best = max(perf, key=lambda k: perf[k][0])
    outs = sorted(v[1] for v in perf.values())
    rank_pct = outs.index(perf[best][1]) / max(1, len(outs) - 1)
    fails.append(rank_pct)
pbo = round(float(np.mean([1 - r for r in fails])), 3) if fails else None

res = {"generated": "2026-10-26", "deployed_spec": "A格&vol>0.45&m20<=-5% (test窗2023+)",
       "n_events": n, "mean": round(mu, 4), "std": round(sd, 4), "skew": round(skew, 2),
       "kurt": round(kurt, 2), "sr_per_event": round(sr, 4),
       "trial_sharpe_var": round(v_sr, 6), "DSR": dsr_table,
       "PBO_cscv_lite_16splits": pbo, "pbo_interpretation": "<0.5=样本内选择有预测力; ≈0.5=无异于随机"}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
print(f"部署格: n={n} 均值={mu:+.2%} 事件SR={sr:.3f} 偏度={skew:.2f} 峰度={kurt:.2f}")
for k2, v2 in dsr_table.items():
    print(f"  DSR({k2}) = {v2:.3f}  {'✓>0.95显著' if v2 > 0.95 else ('~边缘' if v2 > 0.8 else '✗不显著')}")
print(f"  PBO(CSCV-lite 16次分半) = {pbo}  {'✓<0.5有预测力' if pbo is not None and pbo < 0.5 else '✗≈随机'}")
