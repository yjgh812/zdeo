# -*- coding: utf-8 -*-
"""BAYA-SEMANTICS-AB 2006-2026: bp_ep 双语义同台回测(只读研究件, 20260915)

A 深度价值 = 存储bp降序(B/M高) + 存储ep降序(E/P高)   [canonical, 当前生产]
B 质量成长 = 存储bp升序(P/B高) + ep×close降序(EPS高)  [8/13期变体, 选中百亚]
同宇宙同口径: 月度T0=每月首个交易日, T0收盘入场, close<=30, 剔688/4/8/92, PIT ep as-of。
每组输出: 域全体 与 域内R∧地量(amt_ratio<0.35)子组 的 fwd20/40/60 vs 全市场等权;
另链乘月度fwd20成累计曲线对比(月度调仓近似, 20交易日窗≈月度间隔)。
已知局限: 2006-2016 PIT ep覆盖薄(域样本少)该段仅供参考; 无历史ST过滤; 链乘曲线含近似。
"""
import sys, os, csv, sqlite3, json, statistics
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
PIT = r'D:\zcodesj\factor_pit.db'
OUT = r'D:\zcodesj\data\_semantics_ab_2006_result.json'

print('加载qfq(2003起)...', flush=True)
qfq = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
df = pd.read_sql_query("SELECT code, date, close, high, amount FROM stock_daily_meta_qfq "
                       "WHERE date>='20030101' ORDER BY code, date", qfq)
qfq.close()
df['code'] = df['code'].astype(str)
df = df[~df['code'].str.startswith(('688', '4', '8', '92'))]
print(f'行数 {len(df)}, 股票 {df.code.nunique()}', flush=True)

g = df.groupby('code', sort=False)
c = g['close']; h = g['high']; a = g['amount']
df['ret'] = g['close'].pct_change()
df['pos250'] = (df['close'] - c.transform(lambda s: s.rolling(250).min())) / \
               (h.transform(lambda s: s.rolling(250).max()) - c.transform(lambda s: s.rolling(250).min())) * 100
df['dist_hi'] = (df['close'] / h.transform(lambda s: s.rolling(250).max()) - 1) * 100
df['ret20'] = df['close'] / g['close'].shift(20) - 1
df['ret60'] = df['close'] / g['close'].shift(60) - 1
df['vol20'] = g['ret'].transform(lambda s: s.rolling(20).std(ddof=1)) * 100
amt20 = a.transform(lambda s: s.rolling(20).mean())
df['amt_ratio'] = amt20 / amt20.groupby(df['code']).transform(lambda s: s.rolling(250).max())
df['R'] = ((df['pos250'] <= 45) & (df['dist_hi'] <= -30) & (df['ret60'] <= 0) &
           (df['ret20'] >= -12) & (df['vol20'] <= 4))
df['desert'] = df['amt_ratio'] < 0.35
print('滚动特征完成', flush=True)

df['ym'] = df['date'].str[:6]
t0s = df.groupby('ym')['date'].min()
t0s = t0s[t0s.index >= '200601']
print(f'月度T0: {len(t0s)} 期', flush=True)

pc = sqlite3.connect(f'file:{PIT}?mode=ro', uri=True)
pit = pd.read_sql_query('SELECT code, pit_avail_date, ep, report_date FROM stock_factors_fundamental_pit_v2 '
                        'WHERE ep IS NOT NULL', pc)
pc.close()
pit['code'] = pit['code'].astype(str)
pit = pit.sort_values(['code', 'pit_avail_date', 'report_date']).drop_duplicates(['code', 'pit_avail_date'], keep='last')
pit = pit[['code', 'pit_avail_date', 'ep']].rename(columns={'pit_avail_date': 'avail'})
pit['avail'] = pit['avail'].astype(int)

panel = df[df['date'].isin(set(t0s.values))][['code', 'date', 'close', 'pos250', 'dist_hi',
                                              'ret20', 'ret60', 'vol20', 'amt_ratio', 'R', 'desert']].copy()
panel = panel[(panel['close'] <= 30) & panel['pos250'].notna()]
panel = panel.rename(columns={'date': 't0'})
panel['t0i'] = panel['t0'].astype(int)
panel = panel.sort_values('t0i')
merged = pd.merge_asof(panel, pit.sort_values('avail'), left_on='t0i', right_on='avail', by='code')
merged = merged[merged['ep'].notna()].copy()
merged['eps'] = merged['ep'] * merged['close']
print(f'panel: {len(merged)} 行', flush=True)

# 双语义排名: 先合并PIT原始bp
pc = sqlite3.connect(f'file:{PIT}?mode=ro', uri=True)
pit2 = pd.read_sql_query('SELECT code, pit_avail_date, bp, report_date FROM stock_factors_fundamental_pit_v2 '
                         'WHERE bp IS NOT NULL', pc)
