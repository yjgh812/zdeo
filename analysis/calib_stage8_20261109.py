import sqlite3, json, os
import numpy as np, pandas as pd
from scipy import stats

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
ST_DB = r'D:\zcodesj\data\st_history.db'
OUT = r'D:\zcodesj\reports\CALIB_STAGE8_20261109'
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
lo60 = g['close'].transform(lambda s: s.rolling(60).min())
hi60 = g['close'].transform(lambda s: s.rolling(60).max())
m['pos'] = (m['close'] - lo60) / (hi60 - lo60 + 1e-12)
m['fwd10'] = g['close'].transform(lambda s: s.shift(-11) / s.shift(-1) - 1.0 - 0.003)
m = m.dropna(subset=['pos', 'fwd10'])
m['win'] = (m['fwd10'] > 0).astype(int)
m['pband'] = np.where(m['pos'] <= 0.33, 'L', np.where(m['pos'] <= 0.66, 'M', 'H'))
m['tband'] = pd.cut(m['tr'], [-0.01, 1, 3, 7, 15, 30, 1e9], labels=['t01', 't13', 't37', 't715', 't1530', 't30p'])
m = m.dropna(subset=['tband'])
m['cell'] = m['pband'].astype(str) + '_' + m['tband'].astype(str)
m['yearmonth'] = m['date'].str[:6]
TRAIN = m[m['date'] < '20250101']
TEST = m[m['date'] >= '20250101']
ptr = TRAIN.groupby('cell').agg(pred_win=('win', 'mean'), pred_mean=('fwd10', 'mean'), n_train=('win', 'size')).reset_index()
dte = TEST.groupby('cell').agg(real_win=('win', 'mean'), real_mean=('fwd10', 'mean'), n_test=('win', 'size')).reset_index()
tab = ptr.merge(dte, on='cell', how='inner')
w = tab['n_test'] / tab['n_test'].sum()
res = {}
res['ECE'] = round(float((w * (tab['pred_win'] - tab['real_win']).abs()).sum()), 4)
brier_terms = TEST.merge(tab[['cell', 'pred_win']], on='cell', how='inner')
res['Brier'] = round(float(((brier_terms['pred_win'] - brier_terms['win']) ** 2).mean()), 4)
res['dir_match_rate'] = round(float(((np.sign(tab['pred_mean']) == np.sign(tab['real_mean']))).mean()), 3)
res['n_cells'] = int(len(tab))
res['table'] = tab.round(4).to_dict('records')
poison = tab[tab['cell'] == 'H_t1530']
best = tab[tab['cell'] == 'H_t01']
res['C3_poison_real'] = {'mean': float(poison['real_mean'].iloc[0]) if len(poison) else None,
                         't': None}
if len(poison):
    sub = TEST[TEST['cell'] == 'H_t1530']
    gg = sub.groupby('date')['fwd10'].mean()
    res['C3_poison_real']['t'] = round(float(stats.ttest_1samp(gg, 0).statistic), 2)
res['C4_best_real'] = {'mean': float(best['real_mean'].iloc[0]) if len(best) else None, 't': None}
if len(best):
    sub = TEST[TEST['cell'] == 'H_t01']
    gg = sub.groupby('date')['fwd10'].mean()
    res['C4_best_real']['t'] = round(float(stats.ttest_1samp(gg, 0).statistic), 2)
moy = TEST.groupby('yearmonth')['win'].mean()
moy_all = m.groupby('yearmonth')['win'].mean()
res['monthly_base_rate'] = {k: round(float(v), 3) for k, v in moy_all.items()}
meta = {'train_rows': int(len(TRAIN)), 'test_rows': int(len(TEST)),
        'train_range': [str(TRAIN['date'].min()), str(TRAIN['date'].max())],
        'test_range': [str(TEST['date'].min()), str(TEST['date'].max())]}
out = {'prereg': 'PREREG_CALIB_STAGE8_20261109', 'meta': meta, 'results': res}
json.dump(out, open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in out.items() if k != 'results'}, ensure_ascii=False, indent=1))
print({k: v for k, v in res.items() if k not in ('table', 'monthly_base_rate')})
print('monthly_base_rate(近24个月):', dict(list(res['monthly_base_rate'].items())[-24:]))