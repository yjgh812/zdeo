# -*- coding: utf-8 -*-
"""
bt_momvol_enhancer_final — mom20+vol_low 增强层 backtrader全约束终验
====================================================================
候选: mom20+vol_low@T20 向量口径 +9.5%/Sharpe0.59/MDD-23.6% (2018-2026)
本脚本: 同信号同数据整建制入backtrader, 三轨对账:
  [V] 向量基线(月末close当日成交, 0.4%%/次) — 同脚本重算避免口径漂移
  [B] bt理想化+coc(零成本, 当日收盘成交)   — 双引擎对账
  [C] bt全约束+coc(真实费用/整手/T+1/封停/量上限) — 真实执行终验
另产出: 最新一期(2026-07月末)Top20名单(名称经stock_info核实)
数据口径(全部教训应用): qfq复权+清洁只杀上行+选股收益两端同清洁+
  原始价≤30过滤+流动性(20日均额>2000万)+停牌volume=0
运行: python D:/zcodesj/bt_momvol_enhancer_final.py
"""
import sqlite3
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd
import backtrader as bt_mod

from bt_ashare_realistic import add_ashare_data, align_dataframes, build_cerebro

# exec截取WficirTop50类定义(现金时序修复版),避免旧脚本全量执行副作用
_src = open(r'D:/zcodesj/bt_wficir_top50_bt.py', encoding='utf-8').read()
_cut = _src.index('# ---------------- 3.')
_ns = dict(bt_mod=bt_mod, build_cerebro=build_cerebro,
           add_ashare_data=add_ashare_data)
exec(compile(_src[:_cut], 'wt50', 'exec'), _ns)
WficirTop50 = _ns['WficirTop50']

META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
QFQ_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
START, END = 20180101, 20260731
PRE = 20170701
TOPN = 20
CASH = 2_000_000.0

t0 = time.time()


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


# ── 1. 月末与信号 ──
log('加载qfq与原始数据...')
con = sqlite3.connect(META_DB)
dates = [r[0] for r in con.execute(
    'SELECT DISTINCT date FROM stock_daily_meta WHERE date BETWEEN ? AND ? ORDER BY date',
    (PRE, END))]
df = pd.DataFrame({'d': dates})
df['ym'] = df['d'].astype(str).str[:6]
me_dates = [int(x) for x in df.groupby('ym')['d'].max().tolist()]
me_dates = [d for d in me_dates if d >= 20180131]
keep = set(me_dates)
raw_amt = pd.read_sql_query(
    'SELECT code, date, close, amount FROM stock_daily_meta '
    'WHERE date BETWEEN ? AND ? AND close > 0', con, params=(PRE, END))
con.close()
con = sqlite3.connect('file:%s?mode=ro' % QFQ_DB, uri=True)
qfq = pd.read_sql_query(
    'SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq '
    'WHERE date BETWEEN ? AND ? AND close > 0', con, params=(PRE, END))
con.close()
raw_amt['date'] = raw_amt['date'].astype(int)
qfq['date'] = qfq['date'].astype(int)
log('原始行=%d qfq行=%d' % (len(raw_amt), len(qfq)))

log('构建因子(qfq清洁只杀上行)...')
close_p = qfq.pivot(index='date', columns='code', values='close').sort_index()
med = close_p.rolling(21, min_periods=5).median()
bad = (close_p / med > 3) | (close_p.pct_change() > 0.21)
close_p = close_p.mask(bad).ffill()
mom20 = close_p.pct_change(20)
vol20 = close_p.pct_change().rolling(20).std()
amt20 = raw_amt.pivot(index='date', columns='code', values='amount').sort_index().rolling(20).mean()
raw_close_me = raw_amt[raw_amt['date'].isin(keep)].pivot(
    index='date', columns='code', values='close')
raw_close_me.index = raw_close_me.index.astype(int)
pxs_raw = raw_amt[raw_amt['date'].isin(keep)].set_index(['date', 'code'])['close']
# 向量基线用qfq清洁价(与因子同口径)
pxs_qfq_clean = close_p.stack()
pxs_qfq_clean.index.names = ['date', 'code']
del med, bad, raw_amt

combo = (mom20.rank(axis=1, pct=True) - vol20.rank(axis=1, pct=True)) / 2   # mom高+vol低
signals = {}
for d in me_dates:
    if d not in combo.index or d not in raw_close_me.index or d not in amt20.index:
        continue
    f = combo.loc[d].dropna()
    day_px = raw_close_me.loc[d]
    liq = amt20.loc[d]
    f = f[[c for c in f.index if c in day_px.index
           and day_px[c] == day_px[c] and 0 < day_px[c] <= 30
           and c in liq.index and liq[c] == liq[c] and liq[c] > 2e7]]
    if len(f) < TOPN * 3:
        continue
    signals[int(d)] = f.nlargest(TOPN).index.tolist()
log('信号月=%d' % len(signals))

# ── 2. 向量基线(同口径重算) ──
me = sorted(signals)
rets = []
for d0, d1 in zip(me[:-1], me[1:]):
    rs = []
    for c in signals[d0]:
        try:
            p0, p1 = pxs_qfq_clean.loc[(d0, c)], pxs_qfq_clean.loc[(d1, c)]
            if p0 > 0 and p1 > 0:
                rs.append(p1 / p0 - 1)
        except KeyError:
            pass
    if rs:
        rets.append(np.mean(rs) - 0.004)
