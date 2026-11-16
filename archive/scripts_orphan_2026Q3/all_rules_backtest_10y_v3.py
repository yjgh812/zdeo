#!/usr/bin/env python3
"""全量拦截规则十年验证 v3 - pandas向量化 (2016-2026)"""

import sqlite3, time
import pandas as pd
import numpy as np

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
t0 = time.time()

print("Loading 10-year data...")
con = sqlite3.connect(DB)
df = pd.read_sql("""
    SELECT code, date, open, high, low, close, vol
    FROM stock_daily_hsj
    WHERE date >= 20160101
    ORDER BY code, date
""", con)
con.close()
print(f"  {len(df):,} rows, {df['code'].nunique()} codes, {df['date'].nunique()} days | {time.time()-t0:.1f}s")

# ============ 预处理 ============
df = df.sort_values(["code", "date"]).reset_index(drop=True)

# 前向收益(每只股票按日期排序后 shift)
g = df.groupby("code")
df["prev_close"] = g["close"].shift(1)
df["close_m5"] = g["close"].shift(5)
df["close_1d"] = g["close"].shift(-1)
df["close_5d"] = g["close"].shift(-5)
df["close_20d"] = g["close"].shift(-20)

# 换手率: 当日成交量 vs 前20日均量
df["vol_ma20"] = g["vol"].transform(lambda x: x.rolling(20, min_periods=5).mean())

# 收盘位置
df["pos_ratio"] = np.where(df["high"] > df["low"],
                           (df["close"] - df["low"]) / (df["high"] - df["low"]),
                           np.nan)

# 当日涨跌幅
df["chg"] = np.where(df["prev_close"] > 0,
                     (df["close"] - df["prev_close"]) / df["prev_close"] * 100,
                     np.nan)

# 5日涨幅
df["chg5"] = np.where(df["close_m5"] > 0,
                      (df["close"] - df["close_m5"]) / df["close_m5"] * 100,
                      np.nan)

# 前向收益
df["fwd5"] = np.where(df["close_5d"] > 0,
                      (df["close_5d"] - df["close"]) / df["close"] * 100,
                      np.nan)
df["fwd20"] = np.where(df["close_20d"] > 0,
                       (df["close_20d"] - df["close"]) / df["close"] * 100,
                       np.nan)

print(f"  Preprocessing done | {time.time()-t0:.1f}s")

# ============ 采样: 每月2天, 覆盖2016-2026 ============
all_dates = sorted(df["date"].unique())
sample_dates = set()
for d in all_dates:
    ds = str(d)
    day = ds[6:8]
    if day in ("01", "02", "03", "14", "15", "16"):
        sample_dates.add(d)

sampled = df[df["date"].isin(sample_dates)].copy()
sampled = sampled[(sampled["close"] > 0) & sampled["fwd5"].notna()]
print(f"  Sampled: {len(sampled):,} rows on {len(sample_dates)} days | {time.time()-t0:.1f}s")

# ============ 验证 ============
def validate(name, mask, desc=""):
    n_f = int(mask.sum())
    n_t = int(len(mask))
    if n_f == 0 or n_t == n_f:
        return {"rule": name, "n_f": n_f, "n_t": n_t, "verdict": "NO_DATA", "desc": desc}
    f5 = sampled.loc[mask, "fwd5"].mean()
    u5 = sampled.loc[~mask, "fwd5"].mean()
    f20 = sampled.loc[mask, "fwd20"].mean()
    u20 = sampled.loc[~mask, "fwd20"].mean()
    f_w = sampled.loc[mask, "fwd5"].gt(0).mean() * 100
    u_w = sampled.loc[~mask, "fwd5"].gt(0).mean() * 100
    diff = u5 - f5
    verdict = "CORRECT" if diff > 0.8 else ("WRONG" if diff < -0.8 else "NEUTRAL")
    return {"rule": name, "n_f": n_f, "n_t": n_t,
            "f5": f5, "u5": u5, "f20": f20, "u20": u20,
            "f_w": f_w, "u_w": u_w, "diff": diff, "verdict": verdict, "desc": desc}

results = []

# --- S-3 价格 ---
results.append(validate("S-3价格<=30", sampled["close"] <= 30, "价格<=30"))
results.append(validate("价格<=10", sampled["close"] <= 10, "低价"))
results.append(validate("价格>30", sampled["close"] > 30, "高价"))
results.append(validate("价格>100", sampled["close"] > 100, "超高价"))
results.append(validate("价格<3", sampled["close"] < 3, "ST级低价"))

