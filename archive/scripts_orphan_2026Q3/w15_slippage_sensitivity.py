# -*- coding: utf-8 -*-
"""
W15: T+1 滑点/手续费敏感性测试
====================================
W14 结论: T+1 均值 +0.968%, 收益/风险比 0.51 (最优)
但 W14 未考虑交易成本, 本脚本验证:
  - 扣除双边手续费(0.0257%, 万2.5+0.057% 印花税单边)
  - 扣除滑点(0.1% / 0.3% / 0.5% / 1.0% 单边)
  - 不同成本假设下 T+1 是否仍为正

A股交易成本构成(2024后):
  - 佣金: 万2.5 (双边) = 0.05%
  - 印花税: 0.05% (卖出单边, 2023.8 减半后)
  - 过户费: 0.001% (双边)
  - 合计固定成本: 约 0.101% 双边
  - 滑点: 市价单 0.1-0.3%, 涨停板挂单可能 0.5-1.0%

总成本 = 固定成本 + 滑点 * 2 (买卖双边)
"""
import sqlite3
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"

# 成本场景
FIXED_COST = 0.00101  # 双边固定成本(佣金+印花+过户) = 0.101%
SCENARIOS = {
    '理想(零成本)': 0.0,
    '仅固定成本(0.10%)': FIXED_COST,
    '低滑点(0.1%单边)': FIXED_COST + 0.002,    # 双边滑点 0.2%
    '中滑点(0.3%单边)': FIXED_COST + 0.006,    # 双边滑点 0.6%
    '高滑点(0.5%单边)': FIXED_COST + 0.010,    # 双边滑点 1.0%
    '极端(1.0%单边)':   FIXED_COST + 0.020,    # 双边滑点 2.0%
}

print("=" * 80)
print(" W15: T+1 持仓周期 - 交易成本敏感性测试")
print("=" * 80)
print(f"\n固定成本(双边): 佣金万2.5 + 印花税0.05% + 过户费 = {FIXED_COST*100:.3f}%")
print(f"测试场景: {list(SCENARIOS.keys())}\n")

# 加载数据
print("[1] 加载数据...")
conn = sqlite3.connect(DB)
df = pd.read_sql("""
    SELECT code, date, open, high, low, close, vol, amount
    FROM stock_daily
    WHERE date >= 20211201
    ORDER BY code, date
""", conn)
df['trade_date'] = df['date'].astype(str)
conn.close()
print(f"  {len(df):,} 行, {df['code'].nunique()} 只\n")

# 过滤主板+创业板
df['code6'] = df['code'].str[:6]
df = df[df['code6'].str.match(r'^(60|00|30)')]
df = df[~df['code6'].str.match(r'^(688|8|43)')]
print(f"  过滤后: {len(df):,} 行, {df['code'].nunique()} 只\n")

# 计算因子和突破信号
df = df.sort_values(['code', 'trade_date']).reset_index(drop=True)
df['high_20_prev'] = df.groupby('code')['high'].transform(
    lambda x: x.rolling(20, min_periods=10).max().shift(1)
)
df['is_breakout'] = df['close'] > df['high_20_prev']
df['ret_1d'] = df.groupby('code')['close'].pct_change()

# 横盘过滤(40日振幅 15-80%)
df['high_40'] = df.groupby('code')['high'].transform(
    lambda x: x.rolling(40, min_periods=20).max()
)
df['low_40'] = df.groupby('code')['low'].transform(
    lambda x: x.rolling(40, min_periods=20).min()
)
df['F_AMP40'] = (df['high_40'] - df['low_40']) / df['low_40']
df['is_consol'] = (df['F_AMP40'] >= 0.15) & (df['F_AMP40'] <= 0.80)

# T+1 真实收益(突破日买入 close, 次日 close 卖出)
df['RET_1D'] = df.groupby('code')['close'].transform(lambda x: x.shift(-1) / x - 1)

# 横盘突破样本
sig = df[df['is_breakout'] & df['is_consol'] & df['RET_1D'].notna()].copy()
print(f"[2] 横盘突破样本: {len(sig):,}\n")

# 按年分组
sig['year'] = sig['trade_date'].str[:4]

# ============================================================
# 全样本敏感性
# ============================================================
print("=" * 80)
print(" [3] 全样本敏感性 - T+1 在不同成本下是否仍为正")
print("=" * 80)
print(f"\n{'场景':<25}{'总成本':>10}{'均值':>10}{'胜率':>10}{'正值年份':>12}{'判定':>10}")
print("-" * 80)

results = {}
for name, total_cost in SCENARIOS.items():
    net = sig['RET_1D'] - total_cost
    mean_ret = net.mean()
    win_rate = (net > 0).mean()
    by_year = sig.groupby('year').apply(lambda x: (x['RET_1D'] - total_cost).mean())
    pos_years = (by_year > 0).sum()
    total_years = len(by_year)
    verdict = "可行" if mean_ret > 0 and pos_years >= 4 else "不可行"
    results[name] = (total_cost, mean_ret, win_rate, pos_years, total_years)
    print(f"{name:<25}{total_cost*100:>9.2f}%{mean_ret*100:>9.3f}%{win_rate*100:>9.1f}%{pos_years}/{total_years:>10}{verdict:>10}")

