# -*- coding: utf-8 -*-
"""
w12_pattern_factor_ic.py — P0 过滤后 13 笔发现的 3 个量价特征的全量 IC 验证

目的：用全量数据验证 13 笔小样本发现是否过拟合
方法：计算因子值 → 前向 N 日收益 → Spearman IC → 汇总

三个候选因子（13 笔发现的差异）：
  F_AMP40:  近 40 日振幅 (high.max-low.min)/close.mean
  F_CV20:   近 20 日 close 变异系数 std/mean
  F_PREM:   当日溢价 (close/prev_close - 1)

样本外时间窗：2022-01-01 ~ 2026-08-10（约 1100 交易日）
标的池：沪深主板+创业板（剔除科创/北交），剔除 ST/停牌/次新（简化用代码过滤）
"""
import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import warnings
warnings.filterwarnings('ignore')

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
START_DATE = 20220101
END_DATE = 20260810

print("=" * 72)
print(" W12: 量价特征全量 IC 验证（3 候选因子）")
print(f" 时间窗: {START_DATE} ~ {END_DATE}")
print("=" * 72)

# ===== 1. 加载数据 =====
print("\n[1] 加载数据...")
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

# 只取主板(60/00)+创业板(30)，排除科创(688)/北交(8/4)
# 限制到 2021-01-01 起（留 40 天 buffer 算因子）
sql = """
SELECT code, date, open, high, low, close, volume
FROM stock_daily
WHERE date >= 20211201 AND date <= ?
  AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%')
  AND code NOT LIKE '688%'
  AND code NOT LIKE '8%'
  AND code NOT LIKE '43%'
"""
df = pd.read_sql(sql, conn, params=(END_DATE,))
conn.close()
print(f"  加载 {len(df):,} 行，{df['code'].nunique()} 只股票")

# 转日期
df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
df = df.sort_values(['code', 'date']).reset_index(drop=True)

# ===== 2. 计算因子 =====
print("\n[2] 计算因子...")

def calc_factors(group):
    g = group.sort_values('date').copy()
    g['prev_close'] = g['close'].shift(1)

    # F_AMP40: 40 日振幅
    g['high_40'] = g['high'].rolling(40, min_periods=20).max()
    g['low_40'] = g['low'].rolling(40, min_periods=20).min()
    g['close_mean_40'] = g['close'].rolling(40, min_periods=20).mean()
    g['F_AMP40'] = (g['high_40'] - g['low_40']) / g['close_mean_40']

    # F_CV20: 20 日变异系数
    g['close_std_20'] = g['close'].rolling(20, min_periods=10).std()
    g['close_mean_20'] = g['close'].rolling(20, min_periods=10).mean()
    g['F_CV20'] = g['close_std_20'] / g['close_mean_20']

    # F_PREM: 当日溢价
    g['F_PREM'] = g['close'] / g['prev_close'] - 1

    # 前向收益（T+1 到 T+5）
    g['RET_1D'] = g['close'].shift(-1) / g['close'] - 1
    g['RET_5D'] = g['close'].shift(-5) / g['close'] - 1

    return g

# 分组计算（可能较慢，限制股票数提速）
# 先测试前 500 只
codes = df['code'].unique()
print(f"  总股票数: {len(codes)}，全量计算（可能需几分钟）...")

result_parts = []
for i, code in enumerate(codes):
    g = df[df['code'] == code]
    if len(g) < 60:
        continue
    result_parts.append(calc_factors(g))
    if (i + 1) % 2000 == 0:
        print(f"    已处理 {i+1}/{len(codes)}")

df_f = pd.concat(result_parts, ignore_index=True)
print(f"  因子计算完成: {len(df_f):,} 行")

# 过滤到回测窗口
df_f = df_f[(df_f['date'] >= pd.Timestamp(str(START_DATE))) &
            (df_f['date'] <= pd.Timestamp(str(END_DATE)))].copy()

