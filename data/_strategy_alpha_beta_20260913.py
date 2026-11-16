# -*- coding: utf-8 -*-
"""主策略 α/β 分解(只读验证): 组合50/50 + 六臂 vs 全市场等权日收益回归, 检验十年"+203%"的β占比.
市场代理 = qfq 全市场日等权收益(AVG(change_pct)/100, 一次GROUP BY).
输出: β / 年化α / t(α) / R², 分全窗口与分段(2016-2019/2020-2023/2024-2026).
方法论沿用画像线 §十三(β剥离): β>1=放大器, α不显著=无选股增量."""
import sys, os, sqlite3, glob
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
from scipy import stats

DIR = r'D:\zcodesj\reports\drift_check_20260912e_0911end'
QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'

# 1. 市场日收益(全市场等权)
con = sqlite3.connect(QFQ, uri=True, timeout=120)
mkt = pd.read_sql_query(
    "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
    "WHERE date>='20160101' GROUP BY date ORDER BY date", con)
con.close()
mkt['date'] = mkt['date'].astype(str)
mkt = mkt.set_index('date')['ret']
print(f"市场日收益: {len(mkt)} 交易日 {mkt.index[0]}~{mkt.index[-1]}")

def load_equity(name):
    p = os.path.join(DIR, name)
    df = pd.read_csv(p, encoding='utf-8-sig')
    df.columns = [c.strip().lower() for c in df.columns]
    dcol = next(c for c in df.columns if 'date' in c)
    num_cols = [c for c in df.columns if c != dcol and pd.api.types.is_numeric_dtype(df[c])]
    if not num_cols:
        raise ValueError(f'{name} 无数值列: {list(df.columns)}')
    # 净值列优先级: value/equity/nav > 首个数值列(防误取cumulative_return类派生列)
    vcol = next((c for c in ('value', 'equity', 'nav') if c in num_cols), num_cols[0])
    df[dcol] = df[dcol].astype(str).str.replace('-', '')
    return df.set_index(dcol)[vcol]

def reg(seg_name, sret, mret):
    """sret/mret: 对齐后的日收益 series"""
    x = mret.values; y = sret.values
    n = len(y)
    if n < 30:
        print(f"  {seg_name}: n={n} 太少"); return
    r = stats.linregress(x, y)
    se = r.intercept_stderr  # 截距标准误(scipy原生; 此前误用slope公式=所有t≈0的bug)
    t_a = r.intercept / se if (se and se > 0) else np.nan
    p_a = 2 * (1 - stats.t.cdf(abs(t_a), n - 2)) if np.isfinite(t_a) else np.nan
    print(f"  {seg_name:22s} n={n:4d}  β={r.slope:5.2f}  年化α={r.intercept*250*100:+6.1f}%  t(α)={t_a:+5.2f}  p={p_a:.3f}{' ✅' if (np.isfinite(p_a) and p_a<0.05) else ' ❌'}  R²={r.rvalue**2:.3f}")

TARGETS = ['portfolio_combo_equity.csv', 'bp_ep_core_equity.csv', 'ml_signal_equity.csv',
           'breakout_signal_equity.csv', 'composite_signal_equity.csv', 'p2_signal_equity.csv',
           'v375_signal_equity.csv']
SEGS = [('全窗口', '20160101', '20261231'), ('段1 2016-2019', '20160101', '20191231'),
        ('段2 2020-2023', '20200101', '20231231'), ('段3 2024-2026', '20240101', '20261231')]

for f in TARGETS:
    p = os.path.join(DIR, f)
    if not os.path.exists(p):
        print(f"\n== {f}: 缺文件"); continue
    eq = load_equity(f)
    sret = eq.pct_change().dropna()
    common = sret.index.intersection(mkt.index)
    sret, mret = sret.loc[common], mkt.loc[common]
    print(f"\n== {os.path.basename(f).replace('_equity.csv','')} (净值{len(eq)}日, 总收益{(eq.iloc[-1]/eq.iloc[0]-1)*100:+.1f}%) ==")
    for name, d0, d1 in SEGS:
        idx = [d for d in common if d0 <= d <= d1]
        if len(idx) < 30:
            continue
        reg(name, sret.loc[idx], mret.loc[idx])
