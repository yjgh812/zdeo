# -*- coding: utf-8 -*-
"""量能×位置 stage-1 预筛 (prereg: data/PREREG_VOLPOS_STAGE1_20261109.json, 冻结, trial_budget=1)
H1=低位放量优于低位缩量(吸筹); H2=高位放量劣于高位缩量(出逃)。只实现冻结判据, 禁调参重跑。"""
import sqlite3, json, os
import numpy as np, pandas as pd
from scipy import stats

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
ST_DB = r'D:\zcodesj\data\st_history.db'
OUT = r'D:\zcodesj\reports\VOLPOS_STAGE1_20261109'
os.makedirs(OUT, exist_ok=True)

def q(sql, db, **kw):
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    df = pd.read_sql(sql, con, **kw)
    con.close()
    return df

print('[1] read qfq...', flush=True)
px = q("SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE date>='20200101'", QFQ_DB)
px = px[(px['volume'] > 0) & (px['close'] > 0)].copy()
px['date'] = px['date'].astype(str)
px = px[~px['code'].str[0].isin(['4', '8', '9'])]
px = px[~px['code'].str[:3].isin(['688', '689'])]
print('  px rows', len(px), 'codes', px['code'].nunique(), flush=True)

print('[2] read daily_basic...', flush=True)
db1 = q("SELECT ts_code,trade_date,turnover_rate,pe_ttm,pb FROM daily_basic", FUND_DB)
db1 = db1.dropna(subset=['turnover_rate']).copy()
db1['date'] = db1['trade_date'].astype(str)
db1['code'] = db1['ts_code'].str[:6]
db1 = db1[db1['code'].str[0].isin(['0', '3', '6'])]
print('  basic rows', len(db1), 'range', db1['date'].min(), '~', db1['date'].max(), flush=True)
print('[3] ST point-in-time filter...', flush=True)
st = q("SELECT code,date FROM st_daily WHERE is_st=1", ST_DB).drop_duplicates()
st['date'] = st['date'].astype(str)
st['flag'] = 1
px = px.merge(st, on=['code', 'date'], how='left')
px = px[px['flag'].isna()].drop(columns=['flag'])
print('  px after ST', len(px), flush=True)

print('[4] merge panel...', flush=True)
m = px.merge(db1[['code', 'date', 'turnover_rate', 'pe_ttm', 'pb']], on=['code', 'date'], how='inner')
m = m.dropna(subset=['turnover_rate']).sort_values(['code', 'date']).reset_index(drop=True)
for colx in ('turnover_rate', 'pe_ttm', 'pb'):
    m[colx] = pd.to_numeric(m[colx], errors='coerce')
print('  merged', len(m), 'codes', m['code'].nunique(), 'range', m['date'].min(), '~', m['date'].max(), flush=True)
g = m.groupby('code', observed=True)
lo60 = g['close'].transform(lambda s: s.rolling(60).min())
hi60 = g['close'].transform(lambda s: s.rolling(60).max())
m['pos'] = (m['close'] - lo60) / (hi60 - lo60 + 1e-12)
m['tr5'] = g['turnover_rate'].transform(lambda s: s.rolling(5).mean())
m['tr60'] = g['turnover_rate'].transform(lambda s: s.rolling(60).mean())
m['vr'] = m['tr5'] / (m['tr60'] + 1e-12)
m['ret20'] = g['close'].transform(lambda s: s / s.shift(20) - 1.0)
m['fwd10'] = g['close'].transform(lambda s: s.shift(-11) / s.shift(-1) - 1.0 - 0.003)
m['fwd20'] = g['close'].transform(lambda s: s.shift(-21) / s.shift(-1) - 1.0 - 0.003)
m = m.dropna(subset=['pos', 'vr', 'fwd10', 'fwd20', 'ret20'])
print('  feature rows', len(m), flush=True)

bp = np.where(m['pb'] > 0, 1.0 / m['pb'], 0.0)
ep = np.where(m['pe_ttm'] > 0, 1.0 / m['pe_ttm'], 0.0)
m['bp'] = bp
m['ep'] = ep

print('[5] FM cells (pos x vr quintiles)...', flush=True)
def q5(s):
    """rank-based五分位(0..4), NaN安全"""
    r = s.rank(pct=True)
    return (np.ceil(r * 5 - 1e-9) - 1).where(r.notna()).astype('Int64')

m['pq'] = m.groupby('date')['pos'].transform(q5)
m['vq'] = m.groupby('date')['vr'].transform(q5)
m = m.dropna(subset=['pq', 'vq'])
cell = m.groupby(['date', 'pq', 'vq']).agg(
    n=('code', 'size'),
    f10=('fwd10', 'mean'),
    f20=('fwd20', 'mean')).reset_index()
cell = cell[cell['n'] >= 50]
print('  cells', len(cell), flush=True)