# 剔除异常值（因子值极端的）
for col in ['F_AMP40', 'F_CV20', 'F_PREM']:
    q01 = df_f[col].quantile(0.01)
    q99 = df_f[col].quantile(0.99)
    df_f = df_f[(df_f[col] >= q01) & (df_f[col] <= q99)]

print(f"  过滤后: {len(df_f):,} 行")

# ===== 3. 计算 IC =====
print("\n[3] 计算前向 IC（Spearman rank correlation）...")

# 逐日计算截面 IC，再平均
df_f['date_str'] = df_f['date'].dt.strftime('%Y-%m-%d')

factors = ['F_AMP40', 'F_CV20', 'F_PREM']
horizons = ['RET_1D', 'RET_5D']

ic_results = {}
for factor in factors:
    for horizon in horizons:
        # 逐日截面 IC
        daily_ics = []
        for date, group in df_f.groupby('date'):
            valid = group[[factor, horizon]].dropna()
            if len(valid) < 30:  # 截面最少 30 只
                continue
            ic, pval = spearmanr(valid[factor], valid[horizon])
            if not np.isnan(ic):
                daily_ics.append(ic)

        if daily_ics:
            ic_arr = np.array(daily_ics)
            ic_mean = ic_arr.mean()
            ic_std = ic_arr.std()
            icir = ic_mean / ic_std if ic_std > 0 else 0
            ir_ratio = abs(ic_mean) / (ic_std / np.sqrt(len(ic_arr))) if ic_std > 0 else 0  # t-stat
            positive_pct = (ic_arr > 0).mean() * 100

            key = f"{factor}_{horizon}"
            ic_results[key] = {
                'ic_mean': ic_mean,
                'ic_std': ic_std,
                'icir': icir,
                't_stat': ir_ratio,
                'positive_pct': positive_pct,
                'n_days': len(daily_ics),
            }

# ===== 4. 输出结果 =====
print(f"\n{'='*72}")
print(" IC 验证结果")
print(f"{'='*72}")
print(f"{'因子':<12} {'期限':<8} {'IC均值':>8} {'IC标准差':>8} {'ICIR':>8} {'t-stat':>8} {'正IC占比':>8} {'天数':>6}")
print("-" * 76)

for key, vals in sorted(ic_results.items()):
    parts = key.rsplit('_', 1)
    factor = parts[0]
    horizon_label = 'T+1' if parts[1] == '1D' else 'T+5'
    sig = '***' if vals['t_stat'] > 3 else ('**' if vals['t_stat'] > 2 else ('*' if vals['t_stat'] > 1.96 else ''))
    print(f"{factor:<12} {horizon_label:<8} {vals['ic_mean']:>+8.4f} {vals['ic_std']:>8.4f} "
          f"{vals['icir']:>+8.4f} {vals['t_stat']:>+8.2f}{sig} {vals['positive_pct']:>7.1f}% {vals['n_days']:>6}")

print(f"\n{'='*72}")
print(" 结论判定")
print(f"{'='*72}")
print(" 判定标准: |IC| > 0.03 且 t-stat > 2 为显著因子（A股IC>0.05不可达，0.03是实战下限）")
print()

for key, vals in sorted(ic_results.items()):
    factor, h = key.rsplit('_', 1)
    is_sig = abs(vals['ic_mean']) > 0.03 and vals['t_stat'] > 2
    verdict = "✅ 显著（可考虑采纳）" if is_sig else "❌ 不显著（13笔小样本过拟合）"
    print(f"  {factor} ({'T+1' if h=='1D' else 'T+5'}): IC={vals['ic_mean']:+.4f} t={vals['t_stat']:+.2f} → {verdict}")

# 保存结果
import json
out_path = r"D:\zcodesj\w12_results"
import os
os.makedirs(out_path, exist_ok=True)
with open(os.path.join(out_path, "ic_results.json"), 'w') as f:
    json.dump(ic_results, f, indent=2, default=str)
print(f"\n结果已保存到 {out_path}")
