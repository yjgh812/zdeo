# -*- coding: utf-8 -*-
"""
量能规则描述性实证脚本 v2（一次性研究脚本，只读，零生产接线）
日期: 2026-09-16  |  归属: 成交量知识第二轮深化任务(主人令)
v2 修正:
  1) add_fwd 位移方向修正(v1 shift 正负号写反, 结果作废)
  2) 收益一律用 qfq 库 close(daily_basic.close 2026 段已证实污染, 弃用其收益口径)
  3) volume_ratio 过滤 0 值(停牌/缺失)
  4) sw_industry 日期格式自适应(YYYY-MM-DD 与 YYYYMMDD 两段并存风险)
数据源(全部只读): qfq真主库 / dragon_quant.db(stock_info) / fundamental_data.db(daily_basic) / sw_industry.db / dragon_quant_meta.db(指数)
口径: next-bar 入场(T+1收盘; 双日确认=T+2收盘); 主板60/00; 剔ST/退市/2017前上市; close>=2; 统计窗2018+
声明: 开发样本内描述性统计, 非 prereg, 不构成生产放行
"""
import sqlite3, json, time
import numpy as np
import pandas as pd

T0 = time.time()
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
FUND = r'D:\workbuddyspace\databases\fundamental_data.db'
SWDB = r'D:\workbuddyspace\databases\sw_industry.db'
META = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
OUT = r'D:\zcodesj\data\_volume_rules_empirical_20260916_result.json'
STAT_START = '20180101'
RESULT = {'meta': {'script_v': 2, 'stat_start': STAT_START, 'entry_rule': 'next_bar_close(qfq close)',
                   'disclaimer': 'development-sample descriptive stats only, NOT prereg'}}

def ro(p):
    return sqlite3.connect(f'file:{p}?mode=ro', uri=True)

info = pd.read_sql("SELECT code,name,is_st,status,instrument_type,list_date FROM stock_info",
                   ro(MAIN))
valid = info[(info.instrument_type == 'stock') & (info.is_st == '0') & (info.status == 'normal')
             & (info.code.str.startswith(('60', '00'))) & (info.list_date < '20170101')]
codes_all = sorted(valid.code.tolist())
name_map = dict(zip(info.code, info.name))
print(f'[load] valid codes={len(codes_all)}', flush=True)

SIG_COLS = ['code', 'date', 'f5e1', 'f20e1', 'f60e1', 'f5e2', 'f20e2', 'f60e2']
SIGNAL_KEYS = ['S1_突破放量双日确认', 'S2_突破放量单日脉冲', 'S3_上升趋势缩量回踩', 'S4_上升趋势放量回调',
               'S5_高位放量滞涨', 'S6_高位天量', 'S7a_下跌趋势地量', 'S7b_企稳趋势地量',
               'S8a_涨停_一字板', 'S8b_涨停_缩量板', 'S8c_涨停_放量换手板', 'S8d_涨停_普通板']
signals = {k: [] for k in SIGNAL_KEYS}
baseline = []

def add_fwd(g, lag, hs):
    """fwd h 日收益, 入场=T+lag 收盘; shift 方向: 取 T+lag+h 的 close"""
    entry = g['close'].shift(-lag)
    for h in hs:
        g[f'f{h}e{lag}'] = g['close'].shift(-(lag + h)) / entry - 1.0
    return g

