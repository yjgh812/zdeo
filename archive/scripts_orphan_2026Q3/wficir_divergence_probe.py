# -*- coding: utf-8 -*-
"""
wficir_divergence_probe — 向量引擎第4个bug定位
================================================
双引擎同进程: 同一信号/同一价格, 逐月对比实际持仓(集合+数量),
找到首个显著分歧月并输出该月买卖明细。
运行: python D:/zcodesj/wficir_divergence_probe.py
"""
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd

t0 = time.time()


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


# ── 1. 复用信号管线 ──
log('加载信号管线...')
src = open(r'D:\zcodesj\bt_wficir_realistic_recheck.py',
           encoding='utf-8').read().splitlines(keepends=True)
cut = next(i for i, l in enumerate(src)
           if l.startswith("# ------------------------------------------------ L1 "))
g = {'__name__': 'm'}
exec(compile(''.join(src[:cut]), 'sig', 'exec'), g)
reb_dates, rd_score = g['reb_dates'], g['rd_score']
prices = g['prices']
SLIP, STAMP, TRANSFER, COMM, COMM_MIN = (g['SLIP'], g['STAMP'],
                                         g['TRANSFER'], g['COMM'], g['COMM_MIN'])
CASH = g['CASH']
log('信号管线就绪(清洁v2价格)')

# 信号表(两引擎共用)
signals = {}
for rd_ in reb_dates:
    h, _ = rd_score(rd_, 'fixed', 50)
    if h:
        signals[int(pd.Timestamp(rd_).strftime('%Y%m%d'))] = h
ymlist = sorted(signals)

# ── 2. 向量引擎快照 ──
log('向量引擎逐月快照...')
vec_cash = CASH
vec_shares = {}
vec_snap = {}          # ym -> (调仓后shares, cash)
for rd_ in reb_dates:
    h = signals[int(pd.Timestamp(rd_).strftime('%Y%m%d'))]
    new_set = set(h)
    px = prices.loc[rd_]
    for c in list(vec_shares):
        if c in new_set:
            continue
        p = px.get(c)
        if p is None or p != p or vec_shares[c] <= 0:
            continue
        v = vec_shares.pop(c) * p
        proceeds = v * (1 - SLIP)
        fee = proceeds * (STAMP + TRANSFER) + max(proceeds * COMM, COMM_MIN)
        vec_cash += proceeds - fee
    mkt_now = sum(sh * px.get(c, np.nan) for c, sh in vec_shares.items()
                  if not pd.isna(px.get(c)))
    budget = (vec_cash + mkt_now) * 0.98 / len(new_set)
    for c in sorted(new_set - set(vec_shares)):
        p = px.get(c)
        if p is None or p != p or p <= 0:
            continue
        n_lot = int(budget / (p * (1 + COMM + TRANSFER + SLIP)) / 100)
        if n_lot <= 0:
            continue
        q = n_lot * 100
        v = q * p
        fee = v * TRANSFER + max(v * COMM, COMM_MIN)
        cost = v * (1 + SLIP) + fee
        if cost > vec_cash:
            q *= vec_cash / cost
            v, cost = q * p, vec_cash
        vec_cash -= cost
        vec_shares[c] = vec_shares.get(c, 0) + q
    vec_snap[int(pd.Timestamp(rd_).strftime('%Y%m%d'))] = (dict(vec_shares), vec_cash)

# ── 3. bt引擎(coc理想化)快照 ──
import backtrader as bt_mod
from bt_ashare_realistic import add_ashare_data, align_dataframes, build_cerebro
import sqlite3

log('bt引擎数据加载...')
codes = sorted({c for lst in signals.values() for c in lst})
con = sqlite3.connect(r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db')
ph = ','.join('?' * len(codes))
ohlcv = pd.read_sql_query(
    'SELECT code, date, open, high, low, close, volume FROM stock_daily_meta '
    'WHERE code IN (%s) AND date BETWEEN ? AND ? ORDER BY code, date'
    % ph, con, params=codes + [20160104, 20260731])
con.close()
dfs = {}
for code, gg in ohlcv.groupby('code'):
    idx = pd.to_datetime(gg['date'].astype(str), format='%Y%m%d')
    df = gg.drop(columns=['code', 'date']).set_index(idx).astype(float)
    med = df['close'].rolling(21, min_periods=5).median()
    p = ((df['close'] / med > 3) | (df['close'] / med < 1.0 / 3)
         | (df['close'].pct_change().abs() > 0.21))
    for col in ('open', 'high', 'low', 'close'):
        df.loc[p, col] = np.nan
        df[col] = df[col].ffill()
    df['volume'] = df['volume'].fillna(0.0)
    dfs[code] = df
dfs = align_dataframes(dfs)
for df in dfs.values():
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].ffill()
    df['volume'] = df['volume'].fillna(0.0)
log('%d只×%dbar' % (len(dfs), len(next(iter(dfs.values())))))


