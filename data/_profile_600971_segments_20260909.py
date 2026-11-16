# -*- coding: utf-8 -*-
"""一次性研究脚本: 600971恒源煤电 2024-01-01~2026-09-09 涨跌段(zigzag)特征分析.
画像工具 stock_profile_collect.py 统计窗口硬编码2026年内, 本脚本补齐2024起全窗口.
只读: qfq库 + portrait_agent/cache/panel_v5_full.pkl. 输出JSON+打印摘要.
"""
import os, sys, json
sys.path.insert(0, r'D:\zcodesj')
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats as sps

CODE = '600971'
NAME = '恒源煤电'
QFQ_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
HERE = r'D:\zcodesj\portrait_agent'
OUT = r'D:\zcodesj\data\stock_profile_600971_segments_20260909.json'

con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
df = pd.read_sql_query(
    "SELECT date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
    "WHERE code=? AND date>='20230101' ORDER BY date", con, params=[CODE])
con.close()
df = df.reset_index(drop=True)
o, h, l, c = (df[k].astype(float) for k in ['open', 'high', 'low', 'close'])
v, amt, chg = df['volume'].astype(float), df['amount'].astype(float), df['change_pct'].astype(float)
dates = df['date'].tolist()
w0 = next(i for i, d in enumerate(dates) if d >= '20240101')
print(f'{NAME}({CODE}) K线 {dates[0]}~{dates[-1]} 共{len(df)}行, 窗口起点 {dates[w0]}')

# ===== 特征列(与画像工具口径一致) =====
hi250 = h.rolling(250, min_periods=60).max()
lo250 = l.rolling(250, min_periods=60).min()
pos250 = ((c - lo250) / (hi250 - lo250) * 100)
ret20 = c.pct_change(20) * 100
ret60 = c.pct_change(60) * 100
amt5, amt20 = amt.rolling(5).mean(), amt.rolling(20).mean()
volr = amt5 / amt20          # 量比(5日均额/20日均额)
amp = (h - l) / c.shift(1) * 100
vol20 = c.pct_change().rolling(20).std() * 100
ma20 = c.rolling(20).mean()

# ===== zigzag 涨跌段(阈值10%) =====
def zigzag(cv, pct=10.0):
    n = len(cv); piv = [0]; d = 0; hi = lo = 0
    for i in range(1, n):
        if d >= 0 and cv[i] >= cv[hi]: hi = i
        if d <= 0 and cv[i] <= cv[lo]: lo = i
        if d >= 0 and cv[i] <= cv[hi] * (1 - pct / 100):
            piv.append(hi); d = -1; lo = i
        elif d <= 0 and cv[i] >= cv[lo] * (1 + pct / 100):
            piv.append(lo); d = 1; hi = i
    if piv[-1] != n - 1: piv.append(n - 1)
    return piv

piv = zigzag(c.values)
legs = []
for a, b in zip(piv[:-1], piv[1:]):
    if b <= a: continue
    chgp = (c.iloc[b] / c.iloc[a] - 1) * 100
    typ = 'up' if chgp > 0 else 'down'
    seg_amt = amt.iloc[a + 1:b + 1]
    base_amt = amt.iloc[max(0, a - 20):a + 1].mean()
    rec = {
        '方向': typ, '起点': dates[a], '终点': dates[b], '天数': b - a, '涨跌%': round(chgp, 1),
        '起点pos250': round(pos250.iloc[a], 0), '起点ret60%': round(ret60.iloc[a], 1),
        '起点ret20%': round(ret20.iloc[a], 1), '起点量比5v20': round(volr.iloc[a], 2),
        '段内放量倍数(段均/前20日均)': round(seg_amt.mean() / base_amt, 2) if base_amt > 0 else None,
        '段内最大单日涨%': round(chg.iloc[a + 1:b + 1].max(), 1),
        '段内最大单日跌%': round(chg.iloc[a + 1:b + 1].min(), 1),
        '段内量价相关': round(float(np.corrcoef(chg.iloc[a + 1:b + 1],
                                               seg_amt.pct_change().fillna(0))[0, 1]), 2) if b - a >= 5 else None,
    }
    if typ == 'up' and b + 1 < len(c):
        fut = c.iloc[b + 1:min(b + 61, len(c))]
        rec['段后60日最大回吐%'] = round((fut.min() / c.iloc[b] - 1) * 100, 1)
    legs.append(rec)

