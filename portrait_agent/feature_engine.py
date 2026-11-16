# -*- coding: utf-8 -*-
"""五维特征引擎(计划书§2.2步骤1/3): 沪深A股含科创板, 近3年(750交易日)全量特征.
股票池规则(计划书§1.2/§2.2): 剔北交所/ST/次新<60bar/收盘>20元/流动性(20日均额≥500万).
数据: qfq真库(价格) + fundamental_data.db daily_basic(市值/换手/估值, 科创板该源缺失置NaN)
      + industry_map_ths(名称/行业). 北向源停采(2026-09-01裁决)→该维降级跳过, 自检标注."""
import sqlite3, os, sys, time
import numpy as np
import pandas as pd

QFQ_DB  = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
TRAD_PREFIX = ('600','601','603','605','000','001','002','003','300','301','302','688','689')  # 含科创板(计划书§1.2)
CHINEXT = ('300','301','302'); STAR = ('688','689')
PRICE_CAP = 20.0            # 计划书§1.2 价格约束
MIN_BARS = 60               # 上市满60交易日(计划书§1.2)
MIN_AMT20 = 500e4           # 20日均成交额≥500万(计划书§2.2)
WARM = '20230101'           # 预热: 3年窗口+指标预热

def ema_sma_seed(a, p):
    out = np.full(len(a), np.nan)
    if len(a) < p: return out
    out[p-1] = a[:p].mean(); m = 2.0/(p+1)
    for i in range(p, len(a)):
        out[i] = a[i]*m + out[i-1]*(1-m)
    return out

def macd(c):
    n = len(c); e12 = ema_sma_seed(c,12); e26 = ema_sma_seed(c,26)
    dif = np.full(n, np.nan)
    if n >= 26: dif[25:] = e12[25:]-e26[25:]
    d9 = ema_sma_seed(dif[25:],9) if n>=26 else None
    dea = np.full(n, np.nan)
    if d9 is not None and len(d9)>=9 and not np.isnan(d9[8]):
        dea[33:] = d9[8:]
    return dif, dea

def board(code):
    if code[:3] in ('600','601','603','605'): return '主板'
    if code[:3] in ('000','001','002','003'): return '主板'
    if code[:3] in CHINEXT: return '创业板'
    if code[:3] in STAR: return '科创板'
    return '其他'

FEATURES = ['log_mv','log_cmv','turnover20','ret5','ret20','ret60','vol20','vol60','amp20',
            'pos250','dist_hi','bias20','nh20_cnt','lim_cnt250','dif','hist','bars_golden',
            'dif_pos60','amt5_amt20','maxstreak','pb','pe_ttm','loss']

