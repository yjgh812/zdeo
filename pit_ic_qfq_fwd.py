#!/usr/bin/env python3
"""PIT IC with proper forward returns from QFQ (next-bar, N configurable).

20260815 勘误(根因闭环): 旧版三缺陷致 droe IC 假负(-0.027):
  1) 法定截止日(0430/0831/1031)撞周末/假期时精确匹配失败 → 静默丢60%行
  2) 年报+一季报 pit_avail 同为 0430, 同日双报未去重(重复计数)
  3) 全样本 pooled Spearman 混30年时序+截面方差, 与逐日截面IC不可比
现版: 匹配截止日后首个交易日 + 同code同生效日取最新report_date +
      pooled/逐日截面双口径输出。截面口径与 factor_combo_walkforward 对齐。
"""
import argparse, sqlite3
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=20, help="前瞻收益交易日窗口")
N = ap.parse_args().n

pit_db = sqlite3.connect("file:D:/zcodesj/factor_pit.db?mode=ro", uri=True, timeout=60)
pdf = pd.read_sql_query("""
    SELECT pit_avail_date, report_date, ep, bp, droe, roa, code
    FROM stock_factors_fundamental_pit
    WHERE droe IS NOT NULL AND ep IS NOT NULL AND n_days <= 60
""", pit_db)
pit_db.close()
pdf["code"] = pdf["code"].astype(str).str[-6:]
print(f"PIT rows: {len(pdf)} | Forward window N={N}")

qfq_db = sqlite3.connect(
    "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro",
    uri=True, timeout=60)
qdf = pd.read_sql_query(
    "SELECT code, date, close FROM stock_daily_meta_qfq WHERE close > 0", qfq_db)
qfq_db.close()
qdf["code"] = qdf["code"].astype(str).str[-6:]
qdf = qdf.sort_values(["code", "date"])
arrays = {c: (g["date"].to_numpy(np.int64), g["close"].to_numpy(float))
          for c, g in qdf.groupby("code", sort=False)}
print(f"QFQ: {len(arrays)} codes loaded")

rows, miss = [], {"code": 0, "date": 0, "fwd": 0}
for r in pdf.itertuples(index=False):
    arr = arrays.get(r.code)
    if arr is None:
        miss["code"] += 1
        continue
    dates, closes = arr
    i = np.searchsorted(dates, int(r.pit_avail_date), side="left")  # 首个交易日≥截止日
    if i >= len(dates):
        miss["date"] += 1
        continue
    if i + N >= len(dates):
        miss["fwd"] += 1
        continue
    c0, c1 = closes[i], closes[i + N]
    if not (c0 > 0 and c1 > 0):
        miss["date"] += 1
        continue
    fr = (c1 / c0 - 1) * 100
    if abs(fr) < 100:
        rows.append((r.code, int(dates[i]), int(r.report_date), r.ep, r.bp, r.droe, r.roa, fr))

df = pd.DataFrame(rows, columns=["code", "eff_date", "report_date",
                                 "ep", "bp", "droe", "roa", "fwd"])
dup = df.duplicated(["code", "eff_date"]).sum()
df = (df.sort_values(["code", "eff_date", "report_date"])
        .drop_duplicates(["code", "eff_date"], keep="last"))  # 同日双报→最新report_date
print(f"Valid records: {len(df)} (同日双报去重 {dup} 行) | miss: {miss}")

rets = df["fwd"].to_numpy()
print("\n== pooled IC (30年混合, 仅作参考, 与截面IC不可比) ==")
for name in ["ep", "bp", "droe", "roa"]:
    vals = df[name].to_numpy()
    mask = (np.abs(vals) < np.percentile(np.abs(vals), 99)) & (np.abs(rets) < 20)
    rho, p = spearmanr(vals[mask], rets[mask])
    print(f"  {name}: IC={rho:.4f}  p={p:.4f}  n={mask.sum()}")

print("\n== 逐日截面IC均值 (与 factor_combo_walkforward 同口径) ==")
for name in ["ep", "bp", "droe", "roa"]:
    ics = []
    for _, sub in df.groupby("eff_date"):
        if len(sub) >= 30:
            rho, _ = spearmanr(sub[name], sub["fwd"])
            if not np.isnan(rho):
                ics.append(rho)
    if len(ics) >= 5:
        a = np.array(ics)
        t = a.mean() / a.std() * np.sqrt(len(a))
        print(f"  {name}: IC={a.mean():+.4f}  t={t:+.1f}  截面数={len(a)}  正率={(a > 0).mean():.0%}")

print("""
=== Interpretation (20260815 根因闭环) ===
旧版 droe 假负根因 = 周末匹配丢行 + 同日双报重复 + pooled跨30年三重失真。
逐日承载口径的真值见 factor_combo_walkforward.py --source pit
(2020-2026: droe IC≈+0.012 转正, roa≈-0.017 真负, bp_ep(PIT生产口径)≈+0.023)。
""")