legdf = pd.DataFrame(legs)
legwin = legdf[legdf['终点'] >= dates[w0]].copy()
major = legwin[legwin['涨跌%'].abs() >= 15].copy()
print(f'\n===== 涨跌段全景(zigzag10%, 窗口内段数={len(legwin)}, 其中|chg|>=15%大段={len(major)}) =====')
print(legwin.to_string(index=False))

ups = major[major['方向'] == 'up']; dns = major[major['方向'] == 'down']
agg = {
    '窗口': f'{dates[w0]}~{dates[-1]}',
    '大段涨(>=15%)数': len(ups), '大段跌(>=15%)数': len(dns),
    '大段涨中位%': round(ups['涨跌%'].median(), 1) if len(ups) else None,
    '大段涨中位天数': int(ups['天数'].median()) if len(ups) else None,
    '大段涨起点pos250中位': round(ups['起点pos250'].median(), 0) if len(ups) else None,
    '大段涨起点ret60中位%': round(ups['起点ret60%'].median(), 1) if len(ups) else None,
    '大段涨起点量比中位': round(ups['起点量比5v20'].median(), 2) if len(ups) else None,
    '大段涨段后回吐中位%': round(ups['段后60日最大回吐%'].median(), 1) if len(ups) and ups['段后60日最大回吐%'].notna().any() else None,
    '大段跌中位%': round(dns['涨跌%'].median(), 1) if len(dns) else None,
    '大段跌中位天数': int(dns['天数'].median()) if len(dns) else None,
    '大段跌起点pos250中位': round(dns['起点pos250'].median(), 0) if len(dns) else None,
    '大段跌起点量比中位': round(dns['起点量比5v20'].median(), 2) if len(dns) else None,
}
print('\n大段聚合:', json.dumps(agg, ensure_ascii=False))

# ===== 年度收益 + 月度收益 =====
yr = {}
for y in ('2024', '2025', '2026'):
    ii = [i for i, d in enumerate(dates) if d.startswith(y)]
    if ii: yr[y] = round((c.iloc[ii[-1]] / c.iloc[ii[0] - 1] - 1) * 100, 1) if ii[0] > 0 else None
mret = {}
for mth in sorted({d[:6] for d in dates[w0:]}):
    ii = [i for i, d in enumerate(dates) if d.startswith(mth)]
    if len(ii) >= 2 and ii[0] > 0:
        mret[mth] = round((c.iloc[ii[-1]] / c.iloc[ii[0] - 1] - 1) * 100, 1)

# ===== 窗口日收益分布 =====
rets = c.pct_change().iloc[w0 + 1:].dropna() * 100
up_d, dn_d = rets[rets > 0], rets[rets < 0]
dist = {'胜率%': round((rets > 0).mean() * 100, 1),
        '盈亏比': round(up_d.mean() / abs(dn_d.mean()), 2),
        '偏度': round(float(sps.skew(rets)), 2), '峰度': round(float(sps.kurtosis(rets)), 2),
        'vol20最新%': round(vol20.iloc[-1], 2), '年化波动近似%': round(rets.std() * np.sqrt(242), 1),
        '窗口累计%': round((c.iloc[-1] / c.iloc[w0] - 1) * 100, 1),
        '窗口最大回撤%': round((c / c.cummax() - 1).iloc[w0:].min() * 100, 1)}

# ===== 极端日档案(|chg|>=4%) =====
ext_idx = [i for i in range(w0, len(df)) if abs(chg.iloc[i]) >= 4]
ext = [{'日期': dates[i], '涨跌%': round(chg.iloc[i], 1), '收盘': round(c.iloc[i], 2),
        'pos250': round(pos250.iloc[i], 0), '量比5v20': round(volr.iloc[i], 2),
        '成交亿': round(amt.iloc[i] / 1e8, 2)} for i in ext_idx]
lim_days = [dates[i] for i in range(w0, len(df)) if chg.iloc[i] >= 9.8]

# ===== panel 九族事件轨迹(2024+) + PIT + 板块 =====
print('\n加载 panel(≈40s)...', flush=True)
panel = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v5_full.pkl'))
sub = panel[(panel['code'] == CODE) & (panel['date'] >= '20240101')].sort_values('date')
EVENTS = ['island_5', 'limit_break_5', 'gap_up_run_5', 'new_high_3', 'dip_reb_10', 'rounded_20',
          'chan_buy1_10', 'chan_buy2_10', 'chan_buy3_10', 'chan_bdiv_10', 'lhb_buy_5', 'ind_lim_ge3',
          'vwap60_bias', 'vwap20_bias', 'up4of5', 'higher_low', 'new_high20', 'gap_up_hold']
