import sqlite3, json, os
import numpy as np, pandas as pd
from scipy import stats

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
ST_DB = r'D:\zcodesj\data\st_history.db'
OUT = r'D:\zcodesj\reports\MICRO_STAGE7_20261109'
os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect('file:' + QFQ_DB + '?mode=ro', uri=True)
px = pd.read_sql("SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='20200101'", con)
con.close()
px = px[(px['volume'] > 0) & (px['close'] > 0) & (px['amount'] > 0)].copy()
px['date'] = px['date'].astype(str)
px = px[~px['code'].str[0].isin(['4', '8', '9'])]
px = px[~px['code'].str[:3].isin(['688', '689'])]
con = sqlite3.connect('file:' + FUND_DB + '?mode=ro', uri=True)
db1 = pd.read_sql("SELECT ts_code,trade_date,turnover_rate FROM daily_basic", con)
con.close()
db1['date'] = db1['trade_date'].astype(str)
db1['code'] = db1['ts_code'].str[:6]
db1['tr'] = pd.to_numeric(db1['turnover_rate'], errors='coerce')
db1 = db1[['code', 'date', 'tr']].dropna()
m = px.merge(db1, on=['code', 'date'], how='inner').dropna(subset=['tr'])
m = m.sort_values(['code', 'date']).reset_index(drop=True)
g = m.groupby('code', observed=True)
lo60 = g['close'].transform(lambda s: s.rolling(60).min())
hi60 = g['close'].transform(lambda s: s.rolling(60).max())
m['pos'] = (m['close'] - lo60) / (hi60 - lo60 + 1e-12)
m['tr60'] = g['tr'].transform(lambda s: s.rolling(60).mean())
m['vr'] = (g['tr'].transform(lambda s: s.rolling(5).mean())) / (m['tr60'] + 1e-12)
m['dr'] = g['close'].transform(lambda s: s / s.shift(1) - 1.0)
m['fwd10'] = g['close'].transform(lambda s: s.shift(-11) / s.shift(-1) - 1.0 - 0.003)
m['fwd20'] = g['close'].transform(lambda s: s.shift(-21) / s.shift(-1) - 1.0 - 0.003)
pc = (m['tr'] * m['close'])
wsum = pc.groupby(m['code']).transform(lambda s: s.rolling(60).sum())
tsum = m['tr'].groupby(m['code']).transform(lambda s: s.rolling(60).sum())
m['profit'] = m['close'] / (wsum / (tsum + 1e-12)) - 1.0
m['hot'] = (m['tr'] > 1.5 * m['tr60']).astype(int)
m['uphot'] = ((m['dr'] > 0) & (m['hot'] == 1)).astype(int)
m['dnhot'] = ((m['dr'] < 0) & (m['hot'] == 1)).astype(int)
m['volup20'] = g['uphot'].transform(lambda s: s.rolling(20).sum())
m['voldn20'] = g['dnhot'].transform(lambda s: s.rolling(20).sum())
m['amihud'] = m['dr'].abs() / m['amount']
m['ami_q90'] = g['amihud'].transform(lambda s: s.shift(1).rolling(60).quantile(0.9))
m = m.dropna(subset=['pos', 'vr', 'profit', 'fwd10', 'fwd20', 'amihud', 'ami_q90'])
m['volup20'] = m['volup20'].fillna(0)
m['voldn20'] = m['voldn20'].fillna(0)
g = m.groupby('code', observed=True)
m['hi_impact'] = (m['amihud'] > m['ami_q90']).astype(int)
print('rows', len(m), flush=True)

def q5(s):
    r = s.rank(pct=True)
    return (np.ceil(r * 5 - 1e-9) - 1).where(r.notna()).astype('Int64')

def _ds(x, cond, col):
    return x[cond][['date', col]].dropna().set_index('date')[col]

def cellm(x, cond, col):
    s = _ds(x, cond, col)
    if len(s) < 300:
        return int(len(s)), None, None
    return int(len(s)), float(s.mean()), float(stats.ttest_1samp(s, 0).statistic)

def spread(x, col, cq, lo=0, hi=4):
    a = _ds(x, x[cq] == lo, col)
    b = _ds(x, x[cq] == hi, col)
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    if len(d) < 300:
        return int(len(d)), None, None
    return int(len(d)), float(d.mean()), float(stats.ttest_1samp(d, 0).statistic)

res = {}
m['pq5'] = m.groupby('date')['profit'].transform(q5)
c1 = m.groupby(['date', 'pq5']).agg(n=('code', 'size'), f20=('fwd20', 'mean')).reset_index()
c1 = c1[c1['n'] >= 50]
res['T16a_Q5mQ1_f20'] = spread(c1, 'f20', 'pq5')
qm = c1.groupby('pq5')['f20'].mean()
res['T16a_q_means'] = {int(k): round(float(v), 5) for k, v in qm.items()}
m['vu5'] = m.groupby('date')['volup20'].transform(q5)
c2 = m.groupby(['date', 'vu5']).agg(n=('code', 'size'), f20=('fwd20', 'mean')).reset_index()
c2 = c2[c2['n'] >= 50]
res['T16b_volup_Q5mQ1'] = spread(c2, 'f20', 'vu5')
m['vd5'] = m.groupby('date')['voldn20'].transform(q5)
c3 = m.groupby(['date', 'vd5']).agg(n=('code', 'size'), f20=('fwd20', 'mean')).reset_index()
c3 = c3[c3['n'] >= 50]
res['T16b_voldn_Q5mQ1'] = spread(c3, 'f20', 'vd5')
mv = m[m['vr'] >= 1.5].copy()
cv = mv.groupby(['date', 'hi_impact']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
cv = cv[cv['n'] >= 30]
a = _ds(cv, cv['hi_impact'] == 1, 'f10')
b = _ds(cv, cv['hi_impact'] == 0, 'f10')
j = a.index.join(b.index, how='inner')
d = a.loc[j] - b.loc[j]
res['T17_hi_minus_lo_impact'] = (int(len(d)), float(d.mean()), float(stats.ttest_1samp(d, 0).statistic)) if len(d) >= 300 else (int(len(d)), None, None)
res['T17_means'] = {k: cellm(cv, cv['hi_impact'] == int(k), 'f10') for k in [0, 1]}
meta = {'rows': int(len(m)), 'days': int(m['date'].nunique()), 'range': [str(m['date'].min()), str(m['date'].max())]}
out = {'prereg': 'PREREG_MICRO_STAGE7_20261109', 'meta': meta, 'results': res}
json.dump(out, open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, default=str, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))