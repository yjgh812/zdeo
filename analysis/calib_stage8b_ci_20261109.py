import sqlite3, json, os, math
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
m['pband'] = np.where(m['pos'] <= 0.33, 'L', np.where(m['pos'] <= 0.66, 'M', 'H'))
m['tband'] = pd.cut(m['tr'], [-0.01, 1, 3, 7, 15, 30, 1e9], labels=['t01', 't13', 't37', 't715', 't1530', 't30p'])
m = m.dropna(subset=['tband'])
m['cell'] = m['pband'].astype(str) + '_' + m['tband'].astype(str)
T = m[m['date'] >= '20250101']
rows = []
for cell, sub in T.groupby('cell'):
    gg = sub.groupby('date')['fwd10'].mean().dropna()
    if len(gg) < 30:
        rows.append({'cell': cell, 'days': int(len(gg)), 'mean': None, 't': None})
        continue
    t = stats.ttest_1samp(gg, 0).statistic
    se = gg.std() / math.sqrt(len(gg))
    mu = float(gg.mean())
    rows.append({'cell': cell, 'days': int(len(gg)), 'mean': round(mu * 100, 3),
                 't': round(float(t), 2), 'ci95': [round((mu - 1.96 * se) * 100, 2), round((mu + 1.96 * se) * 100, 2)],
                 'cross_zero': bool(mu - 1.96 * se < 0 < mu + 1.96 * se)})
rows.sort(key=lambda r: -(r['mean'] or 0))
json.dump({'n_test_rows': int(len(T)), 'cells': rows}, open(os.path.join(OUT, 'reliability_oos_ci.json'), 'w'), ensure_ascii=False, indent=1)
for r in rows:
    print(r)