# -*- coding: utf-8 -*-
"""
hnew1_4_empirical_20260916.py — H-NEW1~4 描述性补验 + H-NEW6 行业量能影子快照（一次性, 只读计算）
前置: daily_basic 指数污染已修复(repair_daily_basic_index_pollution_20260916.py --apply)
口径: 与 _volume_rules_empirical_20260916.py v2 完全一致(主板60/00, 剔ST/退市/次新, next-bar, 2018+)
声明: 开发样本内描述性统计, 非 prereg, 不构成生产放行
"""
import json, sqlite3, time
import numpy as np
import pandas as pd

T0 = time.time()
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
FUND = r'D:\workbuddyspace\databases\fundamental_data.db'
SWDB = r'D:\workbuddyspace\databases\sw_industry.db'
OUT = r'D:\zcodesj\data\_hnew14_empirical_20260916_result.json'
STAT_START = '20180101'
R = {'meta': {'disclaimer': 'development-sample descriptive stats only, NOT prereg'}}

def ro(p):
    return sqlite3.connect(f'file:{p}?mode=ro', uri=True)

info = pd.read_sql("SELECT code,name,is_st,status,instrument_type,list_date FROM stock_info", ro(MAIN))
valid = info[(info.instrument_type == 'stock') & (info.is_st == '0') & (info.status == 'normal')
             & (info.code.str.startswith(('60', '00'))) & (info.list_date < '20170101')]
codes_all = sorted(valid.code.tolist())
print(f'[load] codes={len(codes_all)}', flush=True)

# ---- 行业映射(fund.industry_map, 静态非PIT, 描述性容忍) ----
con = ro(FUND)
im = pd.read_sql("SELECT ts_code, industry_name, level FROM industry_map", con)
con.close()
print('[map] level分布:', im.level.value_counts().to_dict())
lv1 = im[im.level.astype(str).isin(['1', 'L1', '一', '一级'])] if im.level.astype(str).nunique() > 1 else im
code2ind = dict(zip(im.ts_code.str[:6], im.industry_name))
print('[map] L1映射行数:', len(lv1), '覆盖股票数:', len(code2ind), '样例:', list(code2ind.items())[:2])

# ---- 行业L1指数量能状态(industry_kline_sw) ----
con = ro(SWDB)
sw = pd.read_sql("SELECT ts_code,name,trade_date,close,volume FROM industry_kline_sw WHERE level='L1'", con)
con.close()
sw = sw[sw.volume > 0].sort_values(['ts_code', 'trade_date']).copy()
sw['trade_date'] = sw['trade_date'].astype(str).str.replace('-', '', regex=False)
frames = []
for code, g in sw.groupby('ts_code', sort=False):
    if len(g) < 300:
        continue
    g = g.reset_index(drop=True).copy()
    g['volr'] = g['volume'] / g['volume'].rolling(20, min_periods=15).mean()
    g['hot5'] = g['volr'].rolling(5).apply(lambda x: (x >= 1.0).sum(), raw=True)
    g['fwd20'] = g['close'].shift(-21) / g['close'].shift(-1) - 1.0
    g['c1'] = (g.volr >= 1.3) & (g.hot5 >= 4)
    frames.append(g)
swall = pd.concat(frames)
ind_state = {(r.name, r.trade_date): (bool(r.c1), float(r.volr) if pd.notna(r.volr) else np.nan)
             for r in swall.itertuples() if pd.notna(r.volr)}
ind_names = dict(zip(swall.ts_code, swall.name))
print(f'[sw] L1序列 {swall.ts_code.nunique()} 行 {len(swall)} 最新日 {swall.trade_date.max()}', flush=True)

# ---- H-NEW6 影子快照: 最新日各行业量能状态 ----
latest = swall.trade_date.max()
snap = swall[swall.trade_date == latest][['name', 'volr', 'hot5', 'c1']].sort_values('volr', ascending=False)
R['HNEW6_行业量能影子快照'] = {'date': latest,
                               '持续放量C1': snap[snap.c1 == True].to_dict('records'),
                               '量能前5与后5': pd.concat([snap.head(5), snap.tail(5)]).to_dict('records')}
print('[HNEW6] 最新日C1行业:', [x['name'] for x in R['HNEW6_行业量能影子快照']['持续放量C1']], flush=True)

