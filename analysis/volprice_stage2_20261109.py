# -*- coding: utf-8 -*-
"""量价stage-2 (prereg: data/PREREG_VOLPRICE_STAGE2_20261109.json, 冻结, 每检验trial_budget=1)
T1=CTC低CTC更优; T2=低位堆量vs脉冲; T3=红肥绿瘦; T4=高位放量滞涨"""
import sqlite3, json, os
import numpy as np, pandas as pd
from scipy import stats

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
ST_DB = r'D:\zcodesj\data\st_history.db'
OUT = r'D:\zcodesj\reports\VOLPRICE_STAGE2_20261109'
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
db1 = q("SELECT ts_code,trade_date,turnover_rate FROM daily_basic", FUND_DB)
db1 = db1.dropna(subset=['turnover_rate']).copy()
db1['date'] = db1['trade_date'].astype(str)
db1['code'] = db1['ts_code'].str[:6]
db1 = db1[db1['code'].str[0].isin(['0', '3', '6'])]

print('[3] ST...', flush=True)
st = q("SELECT code,date FROM st_daily WHERE is_st=1", ST_DB).drop_duplicates()
st['date'] = st['date'].astype(str)
st['flag'] = 1
px = px.merge(st, on=['code', 'date'], how='left')
px = px[px['flag'].isna()].drop(columns=['flag'])

print('[4] merge...', flush=True)
m = px.merge(db1[['code', 'date', 'turnover_rate']], on=['code', 'date'], how='inner')
m['turnover_rate'] = pd.to_numeric(m['turnover_rate'], errors='coerce')
m = m.dropna(subset=['turnover_rate']).sort_values(['code', 'date']).reset_index(drop=True)
g = m.groupby('code', observed=True)
lo60 = g['close'].transform(lambda s: s.rolling(60).min())
hi60 = g['close'].transform(lambda s: s.rolling(60).max())
m['pos'] = (m['close'] - lo60) / (hi60 - lo60 + 1e-12)
m['tr5'] = g['turnover_rate'].transform(lambda s: s.rolling(5).mean())
m['tr60'] = g['turnover_rate'].transform(lambda s: s.rolling(60).mean())
m['vr'] = m['tr5'] / (m['tr60'] + 1e-12)
m['ret20'] = g['close'].transform(lambda s: s / s.shift(20) - 1.0)
m['ret5'] = g['close'].transform(lambda s: s / s.shift(5) - 1.0)
m['dr'] = g['close'].transform(lambda s: s / s.shift(1) - 1.0)
m['fwd10'] = g['close'].transform(lambda s: s.shift(-11) / s.shift(-1) - 1.0 - 0.003)
m['fwd20'] = g['close'].transform(lambda s: s.shift(-21) / s.shift(-1) - 1.0 - 0.003)
m = m.dropna(subset=['pos', 'vr', 'fwd10', 'fwd20', 'ret5'])
print('  feature rows', len(m), flush=True)

print('[5] quintiles...', flush=True)
def q5(s):
    r = s.rank(pct=True)
    return (np.ceil(r * 5 - 1e-9) - 1).where(r.notna()).astype('Int64')
m['pq'] = m.groupby('date')['pos'].transform(q5)
m['vq'] = m.groupby('date')['vr'].transform(q5)
m = m.dropna(subset=['pq', 'vq'])
g = m.groupby('code', observed=True)
print('[6] extra features...', flush=True)
# hot days (近5日 tr>1.5x tr60)
m['hot'] = (m['turnover_rate'] > 1.5 * m['tr60']).astype(int)
m['hot5'] = g['hot'].transform(lambda s: s.rolling(5).sum())
# red-green log ratio 近10日
m['up_vol'] = np.where(m['dr'] > 0, m['volume'], 0.0)
m['dn_vol'] = np.where(m['dr'] < 0, m['volume'], 0.0)
m['upv10'] = g['up_vol'].transform(lambda s: s.rolling(10).sum())
m['dnv10'] = g['dn_vol'].transform(lambda s: s.rolling(10).sum())
m['rg'] = np.log(m['upv10'] + 1.0) - np.log(m['dnv10'] + 1.0)
# CTC: 20日corr(close, turnover) 标准化积法
cm = g['close'].transform(lambda s: s.rolling(20).mean())
cs = g['close'].transform(lambda s: s.rolling(20).std())
tm = g['turnover_rate'].transform(lambda s: s.rolling(20).mean())
ts_ = g['turnover_rate'].transform(lambda s: s.rolling(20).std())
prod = (m['close'] - cm) * (m['turnover_rate'] - tm)
m['ctc'] = prod.groupby(m['code']).transform(lambda s: s.rolling(20).mean()) / (cs * ts_ + 1e-12)
m = m.dropna(subset=['ctc', 'hot5', 'upv10'])
m = m[m['dnv10'] > 0]
print('  rows after extras', len(m), flush=True)

