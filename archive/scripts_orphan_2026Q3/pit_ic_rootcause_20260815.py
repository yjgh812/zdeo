#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pit_ic_rootcause_20260815.py — droe PIT pooled IC 为负的根因分解
================================================================
背景: pit_ic_qfq_fwd.py 得 droe pooled IC=-0.0274(N=20), 与8/15仲裁
     (qfq+next-bar, droe IC≈+0.022) 方向相反。

分解四个嫌疑:
  H1 pooled≠截面IC: 30年混合时序+截面方差, 辛普森悖论 → 分年看符号
  H2 同日双报重复: 年报+一季报 pit_avail 同为0430, 未去重
  H3 精确匹配丢60%行: 法定截止日撞周末/假期即丢, +退市股不在qfq库
  H4 时代结构: droe 在早期(壳股时代)与2016+符号可能相反

方法: 复用 pit_ic_qfq_fwd 的过滤口径(n_days<=60, droe/ep非空),
     修 H3(匹配 cutoff 后首个交易日), 修 H2(同code同生效日取最新report_date),
     输出 pooled / 截面逐日 / 分年 三层对比。
"""
import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

N = 20
FACTORS = ["ep", "bp", "droe", "roa"]

# ── 1. PIT 行 ──
pit = sqlite3.connect("file:D:/zcodesj/factor_pit.db?mode=ro", uri=True, timeout=60)
pdf = pd.read_sql_query(
    "SELECT code, report_date, pit_avail_date, ep, bp, droe, roa "
    "FROM stock_factors_fundamental_pit "
    "WHERE droe IS NOT NULL AND ep IS NOT NULL AND n_days <= 60", pit)
pit.close()
pdf["code"] = pdf["code"].astype(str).str[-6:]
print(f"PIT rows: {len(pdf)}  codes: {pdf['code'].nunique()}")

# ── 2. qfq 价格 → 每code排序数组 ──
qfq = sqlite3.connect(
    "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro",
    uri=True, timeout=60)
qdf = pd.read_sql_query(
    "SELECT code, date, close FROM stock_daily_meta_qfq WHERE close > 0", qfq)
qfq.close()
qdf["code"] = qdf["code"].astype(str).str[-6:]
qdf = qdf.sort_values(["code", "date"])
print(f"QFQ rows: {len(qdf)}  codes: {qdf['code'].nunique()}")

arrays = {}
for code, g in qdf.groupby("code", sort=False):
    arrays[code] = (g["date"].to_numpy(np.int64), g["close"].to_numpy(float))
print("QFQ grouped.")

# ── 3. 逐行: 首个交易日>=pit_avail_date 匹配 + N日前瞻收益 ──
rows, miss_code, miss_date, miss_fwd = [], 0, 0, 0
for r in pdf.itertuples(index=False):
    arr = arrays.get(r.code)
    if arr is None:
        miss_code += 1
        continue
    dates, closes = arr
    i = np.searchsorted(dates, int(r.pit_avail_date), side="left")  # H3修复
    if i >= len(dates):
        miss_date += 1
        continue
    j = i + N
    if j >= len(dates):
        miss_fwd += 1
        continue
    c0, c1 = closes[i], closes[j]
    if not (c0 > 0 and c1 > 0):
        miss_date += 1
        continue
    fr = c1 / c0 - 1.0
    if abs(fr) < 1.0:
        rows.append((r.code, int(dates[i]), int(r.report_date),
                     r.ep, r.bp, r.droe, r.roa, fr))
print(f"matched: {len(rows)} | 无qfq(退市/未覆盖): {miss_code} | "
      f"截止日超末交易日: {miss_date} | 前瞻不足{N}日: {miss_fwd}")

df = pd.DataFrame(rows, columns=["code", "eff_date", "report_date",
                                 "ep", "bp", "droe", "roa", "fwd"])
dup_before = df.duplicated(["code", "eff_date"]).sum()

# H2修复: 同code同生效日(年报+一季报同0430) → 取最新report_date
df = df.sort_values(["code", "eff_date", "report_date"]).drop_duplicates(
    ["code", "eff_date"], keep="last")
df["year"] = df["eff_date"] // 10000
print(f"同日双报去重: {dup_before} 行 → 保留 {len(df)} 行\n")


def pooled(d: pd.DataFrame, tag: str) -> None:
    print(f"── pooled IC [{tag}] n={len(d)} ──")
    for f in FACTORS:
        m = (d[f].abs() < d[f].abs().quantile(0.99)) & (d["fwd"].abs() < 0.20)
        rho, p = spearmanr(d.loc[m, f], d.loc[m, "fwd"])
        print(f"  {f:<5} IC={rho:+.4f} (p={p:.2e})")


def cross_sectional(d: pd.DataFrame, tag: str) -> pd.Series:
    """逐日截面Spearman均值 — 仲裁口径"""
    ics = {f: [] for f in FACTORS}
    for _, sub in d.groupby("eff_date"):
        if len(sub) < 30:
            continue
        for f in FACTORS:
            rho, _ = spearmanr(sub[f], sub["fwd"])
            if not np.isnan(rho):
                ics[f].append(rho)
    print(f"── 逐日截面IC [{tag}] ──")
    for f in FACTORS:
        a = np.array(ics[f])
        t = a.mean() / a.std() * np.sqrt(len(a)) if len(a) > 2 else 0.0
        print(f"  {f:<5} IC={a.mean():+.4f}  t={t:+.1f}  天数={len(a)}  正率={(a > 0).mean():.0%}")
    return pd.Series({f: np.mean(ics[f]) for f in FACTORS})


print("\n========== A. 不去重(原脚本等价, 仅修H3周末匹配) ==========")
pooled(df.drop_duplicates([]) if False else df, "全部年份")
cross_sectional(df, "全部年份")

print("\n========== B. 分年截面IC均值(droe/roa) — 验H1/H4 ==========")
yearly = {}
for y, sub in df.groupby("year"):
    if len(sub) < 500:
        continue
    row = {}
    for f in FACTORS:
        vals = []
        for _, s2 in sub.groupby("eff_date"):
            if len(s2) < 30:
                continue
            rho, _ = spearmanr(s2[f], s2["fwd"])
            if not np.isnan(rho):
                vals.append(rho)
        row[f] = np.mean(vals) if vals else np.nan
        row[f + "_d"] = len(vals)
    yearly[y] = row
yt = pd.DataFrame(yearly).T
print(yt[[f for f in FACTORS]].round(4).to_string())

print("\n========== C. 分时段pooled — 验H4 ==========")
for lo, hi in [(1995, 2010), (2010, 2016), (2016, 2027)]:
    sub = df[(df["year"] >= lo) & (df["year"] < hi)]
    if len(sub) > 1000:
        pooled(sub, f"{lo}-{hi-1}")