fwd20 = (sub['close'].shift(-20) / sub['close'] - 1) * 100
ev = {}
for f in EVENTS:
    if f not in sub.columns: continue
    on = sub[sub[f].fillna(0) > 0.5]
    if len(on):
        idx = on.index
        ev[f] = {'天数': int(len(on)), '首末': f"{on['date'].iloc[0]}~{on['date'].iloc[-1]}",
                 '事件日fwd20均值%': round(float(fwd20.loc[idx].mean()), 1) if fwd20.loc[idx].notna().any() else None}
pit_rows = sub[['date', 'pit_roe', 'pit_ep']].dropna(subset=['pit_roe'])
pit_change = []
prev = None
for _, r in pit_rows.iterrows():
    if prev is None or abs(r['pit_roe'] - prev[1]) > 1e-9:
        pit_change.append({'生效日': r['date'], 'ROE%': round(r['pit_roe'], 2),
                           'EP': round(r['pit_ep'], 4) if pd.notna(r['pit_ep']) else None})
        prev = (r['date'], r['pit_roe'])
# 板块等权超额(煤炭L1, 与工具口径同)
L1 = '煤炭'
ag = panel[panel['L1'] == L1].sort_values('date')
jj = sub.set_index('date')
jj_ret = (jj['close'] / jj['close'].shift(1) - 1) * 100
ag_ret = ag.groupby('date').apply(
    lambda g: (g['close'].mean() / g['close'].shift(1).mean() - 1) * 100 if len(g) > 10 else np.nan,
    include_groups=False).dropna()
both = pd.DataFrame({'a': jj_ret, 'b': ag_ret}).dropna()
both['rel'] = both['a'] - both['b']
sec_ex = {'20日': round(both['rel'].tail(20).sum(), 1), '60日': round(both['rel'].tail(60).sum(), 1),
          '120日': round(both['rel'].tail(120).sum(), 1), '2024以来累计': round(both['rel'].sum(), 1)}
sec_yr = {}
for y in ('2024', '2025', '2026'):
    s = ag_ret[ag_ret.index.str.startswith(y)]
    if len(s): sec_yr[y] = round(float((1 + s / 100).prod() - 1) * 100, 1)

out = {'股票': f'{NAME}({CODE})', '窗口分布': dist, '年度收益%': yr, '月度收益%': mret,
       '大段聚合': agg, '全部段': legs, '极端日': ext, '涨停日': lim_days,
       '事件轨迹2024+': ev, 'PIT变化': pit_change, '板块超额%': sec_ex, '板块年度等权%': sec_yr,
       '截面20260909': {'收盘': round(float(c.iloc[-1]), 2), 'pos250': round(float(pos250.iloc[-1]), 0),
                        'ret20%': round(float(ret20.iloc[-1]), 1), 'ret60%': round(float(ret60.iloc[-1]), 1),
                        '量比5v20': round(float(volr.iloc[-1]), 2), 'ma20乖离%': round(float((c.iloc[-1] / ma20.iloc[-1] - 1) * 100), 1)}}
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print('\n===== 窗口分布 ====='); print(json.dumps(dist, ensure_ascii=False))
print('\n===== 年度收益 ====='); print(json.dumps(yr, ensure_ascii=False), '| 板块等权:', json.dumps(sec_yr, ensure_ascii=False))
print('月度收益:', json.dumps(mret, ensure_ascii=False))
print('\n===== 极端日(|chg|>=4%) ====='); print(json.dumps(ext, ensure_ascii=False))
print('\n涨停日:', lim_days)
print('\n===== 事件轨迹2024+ ====='); print(json.dumps(ev, ensure_ascii=False, indent=1))
print('\n===== PIT变化 ====='); print(json.dumps(pit_change, ensure_ascii=False))
print('\n===== 板块超额% ====='); print(json.dumps(sec_ex, ensure_ascii=False))
print('\n截面20260909:', json.dumps(out['截面20260909'], ensure_ascii=False))
print(f'\n落盘 {OUT}')
