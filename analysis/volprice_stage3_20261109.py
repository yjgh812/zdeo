# -*- coding: utf-8 -*-
"""量价stage-3 (prereg: data/PREREG_VOLPRICE_STAGE3_20261109.json, 冻结)
T8=真堆量(2周持续); T9=底量超顶量; T10=市值分层换手; T11=高换手次日抑制"""
import sqlite3, json, os
import numpy as np, pandas as pd
from scipy import stats

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
ST_DB = r'D:\zcodesj\data\st_history.db'
OUT = r'D:\zcodesj\reports\VOLPRICE_STAGE3_20261109'
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
db1 = q("SELECT ts_code,trade_date,turnover_rate,volume_ratio,circ_mv FROM daily_basic", FUND_DB)
db1 = db1.dropna(subset=['turnover_rate']).copy()
db1['date'] = db1['trade_date'].astype(str)
db1['code'] = db1['ts_code'].str[:6]
db1 = db1[db1['code'].str[0].isin(['0', '3', '6'])]
for cx in ('turnover_rate', 'volume_ratio', 'circ_mv'):
    db1[cx] = pd.to_numeric(db1[cx], errors='coerce')

print('[3] ST...', flush=True)
st = q("SELECT code,date FROM st_daily WHERE is_st=1", ST_DB).drop_duplicates()
st['date'] = st['date'].astype(str)
st['flag'] = 1
px = px.merge(st, on=['code', 'date'], how='left')
px = px[px['flag'].isna()].drop(columns=['flag'])

print('[4] merge...', flush=True)
m = px.merge(db1[['code', 'date', 'turnover_rate', 'volume_ratio', 'circ_mv']], on=['code', 'date'], how='inner')
m['turnover_rate'] = pd.to_numeric(m['turnover_rate'], errors='coerce')
m = m.dropna(subset=['turnover_rate']).sort_values(['code', 'date']).reset_index(drop=True)
g = m.groupby('code', observed=True)
lo60 = g['close'].transform(lambda s: s.rolling(60).min())
hi60 = g['close'].transform(lambda s: s.rolling(60).max())
m['pos'] = (m['close'] - lo60) / (hi60 - lo60 + 1e-12)
m['tr5'] = g['turnover_rate'].transform(lambda s: s.rolling(5).mean())
m['tr60'] = g['turnover_rate'].transform(lambda s: s.rolling(60).mean())
m['vr'] = m['tr5'] / (m['tr60'] + 1e-12)
m['hot'] = (m['turnover_rate'] > 1.5 * m['tr60']).astype(int)
m['hot20'] = g['hot'].transform(lambda s: s.rolling(20).sum())
m['hot5'] = g['hot'].transform(lambda s: s.rolling(5).sum())
m['tr5max250'] = g['tr5'].transform(lambda s: s.shift(10).rolling(240).max())
m['tr5q250'] = g['sur5'] if False else g['tr5'].transform(lambda s: s.shift(10).rolling(240).quantile(0.5))
m['fwd10'] = g['close'].transform(lambda s: s.shift(-11) / s.shift(-1) - 1.0 - 0.003)
m['fwd1'] = g['close'].transform(lambda s: s.shift(-2) / s.shift(-1) - 1.0 - 0.003)
m['fwd20'] = g['close'].transform(lambda s: s.shift(-21) / s.shift(-1) - 1.0 - 0.003)
m['fwd1_win'] = (m['fwd1'] + 0.003 > 0).astype(int)
m = m.dropna(subset=['pos', 'vr', 'hot20', 'tr5max250', 'fwd10', 'fwd20'])
print('  feature rows', len(m), flush=True)

print('[5] quintiles...', flush=True)
def q5(s):
    r = s.rank(pct=True)
    return (np.ceil(r * 5 - 1e-9) - 1).where(r.notna()).astype('Int64')
m['pq'] = m.groupby('date')['pos'].transform(q5)
m['vq'] = m.groupby('date')['vr'].transform(q5)
m = m.dropna(subset=['pq', 'vq'])
g = m.groupby('code', observed=True)
print('  quintile rows', len(m), flush=True)

def _ds(x, cond, col):
    return x[cond][['date', col]].dropna().set_index('date')[col]

