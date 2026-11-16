# -*- coding: utf-8 -*-
"""
w13_strategy_contradiction_analysis.py — 策略核心矛盾分层验证

矛盾：
  - 全市场 IC: F_AMP40 负 IC（高波跑输）
  - 横盘突破策略: 专挑高波突破股买入
  → 策略是否在逆风？

关键问题：IC 是全市场横截面均值，但策略只在"横盘突破"子集里开仓。
子集里的条件分布可能完全不同——就像"下雨天出门带伞"在全集看正相关，
但在"已带伞的人"子集里就没意义。

分层验证设计：
  1. 定义"横盘突破"条件：40日振幅在 X~Y 区间 + 收盘价突破20日高点
  2. 在此子集内，按 F_AMP40 五分位分层
  3. 看每层的 T+1/T+5 前向收益
  4. 如果子集内高波仍跑输 → 策略确实逆风
     如果子集内高波反而跑赢 → IC 负值是其他低波股贡献的，策略没错

样本：2022-01-01 ~ 2026-08-10，主板+创业板
"""
import sqlite3
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
START_DATE = 20220101
END_DATE = 20260810

print("=" * 76)
print(" W13: 策略核心矛盾分层验证")
print(" 高波跑输(IC负) vs 横盘突破策略(买高波) → 子集内是否仍逆风？")
print("=" * 76)

# ===== 1. 加载数据 =====
print("\n[1] 加载数据...")
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
sql = """
SELECT code, date, open, high, low, close, volume
FROM stock_daily
WHERE date >= 20211201 AND date <= ?
  AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%')
  AND code NOT LIKE '688%' AND code NOT LIKE '8%' AND code NOT LIKE '43%'
"""
df = pd.read_sql(sql, conn, params=(END_DATE,))
conn.close()
df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
df = df.sort_values(['code', 'date']).reset_index(drop=True)
print(f"  {len(df):,} 行，{df['code'].nunique()} 只")


# ===== 2. 计算因子 + 横盘突破条件 =====
print("\n[2] 计算因子和突破条件...")

def calc(group):
    g = group.sort_values('date').copy()
    g['prev_close'] = g['close'].shift(1)

    # F_AMP40: 40 日振幅
    g['F_AMP40'] = ((g['high'].rolling(40, min_periods=20).max() -
                     g['low'].rolling(40, min_periods=20).min()) /
                    g['close'].rolling(40, min_periods=20).mean())

    # F_CV20: 20 日变异系数
    g['F_CV20'] = (g['close'].rolling(20, min_periods=10).std() /
                   g['close'].rolling(20, min_periods=10).mean())

    # 突破信号：收盘价突破近 20 日最高价（不含当日）
    g['high_20_prev'] = g['high'].rolling(20, min_periods=10).max().shift(1)
    g['is_breakout'] = g['close'] > g['high_20_prev']

    # 横盘条件：40 日振幅在合理区间（0.15~0.80，排除暴涨股和死水股）
    g['is_consolidation'] = (g['F_AMP40'] >= 0.15) & (g['F_AMP40'] <= 0.80)

    # 前向收益
    g['RET_1D'] = g['close'].shift(-1) / g['close'] - 1
    g['RET_5D'] = g['close'].shift(-5) / g['close'] - 1

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
df_f = df_f[(df_f['date'] >= pd.Timestamp(str(START_DATE))) &
            (df_f['date'] <= pd.Timestamp(str(END_DATE)))].copy()

# 去极值
for col in ['F_AMP40', 'F_CV20']:
    q01, q99 = df_f[col].quantile([0.01, 0.99])
    df_f = df_f[(df_f[col] >= q01) & (df_f[col] <= q99)]

print(f"  过滤后: {len(df_f):,} 行")


# ===== 3. 分层分析 =====
print(f"\n{'='*76}")
print(" [3] 分层验证")
print(f"{'='*76}")

# --- 3A. 全市场基准（复现 IC 发现）---
print("\n--- 3A. 全市场：F_AMP40 五分位 T+5 收益（复验负 IC）---")
df_all = df_f.dropna(subset=['F_AMP40', 'RET_5D'])
df_all['quintile'] = pd.qcut(df_all['F_AMP40'], 5, labels=['Q1低波', 'Q2', 'Q3', 'Q4', 'Q5高波'])
ret_by_q = df_all.groupby('quintile')['RET_5D'].agg(['mean', 'median', 'count'])
print(ret_by_q.to_string())
print(f"  Q5-Q1 差异: {(ret_by_q.loc['Q5高波','mean'] - ret_by_q.loc['Q1低波','mean'])*100:+.3f}%/5日")

