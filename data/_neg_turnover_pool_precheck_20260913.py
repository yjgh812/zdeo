# -*- coding: utf-8 -*-
"""neg_turnover 大池增量预检(立项前, 按B4-TURNOVER-TILT档案要求"先回答池内增量问题").
场景: 新选股规则候选池=画像Top500(主人裁决后的池, 含高换手弹性股, 非bp_ep窄池).
设计: 近12月度截面, 每期画像v1.0.0_short打分→Top500 → 池内 turnover 分位 vs 下月收益 IC + 五分层.
对照: 同截面的全市场 IC (档案stage-1已知-0.0788全负) 与池内衰减率."""
import sys, time, sqlite3
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr
from model_v1 import FEATURES, PUBLISH_GATE

T0 = time.time()
QFQ = r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
DB = r'file:D:/zcodesj/backups/fundamental_data/weekly/fundamental_data_20260913.db?mode=ro'

panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel['date'] = panel['date'].astype(str)
all_dates = sorted(panel['date'].unique())
# 近12月度截面(每月首个交易日)
recent = [d for d in all_dates if d >= '20250901']
month_first = {}
for d in recent:
    ym = d[:6]
    if ym not in month_first:
        month_first[ym] = d
dates = sorted(month_first.values())[-13:]
print(f'月度截面 {dates[0]}~{dates[-1]} (n={len(dates)})')

# 换手数据(daily_basic, 日频)
con = sqlite3.connect(DB, uri=True, timeout=60)
tr = pd.read_sql_query("SELECT ts_code, trade_date, turnover_rate FROM daily_basic WHERE trade_date >= '20250801'", con)
con.close()
tr['code'] = tr['ts_code'].str[:6]
tr['date'] = tr['trade_date'].astype(str)
trm = tr.groupby(['code', 'date'])['turnover_rate'].mean().reset_index()

bst = lgb.Booster(model_file=r'D:\zcodesj\portrait_agent\models\v1.0.0_short.txt')

# qfq 月度收盘(前向收益)
con = sqlite3.connect(QFQ, uri=True, timeout=180)
px = pd.read_sql_query("SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= '20250815' ORDER BY code,date", con)
con.close()
px['date'] = px['date'].astype(str)
px['ym'] = px['date'].str[:6]
mc = px.sort_values('date').groupby(['code', 'ym'])['close'].last().reset_index()
mc['fwd'] = mc.groupby('code')['close'].shift(-1) / mc['close'] - 1
fwd_map = {(r['code'], r['ym']): r['fwd'] for _, r in mc.iterrows()}
trd = {c: d.set_index('date')['turnover_rate'] for c, d in trm.groupby('code')}

recs = []
for T in dates[:-1]:
    ym = T[:6]
    cs = panel[panel['date'] == T]
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    gate = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
    gate['prob'] = bst.predict(gate[FEATURES])
    top = gate.sort_values(['prob', 'turnover20'], ascending=False).head(500)
    # 池内换手(daily_basic turnover_rate, 用截面日前20日均值近似)
    d0 = (pd.Timestamp(T) - pd.Timedelta(days=30)).strftime('%Y%m%d')
    def t20(c):
        s = trd.get(c)
        if s is None: return np.nan
        w = s[(s.index >= d0) & (s.index <= T)]
        return w.mean() if len(w) >= 5 else np.nan
    rows = []
    for c in top['code']:
        rows.append({'code': c, 'turn': t20(c), 'fwd': fwd_map.get((c, ym), np.nan)})
    d = pd.DataFrame(rows).dropna()
    if len(d) < 300:
        continue
    ic = spearmanr(d['turn'], d['fwd']).statistic
    d['q'] = pd.qcut(d['turn'].rank(method='first'), 5, labels=False)
    lay = d.groupby('q')['fwd'].mean() * 100
    # 全市场对照(同截面)
    full = []
    for c in gate['code']:
        full.append({'turn': t20(c), 'fwd': fwd_map.get((c, ym), np.nan)})
    fd = pd.DataFrame(full).dropna()
    ic_full = spearmanr(fd['turn'], fd['fwd']).statistic if len(fd) > 500 else np.nan
    recs.append({'T': T, 'pool_ic': ic, 'full_ic': ic_full, 'n_pool': len(d),
                 **{f'q{i+1}': lay.get(i, np.nan) for i in range(5)}})
    print(f"  {T}: 池内IC={ic:+.4f} 全市场IC={ic_full:+.4f} 衰减={1-abs(ic)/abs(ic_full) if ic_full else 0:.0%} | 五档收益(Q1低换手..Q5高): " + ' '.join(f'{lay.get(i, np.nan):+.2f}' for i in range(5)))

r = pd.DataFrame(recs)
print(f"\n=== 汇总(n={len(r)}期) ===")
print(f"池内IC: 均值={r['pool_ic'].mean():+.4f} 中位={r['pool_ic'].median():+.4f} 负占比={(r['pool_ic']<0).mean()*100:.0f}% (负=低换手好)")
print(f"全市场IC: 均值={r['full_ic'].mean():+.4f} (档案stage-1口径-0.0788对照)")
print(f"衰减率: {1-abs(r['pool_ic'].mean())/abs(r['full_ic'].mean()):.0%}")
print(f"五档(池内Q1低换手→Q5高, 均值%): " + ' '.join(f'{r[f"q{i+1}"].mean():+.3f}' for i in range(5)))
mono = all(r[f'q{i}'].mean() >= r[f'q{i+1}'].mean() for i in range(1, 5))
print(f"单调性(Q1≥Q2≥..≥Q5): {'✅单调' if mono else '❌非单调'}")
saved = r.to_csv(r'D:\zcodesj\data\_neg_turnover_pool_precheck_20260913.csv', index=False, encoding='utf-8-sig')
print('落盘 data/_neg_turnover_pool_precheck_20260913.csv')