def gstat(x, cond, col):
    s = _ds(x, cond, col)
    if len(s) < 300:
        return int(len(s)), None, None
    return int(len(s)), float(s.mean()), float(stats.ttest_1samp(s, 0).statistic)

def diff_t(x, cond_a, cond_b, col):
    a = _ds(x, cond_a, col)
    b = _ds(x, cond_b, col)
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    if len(d) < 300:
        return int(len(d)), None, None
    return int(len(d)), float(d.mean()), float(stats.ttest_1samp(d, 0).statistic)

res = {}
print('[6] T8...', flush=True)
mo = m[m['pq'] == 0].copy()
mo['grp'] = np.where(mo['hot20'] >= 8, 'zhen', np.where((mo['hot20'] >= 3) & (mo['hot5'] >= 2), 'duan', 'dry'))
c8 = mo.groupby(['date', 'grp']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c8 = c8[c8['n'] >= 20]
res['T8_zhen_vs_duan'] = diff_t(c8, c8['grp'] == 'zhen', c8['grp'] == 'duan', 'f10')
res['T8_zhen_vs_dry'] = diff_t(c8, c8['grp'] == 'zhen', c8['grp'] == 'dry', 'f10')
res['T8_means'] = {gr: gstat(c8, c8['grp'] == gr, 'f10') for gr in ['zhen', 'duan', 'dry']}

print('[7] T9...', flush=True)
mo9 = mo.copy()
mo9['chao'] = np.where(mo9['tr5'] > mo9['tr5max250'], 'chao', np.where(mo9['tr5'] < mo9['tr5q250'], 'quiet', 'mid'))
c9 = mo9.groupby(['date', 'chao']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c9 = c9[c9['n'] >= 20]
res['T9_chao_vs_quiet'] = diff_t(c9, c9['chao'] == 'chao', c9['chao'] == 'quiet', 'f10')
res['T9_means'] = {gr: gstat(c9, c9['chao'] == gr, 'f10') for gr in ['chao', 'quiet']}

print('[8] T10...', flush=True)
m['cap_tier'] = np.where(m['circ_mv'] < 500000, 'small', np.where(m['circ_mv'] <= 3000000, 'mid', 'big'))
for tier in ['small', 'mid', 'big']:
    mt = m[m['cap_tier'] == tier].copy()
    mt['tq'] = mt.groupby('date')['turnover_rate'].transform(q5)
    ct = mt.groupby(['date', 'tq']).agg(n=('code', 'size'), f20=('fwd20', 'mean')).reset_index()
    ct = ct[ct['n'] >= 30]
    ct = ct.rename(columns={'tq': 'cq'})
    res['T10_%s_hi_minus_lo' % tier] = diff_t(ct, ct['cq'] == 4, ct['cq'] == 0, 'f20')
    qm = ct.groupby('cq')['f20'].mean()
    res['T10_%s_q_means' % tier] = {int(k): round(float(v), 5) for k, v in qm.items()}

print('[9] T11...', flush=True)
sig = m[(m['turnover_rate'] > 4.5) & (m['turnover_rate'] < 5.5) & (m['volume_ratio'] > 2) & (m['volume_ratio'] < 3)].copy()
sig_g = sig[['date', 'fwd1', 'fwd1_win']].groupby('date').mean()
base_g = m[['date', 'fwd1', 'fwd1_win']].groupby('date').mean()
j = sig_g.index.join(base_g.index, how='inner')
d = sig_g.loc[j, 'fwd1'] - base_g.loc[j, 'fwd1']
res['T11_sig_minus_base_f1'] = (int(len(d)), float(d.mean()), float(stats.ttest_1samp(d, 0).statistic)) if len(d) >= 300 else (int(len(d)), None, None)
res['T11_sig_win_rate'] = float(sig['fwd1_win'].mean())
res['T11_base_win_rate'] = float(m['fwd1_win'].mean())
res['T11_sig_days'] = int(len(sig_g))

print('[10] out...', flush=True)
meta = {'rows': int(len(m)), 'days': int(m['date'].nunique()), 'range': [str(m['date'].min()), str(m['date'].max())]}
json.dump({'prereg': 'PREREG_VOLPRICE_STAGE3_20261109', 'meta': meta, 'results': res},
          open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, default=str, indent=1)
print(json.dumps(res, ensure_ascii=False, default=str, indent=1), flush=True)