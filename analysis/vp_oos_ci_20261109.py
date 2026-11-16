import sqlite3, json, os, math
import numpy as np, pandas as pd
from scipy import stats

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
ST_DB = r'D:\zcodesj\data\st_history.db'
OUT = r'D:\zcodesj\reports\VP_OOS_CI_20261109'
os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect('file:' + QFQ_DB + '?mode=ro', uri=True)
px = pd.read_sql("SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE date>='20200101'", con)
con.close()
px = px[(px['volume'] > 0) & (px['close'] > 0)].copy()
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
m['dr'] = g['close'].transform(lambda s: s / s.shift(1) - 1.0)
m['tr5'] = g['tr'].transform(lambda s: s.rolling(5).mean())
m['tr60'] = g['tr'].transform(lambda s: s.rolling(60).mean())
m['hot'] = (m['tr'] > 1.5 * m['tr60']).astype(int)
m['uphot'] = ((m['dr'] > 0) & (m['hot'] == 1)).astype(int)
m['volup20'] = g['uphot'].transform(lambda s: s.rolling(20).sum())
cm = g['close'].transform(lambda s: s.rolling(20).mean())
cs = g['close'].transform(lambda s: s.rolling(20).std())
tm = g['tr'].transform(lambda s: s.rolling(20).mean())
ts_ = g['tr'].transform(lambda s: s.rolling(20).std())
prod = (m['close'] - cm) * (m['tr'] - tm)
m['ctc'] = prod.groupby(m['code']).transform(lambda s: s.rolling(20).mean()) / (cs * ts_ + 1e-12)
pc = m['tr'] * m['close']
wsum = pc.groupby(m['code']).transform(lambda s: s.rolling(60).sum())
tsum = g['tr'].transform(lambda s: s.rolling(60).sum())
m['profit'] = m['close'] / (wsum / (tsum + 1e-12)) - 1.0
m['fwd20'] = g['close'].transform(lambda s: s.shift(-21) / s.shift(-1) - 1.0 - 0.003)
m = m.dropna(subset=['ctc', 'profit', 'volup20', 'fwd20'])
print('rows', len(m), flush=True)

def q5(s):
    r = s.rank(pct=True)
    return (np.ceil(r * 5 - 1e-9) - 1).where(r.notna()).astype('Int64')

def _ds(x, cond, col):
    return x[cond][['date', col]].dropna().set_index('date')[col]

def cellt(x, cond, col):
    s = _ds(x, cond, col)
    if len(s) < 100:
        return {'days': int(len(s)), 'mean': None, 't': None, 'ci': None}
    mu = float(s.mean())
    se = float(s.std() / math.sqrt(len(s)))
    return {'days': int(len(s)), 'mean': round(mu * 100, 3), 't': round(float(stats.ttest_1samp(s, 0).statistic), 2),
            'ci': [round((mu - 1.96 * se) * 100, 2), round((mu + 1.96 * se) * 100, 2)], 'cross_zero': bool(mu - 1.96 * se < 0 < mu + 1.96 * se)}

def spread(x, cq, col):
    a = _ds(x, x[cq] == 0, col)
    b = _ds(x, x[cq] == 4, col)
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    if len(d) < 100:
        return {'days': int(len(d)), 'diff': None, 't': None}
    return {'days': int(len(d)), 'diff_pp': round(float(d.mean()) * 100, 3), 't': round(float(stats.ttest_1samp(d, 0).statistic), 2)}

res = {}
TR = m[m['date'] < '20250101']
TE = m[m['date'] >= '20250101']
for name, col in [('CTC', 'ctc'), ('PROFIT', 'profit'), ('VOLUP20', 'volup20')]:
    res[name] = {}
    for tag, split in [('train_2020_2024', TR), ('test_2025_2026', TE)]:
        sp = split.copy()
        sp['cq'] = sp.groupby('date')[col].transform(q5)
        sp = sp.dropna(subset=['cq'])
        cc = sp.groupby(['date', 'cq']).agg(n=('code', 'size'), f20=('fwd20', 'mean')).reset_index()
        cc = cc[cc['n'] >= 50]
        res[name][tag] = {'Q0': cellt(cc, cc['cq'] == 0, 'f20'), 'Q4': cellt(cc, cc['cq'] == 4, 'f20'),
                          'Q0_minus_Q4': spread(cc, 'cq', 'f20')}
json.dump(res, open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1))