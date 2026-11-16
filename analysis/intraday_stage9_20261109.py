import sqlite3, json, os, math
import numpy as np, pandas as pd
from scipy import stats

DB = r'D:\zcodesj\data\minute_archive.db'
OUT = r'D:\zcodesj\reports\INTRADAY_STAGE9_20261109'
os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
ag = pd.read_sql("""SELECT code, day,
 SUM(CASE WHEN tmin BETWEEN 575 AND 630 THEN vol ELSE 0 END) ve,
 SUM(CASE WHEN tmin BETWEEN 840 AND 900 THEN vol ELSE 0 END) vl,
 AVG(CASE WHEN tmin=900 THEN c END) c900
 FROM bars GROUP BY code, day""", con)
con.close()
ag['day'] = ag['day'].astype(str)
ag = ag[(ag['ve'] > 0) & (ag['vl'] > 0)]
ag = ag[~ag['code'].str[0].isin(['4', '8', '9'])]
ag = ag[~ag['code'].str[:3].isin(['688', '689'])]
ag = ag.sort_values(['code', 'day']).reset_index(drop=True)
g = ag.groupby('code', observed=True)
ag['prev_c'] = g['c900'].shift(1)
ag['dr'] = ag['c900'] / ag['prev_c'] - 1.0
ag['nr'] = g['c900'].shift(-1) / ag['c900'] - 1.0
ag['ratio'] = ag['vl'] / (ag['ve'] + 1e-9)
m = ag.dropna(subset=['dr', 'nr', 'ratio'])
print('stock-days', len(m), 'days', m['day'].nunique(), flush=True)

def q5(s):
    r = s.rank(pct=True)
    return (np.ceil(r * 5 - 1e-9) - 1).where(r.notna()).astype('Int64')

def _ds(x, cond, col):
    return x[cond][['day', col]].dropna().set_index('day')[col]

def spread(x, cq, col):
    a = _ds(x, x[cq] == 4, col)
    b = _ds(x, x[cq] == 0, col)
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    if len(d) < 100:
        return {'days': int(len(d)), 'diff_pp': None, 't': None}
    return {'days': int(len(d)), 'diff_pp': round(float(d.mean()) * 100, 3), 't': round(float(stats.ttest_1samp(d, 0).statistic), 2)}

def cellm(x, cond, col):
    s = _ds(x, cond, col)
    if len(s) < 100:
        return {'days': int(len(s)), 'mean': None, 'win': None}
    return {'days': int(len(s)), 'mean': round(float(s.mean()) * 100, 3), 'win': round(float((s > 0).mean()) * 100, 1)}

res = {}
m['rq'] = m.groupby('day')['ratio'].transform(q5)
m = m.dropna(subset=['rq'])
up = m[m['dr'] > 0].copy()
cu = up.groupby(['day', 'rq']).agg(n=('code', 'size'), nr=('nr', 'mean')).reset_index()
cu = cu[cu['n'] >= 20]
res['T20a_upday_Q4mQ0'] = spread(cu, 'rq', 'nr')
res['T20a_upday_means'] = {str(k): cellm(cu, cu['rq'] == k, 'nr') for k in range(5)}
ca = m.groupby(['day', 'rq']).agg(n=('code', 'size'), nr=('nr', 'mean')).reset_index()
ca = ca[ca['n'] >= 20]
res['T20b_all_Q4mQ0'] = spread(ca, 'rq', 'nr')
res['T20b_all_means'] = {str(k): cellm(ca, ca['rq'] == k, 'nr') for k in range(5)}
meta = {'stock_days': int(len(m)), 'days': int(m['day'].nunique()), 'range': [str(m['day'].min()), str(m['day'].max())]}
out = {'prereg': 'PREREG_INTRADAY_STAGE9_20261109', 'meta': meta, 'results': res}
json.dump(out, open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))