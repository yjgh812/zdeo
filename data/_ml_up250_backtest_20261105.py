# -*- coding: utf-8 -*-
"""ML胜率排名(250日上涨占比) 全历史可实现口径验证 (证伪证据件, 证伪档案ML-WINRATE-UP250-NO-EDGE).
口径: qfq收盘价, T日收盘算250日无未来函数上涨日占比(min 200样本),
      宇宙=主板+创业板(剔688/689/北证, 0.5-30元与复刻生成器一致),
      前向收益=T收盘->T+h收盘(可实现, 无未来函数).
检验: 十分位分层 + 复刻条件(>=55%) + 复刻Top7, 分年看稳定性.
用法: python data/_ml_up250_backtest_20261105.py  (只读qfq库, 落盘逐日CSV)
落盘: data/_ml_up250_daily_check_20261105.csv"""
import sys, sqlite3, numpy as np, pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

DB = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
con = sqlite3.connect(DB, uri=True, timeout=300)
px = pd.read_sql_query(
    "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date>='20150101' ORDER BY date", con)
con.close()
px['date'] = px['date'].astype(str)

wide = px.pivot_table(index='date', columns='code', values='close').sort_index()
print('wide:', wide.shape, wide.index[0], '->', wide.index[-1], flush=True)

codes = [c for c in wide.columns if c[0] in '036' and not c.startswith('688') and not c.startswith('689')]
W = wide[codes]

rets = W.pct_change(fill_method=None)
up = (rets > 0) & rets.notna()
up = up.astype(float)
up[rets.isna()] = np.nan
ratio = up.rolling(250, min_periods=200).mean()

f5 = W.shift(-5) / W - 1
f10 = W.shift(-10) / W - 1
f20 = W.shift(-20) / W - 1
print('factor built', flush=True)

R, F5, F10, F20 = ratio.values, f5.values, f10.values, f20.values
Wv = W.values
dates = W.index
recs = []
for i in range(len(dates)):
    r, f5v, f10v, f20v = R[i], F5[i], F10[i], F20[i]
    ok = ~(np.isnan(r) | np.isnan(f5v) | np.isnan(f10v) | np.isnan(f20v))
    px0 = Wv[i]
    ok &= (px0 > 0.5) & (px0 <= 30.0) & ~np.isnan(px0)
    if ok.sum() < 500:
        continue
    rv, a5, a10, a20 = r[ok], f5v[ok], f10v[ok], f20v[ok]
    rk = pd.Series(rv).rank(pct=True).values
    dec = np.minimum((rk * 10).astype(int), 9)
    row = {'date': dates[i], 'n': int(ok.sum()),
           'pool_f5': float(np.mean(a5)), 'pool_f10': float(np.mean(a10)), 'pool_f20': float(np.mean(a20))}
    for d in range(10):
        m = dec == d
        row[f'd{d+1}_f5'] = float(np.mean(a5[m]))
        row[f'd{d+1}_f10'] = float(np.mean(a10[m]))
        row[f'd{d+1}_f20'] = float(np.mean(a20[m]))
    row['d10_f5'] = float(np.mean(a5[dec == 9]))
    hi = rv >= 0.55
    row['hi_n'] = int(hi.sum())
    for h, arr in ((5, a5), (10, a10), (20, a20)):
        row[f'hi_f{h}'] = float(np.mean(arr[hi])) if hi.sum() else np.nan
        t7 = np.argsort(-rv)[:7]
        row[f'top7_f{h}'] = float(np.mean(arr[t7]))
    recs.append(row)

df = pd.DataFrame(recs)
df['year'] = df['date'].str[:4]
out = r'D:\zcodesj\data\_ml_up250_daily_check_20261105.csv'
df.to_csv(out, index=False, encoding='utf-8-sig')
print('daily rows:', len(df), flush=True)

for h in (5, 10, 20):
    cols = [f'd{i}_f{h}' for i in range(1, 11)]
    print(f'\n=== 十分位平均前向{h}日收益(%) 全期 (D1=250日上涨占比最高) ===')
    mean = df[cols].mean() * 100
    pool = df[f'pool_f{h}'].mean() * 100
    print('deciles :', ' '.join(f'D{i}:{v:.2f}' for i, v in enumerate(mean, 1)))
    print('pool    : %.2f%%' % pool)

print('\n=== D1-D10差(pp) 分年 ===')
for h in (5, 10, 20):
    diff = (df[f'd1_f{h}'] - df[f'd10_f{h}']) * 100
    g = diff.groupby(df['year']).agg(['mean', 'median', 'count'])
    print(f'-- 前向{h}日 --')
    print(g.round(2).to_string())

print('\n=== 复刻条件(>=55%) ===')
print('命中日占比:', round(float((df.hi_n > 0).mean()), 3),
      ' 命中日平均只数:', round(float(df.loc[df.hi_n > 0, 'hi_n'].mean()), 1))
for h in (5, 10, 20):
    sub = df[df['hi_n'] > 0]
    print(f"hi_f{h}: {sub[f'hi_f{h}'].mean()*100:.2f}% vs 池 {sub[f'pool_f{h}'].mean()*100:.2f}%  差 {(sub[f'hi_f{h}']-sub[f'pool_f{h}']).mean()*100:+.2f}pp")

print('\n=== 复刻Top7 ===')
for h in (5, 10, 20):
    print(f"top7_f{h}: {df[f'top7_f{h}'].mean()*100:.2f}% vs 池 {df[f'pool_f{h}'].mean()*100:.2f}%  差 {(df[f'top7_f{h}']-df[f'pool_f{h}']).mean()*100:+.2f}pp")
