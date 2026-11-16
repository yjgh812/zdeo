# -*- coding: utf-8 -*-
"""
w14_holding_period_optimization.py — 横盘突破策略最优持仓周期验证

W13 发现：高波突破股 T+1 略好(+0.74%) 但 T+5 跑输(-0.06%)，
启示持仓周期可能需要缩短。本脚本验证 T+1/T+2/T+3/T+5 哪个最优。

方法：
  1. 横盘突破信号（close > 20日最高价）
  2. 计算各持仓周期的累计收益
  3. 分年验证稳定性
  4. 按振幅分层看是否一致

关键问题：突破后第几天卖收益最高、胜率最高、回撤最小？
"""
import sqlite3
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"

print("=" * 80)
print(" W14: 横盘突破策略最优持仓周期验证")
print("=" * 80)

# ===== 1. 加载数据 =====
print("\n[1] 加载数据...")
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql("""
SELECT code, date, open, high, low, close, volume
FROM stock_daily
WHERE date >= 20211201 AND date <= 20260810
AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%')
AND code NOT LIKE '688%' AND code NOT LIKE '8%' AND code NOT LIKE '43%'
""", conn)
conn.close()
df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
df = df.sort_values(['code', 'date']).reset_index(drop=True)
print(f"  {len(df):,} 行，{df['code'].nunique()} 只")


# ===== 2. 计算因子 + 突破信号 + 各周期收益 =====
print("\n[2] 计算因子和各持仓周期收益...")

def calc(group):
    g = group.sort_values('date').copy()

    # 振幅因子
    g['F_AMP40'] = ((g['high'].rolling(40, min_periods=20).max() -
                     g['low'].rolling(40, min_periods=20).min()) /
                    g['close'].rolling(40, min_periods=20).mean())

    # 突破信号：收盘价突破近 20 日最高价（不含当日）
    g['high_20_prev'] = g['high'].rolling(20, min_periods=10).max().shift(1)
    g['is_breakout'] = g['close'] > g['high_20_prev']

    # 横盘条件
    g['is_consol'] = (g['F_AMP40'] >= 0.15) & (g['F_AMP40'] <= 0.80)

    # 持仓期间最大回撤（用于评估风险）
    # T+1 到 T+5 的路径收益
    for n in [1, 2, 3, 5]:
        g[f'RET_{n}D'] = g['close'].shift(-n) / g['close'] - 1

    # 持仓期间最大跌幅（min low / entry close）
    for n in [1, 2, 3, 5]:
        # 取未来 n 天的最低价
        lows = [g['low'].shift(-i) for i in range(1, n+1)]
        g[f'MAX_DD_{n}D'] = pd.concat(lows, axis=1).min(axis=1) / g['close'] - 1

    # 持仓期间最大涨幅
    for n in [1, 2, 3, 5]:
        highs = [g['high'].shift(-i) for i in range(1, n+1)]
        g[f'MAX_UP_{n}D'] = pd.concat(highs, axis=1).max(axis=1) / g['close'] - 1

    g['year'] = g['date'].dt.year
    return g

parts = []
codes = df['code'].unique()
for i, code in enumerate(codes):
    g = df[df['code'] == code]
    if len(g) < 60:
        continue
    parts.append(calc(g))
    if (i+1) % 2000 == 0:
        print(f"    {i+1}/{len(codes)}")

df_f = pd.concat(parts, ignore_index=True)
df_f = df_f[(df_f['date'] >= '2022-01-01') & (df_f['date'] <= '2026-08-10')]

# 去极值
q01, q99 = df_f['F_AMP40'].quantile([0.01, 0.99])
df_f = df_f[(df_f['F_AMP40'] >= q01) & (df_f['F_AMP40'] <= q99)]

# 横盘突破子集
cb = df_f[(df_f['is_consol']) & (df_f['is_breakout'])].copy()
print(f"  横盘突破样本: {len(cb):,}")