r = np.array(rets)
cum = np.cumprod(1 + r)
vec = dict(cagr=cum[-1] ** (12 / len(r)) - 1,
           sharpe=r.mean() / (r.std() + 1e-12) * np.sqrt(12),
           mdd=(cum / np.maximum.accumulate(cum) - 1).min(), n=len(r))
print('\n[V] 向量基线(qfq清洁价,0.4%%/次): CAGR %.2f%% Sharpe %.2f MDD %.2f%% (%d月)'
      % (vec['cagr'] * 100, vec['sharpe'], vec['mdd'] * 100, vec['n']))

# ── 3. backtrader数据(union, qfq OHLC清洁) ──
codes = sorted({c for lst in signals.values() for c in lst})
log('union=%d只, 加载qfq OHLCV...' % len(codes))
con = sqlite3.connect('file:%s?mode=ro' % QFQ_DB, uri=True)
ph = ','.join('?' * len(codes))
ohlcv = pd.read_sql_query(
    'SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq '
    'WHERE code IN (%s) AND date BETWEEN ? AND ? ORDER BY code, date'
    % ph, con, params=codes + [START - 20000, END])
con.close()
dfs = {}
for code, g in ohlcv.groupby('code'):
    idx = pd.to_datetime(g['date'].astype(str), format='%Y%m%d')
    df2 = g.drop(columns=['code', 'date']).set_index(idx).astype(float)
    m2 = df2['close'].rolling(21, min_periods=5).median()
    b2 = (df2['close'] / m2 > 3) | (df2['close'].pct_change() > 0.21)
    for col in ('open', 'high', 'low', 'close'):
        df2.loc[b2, col] = np.nan
        df2[col] = df2[col].ffill()
    df2['volume'] = df2['volume'].fillna(0.0)
    dfs[code] = df2
dfs = align_dataframes(dfs)
for df3 in dfs.values():
    df3[['open', 'high', 'low', 'close']] = df3[['open', 'high', 'low', 'close']].ffill()
    df3['volume'] = df3['volume'].fillna(0.0)
log('%d只×%dbar' % (len(dfs), len(next(iter(dfs.values())))))


def run_bt(realistic, coc):
    cerebro = build_cerebro(
        cash=CASH, runonce=False, stdstats=False) if realistic else build_cerebro(
        cash=CASH, commission=0, min_commission=0, stamp_duty=0, transfer_fee=0,
        slippage_perc=0, vol_cap=0, t1=False, enforce_limit=False,
        enforce_round_lot=False, forbid_short=False, day_only=False,
        runonce=False, stdstats=False)
    if coc:
        cerebro.broker.set_coc(True)
    for code, df4 in dfs.items():
        add_ashare_data(cerebro, df4, code)
    cerebro.addstrategy(WficirTop50, signals=signals)
    st = cerebro.run()[0]
    v = np.array(st.values)
    first_i = next((i for i, x in enumerate(v) if x != v[0]), 1)
    v = v[max(first_i - 1, 0):]
    rr = v[1:] / v[:-1] - 1
    cc = np.cumprod(1 + rr)
    return dict(cagr=(v[-1] / v[0]) ** (252 / len(rr)) - 1,
                sharpe=rr.mean() / (rr.std() + 1e-12) * np.sqrt(252),
                mdd=(cc / np.maximum.accumulate(cc) - 1).min(),
                final=v[-1], n=len(rr))


log('[B] bt理想化+coc...')
bb = run_bt(realistic=False, coc=True)
log('[C] bt全约束+coc...')
cc_ = run_bt(realistic=True, coc=True)

print('\n[B] bt理想coc:    CAGR %.2f%% Sharpe %.2f MDD %.2f%% 终值%.0f (%d天)'
      % (bb['cagr'] * 100, bb['sharpe'], bb['mdd'] * 100, bb['final'], bb['n']))
print('[C] bt全约束coc:  CAGR %.2f%% Sharpe %.2f MDD %.2f%% 终值%.0f (%d天)'
      % (cc_['cagr'] * 100, cc_['sharpe'], cc_['mdd'] * 100, cc_['final'], cc_['n']))
print('  向量→bt理想 = %+.2f pp | bt理想→全约束(纯真实摩擦) = %+.2f pp'
      % ((bb['cagr'] - vec['cagr']) * 100, (cc_['cagr'] - bb['cagr']) * 100))

# ── 4. 最新名单 ──
last = me[-1]
con = sqlite3.connect('file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro', uri=True)
names = dict(con.execute('SELECT code, name FROM stock_info').fetchall())
con.close()
picks = signals[last]
lines = []
line = []
for i, c in enumerate(picks, 1):
    line.append('%s %s' % (c, names.get(c, '?')))
    if len(line) == 5:
        lines.append('  ' + ' | '.join(line)); line = []
if line:
    lines.append('  ' + ' | '.join(line))
print('\n最新信号日 %s Top%d(名称经stock_info核实):' % (last, TOPN))
print('\n'.join(lines))
out = r'D:\zcodesj\reports\MOMVOL_ENHANCER_FINAL_20260815.md'
with open(out, 'w', encoding='utf-8') as f:
    f.write('# mom20+vol_low 增强层终验 (2026-08-15)\n\n信号日 %s Top%d:\n%s\n'
            % (last, TOPN, '\n'.join(lines)))
print('名单落盘: %s | 耗时%.0f秒' % (out, time.time() - t0))
