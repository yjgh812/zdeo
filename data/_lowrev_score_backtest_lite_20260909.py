# -*- coding: utf-8 -*-
"""一次性研究脚本(内存安全版): 恒源煤电模板相似分历史回测 20260909.
背景: panel_v6_full.pkl(6.5GB)加载瞬时commit超限MemoryError, 绕开大pickle,
自建轻量月度截面: qfq逐股流式算pos250/ret60/量比/fwd20 + factor_pit.db PIT asof join
+ chan_signals_v2.pkl(406KB)缠论一二买事件.
池条件: pos250<=0.40 + (chan_buy1|chan_buy2) 事件 + EP>0(严格层口径; 事件面窄于引擎A是已知口径差, 报告注明).
检验: 池内等权 vs 相似分Top20/Bottom20, 分量五分位单调性. 只读, 输出JSON.
"""
import os, sys, json, sqlite3, gc
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np

QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
PIT = r'D:\zcodesj\factor_pit.db'
CHAN = r'D:\zcodesj\portrait_agent\cache\chan_signals_v2.pkl'
OUT = r'D:\zcodesj\data\lowrev_similar_score_backtest_20260909.json'

# ===== 月度截面日(每月首个交易日, 从qfq全市场行数推断) =====
con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
all_dates = [r[0] for r in con.execute(
    "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>='20160101' ORDER BY date").fetchall()]
con.close()
seen, sample_dates = set(), []
for d in all_dates:
    ym = d[:6]
    if ym not in seen:
        seen.add(ym)
        sample_dates.append(d)
print(f'月度截面 {len(sample_dates)} 个 ({sample_dates[0]}~{sample_dates[-1]})', flush=True)

# ===== PIT asof 表(全量小, 直接载) =====
con = sqlite3.connect(f'file:{PIT}?mode=ro', uri=True)
pit = pd.read_sql_query(
    "SELECT code, pit_avail_date, ep, roe FROM stock_factors_fundamental_pit_v2", con)
con.close()
pit = pit.sort_values(['code', 'pit_avail_date']).reset_index(drop=True)
print(f'PIT {len(pit)} 行', flush=True)

# ===== chan事件映射: (code,YYYYMM) -> 是否有一二买(取月内首个截面前60日+当月窗口) =====
cs = pd.read_pickle(CHAN)
cs['date'] = cs['date'].astype(str)
# 事件有效期: 触发后10个交易日(与chan_buy1_10/ch1an_buy2_10口径一致, 用60日历日近似)
cs['key'] = cs['code'] + '_' + cs['date'].str[:6]
chan_keys = set(cs['key'])
print(f'chan事件 {len(cs)} 条', flush=True)