# ===== 3. 各持仓周期对比 =====
print(f"\n{'='*80}")
print(" [3] 横盘突破各持仓周期表现对比（全样本）")
print(f"{'='*80}")

horizons = [1, 2, 3, 5]
print(f"\n{'周期':<6} {'样本数':>8} {'均值':>10} {'中位数':>10} {'胜率':>8} {'最大涨幅':>10} {'最大回撤':>10} {'收益/风险':>10}")
print("-" * 84)

for n in horizons:
    ret = cb[f'RET_{n}D'].dropna()
    dd = cb[f'MAX_DD_{n}D'].dropna()
    up = cb[f'MAX_UP_{n}D'].dropna()
    if len(ret) < 100:
        continue
    mean_ret = ret.mean() * 100
    med_ret = ret.median() * 100
    wr = (ret > 0).mean() * 100
    avg_up = up.mean() * 100
    avg_dd = dd.mean() * 100
    # 收益/风险比 = 均值 / |最大回撤|
    rr = mean_ret / abs(avg_dd) if avg_dd != 0 else 0
    print(f"T+{n:<4} {len(ret):>8} {mean_ret:>+9.3f}% {med_ret:>+9.3f}% {wr:>7.1f}% {avg_up:>+9.2f}% {avg_dd:>+9.2f}% {rr:>9.2f}")


# ===== 4. 分年验证 =====
print(f"\n{'='*80}")
print(" [4] 分年验证：各周期 T+1/T+2/T+3/T+5 均值收益")
print(f"{'='*80}")
print(f"\n{'年份':<6}", end='')
for n in horizons:
    print(f" {'T+'+str(n)+'均值':>10} {'T+'+str(n)+'胜率':>9}", end='')
print()
print("-" * 80)

for yr in [2022, 2023, 2024, 2025, 2026]:
    sub = cb[cb['year'] == yr]
    print(f"{yr:<6}", end='')
    for n in horizons:
        ret = sub[f'RET_{n}D'].dropna()
        if len(ret) > 10:
            m = ret.mean() * 100
            wr = (ret > 0).mean() * 100
            print(f" {m:>+9.3f}% {wr:>8.1f}%", end='')
        else:
            print(f" {'N/A':>10} {'N/A':>9}", end='')
    print()

# ===== 5. 按振幅分层看各周期 =====
print(f"\n{'='*80}")
print(" [5] 按振幅分层：各周期最优持仓天数")
print(f"{'='*80}")

bins = [0, 0.15, 0.25, 0.35, 0.50, 0.80]
labels = ['<15%', '15-25%', '25-35%', '35-50%', '50-80%']
cb['amp_bin'] = pd.cut(cb['F_AMP40'], bins=bins, labels=labels)

print(f"\n{'振幅':<10}", end='')
for n in horizons:
    print(f" {'T+'+str(n):>10}", end='')
print(f" {'最优周期':>8}")
print("-" * 60)

for label in labels:
    sub = cb[cb['amp_bin'] == label]
    print(f"{label:<10}", end='')
    best_n = None
    best_ret = -999
    for n in horizons:
        ret = sub[f'RET_{n}D'].dropna()
        if len(ret) > 10:
            m = ret.mean() * 100
            print(f" {m:>+9.3f}%", end='')
            if m > best_ret:
                best_ret = m
                best_n = n
        else:
            print(f" {'N/A':>10}", end='')
    print(f" {'T+'+str(best_n):>8}" if best_n else "")

# ===== 6. 核心结论 =====
print(f"\n{'='*80}")
print(" [6] 核心结论")
print(f"{'='*80}")
print("""
关键看上面表格：
- 哪个周期均值收益最高？
- 哪个周期胜率最高？
- 哪个周期收益/风险比最优？
- 分年是否稳定？（如果 T+1 每年都最优 → 可落地；如果忽T+1忽T+5 → 不稳定）
""")