# --- R6 收盘位置 ---
valid_pos = sampled["pos_ratio"].notna()
results.append(validate("R6收高>=70%", valid_pos & (sampled["pos_ratio"] >= 0.70), "收盘位置>=70%"))
results.append(validate("R6收低<=30%", valid_pos & (sampled["pos_ratio"] <= 0.30), "收盘位置<=30%"))
results.append(validate("R6收中30-70%", valid_pos & (sampled["pos_ratio"] > 0.30) & (sampled["pos_ratio"] < 0.70), "收盘位置30-70%"))

# --- R5 当日振幅 ---
amp = (sampled["high"] - sampled["low"]) / sampled["low"]
results.append(validate("R5振幅>10%", (amp > 0.10) & (sampled["low"] > 0), "当日振幅>10%"))
results.append(validate("R5振幅>7%", (amp > 0.07) & (sampled["low"] > 0), "当日振幅>7%"))
results.append(validate("R5振幅<3%", (amp < 0.03) & (sampled["low"] > 0), "当日振幅<3%"))

# --- R1 追高(昨日涨停) ---
lu_yesterday = sampled["chg"].notna() & (sampled["chg"] >= 9.5)
results.append(validate("R1昨日涨停追入", lu_yesterday, "昨日涨停"))
results.append(validate("R1昨日大涨>7%", sampled["chg"].notna() & (sampled["chg"] >= 7.0), "昨日涨>7%"))
results.append(validate("R1昨日大跌<-5%", sampled["chg"].notna() & (sampled["chg"] <= -5.0), "昨日跌>5%"))

# --- R7 5日超买 ---
results.append(validate("R7五日涨>20%", sampled["chg5"].notna() & (sampled["chg5"] >= 20), "5日涨>20%"))
results.append(validate("R7五日涨>10%", sampled["chg5"].notna() & (sampled["chg5"] >= 10), "5日涨>10%"))
results.append(validate("R7五日跌<-10%", sampled["chg5"].notna() & (sampled["chg5"] <= -10), "5日跌>10%"))

# --- R16 放量 ---
valid_vol = sampled["vol_ma20"].notna() & (sampled["vol_ma20"] > 0)
results.append(validate("R16放量>3倍", valid_vol & (sampled["vol"] / sampled["vol_ma20"] > 3.0), "量比>3"))
results.append(validate("R16放量>2倍", valid_vol & (sampled["vol"] / sampled["vol_ma20"] > 2.0), "量比>2"))
results.append(validate("R16缩量<0.5", valid_vol & (sampled["vol"] / sampled["vol_ma20"] < 0.5), "量比<0.5"))

# --- 创业板20cm ---
gem = sampled["code"].astype(str).str.startswith(("300", "301"))
results.append(validate("创业板(300/301)", gem, "创业板"))
results.append(validate("科创板(688)", sampled["code"].astype(str).str.startswith("688"), "科创板"))

# --- 主板 ---
main_board = ~sampled["code"].astype(str).str.startswith(("300", "301", "688", "8", "4", "920"))
results.append(validate("沪深主板", main_board, "主板"))

# ============ 输出 ============
print("\n" + "=" * 100)
print("  全量拦截规则十年验证 (2016-2026, pandas向量化)")
print(f"  采样: {len(sampled):,}样本 / {len(sample_dates)}个交易日 | 耗时: {time.time()-t0:.1f}s")
print("=" * 100)
print(f"{'规则':<20}{'拦截样本':>9}{'总样本':>9}{'拦截5d':>8}{'放行5d':>8}{'拦截20d':>9}{'放行20d':>9}{'拦胜率':>7}{'放胜率':>7}  判定")
print("-" * 100)

for r in results:
    print(f"{r['rule']:<20}{r['n_f']:>9,}{r['n_t']:>9,}"
          f"{r['f5']:>7.2f}%{r['u5']:>7.2f}%"
          f"{r['f20']:>8.2f}%{r['u20']:>8.2f}%"
          f"{r['f_w']:>6.1f}%{r['u_w']:>6.1f}%  {r['verdict']}")

print("-" * 100)
print("CORRECT=被拦截样本5日收益显著更差(规则合理) | WRONG=被拦截反而更好(规则错误) | NEUTRAL=无显著差异")
print("=" * 100)