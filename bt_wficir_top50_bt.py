# -*- coding: utf-8 -*-
"""
bt_wficir_top50_bt — WF-icir Top50合规版 backtrader全约束层双引擎验证
=====================================================================
对照基准: 向量真实执行模拟L4 = CAGR 36.08% / Sharpe 1.35 / MDD -36.5%
  (D:\\zcodesj\\bt_wficir_realistic_recheck.py, 2016-01~2026-07)
本脚本: 同信号同数据整建制搬入 backtrader:
  B轨: bt理想化(零成本零约束,次bar开盘成交)
  C轨: bt全约束(T+1/封板/停牌/整手/万2.5最低5+印花+过户/滑点0.1%/量上限25%/当日有效)
执行假设差异(如实声明): 向量版信号日当日价成交; backtrader信号日收盘出单→
次日开盘成交(更严格)。两引擎差异=T+1/封板/停牌拦截+执行时点+停牌锁死等真实效应。
运行: python D:/zcodesj/bt_wficir_top50_bt.py
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
from bt_bp_ep_full_machine import BpEpFullMachine   # 复用策略(参考)


class WficirTop50(bt_mod.Strategy):
    """Top50月度轮动, cash-aware买入(资金不足按剩余现金缩量,同向量语义)。"""

    params = (('topn', 50), ('signals', None))

    def __init__(self):
        self.me_set = set(self.p.signals)
        self.values = []
        self.month_end_info = []

    def _d(self):
        dt = self.datas[0].datetime
        return int('%d%02d%02d' % (dt.date(0).year, dt.date(0).month, dt.date(0).day))

    def _tradable(self, d):
        c, v = d.close[0], d.volume[0]
        return c == c and c > 0 and v == v and v > 0

    def _pos_size(self, d):
        pos = self.broker.positions.get(d)
        return pos.size if pos else 0

    def next(self):
        self.values.append(self.broker.getvalue())
        d0 = self._d()
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
            if self._pos_size(d) > 0 and self._tradable(d):
                self.sell(data=d, size=self._pos_size(d))
                sell_proceeds += self._pos_size(d) * d.close[0] * 0.985
        # A股卖出资金当日可用于买入: 预支待卖持仓所得(扣费近似)
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
        # 月末诊断在下月首个bar前记录近似值
        self.month_end_info.append((d0, len(targets & held | (targets - held)), len(held)))

START, END = 20160104, 20260731
N_TOP = 50
CASH = 2_000_000.0
META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
VEC_L4 = dict(cagr=0.3608, sharpe=1.35, mdd=-0.3654)

t0 = time.time()


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


# ---------------- 1. exec截取复用信号管线(价格/因子/z_panels/rd_score) ----------------
log('复用WF-icir信号管线(因子加载约2分钟)...')
_src = open(r'D:\zcodesj\bt_wficir_realistic_recheck.py',
            encoding='utf-8').read().splitlines(keepends=True)
_cut = next(i for i, l in enumerate(_src) if l.startswith("# ------------------------------------------------ L1 "))
_g = {'__name__': 'wficir_mod'}
exec(compile(''.join(_src[:_cut]), 'wficir_sig', 'exec'), _g)
reb_dates, rd_score, prices = _g['reb_dates'], _g['rd_score'], _g['prices']

log('生成Top%d月度信号...' % N_TOP)
signals = {}
for rd_ in reb_dates:
    h, _ = rd_score(rd_, 'fixed', N_TOP)
    if h:
        signals[int(pd.Timestamp(rd_).strftime('%Y%m%d'))] = h
log('有效信号月=%d / %d | 示例%s: %s' % (
    len(signals), len(reb_dates), list(signals)[0], signals[list(signals)[0]][:5]))

# ---------------- 2. 加载union日线 → 对齐ffill ----------------
codes = sorted({c for lst in signals.values() for c in lst})
log('历史入选union=%d只,加载日线...' % len(codes))
con = sqlite3.connect(META_DB)
ph = ','.join('?' * len(codes))
ohlcv = pd.read_sql_query(
    'SELECT code, date, open, high, low, close, volume FROM stock_daily_meta '
    'WHERE code IN (%s) AND date BETWEEN ? AND ? ORDER BY code, date'
    % ph, con, params=codes + [START, END])
con.close()
dfs = {}
for code, g in ohlcv.groupby('code'):
    idx = pd.to_datetime(g['date'].astype(str), format='%Y%m%d')
    df = g.drop(columns=['code', 'date']).set_index(idx).astype(float)
    # 清洁v2: 滚动中位数锚定(±3倍)∪单日|涨跌|>21%——杀连续脏价段
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
log('对齐完成: %d只 × %dbar | 内存%.0fMB'
    % (len(dfs), len(next(iter(dfs.values()))),
       sum(df.memory_usage().sum() for df in dfs.values()) / 1e6))

# ---------------- 3. backtrader两轨+coc验证轨 ----------------
def run_cashaware(realistic, coc):
    cerebro = build_cerebro(
        cash=CASH, commission=0, min_commission=0, stamp_duty=0,
        transfer_fee=0, slippage_perc=0, vol_cap=0, t1=False,
        enforce_limit=False, enforce_round_lot=False, forbid_short=False,
        day_only=False, runonce=False, stdstats=False) if not realistic else         build_cerebro(cash=CASH, runonce=False, stdstats=False)
    if coc:
        cerebro.broker.set_coc(True)
    for code, df in dfs.items():
        add_ashare_data(cerebro, df, code)
    cerebro.addstrategy(WficirTop50, signals=signals)
    st = cerebro.run()[0]
    v = np.array(st.values)
    # 同期口径: 从首个信号月起算(前段空仓横盘剔除)
    first_i = next(i for i, x in enumerate(v) if x != v[0])
    v = v[first_i - 1:]
    r = v[1:] / v[:-1] - 1
    cum = np.cumprod(1 + r)
    return dict(final=v[-1], ann=(v[-1] / v[0]) ** (252 / len(r)) - 1,
                sharpe=r.mean() / (r.std() + 1e-12) * np.sqrt(252),
                mdd=(cum / np.maximum.accumulate(cum) - 1).min(),
                broker=st.broker, n_days=len(r))

log('B2轨: coc当日收盘+cash-aware(=向量语义)...')
nc = run_cashaware(realistic=False, coc=True)
log('C2轨: bt全约束+coc+cash-aware(真实执行,同期口径)...')
rc = run_cashaware(realistic=True, coc=True)
rc['broker'].print_event_summary()
rb = rc

# ---------------- 4. 双引擎对账 ----------------
print('\n' + '=' * 80)
print('WF-icir Top50合规版 双引擎对账 %d-%d (清洁v2, cash-aware, coc=当日收盘成交)' % (START, END))
print('=' * 80)
print('  %-22s CAGR %8.2f%%  Sharpe %6.2f  MDD %8.2f%%  终值%.0f'
      % ('向量L4(独立重算一致)', 36.08, 1.35, -36.54, 24854589))
print('  %-22s CAGR %8.2f%%  Sharpe %6.2f  MDD %8.2f%%  终值%.0f  (%d天)'
      % ('B2-bt理想coc+cashaware', nc['ann'] * 100, nc['sharpe'],
         nc['mdd'] * 100, nc['final'], nc['n_days']))
print('  %-22s CAGR %8.2f%%  Sharpe %6.2f  MDD %8.2f%%  终值%.0f  (%d天)'
      % ('C2-bt全约束coc同期口径', rc['ann'] * 100, rc['sharpe'],
         rc['mdd'] * 100, rc['final'], rc['n_days']))
print('  向量→B2 = %+.2f pp | B2→C2(纯真实摩擦) = %+.2f pp'
      % ((nc['ann'] - 0.3608) * 100, (rc['ann'] - nc['ann']) * 100))
print('  耗时%.0f秒' % (time.time() - t0))
