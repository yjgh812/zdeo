# -*- coding: utf-8 -*-
"""一次性研究脚本: "恒源煤电模板相似分" 历史逐月截面回测 (20260909).
假设: 在低位反转严格池(pos<=0.40+反转事件+负过滤+EP>0)内, 按恒源模板相似分排序
      取Top20 是否跑赢池内等权基准(fwd20口径)?
这是"约束层正/排序键负"(WINRATE-RANK-TOP10判死)与"个案模板≠系统规律"(金健判死)
两条既有证伪路径的直接检验. 只读panel+qfq, 输出JSON+打印.
"""
import os, sys, json, sqlite3, gc
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np

HERE = r'D:\zcodesj\portrait_agent'
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
OUT = r'D:\zcodesj\data\lowrev_similar_score_backtest_20260909.json'

KEEP = ['date', 'code', 'close', 'pos250', 'ret20', 'ret60', 'pit_ep', 'pit_roe',
        'fwd20_close', 'chan_buy1_10', 'chan_buy2_10', 'island_5', 'gap_up_hold',
        'dip_reb_10', 'higher_low', 'rounded_20', 'lhb_buy_5', 'vwap60_bias', 'vwap20_bias']
p = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v5_full.pkl'))
p = p[p['date'].astype(str) >= '20160101'][KEEP].copy()
p['date'] = p['date'].astype(str)
gc.collect()
print(f'panel裁剪后 {len(p)} 行, 内存落定', flush=True)
dates = sorted(p['date'].unique())
dates = [d for d in dates if d >= '20160101']
# 月度采样: 每月首个交易日
sample_dates = []
seen = set()
for d in dates:
    ym = d[:6]
    if ym not in seen:
        seen.add(ym)
        sample_dates.append(d)
print(f'采样截面 {len(sample_dates)} 个月 ({sample_dates[0]}~{sample_dates[-1]})')

EV = ['chan_buy1_10', 'chan_buy2_10', 'island_5', 'gap_up_hold', 'dip_reb_10', 'higher_low']
NEG = ['rounded_20', 'lhb_buy_5', 'vwap60_bias', 'vwap20_bias']

# ===== 逐截面处理 =====
con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
rows = []
for t in sample_dates:
    snap = p[p['date'] == t]
    pos = pd.to_numeric(snap['pos250'], errors='coerce')
    ep = pd.to_numeric(snap['pit_ep'], errors='coerce')
    r20 = pd.to_numeric(snap['ret20'], errors='coerce')
    r60 = pd.to_numeric(snap['ret60'], errors='coerce')
    fwd = pd.to_numeric(snap['fwd20_close'], errors='coerce')
    ev = (snap[EV].fillna(0) > 0.5).any(axis=1)
    neg = (snap[NEG].fillna(1) <= 0.5).all(axis=1)
    pool = snap[(pos <= 0.40) & ev & neg & (ep > 0)].copy()
    pool['fwd'] = fwd[pool.index]
    pool = pool.dropna(subset=['fwd'])
    if len(pool) < 30:
        continue
    # 量比5v20: qfq按截面日取近30行补拉(分批, 控内存)
    codes = pool['code'].tolist()
    vr = {}
    for i in range(0, len(codes), 300):
        ph = ",".join(f"'{c}'" for c in codes[i:i + 300])
        q = pd.read_sql_query(
            f"SELECT code,amount FROM (SELECT code,date,amount,ROW_NUMBER() OVER "
            f"(PARTITION BY code ORDER BY date DESC) rn FROM stock_daily_meta_qfq "
            f"WHERE code IN ({ph}) AND date<='{t}') WHERE rn<=20", con)
        for code, g in q.groupby('code'):
            if len(g) >= 15:
                a20 = g['amount'].mean()
                a5 = g.nlargest(5, 'rn')['amount'].mean()
                if a20 > 0:
                    vr[code] = a5 / a20
    pool['vr'] = pool['code'].map(vr)
    # 分量分(0~1): 低位/未透支/盈利/缩量/温和
    pool['s_low'] = (1 - pool['pos250'] / 0.40).clip(0, 1)
    pool['s_far'] = ((10 - pool['ret60']) / 25).clip(0, 1)
    pool['s_epi'] = (np.log1p(pool['pit_roe'].clip(0)) / np.log1p(8)).clip(0, 1)
    pool['s_vr'] = ((1.2 - pool['vr']) / 0.8).clip(0, 1)
    pool['s_calm'] = ((5 - pool['ret20'].abs()) / 5).clip(0, 1)
    pool['score'] = (pool['s_low'] * 30 + pool['s_far'] * 20 + pool['s_epi'] * 20 +
                     pool['s_vr'].fillna(0.5) * 20 + pool['s_calm'] * 10)
    pool_mean = pool['fwd'].mean()
    top20 = pool.nlargest(20, 'score')
    bot20 = pool.nsmallest(20, 'score')
    # 分量五分位(池内, 分量各自独立检验)
    qrec = {}
    for comp in ['s_low', 's_far', 's_epi', 's_vr', 's_calm']:
        try:
            qs = pd.qcut(pool[comp].rank(method='first'), 5, labels=False)
            qrec[comp] = [round(float(pool.loc[qs == i, 'fwd'].mean()), 2) for i in range(5)]
        except Exception:
            qrec[comp] = None
    rows.append({'date': t, 'n_pool': len(pool), 'pool_fwd': round(pool_mean, 2),
                 'top20_fwd': round(top20['fwd'].mean(), 2), 'bot20_fwd': round(bot20['fwd'].mean(), 2),
                 'quintile': qrec})