# ============================================================
# 分年明细(在不同成本下)
# ============================================================
print("\n" + "=" * 80)
print(" [4] 分年 T+1 收益(成本调整后)")
print("=" * 80)
year_baseline = sig.groupby('year')['RET_1D'].mean()
print(f"\n{'年份':<8}", end="")
for name in SCENARIOS:
    print(f"{name[:10]:>13}", end="")
print()
print("-" * 80)
for year in sorted(sig['year'].unique()):
    print(f"{year:<8}", end="")
    for name, total_cost in SCENARIOS.items():
        yret = sig[sig['year'] == year]['RET_1D'].mean() - total_cost
        mark = "+" if yret > 0 else " "
        print(f"{mark}{yret*100:>10.3f}%", end="")
    print()

# ============================================================
# 关键临界点: T+1 盈亏平衡所需的滑点
# ============================================================
print("\n" + "=" * 80)
print(" [5] 盈亏平衡分析")
print("=" * 80)
gross_mean = sig['RET_1D'].mean()
breakeven_total = gross_mean
breakeven_slippage = (breakeven_total - FIXED_COST) / 2  # 单边滑点
print(f"\n  毛收益均值: {gross_mean*100:.3f}%")
print(f"  双边固定成本: {FIXED_COST*100:.3f}%")
print(f"  盈亏平衡时的总成本: {breakeven_total*100:.3f}%")
print(f"  盈亏平衡时的单边滑点: {breakeven_slippage*100:.3f}%")
print(f"  → 只要单边滑点 < {breakeven_slippage*100:.2f}%, T+1 仍为正")

# ============================================================
# 振幅分层下的滑点敏感性(甜区 vs 高波)
# ============================================================
print("\n" + "=" * 80)
print(" [6] 振幅分层下的成本敏感度")
print("=" * 80)
bins = [(0.15, 0.25, '15-25%(甜区)'),
        (0.25, 0.35, '25-35%'),
        (0.35, 0.50, '35-50%'),
        (0.50, 0.80, '50-80%(高波)')]

print(f"\n{'振幅':<18}{'毛收益':>10}{'仅固定':>10}{'低滑点':>10}{'中滑点':>10}{'高滑点':>10}{'临界滑点':>10}")
print("-" * 80)
for lo, hi, label in bins:
    sub = sig[(sig['F_AMP40'] >= lo) & (sig['F_AMP40'] < hi)]
    if len(sub) == 0:
        continue
    gross = sub['RET_1D'].mean()
    be = (gross - FIXED_COST) / 2
    vals = []
    for name, tc in SCENARIOS.items():
        if name in ['极端(1.0%单边)']:
            continue
        v = gross - tc
        vals.append(v)
    print(f"{label:<18}{gross*100:>9.3f}%{vals[0]*100:>9.3f}%{vals[1]*100:>9.3f}%{vals[2]*100:>9.3f}%{vals[3]*100:>9.3f}%{be*100:>9.3f}%")

# ============================================================
# 结论
# ============================================================
print("\n" + "=" * 80)
print(" [7] 结论")
print("=" * 80)

# 找到仍可行的最高成本
viable_scenarios = [n for n, (tc, mr, wr, py, ty) in results.items()
                    if mr > 0 and py >= 4]
print(f"\n  可行场景(均值>0且5年中至少4年为正): {viable_scenarios}")
print(f"  不可行场景: {[n for n in SCENARIOS if n not in viable_scenarios]}")

# 甜区的临界滑点
sweet = sig[(sig['F_AMP40'] >= 0.15) & (sig['F_AMP40'] < 0.25)]
if len(sweet) > 0:
    sw_be = (sweet['RET_1D'].mean() - FIXED_COST) / 2
    print(f"\n  甜区(15-25%)临界单边滑点: {sw_be*100:.2f}%")

# 高波的临界滑点
hi_vol = sig[(sig['F_AMP40'] >= 0.50) & (sig['F_AMP40'] <= 0.80)]
if len(hi_vol) > 0:
    hv_be = (hi_vol['RET_1D'].mean() - FIXED_COST) / 2
    print(f"  高波(50-80%)临界单边滑点: {hv_be*100:.2f}%")

print(f"\n  落地判定:")
print(f"  - 真实场景滑点估计: 市价单 0.1-0.3%, 涨停板挂单 0.3-0.5%")
print(f"  - 若 T+1 在'中滑点(0.3%单边)'下仍为正 → R5 可落地")
print(f"  - 若只在'低滑点'下为正 → 需挂限价单+不追涨跌停")
