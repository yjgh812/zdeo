# -*- coding: utf-8 -*-
"""画像预测器 v0: LightGBM fwd20 回归, 月度截面 walk-forward, 组合级KPI(vs全市场等权).
PREREG_PROFILE_PREDICTOR_20260905 合规实现: 可执行标签(收盘口径+涨停不入组合)+组合级净值KPI+影子条款.
数据: qfq真库(2019-06起预热) + daily_basic(PIT快照) + industry_map_ths. 只读研究, 零生产接线.
"""
import sqlite3, json, os, sys, time
import numpy as np
import pandas as pd
import lightgbm as lgb

sys.stdout.reconfigure(encoding='utf-8')
T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

QFQ_DB  = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
OUT = r'D:\zcodesj\attribution\winner_profile_out\predictor'
os.makedirs(OUT, exist_ok=True)
TRAD_PREFIX = ('600','601','603','605','000','001','002','003','300','301','302')
CHINEXT = ('300','301','302')
WARM = '20190601'

# ---------- 数据 ----------
log('load qfq')
con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
alive = [r[0] for r in con.execute(
    "SELECT code FROM stock_daily_meta_qfq WHERE date>='20260830' GROUP BY code"
) if r[0][:3] in TRAD_PREFIX]
ph = ','.join('?'*len(alive))
qfq = pd.read_sql_query(
    f"SELECT code,date,close,high,low,change_pct,amount FROM stock_daily_meta_qfq "
    f"WHERE date>='{WARM}' AND code IN ({ph})", con, params=alive)
con.close()
log(f'qfq rows={len(qfq)}')

log('load daily_basic + industry')
con2 = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
db = pd.read_sql_query(
    "SELECT ts_code,trade_date,total_mv,circ_mv,turnover_rate,pe_ttm,pb FROM daily_basic WHERE trade_date>='20191101'", con2)
ind = pd.read_sql_query("SELECT code,name,industry FROM industry_map_ths", con2)
con2.close()
db['code'] = db['ts_code'].str[:6]
for c in ['total_mv','circ_mv','turnover_rate','pe_ttm','pb']:
    db[c] = pd.to_numeric(db[c], errors='coerce')
ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]
ind['is_st'] = ind['name'].fillna('').str.contains('ST') | ind['name'].fillna('').str.contains('退')
st_set = set(ind.loc[ind['is_st'], 'code'])
l1_map = dict(zip(ind['code'], ind['L1']))
db_by_code = {c: x.set_index('trade_date')[['total_mv','circ_mv','turnover_rate','pe_ttm','pb']]
              for c, x in db.groupby('code')}

# ---------- 特征panel(复用画像函数) ----------
def ema_sma_seed(a, p):
    out = np.full(len(a), np.nan)
    if len(a) < p: return out
    out[p-1] = a[:p].mean(); m = 2.0/(p+1)
    for i in range(p, len(a)):
        out[i] = a[i]*m + out[i-1]*(1-m)
    return out
def macd_skill(c):
    n = len(c); e12 = ema_sma_seed(c,12); e26 = ema_sma_seed(c,26)
    dif = np.full(n, np.nan)
    if n >= 26: dif[25:] = e12[25:]-e26[25:]
    d9 = ema_sma_seed(dif[25:],9) if n>=26 else None
    dea = np.full(n, np.nan)
    if d9 is not None and len(d9)>=9 and not np.isnan(d9[8]):
        dea[33:] = d9[8:]
    return dif, dea
def zigzag_pivots(close, thr=0.05):
    piv = []
    if len(close) < 3: return piv
    direction = 0; last_p = close[0]; last_i = 0
    for i in range(1, len(close)):
        p = close[i]
        if direction >= 0 and p < last_p*(1-thr):
            if direction > 0 or last_i == 0: piv.append(last_i)
            direction = -1; last_p = p; last_i = i
        elif direction <= 0 and p > last_p*(1+thr):
            if direction < 0 or last_i == 0: piv.append(last_i)
            direction = 1; last_p = p; last_i = i
        else:
            if (direction > 0 and p > last_p) or (direction < 0 and p < last_p):
                last_p = p; last_i = i
    piv.append(last_i)
    return piv

FEATURES = ['log_mv','log_cmv','turnover20','ret20','ret60','vol60','amp20','pos250','dist_hi',
            'bias20','nh20_cnt','lim_cnt250','lim_cnt60','dif','hist','bars_golden','dif_pos60',
            'zz_cnt250','zhongshu60','pos_zs','pb','pe_ttm','amt5_amt20','maxstreak']