pc.close()
pit2['code'] = pit2['code'].astype(str)
pit2 = pit2.sort_values(['code', 'pit_avail_date', 'report_date']).drop_duplicates(['code', 'pit_avail_date'], keep='last')
pit2 = pit2[['code', 'pit_avail_date', 'bp']].rename(columns={'pit_avail_date': 'avail'})
pit2['avail'] = pit2['avail'].astype(int)
merged = pd.merge_asof(merged.sort_values('t0i'), pit2.sort_values('avail'), left_on='t0i', right_on='avail',
                       by='code', suffixes=('', '_bp'))
merged = merged[merged['bp'].notna()].copy()
merged['pct_bp_desc'] = merged.groupby('t0')['bp'].rank(ascending=False, pct=True)
merged['pct_bp_asc'] = merged.groupby('t0')['bp'].rank(ascending=True, pct=True)
merged['pct_eps_desc'] = merged.groupby('t0')['eps'].rank(ascending=False, pct=True)
merged['pct_ep_desc'] = merged.groupby('t0')['ep'].rank(ascending=False, pct=True)
merged['score_A'] = (merged['pct_bp_desc'] + merged['pct_ep_desc']) / 2
merged['score_B'] = (merged['pct_bp_asc'] + merged['pct_eps_desc']) / 2
merged['in_A'] = merged['score_A'] >= merged.groupby('t0')['score_A'].transform(lambda s: s.nlargest(20).min())
merged['in_B'] = merged['score_B'] >= merged.groupby('t0')['score_B'].transform(lambda s: s.nlargest(20).min())
print('双语义域标记完成', flush=True)

alld = sorted(df['date'].unique())
dpos = {d: i for i, d in enumerate(alld)}
close_map = df.set_index(['code', 'date'])['close']
for nd in (20, 40, 60):
    vals = []
    codes = merged['code'].values; t0s_ = merged['t0'].values
    out = np.full(len(merged), np.nan)
    cidx = df.reset_index(drop=True).set_index(['code', 'date'])['close']
    for i in range(len(merged)):
        di = dpos.get(t0s_[i])
        if di is None or di + nd >= len(alld):
            continue
        v = close_map.get((codes[i], alld[di+nd]))
        if v is not None and not pd.isna(v):
            out[i] = (v / merged['close'].values[i] - 1) * 100
    merged[f'fwd{nd}'] = out
print('前向收益完成', flush=True)

result = {'overall': {}, 'yearly': []}
groups = {
    'A_domain_all': merged['in_A'],
    'A_R_desert': merged['in_A'] & merged['R'] & merged['desert'],
    'B_domain_all': merged['in_B'],
    'B_R_desert': merged['in_B'] & merged['R'] & merged['desert'],
    'market': pd.Series(True, index=merged.index),
}
for lab, mask in groups.items():
    sub = merged[mask]
    row = {}
    for nd in (20, 40, 60):
        v = sub[f'fwd{nd}'].dropna()
        row[f'fwd{nd}'] = dict(n=int(len(v)), mean=round(float(v.mean()), 2),
                               median=round(float(v.median()), 2),
                               win=round(float((v > 0).mean()*100), 1)) if len(v) else None
    result['overall'][lab] = row
    print(f"\n[{lab}] n={len(sub)}: " + ' | '.join(
        f"fwd{nd} {row[f'fwd{nd}']['mean']:+.2f}%/中位{row[f'fwd{nd}']['median']:+.2f}%/胜率{row[f'fwd{nd}']['win']:.0f}%"
        if row[f'fwd{nd}'] else f"fwd{nd} 无" for nd in (20, 40, 60)))

# 链乘月度曲线(fwd20近似月度调仓)
merged['year'] = merged['t0'].str[:4]
def chain(mask):
    per = merged[mask].groupby('t0')[f'fwd20'].mean().dropna()
    eq = (1 + per/100).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    return dict(months=int(len(per)), cum=round(float(eq.iloc[-1]), 3), mdd=round(float(dd)*100, 1))
result['chain'] = {lab: chain(mask) for lab, mask in groups.items()}
print('\n链乘月度fwd20(2006起):', json.dumps(result['chain'], ensure_ascii=False))

for y, gy in merged.groupby('year'):
    row = {'year': y}
    for lab, col in [('A', 'in_A'), ('B', 'in_B')]:
        v = gy[gy[col]]['fwd20'].dropna()
        row[lab] = round(float(v.mean()), 2) if len(v) >= 3 else None
    vm = gy['fwd20'].dropna()
    row['mkt'] = round(float(vm.mean()), 2)
    result['yearly'].append(row)

json.dump(result, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'\n落盘 {OUT}', flush=True)
print('\n分年 fwd20 (A价值/B成长/市场):')
for row in result['yearly']:
    print(f"  {row['year']}: A={row['A']} B={row['B']} 市场={row['mkt']}")