# --- 3B. 横盘突破子集 ---
print("\n--- 3B. 横盘突破子集：is_breakout=True 时 F_AMP40 五分位 T+5 ---")
df_bo = df_f[df_f['is_breakout'] == True].dropna(subset=['F_AMP40', 'RET_5D'])
print(f"  突破样本: {len(df_bo):,} 行")
if len(df_bo) > 100:
    df_bo = df_bo.copy()
    df_bo['quintile'] = pd.qcut(df_bo['F_AMP40'], 5, labels=['Q1低波', 'Q2', 'Q3', 'Q4', 'Q5高波'])
    ret_bo = df_bo.groupby('quintile')['RET_5D'].agg(['mean', 'median', 'count'])
    print(ret_bo.to_string())
    print(f"  Q5-Q1 差异: {(ret_bo.loc['Q5高波','mean'] - ret_bo.loc['Q1低波','mean'])*100:+.3f}%/5日")

# --- 3C. 横盘+突破子集 ---
print("\n--- 3C. 横盘+突破子集：is_consolidation & is_breakout ---")
df_cb = df_f[(df_f['is_consolidation'] == True) & (df_f['is_breakout'] == True)].dropna(subset=['F_AMP40', 'RET_5D'])
print(f"  横盘突破样本: {len(df_cb):,} 行")
if len(df_cb) > 100:
    df_cb = df_cb.copy()
    df_cb['quintile'] = pd.qcut(df_cb['F_AMP40'], 5, labels=['Q1低波', 'Q2', 'Q3', 'Q4', 'Q5高波'])
    ret_cb = df_cb.groupby('quintile')['RET_5D'].agg(['mean', 'median', 'count'])
    print(ret_cb.to_string())
    print(f"  Q5-Q1 差异: {(ret_cb.loc['Q5高波','mean'] - ret_cb.loc['Q1低波','mean'])*100:+.3f}%/5日")

# --- 3D. T+1 也看一遍 ---
print("\n--- 3D. 横盘突破子集 T+1（屠龙是 T+1 策略）---")
df_cb1 = df_f[(df_f['is_consolidation'] == True) & (df_f['is_breakout'] == True)].dropna(subset=['F_AMP40', 'RET_1D'])
if len(df_cb1) > 100:
    df_cb1 = df_cb1.copy()
    df_cb1['quintile'] = pd.qcut(df_cb1['F_AMP40'], 5, labels=['Q1低波', 'Q2', 'Q3', 'Q4', 'Q5高波'])
    ret_cb1 = df_cb1.groupby('quintile')['RET_1D'].agg(['mean', 'median', 'count'])
    print(ret_cb1.to_string())
    print(f"  Q5-Q1 差异: {(ret_cb1.loc['Q5高波','mean'] - ret_cb1.loc['Q1低波','mean'])*100:+.3f}%/1日")

# --- 3E. 反向验证：低波突破 vs 高波突破的胜率 ---
print("\n--- 3E. 横盘突破：不同振幅区间的胜率（T+1>0 占比）---")
df_win = df_f[(df_f['is_consolidation'] == True) & (df_f['is_breakout'] == True)].dropna(subset=['F_AMP40', 'RET_1D']).copy()
bins = [0, 0.15, 0.25, 0.35, 0.50, 0.80]
labels = ['<15%', '15-25%', '25-35%', '35-50%', '50-80%']
df_win['amp_bin'] = pd.cut(df_win['F_AMP40'], bins=bins, labels=labels)
for label in labels:
    sub = df_win[df_win['amp_bin'] == label]
    if len(sub) > 0:
        wr = (sub['RET_1D'] > 0).mean() * 100
        avg = sub['RET_1D'].mean() * 100
        print(f"  振幅 {label:<8}: {len(sub):>6}样本 | 胜率 {wr:.1f}% | 均值 {avg:+.3f}%/T+1")

print(f"\n{'='*76}")
print(" 核心结论")
print(f"{'='*76}")
print("""
 看上面 3B/3C/3E 的结果：
 - 如果横盘突破子集内 Q5(高波) 收益 > Q1(低波) → IC负值是其他股票贡献的，策略没错
 - 如果子集内 Q5 仍 < Q1 → 策略确实在逆风，需考虑低波突破方向
 - 看 3E 找最优振幅区间，可能存在"中波最优"的甜区
""")