class Probe(bt_mod.Strategy):
    """coc理想化+cash-aware, 每月记录调仓后实际持仓(在下一信号日开头记录)。"""
    params = (('topn', 50), ('signals', None))

    def __init__(self):
        self.me_set = set(self.p.signals)
        self.snap = {}
        self.pending_ym = None

    def _d(self):
        dt = self.datas[0].datetime
        return int('%d%02d%02d' % (dt.date(0).year, dt.date(0).month, dt.date(0).day))

    def _tradable(self, d):
        c, v = d.close[0], d.volume[0]
        return c == c and c > 0 and v == v and v > 0

    def next(self):
        d0 = self._d()
        # 信号日先记录"上月调仓后实际持仓"
        if self.pending_ym is not None:
            self.snap[self.pending_ym] = {
                data._name: pos.size for data, pos in self.broker.positions.items()
                if pos and pos.size > 0}
            self.pending_ym = None
        if d0 not in self.me_set:
            return
        targets = set(self.p.signals[d0])
        held = {data._name for data, pos in self.broker.positions.items()
                if pos and pos.size > 0}
        sell_proceeds = 0.0
        for code in held:
            if code in targets:
                continue
            d = self.getdatabyname(code)
            pos = self.broker.positions.get(d)
            if pos and pos.size > 0 and self._tradable(d):
                self.sell(data=d, size=pos.size)
                sell_proceeds += pos.size * d.close[0] * 0.985   # 预支卖出所得(A股卖出资金当日可用)
        budget = self.broker.getvalue() * 0.98 / self.p.topn
        cash_left = self.broker.get_cash() + sell_proceeds
        for code in sorted(targets - held):
            d = self.getdatabyname(code)
            if not self._tradable(d):
                continue
            c = d.close[0]
            cap = min(budget, cash_left)
            size = int(cap / (c * 1.003) / 100) * 100
            if size <= 0:
                continue
            self.buy(data=d, size=size)
            cash_left -= size * c * 1.003
        self.pending_ym = d0      # 下次信号日(或结束)记录本次调仓结果


log('bt coc引擎运行...')
cerebro = build_cerebro(
    cash=CASH, commission=0, min_commission=0, stamp_duty=0,
    transfer_fee=0, slippage_perc=0, vol_cap=0, t1=False,
    enforce_limit=False, enforce_round_lot=False, forbid_short=False,
    day_only=False, runonce=False, stdstats=False)
cerebro.broker.set_coc(True)
for code, df in dfs.items():
    add_ashare_data(cerebro, df, code)
cerebro.addstrategy(Probe, signals=signals)
st = cerebro.run()[0]
if st.pending_ym is not None:
    st.snap[st.pending_ym] = {
        data._name: pos.size for data, pos in st.broker.positions.items()
        if pos and pos.size > 0}
bt_snap = st.snap
log('bt快照月数=%d | 向量快照月数=%d' % (len(bt_snap), len(vec_snap)))

# ── 4. 逐月对比 ──
print('\n逐月对比(共同月): n_bt/n_vec | 交集 | 数量比中位数(bt/vec)')
common = [ym for ym in ymlist if ym in bt_snap and ym in vec_snap]
first_big = None
for ym in common:
    bs, vs = bt_snap[ym], vec_snap[ym][0]
    inter = set(bs) & set(vs)
    ratios = [bs[c] / vs[c] for c in inter if vs[c] > 0]
    rmed = float(np.median(ratios)) if ratios else float('nan')
    flag = ''
    if (len(bs) != len(vs) or len(inter) < min(len(bs), len(vs)) - 2
            or (ratios and (rmed < 0.8 or rmed > 1.25))):
        flag = ' ←← 分歧'
        if first_big is None:
            first_big = ym
    if ym in common[:3] or flag or ym == common[-1]:
        print('  %d: %d/%d 交%d 量比%.3f%s'
              % (ym, len(bs), len(vs), len(inter), rmed, flag))

if first_big:
    print('\n首个分歧月明细 %d:' % first_big)
    bs, (vs, vc) = bt_snap[first_big], vec_snap[first_big]
    only_bt = sorted(set(bs) - set(vs))
    only_vec = sorted(set(vs) - set(bs))
    print('  bt独有(%d): %s' % (len(only_bt), only_bt[:10]))
    print('  向量独有(%d): %s' % (len(only_vec), only_vec[:10]))
    inter = set(bs) & set(vs)
    ratios = sorted(((bs[c] / vs[c], c, bs[c], vs[c]) for c in inter if vs[c]))
    print('  数量比TOP5偏离:')
    for r, c, b, v in ratios[:5]:
        print('    %s bt=%d vec=%d ratio=%.2f' % (c, b, v, r))
    for r, c, b, v in ratios[-3:]:
        print('    %s bt=%d vec=%d ratio=%.2f' % (c, b, v, r))
else:
    print('未发现显著分歧!')
print('耗时%.0f秒' % (time.time() - t0))
