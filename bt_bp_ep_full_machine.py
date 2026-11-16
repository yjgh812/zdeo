# -*- coding: utf-8 -*-
"""
bt_bp_ep_full_machine — bp+ep 完整生产口径×真实执行 复测
=====================================================
在上轮五轨基础上补最后两轨:
  D1: bt理想化(零成本) + MDD减仓(真实下单逻辑,零成本)  → 减仓逻辑的"免费"贡献
  D2: bt真实约束(全约束) + MDD减仓(真实下单,收费/整手/T+1) → 完整生产口径×真实执行
  复跑 B(理想化无MDD)/C(真实无MDD) 保证同环境四轨对照:
    D1-B = MDD减仓逻辑在零成本下的贡献
    D2-C = MDD减仓在真实执行下的净贡献
    D1-D2 = 减仓动作本身的真实成本(费+滑点+T+1+封停拦截)
老框架语义: 组合DD<-20%减仓至50%, DD修复至>-6%恢复; 老框架是免费权重缩放(不收费
不整手), 本版为真实买卖单。
⚠️ 宇宙口径注记(2026-10-23 审计发现B, 证据=reports/AUDIT_EXTERNAL_TDX_PIPELINE_20261023.md §六/§七):
   选股核心直接复用 bt_bp_ep_realistic_backtest.build_signals(本文件:27-30 import),
   同样未剔 ST/科创/北交; 材料度按 realistic 口径实测(科创/北交0笔、快照ST 0.20%),
   数字级影响可忽略。解读收益按「含科创/北交/ST历史」口径理解; ST 时点过滤修复须
   ST 历史源(审计报告§七), 源就位前不改选股逻辑。
运行: python D:/zcodesj/bt_bp_ep_full_machine.py
"""
import sqlite3
import sys
import time

sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd
import backtrader as bt

from bt_ashare_realistic import build_cerebro, add_ashare_data, align_dataframes
from bt_bp_ep_realistic_backtest import (   # 复用上轮管线(纯函数,无副作用)
    month_end_dates, load_industry, build_signals, META_DB,
    START, END, TOPN, CASH,
)

MDD_TRIGGER, MDD_RECOVER = -0.20, -0.06


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


class BpEpFullMachine(bt.Strategy):
    """bp+ep月度Top20(行业≤4) + MDD减仓保护(真实下单版)。"""

    params = (('topn', TOPN), ('signals', None), ('mdd', True))

    def __init__(self):
        self.me_set = set(self.p.signals)
        self.values = []
        self.peak = 0.0
        self.protected = False
        self.last_targets = set()
        self.all_codes = []

    def start(self):
        self.all_codes = [d._name for d in self.datas]

    def _d(self):
        dt = self.datas[0].datetime
        return int('%d%02d%02d' % (dt.date(0).year, dt.date(0).month, dt.date(0).day))

    def _tradable(self, d):
        c = d.close[0]
        v = d.volume[0]
        return c == c and c > 0 and v == v and v > 0

    def _pos_size(self, d):
        # 勿用 getposition(会对未交易股票创建零仓位条目,
        # _get_value遍历时0×NaN收盘污染整个组合估值)
        p = self.broker.positions.get(d)
        return p.size if p else 0

    def next(self):
        v = self.broker.getvalue()
        self.values.append(v)
        self.peak = max(self.peak, v)
        dd = v / self.peak - 1 if self.peak > 0 else 0.0
        if self.p.mdd:
            if not self.protected and dd < MDD_TRIGGER:
                self.protected = True
                self._scale_down()
            elif self.protected and dd > MDD_RECOVER:
                self.protected = False
                self._scale_up()
        d0 = self._d()
        if d0 not in self.me_set:
            return
        targets = set(self.p.signals[d0])
        self.last_targets = targets
        held = {data._name for data, p in self.broker.positions.items()
                if p and p.size > 0}
        for code in held:                       # 先卖非目标
            if code in targets:
                continue
            d = self.getdatabyname(code)
            if self._pos_size(d) > 0 and self._tradable(d):
                self.sell(data=d, size=self._pos_size(d))
        scale = 0.5 if self.protected else 1.0
        budget = v * 0.95 / self.p.topn * scale
        for code in sorted(targets - held):     # 后买目标
            d = self.getdatabyname(code)
            if not self._tradable(d):
                continue
            size = int(budget / (d.close[0] * 1.002) / 100) * 100
            if size > 0:
                self.buy(data=d, size=size)

    def _scale_down(self):
        """减仓至50%: 每只持仓卖出一半(整手; 当日买入份额会被T+1真实拦截)。"""
        for data, pos in list(self.broker.positions.items()):
            if pos and pos.size >= 200 and self._tradable(data):
                keep = pos.size // 2 // 100 * 100
                sell_sz = pos.size - keep
                if sell_sz >= 100:
                    self.sell(data=data, size=sell_sz)

    def _scale_up(self):
        """恢复满仓: 对最近目标集补足到等权预算。"""
        budget = self.broker.getvalue() * 0.95 / self.p.topn
        for code in sorted(self.last_targets):
            d = self.getdatabyname(code)
            if not self._tradable(d):
                continue
            tgt = int(budget / (d.close[0] * 1.002) / 100) * 100
            add = tgt - self._pos_size(d)
            if add >= 100:
                self.buy(data=d, size=add)