qfq = qfq.sort_values(['code','date'])
log('build monthly cross-section feature panel')
all_dates = sorted(qfq['date'].unique())
month_last = {}
for d in all_dates:
    month_last[d[:6]] = d            # 每月最后交易日
cs_dates = sorted(month_last.values())
cs_dates = [d for d in cs_dates if d >= '20200701']   # 预热后首个截面
log(f'cross-section dates: {len(cs_dates)} ({cs_dates[0]}~{cs_dates[-1]})')

rows = []
q_groups = {c: g for c, g in qfq.groupby('code', sort=False)}
for k, (code, g) in enumerate(q_groups.items()):
    if k % 900 == 0: log(f'  stock {k}/{len(q_groups)}')
    g = g.drop_duplicates('date').reset_index(drop=True)
    c = g['close'].values.astype(float)
    hi = g['high'].values.astype(float); lo = g['low'].values.astype(float)
    chg = g['change_pct'].values.astype(float); amt = g['amount'].values.astype(float)
    dt = g['date'].values
    n = len(c)
    if n < 160: continue
    s = pd.Series(c)
    ret = s.pct_change()
    lim_thr = 19.8 if code[:3] in CHINEXT else 9.8
    lim = chg >= lim_thr
    streak = np.zeros(n); mx = 0.0; cur = 0
    for i in range(n):
        cur = cur+1 if lim[i] else 0
        if cur > mx: mx = cur
        streak[i] = mx
    dif, dea = macd_skill(c)
    pos_ = dif > dea
    cross = np.where(pos_ & ~np.roll(pos_,1))[0]; cross = cross[cross>0]
    piv = zigzag_pivots(c); piv_arr = np.array(piv)
    amp = (hi-lo)/np.roll(c,1); amp[0] = np.nan
    med120 = s.rolling(120, min_periods=60).median()
    inband = (np.abs(s-med120) <= med120*0.075).astype(float)
    ma20 = s.rolling(20).mean()
    hi250 = s.rolling(250, min_periods=60).max(); lo250 = s.rolling(250, min_periods=60).min()
    hi20 = s.rolling(20).max()
    vol60 = ret.rolling(60).std()
    b = db_by_code.get(code)
    turn20_series = None; fund_map = {}
    if b is not None and len(b):
        tb = b['turnover_rate']
        turn20_series = pd.Series(tb.reindex(dt).rolling(20, min_periods=10).mean().values, index=np.arange(n))
        for col in ['total_mv','circ_mv','pe_ttm','pb']:
            fund_map[col] = b[col].reindex(dt).ffill(limit=7).values
    cs_idx = {d: i for i, d in enumerate(dt)}
    for d in cs_dates:
        j = cs_idx.get(d)
        if j is None or j < 150: continue
        mv = fund_map.get('total_mv')
        if mv is None or np.isnan(mv[j]): continue
        last_ci = cross[cross <= j]
        rows.append({'code': code, 'date': d, 'fwd20': (c[j+20]/c[j]-1) if j+20 < n else np.nan,
            'lim_today': bool(lim[j]), 'close': c[j], 'L1': l1_map.get(code, '未知'),
            'log_mv': np.log10(mv[j]), 'log_cmv': np.log10(fund_map['circ_mv'][j]) if pd.notna(fund_map['circ_mv'][j]) else np.nan,
            'turnover20': turn20_series.iloc[j] if turn20_series is not None else np.nan,
            'ret20': c[j]/c[j-20]-1 if j>=20 else np.nan, 'ret60': c[j]/c[j-60]-1 if j>=60 else np.nan,
            'vol60': vol60.iloc[j], 'amp20': float(pd.Series(amp).rolling(20).mean().iloc[j]),
            'pos250': (c[j]-lo250.iloc[j])/(hi250.iloc[j]-lo250.iloc[j]),
            'dist_hi': c[j]/hi250.iloc[j]-1, 'bias20': c[j]/ma20.iloc[j]-1,
            'nh20_cnt': float((s.rolling(20).max()==s).rolling(20).sum().iloc[j]),
            'lim_cnt250': float(pd.Series(lim.astype(float)).rolling(250,min_periods=60).sum().iloc[j]),
            'lim_cnt60': float(pd.Series(lim.astype(float)).rolling(60,min_periods=20).sum().iloc[j]),
            'dif': dif[j], 'hist': (dif[j]-dea[j]) if not np.isnan(dea[j]) else np.nan,
            'bars_golden': (j-last_ci.max()) if len(last_ci) else np.nan,
            'dif_pos60': float(pd.Series(np.where(np.isnan(dif), np.nan, (dif>0).astype(float))).rolling(60,min_periods=30).mean().iloc[j]),
            'zz_cnt250': float(np.searchsorted(piv_arr, j, 'right') - np.searchsorted(piv_arr, max(j-249,0), 'left')),
            'zhongshu60': float(inband.rolling(60,min_periods=30).mean().iloc[j]),
            'pos_zs': c[j]/med120.iloc[j]-1 if pd.notna(med120.iloc[j]) else np.nan,
            'pb': fund_map['pb'][j], 'pe_ttm': fund_map['pe_ttm'][j],
            'amt5_amt20': (pd.Series(amt).rolling(5).mean().iloc[j]/pd.Series(amt).rolling(20).mean().iloc[j]) if j>=20 and pd.Series(amt).rolling(20).mean().iloc[j] > 0 else np.nan,
            'maxstreak': streak[j]})