# ===== 逐股流式: 只算截面日附近窗口 =====
codes_all = None
con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
codes_all = [r[0] for r in con.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq").fetchall()]
con.close()
print(f'股票 {len(codes_all)} 只, 流式处理...', flush=True)

import bisect
date_idx = {d: i for i, d in enumerate(all_dates)}
rows = []
con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
for k, code in enumerate(codes_all):
    if k % 600 == 0:
        print(f'  {k}/{len(codes_all)}', flush=True)
    df = pd.read_sql_query(
        "SELECT date,close,amount FROM stock_daily_meta_qfq WHERE code=? AND date>='20150601' "
        "ORDER BY date", con, params=[code])
    if len(df) < 120:
        continue
    df['date'] = df['date'].astype(str)
    idx_of = {d: i for i, d in enumerate(df['date'])}
    # PIT asof: 每截面生效的ep/roe
    pp = pit[pit['code'] == code]
    pp_avail = pp['pit_avail_date'].tolist()
    for t in sample_dates:
        i = idx_of.get(t)
        if i is None or i < 100 or i + 20 >= len(df):
            continue
        # pos250/ret60/量比
        win = df.iloc[max(0, i-249):i+1]
        hi = win['close'].max(); lo = win['close'].min()
        if hi <= lo:
            continue
        pos250 = (df['close'].iloc[i] - lo) / (hi - lo)
        c60 = df['close'].iloc[max(0, i-60)]
        ret60 = (df['close'].iloc[i] / c60 - 1) * 100
        c20 = df['close'].iloc[max(0, i-20)]
        ret20 = (df['close'].iloc[i] / c20 - 1) * 100
        amt = df['amount'].iloc[max(0, i-19):i+1]
        vr = amt.tail(5).mean() / amt.mean() if amt.mean() > 0 else np.nan
        fwd = (df['close'].iloc[i+20] / df['close'].iloc[i] - 1) * 100
        ym = t[:6]
        has_chan = (code + '_' + ym) in chan_keys
        rows.append((code, t, round(pos250, 4), round(ret20, 2), round(ret60, 2), round(vr, 3),
                     int(has_chan), round(fwd, 3)))
con.close()
cols = ['code', 'date', 'pos250', 'ret20', 'ret60', 'vr', 'chan', 'fwd']
panel = pd.DataFrame(rows, columns=cols)
del rows
gc.collect()
print(f'轻量面板 {len(panel)} 行', flush=True)

# ===== PIT asof join(向量化) =====
pit_ep = {}
pit_roe = {}
for code, pp in pit.groupby('code'):
    s_ep = pp.set_index('pit_avail_date')['ep'].dropna()
    s_roe = pp.set_index('pit_avail_date')['roe'].dropna()
    pit_ep[code] = (pp['pit_avail_date'].tolist(), pp['ep'].tolist())
    pit_roe[code] = (pp['pit_avail_date'].tolist(), pp['roe'].tolist())
ep_out = np.full(len(panel), np.nan)
roe_out = np.full(len(panel), np.nan)
codes_arr = panel['code'].values
dates_arr = panel['date'].values.astype(int)
for ci, code in enumerate(pd.unique(codes_arr)):
    m = codes_arr == code
    if not m.any():
        continue
    av, epv = pit_ep.get(code, ([], []))
    avr, roev = pit_roe.get(code, ([], []))
    av_i = np.searchsorted(np.array(av, dtype=int), dates_arr[m], side='right') - 1
    ep_out[np.where(m)[0]] = [epv[j] if j >= 0 else np.nan for j in av_i]
    roe_out[np.where(m)[0]] = [roev[j] if j >= 0 else np.nan for j in av_i]
panel['ep'] = ep_out
panel['roe'] = roe_out
gc.collect()

panel.to_pickle(r'D:\zcodesj\data\_lowrev_lite_panel_20260909.pkl')
print('面板已落盘 _lowrev_lite_panel_20260909.pkl', flush=True)

# ===== 截面回测 =====
res_rows = []
quint_rows = []
for t in sample_dates:
    snap = panel[panel['date'] == t]
    pool = snap[(snap['pos250'] <= 0.40) & (snap['chan'] == 1) & (snap['ep'] > 0)].dropna(subset=['fwd'])
    if len(pool) < 30:
        continue
    pool = pool.copy()
    pool['s_low'] = (1 - pool['pos250'] / 0.40).clip(0, 1)
    pool['s_far'] = ((10 - pool['ret60']) / 25).clip(0, 1)
    pool['s_epi'] = (np.log1p(pool['roe'].clip(lower=0)) / np.log1p(8)).clip(0, 1)
    pool['s_vr'] = ((1.2 - pool['vr']) / 0.8).clip(0, 1)
    pool['s_calm'] = ((5 - pool['ret20'].abs()) / 5).clip(0, 1)
    pool['score'] = (pool['s_low']*30 + pool['s_far']*20 + pool['s_epi']*20 +
                     pool['s_vr'].fillna(0.5)*20 + pool['s_calm']*10)
    top20 = pool.nlargest(20, 'score')
    bot20 = pool.nsmallest(20, 'score')
    res_rows.append({'date': t, 'n': len(pool), 'pool': round(pool['fwd'].mean(), 3),
                     'top': round(top20['fwd'].mean(), 3), 'bot': round(bot20['fwd'].mean(), 3)})
    for comp in ['s_low', 's_far', 's_epi', 's_vr', 's_calm']:
        try:
            qs = pd.qcut(pool[comp].rank(method='first'), 5, labels=False)
            quint_rows.append({'date': t, 'comp': comp,
                               **{f'q{i+1}': round(float(pool.loc[qs == i, 'fwd'].mean()), 3) for i in range(5)}})
        except Exception:
            pass

r = pd.DataFrame(res_rows)
r['excess'] = r['top'] - r['pool']
r['bot_ex'] = r['bot'] - r['pool']

def nwt(x, lag=3):
    x = np.asarray(x.dropna())
    n = len(x)
    if n < 5:
        return np.nan, np.nan
    mu = x.mean(); g = x - mu
    nw = np.dot(g, g) / n
    for k in range(1, lag + 1):
        if len(g) > k:
            nw += 2 * np.dot(g[:-k], g[k:]) / n
    se = np.sqrt(max(nw, 1e-12) / n)
    return round(mu, 3), round(mu / se, 2)

m_e, t_e = nwt(r['excess'])
m_b, t_b = nwt(r['bot_ex'])
agg = {'截面数': len(r), '池均规模': int(r['n'].mean()),
       '池等权均值%': round(r['pool'].mean(), 2), 'Top20均值%': round(r['top'].mean(), 2),
       'Bot20均值%': round(r['bot'].mean(), 2),
       'Top超额均值%': m_e, 'Top超额NW_t': t_e, 'Bot超额均值%': m_b, 'Bot超额NW_t': t_b,
       '超额正占比%': round((r['excess'] > 0).mean() * 100, 1)}
print('\n===== 全期聚合 =====')
print(json.dumps(agg, ensure_ascii=False))

yr = {}
for y in sorted({d[:4] for d in r['date']}):
    sub = r[r['date'].str.startswith(y)]
    m_y, t_y = nwt(sub['excess'])
    yr[y] = {'n': len(sub), 'Top均值%': round(sub['top'].mean(), 2), '池均值%': round(sub['pool'].mean(), 2),
             '超额%': m_y, 't': t_y}
print('\n===== 分年 =====')
print(json.dumps(yr, ensure_ascii=False, indent=1))

q = pd.DataFrame(quint_rows)
quint = q.groupby('comp').mean(numeric_only=True).round(3).drop(columns=['date'])
print('\n===== 分量五分位fwd20 (Q1=最低→Q5=最高, 正单调=该分量有料) =====')
print(quint.to_string())

json.dump({'口径': '轻量面板=qfq逐股流式+factor_pit v2 asof+chan_signals_v2一二买(月key近似60日历日有效); 事件面窄于引擎A是已知口径差',
           '聚合': agg, '分年': yr, '分量五分位': json.loads(quint.to_json()),
           '逐截面': res_rows}, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print(f'\n落盘 {OUT}')