QCOLS = 'code,date,open,high,low,close,volume,change_pct'
BATCH = 600
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
        g = g.reset_index(drop=True)
        v, c = g['volume'], g['close']
        g['ma5v'] = v.rolling(5).mean(); g['ma20v'] = v.rolling(20).mean()
        g['ma20p'] = c.rolling(20).mean(); g['ma60p'] = c.rolling(60).mean()
        g['volr20'] = v / g['ma20v']
        g['volshrink5'] = v / g['ma5v'].shift(1)
        lo = c.rolling(250, min_periods=200).min(); hi = c.rolling(250, min_periods=200).max()
        g['pos250'] = (c - lo) / (hi - lo).replace(0, np.nan)
        g['vmax250'] = v >= v.rolling(250, min_periods=200).max()
        g['vmin60'] = v <= v.rolling(60, min_periods=50).min()
        g = add_fwd(g, 1, [5, 20, 60]); g = add_fwd(g, 2, [5, 20, 60])
        g['ret1'] = g['change_pct'] / 100.0
        g['vol_next'] = v.shift(-1)
        g['up'] = g['ma20p'] > g['ma60p']
        inwin = g['date'] >= STAT_START
        baseline.append(g.loc[inwin, ['f5e1', 'f20e1']].assign(code=code))
        def take(mask, key):
            if mask.any():
                signals[key].append(g.loc[mask, SIG_COLS])
        take((g['volr20'] >= 1.8) & (g['vol_next'] >= 0.8 * v) & (g['ret1'] > 0) & inwin, 'S1_突破放量双日确认')
        take((g['volr20'] >= 1.8) & (g['vol_next'] < 0.8 * v) & (g['ret1'] > 0) & inwin, 'S2_突破放量单日脉冲')
        take(g['up'] & (g['ret1'] < 0) & (v < 0.7 * g['ma5v']) & inwin, 'S3_上升趋势缩量回踩')
        take(g['up'] & (g['ret1'] < 0) & (g['volr20'] >= 1.5) & inwin, 'S4_上升趋势放量回调')
        take((g['pos250'] >= 0.8) & (g['volr20'] >= 1.5) & (g['ret1'] <= 0.01) & inwin, 'S5_高位放量滞涨')
        take((g['pos250'] >= 0.9) & g['vmax250'] & inwin, 'S6_高位天量')
        take((g['pos250'] <= 0.3) & g['vmin60'] & ~g['up'] & inwin, 'S7a_下跌趋势地量')
        take((g['pos250'] <= 0.3) & g['vmin60'] & g['up'] & inwin, 'S7b_企稳趋势地量')
        lu = (g['change_pct'] >= 9.7) & (g['close'] >= g['high'] - 1e-9) & inwin
        yizi = lu & (g['high'] <= g['low'] * 1.001)
        suoliang = lu & (g['volshrink5'] < 0.6) & ~yizi
        fangliang = lu & (g['volr20'] > 1.5) & ~yizi & ~suoliang
        other = lu & ~yizi & ~suoliang & ~fangliang
        take(yizi, 'S8a_涨停_一字板'); take(suoliang, 'S8b_涨停_缩量板')
        take(fangliang, 'S8c_涨停_放量换手板'); take(other, 'S8d_涨停_普通板')
    print(f'[qfq] batch {bi//BATCH + 1} done t={time.time()-T0:.0f}s', flush=True)

b = pd.concat(baseline, ignore_index=True).dropna()
RESULT['baseline'] = {'n': int(len(b)), 'fwd5_mean': float(b.f5e1.mean()), 'fwd5_win': float((b.f5e1 > 0).mean()),
                      'fwd20_mean': float(b.f20e1.mean()), 'fwd20_win': float((b.f20e1 > 0).mean())}
print('baseline:', RESULT['baseline'], flush=True)

def stat_block(key, e='e1'):
    f = pd.concat(signals[key], ignore_index=True)
    f = f.dropna(subset=[f'f5{e}', f'f20{e}'])
    if len(f) == 0:
        return {'n': 0}
    r = {'n': int(len(f)),
         'fwd5_mean': float(f[f'f5{e}'].mean()), 'fwd5_win': float((f[f'f5{e}'] > 0).mean()),
         'fwd20_mean': float(f[f'f20{e}'].mean()), 'fwd20_win': float((f[f'f20{e}'] > 0).mean())}
    f60 = f[f'f60{e}'].dropna()
    if len(f60) > 100:
        r['fwd60_mean'] = float(f60.mean())
    return r

for k in SIGNAL_KEYS:
    e = 'e2' if k == 'S1_突破放量双日确认' else 'e1'
    RESULT[k] = stat_block(k, e)
    print(k, RESULT[k], flush=True)

