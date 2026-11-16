import sqlite3, json, os
import numpy as np, pandas as pd
from scipy import stats

ZT_DB = r'D:\zcodesj\data\zt_pool_history.db'
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
OUT = r'D:\zcodesj\reports\ZT_TURNOVER_STAGE5_20261109'
os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect('file:' + ZT_DB + '?mode=ro', uri=True)
zt = pd.read_sql("SELECT date,code,name,close FROM zt_pool", con)
con.close()
zt['date'] = zt['date'].astype(str)
zt = zt[~zt['code'].str[0].isin(['4', '8', '9'])]
zt = zt[~zt['code'].str[:3].isin(['688', '689'])]
zt = zt[~zt['name'].str.contains('ST', na=False)]
zt = zt.drop_duplicates(['code', 'date'])
con = sqlite3.connect('file:' + FUND_DB + '?mode=ro', uri=True)
db1 = pd.read_sql("SELECT ts_code,trade_date,turnover_rate FROM daily_basic", con)
con.close()
db1['date'] = db1['trade_date'].astype(str)
db1['code'] = db1['ts_code'].str[:6]
db1['tr'] = pd.to_numeric(db1['turnover_rate'], errors='coerce')
db1 = db1[['code', 'date', 'tr']].dropna()
zt = zt.merge(db1, on=['code', 'date'], how='inner')
zt = zt.rename(columns={'tr': 'turnover'})
zt = zt[zt['turnover'] > 0]
con = sqlite3.connect('file:' + QFQ_DB + '?mode=ro', uri=True)
px = pd.read_sql("SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20200101'", con)
con.close()
px = px[(px['close'] > 0)]
px = px.sort_values(['code', 'date'])
px['nc'] = px.groupby('code')['close'].shift(-1)
nxt = px[['code', 'date', 'nc']].copy()
nxt['date'] = nxt['date'].astype(str)
m = zt.merge(nxt, on=['code', 'date'], how='inner')
m['turnover'] = pd.to_numeric(m['turnover'], errors='coerce')
m = m.dropna(subset=['turnover', 'nc'])
m['r1'] = m['nc'] / m['close'] - 1.0
m['down'] = (m['r1'] <= -0.09).astype(int)
m['up'] = (m['r1'] > 0).astype(int)
tiers = [('lt3', m['turnover'] < 3), ('m310', (m['turnover'] >= 3) & (m['turnover'] < 10)),
         ('m1050', (m['turnover'] >= 10) & (m['turnover'] < 50)), ('gt50', m['turnover'] >= 50)]
res = {}
for nm, cond in tiers:
    sub = m[cond]
    g = sub.groupby('date')[['r1', 'down', 'up']].mean()
    res[nm] = {'days': int(len(g)), 'win': round(float(g['up'].mean()), 4) if len(g) >= 100 else None,
               'r1': round(float(g['r1'].mean()), 5) if len(g) >= 100 else None,
               'down_rate': round(float(g['down'].mean()), 4) if len(g) >= 100 else None,
               't_r1': round(float(stats.ttest_1samp(g['r1'], 0).statistic), 2) if len(g) >= 100 else None}
def tdiff(ca, cb):
    a = m[ca].groupby('date')['r1'].mean()
    b = m[cb].groupby('date')['r1'].mean()
    j = a.index.join(b.index, how='inner')
    d = a.loc[j] - b.loc[j]
    return {'days': int(len(d)), 'diff': round(float(d.mean()), 5) if len(d) >= 300 else None,
            't': round(float(stats.ttest_1samp(d, 0).statistic), 2) if len(d) >= 300 else None}
hi = m['turnover'] >= 10
res['C1_lt3_vs_hi10'] = tdiff(m['turnover'] < 3, hi)
res['C3_gt50_vs_lt3_down'] = tdiff(m['turnover'] >= 50, m['turnover'] < 3)
meta = {'rows': int(len(m)), 'range': [str(m['date'].min()), str(m['date'].max())]}
out = {'prereg': 'PREREG_ZT_TURNOVER_STAGE5_20261109', 'meta': meta, 'results': res}
json.dump(out, open(os.path.join(OUT, 'result.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