def run_bt(signals, dfs, realistic, mdd):
    common = dict(runonce=False, stdstats=False)
    if realistic:
        cerebro = build_cerebro(cash=CASH, **common)
    else:
        cerebro = build_cerebro(
            cash=CASH, commission=0, min_commission=0, stamp_duty=0,
            transfer_fee=0, slippage_perc=0, vol_cap=0, t1=False,
            enforce_limit=False, enforce_round_lot=False, forbid_short=False,
            day_only=False, **common)
    for code, df in dfs.items():
        add_ashare_data(cerebro, df, code)
    cerebro.addstrategy(BpEpFullMachine, signals=signals, mdd=mdd)
    st = cerebro.run()[0]
    v = np.array(st.values)
    rets = v[1:] / v[:-1] - 1
    cum = np.cumprod(1 + rets)
    return dict(final=v[-1],
                ann=(v[-1] / v[0]) ** (252 / len(rets)) - 1 if len(rets) else 0,
                sharpe=rets.mean() / (rets.std() + 1e-12) * np.sqrt(252),
                mdd=(cum / np.maximum.accumulate(cum) - 1).min(),
                broker=st.broker, prot=st.protected)


def fmt(label, r):
    print('  %-14s 终值%12.0f  年化%8.2f%%  Sharpe%7.2f  MDD%8.2f%%'
          % (label, r['final'], r['ann'] * 100, r['sharpe'], r['mdd'] * 100))


if __name__ == '__main__':
    t0 = time.time()
    me_dates = month_end_dates()
    ind_map = load_industry()
    signals, _, _ = build_signals(me_dates, ind_map)
    log('有效信号月=%d' % len(signals))

    codes = sorted({c for lst in signals.values() for c in lst})
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
        dfs[code] = g.drop(columns=['code', 'date']).set_index(idx).astype(float)
    dfs = align_dataframes(dfs)
    for df in dfs.values():
        df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].ffill()
        df['volume'] = df['volume'].fillna(0.0)
    log('%d只 × %dbar 就绪' % (len(dfs), len(next(iter(dfs.values())))))

    log('B: 理想化 无MDD...')
    b = run_bt(signals, dfs, realistic=False, mdd=False)
    fmt('B-理想无MDD', b)
    log('D1: 理想化 +MDD...')
    d1 = run_bt(signals, dfs, realistic=False, mdd=True)
    fmt('D1-理想+MDD', d1)
    log('C: 真实约束 无MDD...')
    c = run_bt(signals, dfs, realistic=True, mdd=False)
    fmt('C-真实无MDD', c)
    log('D2: 真实约束 +MDD (完整生产口径×真实执行)...')
    d2 = run_bt(signals, dfs, realistic=True, mdd=True)
    fmt('D2-真实+MDD', d2)
    d2['broker'].print_event_summary()

    print('\n' + '=' * 80)
    print('bp+ep完整机器四轨对照 %d-%d (老框架口径=+21.35%%)' % (START, END))
    print('=' * 80)
    fmt('B-理想无MDD', b)
    fmt('D1-理想+MDD', d1)
    fmt('C-真实无MDD', c)
    fmt('D2-真实+MDD', d2)
    print('  MDD减仓逻辑免费贡献   D1-B = %+.2f pp/年'
          % ((d1['ann'] - b['ann']) * 100))
    print('  MDD减仓真实净贡献     D2-C = %+.2f pp/年'
          % ((d2['ann'] - c['ann']) * 100))
    print('  减仓动作的真实成本     D1-D2 = %+.2f pp/年'
          % ((d1['ann'] - d2['ann']) * 100))
    print('  期末残留保护状态: %s | 耗时 %.0f秒' % (d2['prot'], time.time() - t0))