# ---- 案例: 高位天量 fwd60 最深前3(2023以来) ----
cases = []
for bi in range(0, len(codes_all), BATCH):
    ch = codes_all[bi:bi + BATCH]
    con = ro(QFQ)
    df = pd.read_sql(f"SELECT {QCOLS} FROM stock_daily_meta_qfq WHERE date>='20170101' AND code IN ({','.join('?'*len(ch))})",
                     con, params=ch)
    con.close()
    df = df[df.volume > 0].sort_values(['code', 'date'])
    for code, g in df.groupby('code', sort=False):
        if len(g) < 400:
            continue
        g = g.reset_index(drop=True)
        v, c, dcol = g['volume'], g['close'], g['date']
        vmax = v.rolling(250, min_periods=200).max()
        lo = c.rolling(250, min_periods=200).min(); hi = c.rolling(250, min_periods=200).max()
        pos = (c - lo) / (hi - lo).replace(0, np.nan)
        m = (pos >= 0.9) & (v >= vmax) & (dcol >= '20230101')
        for ix in np.where(m)[0]:
            if ix + 61 < len(g):
                cases.append({'code': code, 'name': name_map.get(code, ''), 'date': dcol.iloc[ix],
                              'vol_x20davg': round(float(v.iloc[ix] / v.iloc[max(0, ix-20):ix].mean()), 2),
                              'fwd20': round(float(c.iloc[ix+21]/c.iloc[ix+1]-1), 4),
                              'fwd60': round(float(c.iloc[ix+61]/c.iloc[ix+1]-1), 4)})
cases.sort(key=lambda x: x['fwd60'])
RESULT['case_高位天量fwd60最深前3'] = cases[:3]
print('case_高位天量:', cases[:3], flush=True)

# ---- PART B: daily_basic 换手/量比 x qfq收益 ----
print('[B] loading daily_basic + qfq close...', flush=True)
dbcodes = [c + ('.SH' if c.startswith('6') else '.SZ') for c in codes_all]
frames = []
con = ro(FUND)
for i in range(0, len(dbcodes), 800):
    ch = dbcodes[i:i + 800]
    frames.append(pd.read_sql(f"SELECT ts_code,trade_date,turnover_rate,volume_ratio FROM daily_basic "
                              f"WHERE trade_date>='20200101' AND ts_code IN ({','.join('?'*len(ch))})", con, params=ch))
con.close()
db = pd.concat(frames, ignore_index=True)
db['code'] = db.ts_code.str[:6]
qc = []
con = ro(QFQ)
for i in range(0, len(codes_all), BATCH):
    ch = codes_all[i:i + BATCH]
    qc.append(pd.read_sql(f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20200101' AND code IN ({','.join('?'*len(ch))})",
                          con, params=ch))
con.close()
qq = pd.concat(qc, ignore_index=True).rename(columns={'date': 'trade_date'})
db = db.merge(qq, on=['code', 'trade_date'], how='inner')
db.sort_values(['code', 'trade_date'], inplace=True)
db['tr20'] = db.groupby('code', sort=False)['turnover_rate'].transform(lambda s: s.rolling(20, min_periods=15).mean())
db['cf1'] = db.groupby('code', sort=False)['close'].shift(-1)
db['cf21'] = db.groupby('code', sort=False)['close'].shift(-21)
db = db[db.trade_date >= '20210101'].copy()
db['fwd20'] = db.cf21 / db.cf1 - 1.0
db = db.dropna(subset=['tr20', 'fwd20'])
db['q_tr'] = db.groupby('trade_date')['tr20'].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates='drop'))
g1 = db.groupby('q_tr')['fwd20'].agg(n='count', mean='mean', win=lambda s: (s > 0).mean())
print('换手率20日均量 五分位 fwd20 (2021+, qfq收益):')
print(g1, flush=True)
RESULT['B_换手率五分位_fwd20'] = {str(k): {'n': int(v['n']), 'mean': round(float(v['mean']), 4), 'win': round(float(v['win']), 4)} for k, v in g1.iterrows()}
db2 = db[(db.volume_ratio > 0)].dropna(subset=['volume_ratio']).copy()
db2['q_vr'] = db2.groupby('trade_date')['volume_ratio'].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates='drop'))
g2 = db2.groupby('q_vr')['fwd20'].agg(n='count', mean='mean', win=lambda s: (s > 0).mean())
print('量比(收盘,剔0值) 五分位 fwd20:')
print(g2, flush=True)
RESULT['B_量比五分位_fwd20_剔0'] = {str(k): {'n': int(v['n']), 'mean': round(float(v['mean']), 4), 'win': round(float(v['win']), 4)} for k, v in g2.iterrows()}
RESULT['B_数据质量注记'] = {'daily_basic_close_2026污染': '平安银行2026段fwd20均值1477%已证实, 弃用其收益口径, 收益一律qfq',
                            'volume_ratio_0值': 'tushare volume_ratio 存在大量0(停牌/缺失), 用前必须过滤',
                            '600127量比样本': None}