con.close()

r = pd.DataFrame(rows)
r['excess'] = (r['top20_fwd'] - r['pool_fwd']).round(2)
r['bot_excess'] = (r['bot20_fwd'] - r['pool_fwd']).round(2)
print(f'\n有效截面 {len(r)} 个')

def nwt(x, lag=5):
    x = np.asarray(x.dropna())
    n = len(x)
    if n < 5: return np.nan, np.nan
    m = x.mean()
    g = x - m
    l = g[:-1] * g[1:]
    nw = np.dot(g, g) / n + 2 * sum(np.dot(l[:len(l)-k], l[k:]) for k in range(1, lag+1)) / n
    se = np.sqrt(max(nw, 1e-12) / n)
    return round(m, 3), round(m / se, 2)

m_e, t_e = nwt(r['excess'])
m_b, t_b = nwt(r['bot_excess'])
agg = {
    '截面数': len(r), '池均规模': round(r['n_pool'].mean()),
    '池等权fwd20均值%': round(r['pool_fwd'].mean(), 2),
    'Top20均值%': round(r['top20_fwd'].mean(), 2), 'Bottom20均值%': round(r['bot20_fwd'].mean(), 2),
    'Top20超额均值%': m_e, 'Top20超额NW_t': t_e,
    'Bot20超额均值%': m_b, 'Bot20超额NW_t': t_b,
    '超额为正截面占比%': round((r['excess'] > 0).mean() * 100, 1),
    'Top20累计%': round(r['top20_fwd'].sum(), 1), '池累计%': round(r['pool_fwd'].sum(), 1),
}
print('\n===== 全期聚合 ====='); print(json.dumps(agg, ensure_ascii=False))

yr = {}
for y in sorted({d[:4] for d in r['date']}):
    sub = r[r['date'].str.startswith(y)]
    m_y, t_y = nwt(sub['excess'])
    yr[y] = {'n': len(sub), 'Top20均值%': round(sub['top20_fwd'].mean(), 2),
             '池均值%': round(sub['pool_fwd'].mean(), 2), '超额%': m_y, 't': t_y}
print('\n===== 分年 ====='); print(json.dumps(yr, ensure_ascii=False, indent=1))

# 分量五分位(全期平均, Q1=最低分量 → Q5=最高)
quint = {}
for comp in ['s_low', 's_far', 's_epi', 's_vr', 's_calm']:
    arr = [x[comp] for x in r['quintile'] if x and x.get(comp)]
    if arr:
        arr = np.array(arr, dtype=float)
        quint[comp] = [round(float(arr[:, i].mean()), 2) for i in range(5)]
print('\n===== 分量五分位fwd20(Q1低→Q5高, 正单调=分量有效) =====')
print(json.dumps(quint, ensure_ascii=False, indent=1))

json.dump({'聚合': agg, '分年': yr, '分量五分位': quint,
           '逐截面': r.to_dict('records')}, open(OUT, 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=str)
print(f'\n落盘 {OUT}')
