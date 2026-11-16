# -*- coding: utf-8 -*-
"""量价stage-4 (prereg: data/PREREG_VOLPRICE_STAGE4_20261109.json, 冻结)
T12=换手绝对档×位置18格; T13=高换手低量比反向; T14=极值换手位置不对称"""
import sqlite3, json, os
import numpy as np, pandas as pd
from scipy import stats

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
ST_DB = r'D:\zcodesj\data\st_history.db'
OUT = r'D:\zcodesj\reports\VOLPRICE_STAGE4_20261109'
os.makedirs(OUT, exist_ok=True)

def q(sql, db, **kw):
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    df = pd.read_sql(sql, con, **kw)
    con.close()
    return df

print('[1] qfq...', flush=True)
px = q("SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE date>='20200101'", QFQ_DB)
px = px[(px['volume'] > 0) & (px['close'] > 0)].copy()
px['date'] = px['date'].astype(str)
px = px[~px['code'].str[0].isin(['4', '8', '9'])]
px = px[~px['code'].str[:3].isin(['688', '689'])]

print('[2] daily_basic...', flush=True)
db1 = q("SELECT ts_code,trade_date,turnover_rate,volume_ratio FROM daily_basic", FUND_DB)
db1 = db1.dropna(subset=['turnover_rate']).copy()
db1['date'] = db1['trade_date'].astype(str)
db1['code'] = db1['ts_code'].str[:6]
db1 = db1[db1['code'].str[0].isin(['0', '3', '6'])]
for cx in ('turnover_rate', 'volume_ratio'):
    db1[cx] = pd.to_numeric(db1[cx], errors='coerce')

print('[3] ST...', flush=True)
st = q("SELECT code,date FROM st_daily WHERE is_st=1", ST_DB).drop_duplicates()
st['date'] = st['date'].astype(str)
st['flag'] = 1
px = px.merge(st, on=['code', 'date'], how='left')
px = px[px['flag'].isna()].drop(columns=['flag'])

print('[4] merge...', flush=True)
m = px.merge(db1[['code', 'date', 'turnover_rate', 'volume_ratio']], on=['code', 'date'], how='inner')
m['turnover_rate'] = pd.to_numeric(m['turnover_rate'], errors='coerce')
m = m.dropna(subset=['turnover_rate']).sort_values(['code', 'date']).reset_index(drop=True)
g = m.groupby('code', observed=True)
lo60 = g['close'].transform(lambda s: s.rolling(60).min())
hi60 = g['close'].transform(lambda s: s.rolling(60).max())
m['pos'] = (m['close'] - lo60) / (hi60 - lo60 + 1e-12)
m['fwd10'] = g['close'].transform(lambda s: s.shift(-11) / s.shift(-1) - 1.0 - 0.003)
m = m.dropna(subset=['pos', 'fwd10'])
print('  feature rows', len(m), flush=True)
m['pband'] = np.where(m['pos'] <= 0.33, 'L', np.where(m['pos'] <= 0.66, 'M', 'H'))
m['tband'] = pd.cut(m['turnover_rate'], [-0.01, 1, 3, 7, 15, 30, 1e9],
                    labels=['t01', 't13', 't37', 't715', 't1530', 't30p'])
res = {}

print('[5] T12 grid...', flush=True)
c12 = m.groupby(['date', 'pband', 'tband'], observed=True).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c12 = c12[c12['n'] >= 30]
def cell_t(x, cond, col='f10'):
    s = x[cond][['date', col]].dropna().set_index('date')[col]
    if len(s) < 300:
        return int(len(s)), None, None
    return int(len(s)), float(s.mean()), float(stats.ttest_1samp(s, 0).statistic)
def cell_diff(x, ca, cb, col='f10'):
    a = x[ca][['date', col]].dropna().set_index('date')[col]
    b = x[cb][['date', col]].dropna().set_index('date')[col]
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    if len(d) < 300:
        return int(len(d)), None, None
    return int(len(d)), float(d.mean()), float(stats.ttest_1samp(d, 0).statistic)
grid = {}
for p in ['L', 'M', 'H']:
    for t in ['t01', 't13', 't37', 't715', 't1530', 't30p']:
        cond = (c12['pband'] == p) & (c12['tband'] == t)
        grid['%s_%s' % (p, t)] = cell_t(c12, cond)
res['T12_grid'] = grid
L = c12['pband'] == 'L'
res['T12A_L_t37_vs_t01'] = cell_diff(c12, L & (c12['tband'] == 't37'), L & (c12['tband'] == 't01'))
res['T12B_L_t15p_vs_t37'] = cell_diff(c12, L & (c12['tband'].isin(['t1530', 't30p'])), L & (c12['tband'] == 't37'))
H = c12['pband'] == 'H'
res['T12C_H_t20p_vs_t03'] = cell_diff(c12, H & (c12['tband'].isin(['t1530', 't30p'])), H & (c12['tband'].isin(['t01', 't13'])))
print('  T12 done', flush=True)

print('[6] T13/T14...', flush=True)
hi = m['turnover_rate'] > 5
m['sig13'] = np.where(hi & (m['volume_ratio'] < 2), 'hilo', np.where(hi & (m['volume_ratio'] < 3) & (m['volume_ratio'] >= 2), 'himid', 'other'))
c13 = m.groupby(['date', 'sig13']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c13 = c13[c13['n'] >= 20]
res['T13_hilo_vs_himid'] = cell_diff(c13, c13['sig13'] == 'hilo', c13['sig13'] == 'himid')
res['T13_means'] = {k: cell_t(c13, c13['sig13'] == k) for k in ['hilo', 'himid']}
m['sig14'] = np.where((m['turnover_rate'] > 10) & (m['volume_ratio'] > 5), np.where(m['pos'] <= 0.33, 'lowpos', np.where(m['pos'] > 0.66, 'highpos', 'midpos')), 'other')
c14 = m.groupby(['date', 'sig14']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c14 = c14[c14['n'] >= 20]
res['T14_lowpos_minus_highpos'] = cell_diff(c14, c14['sig14'] == 'lowpos', c14['sig14'] == 'highpos')
res['T14_means'] = {k: cell_t(c14, c14['sig14'] == k) for k in ['lowpos', 'midpos', 'highpos']}

print('[7] out...', flush=True)
meta = {'rows': int(len(m)), 'days': int(m['date'].nunique()), 'range': [str(m['date'].min()), str(m['date'].max())]}
json.dump({'prereg': 'PREREG_VOLPRICE_STAGE4_20261109', 'meta': meta, 'results': res},
          open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, default=str, indent=1)
print(json.dumps(res, ensure_ascii=False, default=str, indent=1), flush=True)