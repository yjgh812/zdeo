# -*- coding: utf-8 -*-
"""
hnew25_empirical_20260916.py — H-NEW5 跌停量能分型 + H-NEW2 板块放量分化 描述性补验（一次性, 只读计算）
口径: 与 _volume_rules_empirical_20260916.py v2 一致(主板60/00, 剔ST/退市/次新, next-bar, 2018+)
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
OUT = r'D:\zcodesj\data\_hnew25_empirical_20260916_result.json'
STAT_START = '20180101'
R = {'meta': {'disclaimer': 'development-sample descriptive stats only, NOT prereg'}}

def ro(p):
    return sqlite3.connect(f'file:{p}?mode=ro', uri=True)

info = pd.read_sql("SELECT code,name,is_st,status,instrument_type,list_date FROM stock_info", ro(MAIN))
valid = info[(info.instrument_type == 'stock') & (info.is_st == '0') & (info.status == 'normal')
             & (info.code.str.startswith(('60', '00'))) & (info.list_date < '20170101')]
codes_all = sorted(valid.code.tolist())
con = ro(FUND)
im = pd.read_sql("SELECT ts_code, industry_name FROM industry_map", con)
con.close()
code2ind = {t[:6]: n for t, n in zip(im.ts_code, im.industry_name) if pd.notna(n)}
print(f'[load] codes={len(codes_all)} ind_map={len(code2ind)}', flush=True)

# ---- 行业指数序列(量比/收益) ----
con = ro(SWDB)
sw = pd.read_sql("SELECT ts_code,name,trade_date,close,volume FROM industry_kline_sw WHERE level='L1'", con)
con.close()
sw = sw[sw.volume > 0].copy()
sw['trade_date'] = sw.trade_date.astype(str).str.replace('-', '', regex=False)
sw = sw.sort_values(['ts_code', 'trade_date'])
frames = []
for code, g in sw.groupby('ts_code', sort=False):
    if len(g) < 300:
        continue
    g = g.reset_index(drop=True).copy()
    g['ivolr'] = g['volume'] / g['volume'].rolling(20, min_periods=15).mean()
    g['ifwd20'] = g['close'].shift(-21) / g['close'].shift(-1) - 1.0
    frames.append(g)
swall = pd.concat(frames)
ind_f = {(r.name, r.trade_date): float(r.ifwd20) for r in swall.itertuples() if pd.notna(r.ifwd20)}
print(f'[sw] L1 {swall.ts_code.nunique()} 行 {len(swall)}', flush=True)

# ---- 个股逐日量(供成分聚合) + 跌停分型信号 ----
sig = {k: [] for k in ['D1_一字跌停次日', 'D2_放量跌停次日', 'D3_缩量跌停次日', 'D4_普通跌停次日']}
member_vol = []   # (industry, date, volume) 累积
BATCH = 600
for bi in range(0, len(codes_all), BATCH):
    ch = codes_all[bi:bi + BATCH]
    con = ro(QFQ)
    df = pd.read_sql(f"SELECT code,date,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
                     f"WHERE date>='20170101' AND code IN ({','.join('?'*len(ch))})", con, params=ch)
    con.close()
    df = df[df.volume > 0].sort_values(['code', 'date'])
    for code, g in df.groupby('code', sort=False):
        ind = code2ind.get(code)
        if ind is not None and len(g) > 0:
            member_vol.append(pd.DataFrame({'industry': ind, 'date': g['date'].values,
                                            'volume': g['volume'].values, 'code': code}))
        if len(g) < 300:
            continue
        g = g.reset_index(drop=True)
        v = g['volume']
        g['volr20'] = v / v.rolling(20).mean()
        g['volshrink5'] = v / v.rolling(5).mean().shift(1)
        g['entry'] = g['close'].shift(-1)
        g['fwd5'] = g['close'].shift(-6) / g['entry'] - 1.0
        g['fwd20'] = g['close'].shift(-21) / g['entry'] - 1.0
        dt = (g['change_pct'] <= -9.7) & (g['close'] <= g['low'] + 1e-9) & (g['date'] >= STAT_START)
        if not dt.any():
            continue
        yizi = dt & (g['high'] <= g['low'] * 1.001)
        fangl = dt & (g['volr20'] >= 1.5) & ~yizi
        suol = dt & (g['volshrink5'] < 0.6) & ~yizi & ~fangl
        putong = dt & ~yizi & ~fangl & ~suol
        cols = ['code', 'date', 'fwd5', 'fwd20']
        for key, m in [('D1_一字跌停次日', yizi), ('D2_放量跌停次日', fangl),
                       ('D3_缩量跌停次日', suol), ('D4_普通跌停次日', putong)]:
            if m.any():
                sig[key].append(g.loc[m, cols])
    print(f'[qfq] batch {bi//BATCH+1} t={time.time()-T0:.0f}s', flush=True)

def blk(key):
    if not sig[key]:
        return {'n': 0}
    f = pd.concat(sig[key], ignore_index=True).dropna(subset=['fwd5', 'fwd20'])
    if len(f) == 0:
        return {'n': 0}
    return {'n': int(len(f)), 'fwd5_mean': round(float(f.fwd5.mean()), 4), 'fwd5_win': round(float((f.fwd5 > 0).mean()), 4),
            'fwd20_mean': round(float(f.fwd20.mean()), 4), 'fwd20_win': round(float((f.fwd20 > 0).mean()), 4),
            '次日续跌率': None}

# 次日续跌率: 需要次日 close < 当日 close 概率 — 在信号帧补 next_ret 重算太重, 用 fwd5_win 近似替代说明
for k in sig:
    R[k] = blk(k)
    print(k, R[k], flush=True)

# ---- H-NEW2: 板块指数放量 vs 成分聚合量 分化 ----
mv = pd.concat(member_vol, ignore_index=True)
agg = mv.groupby(['industry', 'date'], as_index=False)['volume'].sum()
agg = agg.sort_values(['industry', 'date'])
frames = []
for ind, g in agg.groupby('industry', sort=False):
    if len(g) < 300:
        continue
    g = g.reset_index(drop=True).copy()
    g['mvolr'] = g['volume'] / g['volume'].rolling(20, min_periods=15).mean()
    frames.append(g)
magg = pd.concat(frames)
swx = swall[['name', 'trade_date', 'ivolr', 'ifwd20']].rename(columns={'name': 'industry', 'trade_date': 'date'})
mg = magg.merge(swx, on=['industry', 'date'], how='inner')
mg = mg.dropna(subset=['mvolr', 'ifwd20'])
mg = mg[mg.date >= STAT_START]
res = {}
for nm, m in [('H2a_板块放量x成分放量(共振)', (mg.ivolr >= 1.3) & (mg.mvolr >= 1.3)),
              ('H2b_板块放量x成分缩量(分化)', (mg.ivolr >= 1.3) & (mg.mvolr <= 0.9)),
              ('H2c_板块放量全样', mg.ivolr >= 1.3)]:
    s = mg.loc[m, 'ifwd20']
    res[nm] = {'n': int(len(s)), 'fwd20_mean': round(float(s.mean()), 4), 'win': round(float((s > 0).mean()), 4)}
    print(nm, res[nm], flush=True)
R['H2_板块分化'] = res
json.dump(R, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print(f'[done] {OUT} {time.time()-T0:.0f}s', flush=True)