# ---- 个股信号(载入同v2) ----
signals = {k: [] for k in ['H1a_低位平台放量突破', 'H1b_高位平台放量突破', 'H3_突破放量x板块共振', 'H4_个股放量x板块缩量', 'BASE_POSLOW', 'BASE_ALL']}
baseline = []
BATCH = 600
QCOLS = 'code,date,open,high,low,close,volume,change_pct'
for bi in range(0, len(codes_all), BATCH):
    ch = codes_all[bi:bi + BATCH]
    con = ro(QFQ)
    df = pd.read_sql(f"SELECT {QCOLS} FROM stock_daily_meta_qfq WHERE date>='20170101' AND code IN ({','.join('?'*len(ch))})",
                     con, params=ch)
    con.close()
    df = df[df.volume > 0].sort_values(['code', 'date'])
    for code, g in df.groupby('code', sort=False):
        if len(g) < 300:
            continue
        ind = code2ind.get(code)
        g = g.reset_index(drop=True)
        v, c = g['volume'], g['close']
        g['ma20v'] = v.rolling(20).mean(); g['ma5v'] = v.rolling(5).mean()
        g['ma20p'] = c.rolling(20).mean(); g['ma60p'] = c.rolling(60).mean()
        g['volr20'] = v / g['ma20v']
        lo = c.rolling(250, min_periods=200).min(); hi = c.rolling(250, min_periods=200).max()
        g['pos250'] = (c - lo) / (hi - lo).replace(0, np.nan)
        g['plat60'] = c.rolling(60, min_periods=50).max().shift(1) / c.rolling(60, min_periods=50).min().shift(1) - 1.0
        g['entry'] = c.shift(-1)
        g['fwd20'] = c.shift(-21) / g['entry'] - 1.0
        g['fwd5'] = c.shift(-6) / g['entry'] - 1.0
        g['ret1'] = g['change_pct'] / 100.0
        g['vol_next'] = v.shift(-1)
        inwin = g['date'] >= STAT_START
        # 行业状态列
        if ind is None:
            g['ic1'] = False
            g['ivolr'] = np.nan
        else:
            g['ic1'] = [ind_state.get((ind, d), (False, np.nan))[0] for d in g['date']]
            g['ivolr'] = [ind_state.get((ind, d), (False, np.nan))[1] for d in g['date']]
        def take(mask, key):
            if mask.any():
                signals[key].append(g.loc[mask, ['code', 'date', 'fwd5', 'fwd20']])
        plat = g['plat60'] <= 0.30
        take((g['pos250'] <= 0.4) & plat & (g['volr20'] >= 1.8) & (g['ret1'] > 0) & inwin, 'H1a_低位平台放量突破')
        take((g['pos250'] >= 0.7) & plat & (g['volr20'] >= 1.8) & (g['ret1'] > 0) & inwin, 'H1b_高位平台放量突破')
        take((g['volr20'] >= 1.8) & (g['vol_next'] >= 0.8 * v) & (g['ret1'] > 0) & g['ic1'] & inwin, 'H3_突破放量x板块共振')
        take((g['volr20'] >= 1.5) & (g['ret1'] > 0) & (g['ivolr'] < 1.0) & inwin, 'H4_个股放量x板块缩量')
        take((g['pos250'] <= 0.4) & inwin, 'BASE_POSLOW')
        baseline.append(g.loc[inwin, ['fwd5', 'fwd20']])

def blk(key):
    if not signals[key]:
        return {'n': 0}
    f = pd.concat(signals[key], ignore_index=True).dropna(subset=['fwd5', 'fwd20'])
    if len(f) == 0:
        return {'n': 0}
    return {'n': int(len(f)), 'fwd5_mean': round(float(f.fwd5.mean()), 4), 'fwd5_win': round(float((f.fwd5 > 0).mean()), 4),
            'fwd20_mean': round(float(f.fwd20.mean()), 4), 'fwd20_win': round(float((f.fwd20 > 0).mean()), 4)}

for k in signals:
    R[k] = blk(k)
    print(k, R[k], flush=True)
b = pd.concat(baseline, ignore_index=True).dropna()
R['baseline'] = {'n': int(len(b)), 'fwd5_mean': round(float(b.fwd5.mean()), 4), 'fwd5_win': round(float((b.fwd5 > 0).mean()), 4),
                 'fwd20_mean': round(float(b.fwd20.mean()), 4), 'fwd20_win': round(float((b.fwd20 > 0).mean()), 4)}
print('baseline:', R['baseline'], flush=True)

json.dump(R, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print(f'[done] {OUT} {time.time()-T0:.0f}s', flush=True)
