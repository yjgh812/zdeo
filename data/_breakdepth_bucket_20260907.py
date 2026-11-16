#!/usr/bin/env python3
"""破位深度分档描述性统计（只读 qfq 库）20260907——验证 trend_checker v2.2 破位幅度≥1% 阈值的数据锚
信号 = 收盘<MA20 且 MA5<MA10<MA20（v2.2 走坏前置条件），按 depth=(MA20-close)/MA20 分档，
统计前向 5/10/20 日收益与胜率，对照全市场无条件基准。描述性统计，非策略回测，不构成接线依据。
"""
import sqlite3
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20160101' AND close>0 ORDER BY code,date",
    con)
g = df.groupby('code', sort=False)
df['ma5'] = g['close'].transform(lambda s: s.rolling(5).mean())
df['ma10'] = g['close'].transform(lambda s: s.rolling(10).mean())
df['ma20'] = g['close'].transform(lambda s: s.rolling(20).mean())
df['fwd5'] = g['close'].shift(-5) / df['close'] - 1
df['fwd10'] = g['close'].shift(-10) / df['close'] - 1
df['fwd20'] = g['close'].shift(-20) / df['close'] - 1

sig = df[(df['close'] < df['ma20']) & (df['ma5'] < df['ma10']) & (df['ma10'] < df['ma20'])].copy()
sig['depth'] = (sig['ma20'] - sig['close']) / sig['ma20'] * 100
bins = [0, 0.5, 1, 2, 4, 1000]
labels = ['<0.5%(贴线)', '0.5-1%', '1-2%', '2-4%', '>=4%(深破)']
sig['bucket'] = pd.cut(sig['depth'], bins=bins, labels=labels, right=False)

base = df[['fwd5', 'fwd10', 'fwd20']].dropna()
print(f"窗口=20160101至今 全市场 | 信号总数={len(sig):,}")
print(f"全市场无条件基准: fwd5={base['fwd5'].mean()*100:+.3f}%/胜{(base['fwd5']>0).mean()*100:.1f}% "
      f"| fwd10={base['fwd10'].mean()*100:+.3f}%/胜{(base['fwd10']>0).mean()*100:.1f}% "
      f"| fwd20={base['fwd20'].mean()*100:+.3f}%/胜{(base['fwd20']>0).mean()*100:.1f}%")
res = sig.dropna(subset=['fwd5', 'fwd10', 'fwd20']).groupby('bucket', observed=True).agg(
    n=('depth', 'size'),
    fwd5=('fwd5', 'mean'), w5=('fwd5', lambda s: (s > 0).mean()),
    fwd10=('fwd10', 'mean'), w10=('fwd10', lambda s: (s > 0).mean()),
    fwd20=('fwd20', 'mean'), w20=('fwd20', lambda s: (s > 0).mean()))
for idx, r in res.iterrows():
    print(f"{idx}: n={r['n']:,} | fwd5={r['fwd5']*100:+.3f}%/胜{r['w5']*100:.1f}% "
          f"| fwd10={r['fwd10']*100:+.3f}%/胜{r['w10']*100:.1f}% "
          f"| fwd20={r['fwd20']*100:+.3f}%/胜{r['w20']*100:.1f}%")
con.close()
