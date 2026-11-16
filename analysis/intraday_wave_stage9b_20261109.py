import sqlite3, json, os, math
import numpy as np, pandas as pd
from scipy import stats

DB = r'D:\zcodesj\data\minute_archive.db'
OUT = r'D:\zcodesj\reports\INTRADAY_WAVE_STAGE9B_20261109'
os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
ag = pd.read_sql("""SELECT code, day,
 SUM(CASE WHEN tmin BETWEEN 575 AND 690 THEN vol ELSE 0 END) vol_M,
 SUM(CASE WHEN tmin BETWEEN 780 AND 900 THEN vol ELSE 0 END) vol_A,
 MAX(CASE WHEN tmin <= 690 THEN h END) high_M,
 MAX(CASE WHEN tmin >= 780 THEN h END) high_A,
 AVG(CASE WHEN tmin=900 THEN c END) c900
 FROM bars GROUP BY code, day""", con)
con.close()
ag['day'] = ag['day'].astype(str)
ag = ag[(ag['vol_M'] > 0) & (ag['vol_A'] > 0)]
ag = ag[~ag['code'].str[0].isin(['4', '8', '9'])]
ag = ag[~ag['code'].str[:3].isin(['688', '689'])]
ag = ag.sort_values(['code', 'day']).reset_index(drop=True)
g = ag.groupby('code', observed=True)
ag['prev_c'] = g['c900'].shift(1)
ag['dr'] = ag['c900'] / ag['prev_c'] - 1.0
ag['nr'] = g['c900'].shift(-1) / ag['c900'] - 1.0
m = ag.dropna(subset=['dr', 'nr', 'high_M', 'high_A'])
print('stock-days', len(m), 'days', m['day'].nunique(), flush=True)

m['grp'] = np.where(~(m['high_A'] > m['high_M']), 'no_new_high',
            np.where(m['vol_A'] > m['vol_M'], 'healthy', 'danger'))

def _ds(x, cond, col):
    return x[cond][['day', col]].dropna().set_index('day')[col]

def cellm(x, cond, col):
    s = _ds(x, cond, col)
    if len(s) < 100:
        return {'days': int(len(s)), 'mean': None, 'win': None, 't': None}
    mu = float(s.mean())
    se = float(s.std() / math.sqrt(len(s)))
    return {'days': int(len(s)), 'mean': round(mu * 100, 3), 'win': round(float((s > 0).mean()) * 100, 1),
            't': round(float(stats.ttest_1samp(s, 0).statistic), 2),
            'ci': [round((mu - 1.96 * se) * 100, 2), round((mu + 1.96 * se) * 100, 2)]}

def diff_t(x, ga, gb, col):
    a = _ds(x, x['grp'] == ga, col)
    b = _ds(x, x['grp'] == gb, col)
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    if len(d) < 100:
        return {'days': int(len(d)), 'diff_pp': None, 't': None}
    return {'days': int(len(d)), 'diff_pp': round(float(d.mean()) * 100, 3), 't': round(float(stats.ttest_1samp(d, 0).statistic), 2)}

up = m[m['dr'] > 0].copy()
cu = up.groupby(['day', 'grp']).agg(n=('code', 'size'), nr=('nr', 'mean')).reset_index()
cu = cu[cu['n'] >= 20]
res = {}
res['C1_healthy_minus_danger'] = diff_t(cu, 'healthy', 'danger', 'nr')
res['group_means_upday'] = {k: cellm(cu, cu['grp'] == k, 'nr') for k in ['healthy', 'danger', 'no_new_high']}
ca = m.groupby(['day', 'grp']).agg(n=('code', 'size'), nr=('nr', 'mean')).reset_index()
ca = ca[ca['n'] >= 20]
res['allday_group_means'] = {k: cellm(ca, ca['grp'] == k, 'nr') for k in ['healthy', 'danger', 'no_new_high']}
res['grp_counts_upday'] = up['grp'].value_counts().to_dict()
meta = {'stock_days': int(len(m)), 'days': int(m['day'].nunique()), 'range': [str(m['day'].min()), str(m['day'].max())]}
out = {'prereg': 'PREREG_INTRADAY_WAVE_STAGE9B_20261109', 'meta': meta, 'results': res}
json.dump(out, open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))