def _ds(cell, cond, col):
    return cell[cond][['date', col]].dropna().set_index('date')[col]

def fm(cell, col='f10'):
    """5x5 单元格: (日数, 均值, 跨日t)"""
    out = {}
    for p in range(5):
        for v in range(5):
            s = _ds(cell, (cell['pq'] == p) & (cell['vq'] == v), col)
            if len(s) < 300:
                out[(p, v)] = (len(s), None, None)
            else:
                out[(p, v)] = (len(s), float(s.mean()), float(stats.ttest_1samp(s, 0).statistic))
    return out

def diff_t(a, b, cell, col='f10'):
    sa = _ds(cell, (cell['pq'] == a[0]) & (cell['vq'] == a[1]), col)
    sb = _ds(cell, (cell['pq'] == b[0]) & (cell['vq'] == b[1]), col)
    j = sa.index.join(sb.index, how='inner')
    d = sa.loc[j] - sb.loc[j]
    if len(d) < 300:
        return len(d), None, None
    return len(d), float(d.mean()), float(stats.ttest_1samp(d, 0).statistic)

print('[6] criteria...', flush=True)
res = {}
res['H1_f10'] = diff_t((0, 4), (0, 0), cell, 'f10')
res['H1_f20'] = diff_t((0, 4), (0, 0), cell, 'f20')
res['H2_f10'] = diff_t((4, 4), (4, 0), cell, 'f10')
res['H2_f20'] = diff_t((0, 4) if False else (4, 4), (4, 0), cell, 'f20')
res['baseline_f10'] = (len(cell), float(cell.groupby('date')['f10'].mean().mean()), float(stats.ttest_1samp(cell.groupby('date')['f10'].mean().dropna(), 0).statistic))

# C3 分年 (日期对齐)
cell['year'] = cell['date'].str[:4]
yearly = {}
for y, yy in cell.groupby('year'):
    sa = _ds(yy, (yy['pq'] == 0) & (yy['vq'] == 4), 'f10')
    sb = _ds(yy, (yy['pq'] == 0) & (yy['vq'] == 0), 'f10')
    j = sa.index.join(sb.index, how='inner')
    d = sa.loc[j] - sb.loc[j]
    yearly[y] = {'days': int(len(d)), 'diff': round(float(d.mean()), 5) if len(d) else None}

res['yearly_H1'] = yearly
vals = [v['diff'] for v in yearly.values() if v['diff'] is not None]
res['yearly_pos_count'] = int(sum(1 for v in vals if v > 0))
res['yearly_total'] = len(vals)

print('[7] pool increment...', flush=True)
m['ve'] = m['bp'] + m['ep']
def zscore(x):
    return (x - x.mean()) / (x.std() + 1e-12)
m['ve_z'] = m.groupby('date')['ve'].transform(zscore)
m['pool'] = m['ve_z'] >= m.groupby('date')['ve_z'].transform(lambda s: s.quantile(0.70))
mp = m[m['pool']].copy()
c2 = mp.groupby(['date', 'pq', 'vq']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c2 = c2[c2['n'] >= 20]
res['H1_pool_f10'] = diff_t((0, 4), (0, 0), c2, 'f10')

print('[8] orthogonal...', flush=True)
mo = m[m['pq'] == 0].copy()
mo['rq'] = mo.groupby('date')['ret20'].transform(q5)
mo = mo.dropna(subset=['rq'])
mo['vq2'] = mo.groupby(['date', 'rq'])['vr'].transform(q5)
mo = mo.dropna(subset=['vq2'])
mo['vq2'] = mo['vq2'].astype(int)
mo['pq'] = 0
c3 = mo.assign(vq=mo['vq2']).groupby(['date', 'pq', 'vq']).agg(n=('code', 'size'), f10=('fwd10', 'mean')).reset_index()
c3 = c3[c3['n'] >= 30]
res['H1_orth_f10'] = diff_t((0, 4), (0, 0), c3, 'f10')

print('[9] output...', flush=True)
meta = {
    'panel_rows': int(len(m)), 'days': int(cell['date'].nunique()),
    'date_range': [str(m['date'].min()), str(m['date'].max())],
    'codes': int(m['code'].nunique()),
}
out = {'prereg': 'PREREG_VOLPOS_STAGE1_20261109', 'meta': meta, 'results': {str(k): v for k, v in res.items()},
       'grid_f10': {f'pq{p}_vq{v}': (int(g0), round(g1, 5) if g1 is not None else None, round(g2, 2) if g2 is not None else None)
                     for (p, v), (g0, g1, g2) in fm(cell, 'f10').items()},
       'grid_f20': {f'pq{p}_vq{v}': (int(g0), round(g1, 5) if g1 is not None else None, round(g2, 2) if g2 is not None else None)
                     for (p, v), (g0, g1, g2) in fm(cell, 'f20').items()}}
json.dump(out, open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])