print('[7] T1 CTC...', flush=True)
def _ds(x, cond, col):
    return x[cond][['date', col]].dropna().set_index('date')[col]

def spread_t(x, col, lo=0, hi=4):
    """Q(标签lo)-Q(标签hi) 逐日差, 跨日t (0=最低五分位,4=最高)"""
    a = _ds(x, x['cq'] == lo, col)
    b = _ds(x, x['cq'] == hi, col)
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    if len(d) < 300:
        return len(d), None, None
    return len(d), float(d.mean()), float(stats.ttest_1samp(d, 0).statistic)

def gstat(x, cond, col):
    s = _ds(x, cond, col)
    if len(s) < 300:
        return int(len(s)), None, None
    return int(len(s)), float(s.mean()), float(stats.ttest_1samp(s, 0).statistic)

res = {}
m['cq'] = m.groupby('date')['ctc'].transform(q5)
c1 = m.groupby(['date', 'cq']).agg(n=('code', 'size'), f20=('fwd20', 'mean'), f10=('fwd10', 'mean')).reset_index()
c1 = c1[c1['n'] >= 50]
res['T1_spread_f20'] = spread_t(c1, 'f20')
res['T1_spread_f10'] = spread_t(c1, 'f10')
qq = c1.groupby('cq')[['f20']].mean()
res['T1_q_means_f20'] = {int(k): round(float(v), 5) for k, v in qq['f20'].items()}
c1['year'] = c1['date'].str[:4]
yly = {}
for y, yy in c1.groupby('year'):
    a = _ds(yy, yy['cq'] == 0, 'f20')
    b = _ds(yy, yy['cq'] == 4, 'f20')
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    yly[y] = round(float(d.mean()), 5) if len(d) >= 20 else None
res['T1_yearly_Q1mQ5_f20'] = yly
print(json.dumps(res, ensure_ascii=False, default=str), flush=True)

print('[8] T2/T3/T4...', flush=True)
mo = m[m['pq'] == 0].copy()
mo['grp'] = np.where(mo['hot5'] >= 3, 'dui', np.where(mo['hot5'] >= 1, 'pulse', 'dry'))
c2 = mo.groupby(['date', 'grp']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c2 = c2[c2['n'] >= 20]
def pair_t(x, ga, gb, col='f10'):
    a = _ds(x, x['grp'] == ga, col)
    b = _ds(x, x['grp'] == gb, col)
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    if len(d) < 300:
        return int(len(d)), None, None
    return int(len(d)), float(d.mean()), float(stats.ttest_1samp(d, 0).statistic)
res['T2_dui_minus_pulse'] = pair_t(c2, 'dui', 'pulse')
res['T2_dui_minus_dry'] = pair_t(c2, 'dui', 'dry')
res['T2_group_means'] = {gr: gstat(c2, c2['grp'] == gr, 'f10') for gr in ['dui', 'pulse', 'dry']}

mo3 = mo.copy()
mo3['rgq'] = mo3.groupby('date')['rg'].transform(q5)
c3 = mo3.groupby(['date', 'rgq']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c3 = c3[c3['n'] >= 50]
res['T3_top_minus_bottom'] = spread_t(c3.rename(columns={'rgq': 'cq'}), 'f10')
mo4 = m[(m['pq'] == 4) & (m['vq'] == 4)].copy()
a4 = mo4[mo4['ret5'] <= 0][['date', 'fwd10']].groupby('date').mean()
res['T4_stall_mean'] = (int(len(a4)), float(a4['fwd10'].mean()), float(stats.ttest_1samp(a4['fwd10'], 0).statistic)) if len(a4) >= 300 else (int(len(a4)), None, None)
b4 = mo4[mo4['ret5'] > 0][['date', 'fwd10']].groupby('date').mean()
j4 = a4.index.join(b4.index, how='inner')
d4 = a4.loc[j4, 'fwd10'] - b4.loc[j4, 'fwd10']
res['T4_stall_minus_rally'] = (int(len(d4)), float(d4.mean()), float(stats.ttest_1samp(d4, 0).statistic)) if len(d4) >= 300 else (int(len(d4)), None, None)

print('[9] out...', flush=True)
meta = {'rows': int(len(m)), 'days': int(m['date'].nunique()), 'range': [str(m['date'].min()), str(m['date'].max())]}
json.dump({'prereg': 'PREREG_VOLPRICE_STAGE2_20261109', 'meta': meta, 'results': res},
          open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, default=str, indent=1)
print(json.dumps(res, ensure_ascii=False, default=str, indent=1), flush=True)