con = ro(FUND)
jj = pd.read_sql("SELECT trade_date,volume_ratio FROM daily_basic WHERE ts_code='600127.SH' AND trade_date>='20260701' AND volume_ratio>0", con)
con.close()
RESULT['B_数据质量注记']['600127量比样本'] = jj.head(5).to_dict('records')
print('600127 量比(剔0)样例:', jj.head(5).to_dict('records'), flush=True)

# ---- PART C: 申万一级板块量能(industry_kline_sw level=L1带名称; sw_industry_daily=sh880xxx无801语义) ----
print('[C] sw...', flush=True)
con = ro(SWDB)
sw = pd.read_sql("SELECT ts_code,name,trade_date,close,volume FROM industry_kline_sw WHERE level='L1'", con)
con.close()
sw = sw[sw.volume > 0].copy()
sw['trade_date'] = sw.trade_date.astype(str).str.replace('-', '', regex=False)
sw = sw[sw.trade_date >= '20180101'].sort_values(['ts_code', 'trade_date'])
res_c = {}
frames = []
for code, g in sw.groupby('ts_code', sort=False):
    if len(g) < 300:
        continue
    g = g.reset_index(drop=True).copy()
    g['volr'] = g['volume'] / g['volume'].rolling(20, min_periods=15).mean()
    g['fwd20'] = g['close'].shift(-21) / g['close'].shift(-1) - 1.0
    g['hot5'] = g['volr'].rolling(5).apply(lambda x: (x >= 1.0).sum(), raw=True)
    frames.append(g)
swall = pd.concat(frames)
vr_next = swall.groupby('ts_code', sort=False)['volr'].shift(-1)
vr_prev = swall.groupby('ts_code', sort=False)['volr'].shift(1)
m1 = (swall.volr >= 1.3) & (swall.hot5 >= 4) & swall.fwd20.notna()
m2 = (swall.volr >= 1.8) & (vr_next < 1.0) & (vr_prev < 1.0) & swall.fwd20.notna()
for nm, m in [('C1_板块持续放量', m1), ('C2_板块单日脉冲', m2)]:
    s = swall.loc[m, 'fwd20']
    res_c[nm] = {'n': int(len(s)), 'fwd20_mean': round(float(s.mean()), 4), 'win': round(float((s > 0).mean()), 4)}
    print(nm, res_c[nm], flush=True)
RESULT['C_板块量能'] = res_c

# ---- PART D: 大盘成交额对账 ----
con = ro(META)
idx = pd.read_sql("SELECT code,date,close,amount FROM index_daily_meta WHERE code IN ('000001','399001') AND date>='20240101'", con)
con.close()
idx['d'] = idx.date.astype(str)
p = idx.pivot(index='d', columns='code', values='amount')
p['total'] = p.sum(axis=1)
top = p.sort_values('total', ascending=False).head(8)
t84 = float(p.loc['20241008', 'total']) if '20241008' in p.index else None
RESULT['D_大盘成交额top8_raw'] = [{'date': d, 'total_raw': float(r['total'])} for d, r in top.iterrows()]
RESULT['D_单位推断'] = {'20241008_raw': t84, '若公共记录3.45万亿为真则raw单位=万亿/': (3.45 / t84) if t84 else None}
print('D top3:', RESULT['D_大盘成交额top8_raw'][:3], flush=True)

with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(RESULT, f, ensure_ascii=False, indent=1, default=str)
print(f'[done] saved -> {OUT}  total {time.time()-T0:.0f}s', flush=True)