panel = pd.DataFrame(rows)
panel = panel[~panel['code'].isin(st_set)]
log(f'panel: {len(panel)} rows, {panel["date"].nunique()} sections, codes={panel["code"].nunique()}')

# ---------- Walk-forward ----------
OOS_START = '20250101'
params = dict(objective='regression', max_depth=6, num_leaves=31, learning_rate=0.05,
              n_estimators=300, subsample=0.8, colsample_bytree=0.8, verbose=-1, n_jobs=4)
oos_dates = [d for d in cs_dates if d >= OOS_START and d <= '20260831']
res = []
for d in oos_dates:
    train = panel[(panel['date'] < d) & panel['fwd20'].notna()]   # expanding: 截至上月末全部已实现截面
    test = panel[panel['date'] == d].copy()
    if len(test) < 1000: continue
    m = lgb.LGBMRegressor(**params)
    m.fit(train[FEATURES], train['fwd20'], categorical_feature=[])
    test['pred'] = m.predict(test[FEATURES])
    # 组合: Top20, 剔涨停/停牌/>30元/无fwd20
    ok = test[(~test['lim_today']) & (test['close'] <= 30) & test['fwd20'].notna()]
    top = ok.nlargest(20, 'pred')
    port = top['fwd20'].mean()
    base = ok['fwd20'].mean()          # 全市场等权(同过滤宇宙)
    res.append({'date': d, 'n': len(ok), 'port': port, 'base': base, 'ex': port-base,
                'top_codes': [(r.code, r.L1, round(r.pred*100,2)) for r in top.itertuples()][:5]})
    log(f'  {d}: port={port*100:+.2f}% base={base*100:+.2f}% ex={(port-base)*100:+.2f}% n={len(ok)}')
wf = pd.DataFrame(res)
wf['port_nav'] = (1+wf['port']).cumprod()
wf['base_nav'] = (1+wf['base']).cumprod()
ann = lambda r: (r.prod())**(12/len(r))-1
mdd = lambda nav: (nav/nav.cummax()-1).min()
fees = 0.0015*2
ann_port_f = ((wf['port']-fees).prod())**(12/len(wf))-1
print(f"\n== OOS {wf['date'].iloc[0]}~{wf['date'].iloc[-1]} n={len(wf)}月 ==")
print(f"组合年化(费前) {ann(wf['port'])*100:+.2f}% | 费后 {ann_port_f*100:+.2f}% | 基线等权年化 {ann(wf['base'])*100:+.2f}%")
print(f"年化超额(费前) {(ann(wf['port'])-ann(wf['base']))*100:+.2f}pp | MDD 组合 {mdd(wf['port_nav'])*100:.2f}% vs 基线 {mdd(wf['base_nav'])*100:.2f}%")
wf['year'] = wf['date'].str[:4]
print("分年超额:", wf.groupby('year')['ex'].apply(lambda x: f"mean{x.mean()*100:+.2f}%/月, {(x>0).mean()*100:.0f}%月为正").to_dict())
print(f"月胜率(超额>0): {(wf['ex']>0).mean()*100:.0f}%")
wf.drop(columns=['top_codes']).to_csv(os.path.join(OUT, 'wf_oos_v0.csv'), index=False, encoding='utf-8-sig')
json.dump({'oos_start': OOS_START, 'n_months': len(wf),
           'ann_port': ann(wf['port']), 'ann_base': ann(wf['base']),
           'ann_port_fee': ann_port_f, 'mdd_port': mdd(wf['port_nav']), 'mdd_base': mdd(wf['base_nav'])},
          open(os.path.join(OUT, 'wf_oos_v0_summary.json'), 'w'), default=float)
log('DONE')