def build(warm=WARM, end=None, log=print):
    """返回 per-stock daily 特征长表(含标签占位由 model_v1 填) + 股票池过滤."""
    t0 = time.time()
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    end_sql = end or con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    alive = [r[0] for r in con.execute(
        "SELECT code FROM stock_daily_meta_qfq WHERE date>=? GROUP BY code", (end_sql,)
    ) if r[0][:3] in TRAD_PREFIX]
    ph = ','.join('?'*len(alive))
    qfq = pd.read_sql_query(
        f"SELECT code,date,close,high,low,change_pct,amount FROM stock_daily_meta_qfq "
        f"WHERE date>=? AND date<=? AND code IN ({ph})", con, params=[warm, end_sql]+alive)
    con.close()
    log(f'[engine] qfq rows={len(qfq)} end={end_sql}')
    con2 = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    db = pd.read_sql_query(
        "SELECT ts_code,trade_date,total_mv,circ_mv,turnover_rate,pe_ttm,pb FROM daily_basic WHERE trade_date>=?",
        con2, params=[warm])
    ind = pd.read_sql_query("SELECT code,name,industry FROM industry_map_ths", con2)
    con2.close()
    db['code'] = db['ts_code'].str[:6]
    for c_ in ['total_mv','circ_mv','turnover_rate','pe_ttm','pb']:
        db[c_] = pd.to_numeric(db[c_], errors='coerce')
    ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]
    ind['is_st'] = ind['name'].fillna('').str.contains('ST') | ind['name'].fillna('').str.contains('退')
    st_set = set(ind.loc[ind['is_st'], 'code'])
    l1_map = dict(zip(ind['code'], ind['L1']))
    db_by_code = {c_: x.set_index('trade_date') for c_, x in db.groupby('code')}

    parts = []
    qfq = qfq.sort_values(['code','date'])
    for k, (code, g) in enumerate(qfq.groupby('code', sort=False)):
        if k % 800 == 0: log(f'[engine] stock {k}/{len(qfq.groupby("code", sort=False))} {time.time()-t0:.0f}s')
        g = g.drop_duplicates('date').reset_index(drop=True)
        c = g['close'].values.astype(float)
        hi = g['high'].values.astype(float); lo = g['low'].values.astype(float)
        chg = g['change_pct'].values.astype(float); amt = g['amount'].values.astype(float)
        n = len(c)
        if n < MIN_BARS + 60: continue
        s = pd.Series(c)
        ret = s.pct_change()
        lim_thr = 19.8 if (code[:3] in CHINEXT or code[:3] in STAR) else 9.8   # 科创板20cm
        lim = chg >= lim_thr
        streak = np.zeros(n); mx = 0.0; cur = 0
        for i in range(n):
            cur = cur+1 if lim[i] else 0
            if cur > mx: mx = cur
            streak[i] = mx
        dif, dea = macd(c)
        pos_ = dif > dea
        cross = np.where(pos_ & ~np.roll(pos_,1))[0]; cross = cross[cross>0]
        amp = (hi-lo)/np.roll(c,1); amp[0] = np.nan
        ma20 = s.rolling(20).mean()
        hi250 = s.rolling(250, min_periods=60).max(); lo250 = s.rolling(250, min_periods=60).min()
        b = db_by_code.get(code)
        mv = cmv = turn = pe = pbv = None
        if b is not None and len(b):
            # 按日期对齐(reindex)而非按位置取值: daily_basic 缺行时旧写法会静默错移
            b2 = b.copy(); b2.index = b2.index.astype(str)
            bb = b2.reindex(g['date'].astype(str).values).ffill(limit=7)  # limit=7: 最多回填7个交易日, 防长缺口陈旧数据静默填入
            if not bb['total_mv'].isna().all():
                mv = bb['total_mv'].values; cmv = bb['circ_mv'].values
                turn = bb['turnover_rate'].values; pe = bb['pe_ttm'].values; pbv = bb['pb'].values
        df = pd.DataFrame({'code': code, 'date': g['date'].values, 'close': c,
                           'board': board(code), 'L1': l1_map.get(code, '未知')})
        # v1.1性能版: 全向量化列运算(替代逐行lambda, 50-100x提速)
        amts = pd.Series(amt)
        last_ci = np.maximum.accumulate(np.where(np.isin(np.arange(n), cross), np.arange(n), -1))
        df['ret5'] = s.pct_change(5); df['ret20'] = s.pct_change(20); df['ret60'] = s.pct_change(60)
        df['vol20'] = ret.rolling(20).std(); df['vol60'] = ret.rolling(60).std()
        df['amp20'] = pd.Series(amp).rolling(20).mean()
        df['pos250'] = (s-lo250)/(hi250-lo250); df['dist_hi'] = s/hi250-1; df['bias20'] = s/ma20-1
        df['nh20_cnt'] = (s.rolling(20).max()==s).rolling(20).sum()
        df['lim_cnt250'] = pd.Series(lim.astype(float)).rolling(250, min_periods=60).sum()
        df['dif'] = dif; df['hist'] = dif-dea
        # dif_pos60 = 60日内 DIF>0 占比(与coldstart原lambda同语义, 归位engine单owner)
        pos_raw = np.where(np.isnan(dif), np.nan, (dif > 0).astype(float))
        df['dif_pos60'] = pd.Series(pos_raw).rolling(60, min_periods=30).mean().values
        df['bars_golden'] = np.where(last_ci>=0, np.arange(n)-last_ci, np.nan)
        df['amt5_amt20'] = amts.rolling(5).mean()/amts.rolling(20).mean()
        df['maxstreak'] = streak
        df['amt20'] = pd.Series(amt).rolling(20, min_periods=10).mean().values
        # 基本面五列全向量化(替代逐元素fget循环, 5列×n次Python调用→一次性ndarray)
        def vcol(a):
            return np.asarray(a, dtype=float) if a is not None else np.full(n, np.nan)
        mv_a, cmv_a, turn_a, pe_a, pb_a = (vcol(x) for x in (mv, cmv, turn, pe, pbv))
        df['log_mv'] = np.log10(np.where(mv_a > 0, mv_a, np.nan))
        df['log_cmv'] = np.log10(np.where(cmv_a > 0, cmv_a, np.nan))
        df['turnover20'] = pd.Series(turn_a).rolling(20, min_periods=10).mean().values
        df['pe_ttm'] = pe_a
        df['pb'] = pb_a
        df['loss'] = (df['pe_ttm'] < 0) | df['pe_ttm'].isna()
        df['lim_today'] = lim
        # 标签原料(供model_v1): 未来5日最高/未来20日收盘
        hi_s = pd.Series(hi)
        revmax5 = hi_s[::-1].rolling(5).max()[::-1].shift(-1)   # revmax[t]=max(hi[t..t+4]); shift(-1)→max(hi[t+1..t+5])
        df['fwd5_high'] = (revmax5.values/c) - 1
        df['fwd20_close'] = s.shift(-20)/s - 1
        parts.append(df)
    panel = pd.concat(parts, ignore_index=True)
    # 股票池过滤(计划书§2.2步骤1): ST/价格/流动性
    n0 = len(panel)
    panel = panel[~panel['code'].isin(st_set)]
    panel = panel[panel['close'] <= PRICE_CAP]
    panel = panel[panel['amt20'] >= MIN_AMT20]
    log(f'[engine] pool filter {n0}->{len(panel)} rows; 剔ST/价>20元/低流动性')
    return panel

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p = build(end='20260904')
    print(p.groupby('board')['code'].nunique())
    print('rows', len(p), 'dates', p['date'].nunique())
    p.to_pickle(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'panel_v1.pkl'))
