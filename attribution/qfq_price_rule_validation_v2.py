#!/usr/bin/env python3
"""S-3价格锁复核 v2（修正版）— 真实价格分桶 + 除权窗口剔除

v1缺陷：用前复权价分桶，历史高价股被折算成低价（茅台2001真实34元→qfq后~2元），
分桶失真。S-3语境=买入时真实成交价>30元，必须用不复权真实价分桶。

收益计算：fwd5/fwd20用同库不复权价，但剔除窗口内含除权跳变的样本
（|单日涨跌|>11%主板/21%创业科创/31%北交所视为除权或异常）。
"""
import sqlite3, time
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"

t0 = time.time()
con = sqlite3.connect(DB)
df = pd.read_sql("""
    SELECT code, date, close
    FROM stock_daily_hsj
    WHERE date >= 20160101
    ORDER BY code, date
""", con, dtype={'code': 'str'})
con.close()
print(f"载入 {len(df):,} 行, {df['code'].nunique():,} 只 | {time.time()-t0:.0f}s")

df = df.sort_values(['code', 'date']).reset_index(drop=True)
g = df.groupby('code')
df['prev_close'] = g['close'].shift(1)

# 除权/异常检测：单日涨跌超限
lim = df['close'] / df['prev_close'] - 1
df['ex_div'] = ((lim > 0.11) | (lim < -0.11)).fillna(False)  # 主板±10%+缓冲
df['ex_div_strict'] = ((lim > 0.21) | (lim < -0.21)).fillna(False)  # 创业板±20%

# 前向窗口是否含除权日（宽口径±11%，窄口径±21%）
df['fwd5_clean'] = g['ex_div'].shift(-5).rolling(5, min_periods=1).sum().fillna(0) < 1
df['fwd20_clean'] = g['ex_div'].shift(-20).rolling(20, min_periods=1).sum().fillna(0) < 1

# 未来收益（跳过除权窗口的样本才有效）
df['fwd5'] = g['close'].shift(-5) / df['close'] - 1
df['fwd20'] = g['close'].shift(-20) / df['close'] - 1

# 首日prev_close为NaN → 剔除（避免新股上市首日误判）
df = df[df['prev_close'].notna()].copy()
# 收益必须有效
df = df[df['fwd5'].notna() & df['fwd5_clean']].copy()

# 采样（每月1/2/3/14/15/16日，与十年回测一致）
df['day'] = df['date'].astype(str).str[6:8]
sampled = df[df['day'].isin(['01', '02', '03', '14', '15', '16'])].copy()

def bucket(p):
    if p <= 10: return '1.<=10'
    elif p <= 20: return '2.<=20'
    elif p <= 30: return '3.<=30'
    elif p <= 50: return '4.<=50'
    else: return '5.>50'

sampled['bucket'] = sampled['close'].apply(bucket)

print(f"采样 {len(sampled):,} 条（剔除除权窗口后）| {time.time()-t0:.0f}s\n")
print("=" * 72)
print("  S-3 PRICE RULE v2 — 真实价格分桶（不复权）· 除权窗口剔除")
print("=" * 72)
print(f"{'价格桶':<12}{'N':>10}{'Fwd5':>10}{'Win5':>9}{'Fwd20':>10}{'Win20':>9}")
print("-" * 72)
for b in ['1.<=10', '2.<=20', '3.<=30', '4.<=50', '5.>50']:
    sub = sampled[sampled['bucket'] == b]
    if sub.empty:
        continue
    f5 = sub['fwd5'].mean() * 100
    f20 = sub['fwd20'].mean() * 100
    w5 = (sub['fwd5'] > 0).mean() * 100
    w20 = (sub['fwd20'] > 0).mean() * 100
    print(f"{b:<12}{len(sub):>10,}{f5:>9.2f}%{w5:>8.1f}%{f20:>9.2f}%{w20:>8.1f}%")

# 中位数对照（均值受极端值影响）
print("-" * 72)
print("  [中位数对照]")
for b in ['1.<=10', '2.<=20', '3.<=30', '4.<=50', '5.>50']:
    sub = sampled[sampled['bucket'] == b]
    if sub.empty:
        continue
    m5 = sub['fwd5'].median() * 100
    m20 = sub['fwd20'].median() * 100
    print(f"{b:<12}{len(sub):>10,}  Fwd5中位 {m5:>7.2f}%  Fwd20中位 {m20:>7.2f}%")

# 近3年子集（2024-2026，贴近当前市场结构）
recent = sampled[sampled['date'] >= 20240101]
print("\n" + "=" * 72)
print("  近3年子集（2024-01 起）")
print("=" * 72)
print(f"{'价格桶':<12}{'N':>10}{'Fwd5':>10}{'Win5':>9}{'Fwd20':>10}{'Win20':>9}")
print("-" * 72)
for b in ['1.<=10', '2.<=20', '3.<=30', '4.<=50', '5.>50']:
    sub = recent[recent['bucket'] == b]
    if sub.empty:
        continue
    f5 = sub['fwd5'].mean() * 100
    f20 = sub['fwd20'].mean() * 100
    w5 = (sub['fwd5'] > 0).mean() * 100
    w20 = (sub['fwd20'] > 0).mean() * 100
    print(f"{b:<12}{len(sub):>10,}{f5:>9.2f}%{w5:>8.1f}%{f20:>9.2f}%{w20:>8.1f}%")
print("\n判定: >50桶Fwd20显著差于<=30桶→S-3 CORRECT保留; 不差→WRONG")
