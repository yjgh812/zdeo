# -*- coding: utf-8 -*-
"""
bt_ashare_realistic_test — A股真实约束层验证(合成数据,确定性断言)
运行: python D:/zcodesj/bt_ashare_realistic_test.py
"""
import sys
import math

sys.path.insert(0, r'D:\zcodesj')

import pandas as pd
import backtrader as bt
from backtrader.order import Order

from bt_ashare_realistic import (
    AShareBroker, build_cerebro, add_ashare_data, align_dataframes,
    limit_pct_for_code,
)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print('  ✅ %s' % name)
    else:
        FAIL.append(name)
        print('  ❌ %s  %s' % (name, detail))


def mkdf(rows):
    """rows: [(open, high, low, close, volume)] → 6个交易日(2024-01-02起)"""
    idx = pd.bdate_range('2024-01-02', periods=len(rows))
    return pd.DataFrame({
        'open': [r[0] for r in rows], 'high': [r[1] for r in rows],
        'low': [r[2] for r in rows], 'close': [r[3] for r in rows],
        'volume': [r[4] for r in rows]}, index=idx)


def scripted(actions):
    """actions: {date_str: [lambda(strat), ...]}，成交回报存 strat.orders"""
    class S(bt.Strategy):
        orders = []

        def next(self):
            d = str(self.data.datetime.date(0))
            for fn in type(self).actions.get(d, []):
                fn(self)

        def notify_order(self, order):
            type(self).orders.append(order)

    S.actions = actions
    return S


def run_one(cerebro, strat_cls):
    cerebro.addstrategy(strat_cls)
    res = cerebro.run()
    return res[0]


D = ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05',
     '2024-01-08', '2024-01-09']


# ================================================== 1. 成交价口径+佣金
def test_fill_and_commission():
    print('\n[1] T日收盘出单 → T+1开盘成交 + 佣金(最低5元+过户费)')
    rows = [(10.0, 10.2, 9.8, 10.0, 10**6)] * 6
    strat = scripted({'2024-01-03': [lambda s: s.buy(size=1000)]})
    c = build_cerebro(slippage_perc=0.001, vol_cap=0.25)
    add_ashare_data(c, mkdf(rows), '600000')
    run_one(c, strat)

    done = [o for o in strat.orders
            if o.status == Order.Completed and o.isbuy()]
    check('买单成交', len(done) == 1)
    if done:
        o = done[0]
        dt = bt.num2date(o.executed.dt).date().isoformat()
        check('成交日=T+1(01-04)', dt == '2024-01-04', dt)
        check('成交价=次日开盘10.0×(1+0.1%滑点)=10.01',
              abs(o.executed.price - 10.01) < 1e-9, o.executed.price)
        exp = max(1000 * o.executed.price * 0.00025, 5.0) \
            + 1000 * o.executed.price * 0.00001
        check('佣金=max(万2.5,5元)+过户费=%.4f' % exp,
              abs(o.executed.comm - exp) < 1e-6, o.executed.comm)


# ================================================== 2. T+1
def test_t1():
    print('\n[2] T+1：当日买入当日卖→废单；次日卖→正常')
    rows = [(10.0, 10.2, 9.8, 10.0, 10**6)] * 6
    strat = scripted({
        '2024-01-03': [lambda s: s.buy(size=1000)],
        '2024-01-04': [lambda s: s.sell(size=1000)],   # 当日卖→拒
        '2024-01-05': [lambda s: s.sell(size=1000)],   # 次日卖→可
    })
    c = build_cerebro(slippage_perc=0.0)
    add_ashare_data(c, mkdf(rows), '600000')
    st = run_one(c, strat)
    broker = st.broker

    rej = [o for o in strat.orders if o.status == Order.Rejected]
    check('当日卖单被拒', len(rej) == 1)
    check('拒单原因=T+1', any('T+1' in e['reason'] for e in broker.event_log),
          broker.event_log)
    sold = [o for o in strat.orders
            if o.status == Order.Completed and not o.isbuy()]
    check('次日卖单成交', len(sold) == 1)
    if sold:
        dt = bt.num2date(sold[0].executed.dt).date().isoformat()
        check('卖出成交于01-08(委托01-05的T+1)', dt == '2024-01-08', dt)
    check('期末持仓=0', st.broker.getposition(st.datas[0]).size == 0)


# ================================================== 3. 一字涨停
def test_sealed_limit_up():
    print('\n[3] 全天封死涨停(主板10%)：买单作废')
    rows = [(10.0, 10.0, 10.0, 10.0, 10**6),
            (10.0, 10.2, 9.9, 10.0, 10**6),
            (11.0, 11.0, 11.0, 11.0, 10**6),   # 01-04一字板=10.0×1.1(执行日)
            (10.8, 11.2, 10.7, 11.0, 10**6),
            (11.0, 11.0, 11.0, 11.0, 10**6),
            (11.0, 11.2, 10.9, 11.1, 10**6)]
    strat = scripted({'2024-01-03': [lambda s: s.buy(size=1000)]})
    c = build_cerebro(slippage_perc=0.0)
    add_ashare_data(c, mkdf(rows), '600000')
    st = run_one(c, strat)
    canc = [o for o in strat.orders if o.status == Order.Canceled]
    check('涨停日买单作废', len(canc) == 1)
    check('事件原因=全天封死涨停',
          any('封死涨停' in e['reason'] for e in st.broker.event_log),
          st.broker.event_log)
    check('未成交任何股', st.broker.getposition(st.datas[0]).size == 0)


# ================================================== 4. 跌停封死
def test_sealed_limit_down():
    print('\n[4] 全天封死跌停：卖单作废、持仓保留')
    rows = [(10.0, 10.2, 9.8, 10.0, 10**6),   # 01-02 买入信号
            (10.0, 10.2, 9.9, 10.1, 10**6),   # 01-03 买成@10
            (10.1, 10.3, 10.0, 10.1, 10**6),  # 01-04 解锁日,卖出信号
            (9.09, 9.09, 9.09, 9.09, 10**6),  # 01-05 一字跌停=10.1×0.9(执行日)
            (9.0, 9.3, 8.9, 9.2, 10**6),
            (9.2, 9.4, 9.1, 9.3, 10**6)]
    strat = scripted({
        '2024-01-02': [lambda s: s.buy(size=1000)],
        '2024-01-04': [lambda s: s.sell(size=1000)],
    })
    c = build_cerebro(slippage_perc=0.0)
    add_ashare_data(c, mkdf(rows), '600000')
    st = run_one(c, strat)
    check('跌停日卖单作废', any(o.status == Order.Canceled
                              and not o.isbuy() for o in strat.orders))
    check('持仓1000股仍保留',
          st.broker.getposition(st.datas[0]).size == 1000)


# ================================================== 5. 整手/卖空/零股
def test_round_lot_short_odd():
    print('\n[5] 整手/禁止卖空/零股规则')
    rows = [(10.0, 10.2, 9.8, 10.0, 10**6)] * 6
    strat = scripted({
        '2024-01-03': [lambda s: s.buy(size=1000)],
        '2024-01-04': [lambda s: s.buy(size=150),       # 非整手→拒
                       lambda s: s.sell(size=1500)],    # 超持仓→拒(卖空)
        '2024-01-05': [lambda s: s.sell(size=950)],     # 非整手非全仓→拒
    })
    c = build_cerebro(slippage_perc=0.0)
    add_ashare_data(c, mkdf(rows), '600000')
    st = run_one(c, strat)
    rej = [o for o in strat.orders if o.status == Order.Rejected]
    check('3笔废单', len(rej) == 3, [o.getstatusname() for o in strat.orders])
    reasons = ' | '.join(e['reason'] for e in st.broker.event_log)
    check('废单原因齐备(非整手/卖空/零股)',
          ('非100整数倍' in reasons) and ('禁止卖空' in reasons)
          and ('零股' in reasons), reasons)


# ================================================== 6. 成交量上限
def test_volume_cap():
    print('\n[6] 成交量上限25%：部分成交后当日作废')
    rows = [(10.0, 10.2, 9.8, 10.0, 10**6),
            (10.0, 10.2, 9.8, 10.0, 10**6),
            (10.0, 10.2, 9.8, 10.0, 1000),   # 01-04执行日仅1000股成交量
            (10.0, 10.2, 9.8, 10.0, 10**6),
            (10.0, 10.2, 9.8, 10.0, 10**6),
            (10.0, 10.2, 9.8, 10.0, 10**6)]
    strat = scripted({'2024-01-03': [lambda s: s.buy(size=10000)]})
    c = build_cerebro(slippage_perc=0.0, vol_cap=0.25)
    add_ashare_data(c, mkdf(rows), '600000')
    st = run_one(c, strat)
    part = [o for o in strat.orders if o.status == Order.Canceled]
    check('部分成交单已作废', len(part) == 1)
    if part:
        check('成交200股(1000×25%取整手)',
              part[0].executed.size == 200, part[0].executed.size)
    check('持仓=200', st.broker.getposition(st.datas[0]).size == 200)


# ================================================== 7. 印花税仅卖出
def test_stamp_duty():
    print('\n[7] 费用结构：印花税仅卖出收取')
    rows = [(10.0, 10.0, 10.0, 10.0, 10**6)] * 6   # 平板bar,滑点被bar范围钳制→原价
    strat = scripted({
        '2024-01-03': [lambda s: s.buy(size=10000)],
        '2024-01-05': [lambda s: s.sell(size=10000)],   # 01-04成交解锁后
    })
    c = build_cerebro(slippage_perc=0.001)
    add_ashare_data(c, mkdf(rows), '600000')
    st = run_one(c, strat)
    done = {('b' if o.isbuy() else 's'): o for o in strat.orders
            if o.status == Order.Completed}
    check('买卖均成交', set(done) == {'b', 's'})
    if set(done) == {'b', 's'}:
        b, s = done['b'], done['s']
        vb = b.executed.size * b.executed.price
        vs = abs(s.executed.size) * s.executed.price
        exp_b = max(vb * 0.00025, 5.0) + vb * 0.00001
        exp_s = max(vs * 0.00025, 5.0) + vs * 0.00001 + vs * 0.0005
        check('买入费=%.4f(无印花税)' % exp_b,
              abs(b.executed.comm - exp_b) < 1e-6, b.executed.comm)
        check('卖出费=%.4f(含印花税50元级)' % exp_s,
              abs(s.executed.comm - exp_s) < 1e-6, s.executed.comm)
        check('卖出费>买入费(印花税生效)', s.executed.comm > b.executed.comm)


# ================================================== 8. 停牌
def test_suspension():
    print('\n[8] 停牌：对齐NaN行 → 订单作废')
    a = mkdf([(10.0, 10.2, 9.8, 10.0, 10**6)] * 6)
    b_rows = [(10.0, 10.2, 9.8, 10.0, 10**6)] * 5
    b = mkdf(b_rows)
    b = b.drop(index=b.index[2])                       # 01-04停牌
    dfs = align_dataframes({'600000': a, '000001': b})
    check('对齐后B在01-04为NaN行',
          len(dfs['000001']) == 6 and math.isnan(dfs['000001'].iloc[2]['open']),
          dfs['000001'].iloc[2].to_dict())

    strat = scripted({'2024-01-03': [lambda s: s.buy(data=s.datas[1], size=1000)]})
    c = build_cerebro(slippage_perc=0.0)
    for code, df in dfs.items():
        add_ashare_data(c, df, code)
    st = run_one(c, strat)
    check('停牌日订单作废', any('停牌' in e['reason']
                              for e in st.broker.event_log),
          st.broker.event_log)
    check('B未成交', st.broker.getposition(st.datas[1]).size == 0)


# ================================================== 9. 限价单当日有效
def test_limit_day_only():
    print('\n[9] 限价单当日有效：未触及 → 收盘作废')
    rows = [(10.0, 10.2, 9.8, 10.0, 10**6)] * 6       # 最低9.8>委托9.5
    strat = scripted({'2024-01-03': [
        lambda s: s.buy(size=1000, exectype=bt.Order.Limit, price=9.5)]})
    c = build_cerebro(slippage_perc=0.0)
    add_ashare_data(c, mkdf(rows), '600000')
    st = run_one(c, strat)
    check('限价单作废(当日有效)', any(
        o.status == Order.Canceled for o in strat.orders))
    check('原因=当日有效', any('当日有效' in e['reason']
                             for e in st.broker.event_log))
    check('未成交', st.broker.getposition(st.datas[0]).size == 0)


# ================================================== 10. 端到端多股票
def test_end_to_end():
    print('\n[10] 端到端：3股票×250bar 随机游走 SMA轮动(约束全开)')
    import numpy as np
    rng = np.random.default_rng(42)
    idx = pd.bdate_range('2023-01-02', periods=250)
    dfs = {}
    for code in ('600000', '300750', '688981'):
        ret = rng.normal(0.0005, 0.02, len(idx))
        close = 10.0 * np.cumprod(1 + ret)
        high = close * (1 + np.abs(rng.normal(0, 0.008, len(idx))))
        low = close * (1 - np.abs(rng.normal(0, 0.008, len(idx))))
        op = close * (1 + rng.normal(0, 0.004, len(idx)))
        dfs[code] = pd.DataFrame({
            'open': op, 'high': np.maximum.reduce([op, high, close]),
            'low': np.minimum.reduce([op, low, close]), 'close': close,
            'volume': rng.integers(5e5, 5e6, len(idx)).astype(float)},
            index=idx)
    dfs = align_dataframes(dfs)

    class Rot(bt.Strategy):
        def __init__(self):
            self.sma = {d._name: bt.ind.SMA(d.close, period=20)
                        for d in self.datas}

        def next(self):
            for d in self.datas:
                c = d.close[0]
                if c != c:      # 停牌NaN跳过
                    continue
                name = d._name
                pos = self.getposition(d)
                if pos.size == 0 and c > self.sma[name][0]:
                    self.buy(data=d, size=2000)
                elif pos.size > 0 and c < self.sma[name][0]:
                    self.sell(data=d, size=pos.size)

    c = build_cerebro(cash=2_000_000, slippage_perc=0.001, vol_cap=0.25)
    for code, df in dfs.items():
        add_ashare_data(c, df, code)
    c.addstrategy(Rot)
    c.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
    c.addanalyzer(bt.analyzers.DrawDown, _name='dd')
    c.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
    res = c.run()
    st = res[0]
    val = st.broker.getvalue()
    check('全程无异常跑完且终值有限',
          (val == val) and (val not in (float('inf'), float('-inf'))), val)
    tr = st.analyzers.trades.get_analysis()
    total = tr.get('total', {}).get('total', 0)
    check('有完整交易闭环(>0)', total > 0, total)
    st.broker.print_event_summary()
    print('  终值=%.2f 总交易=%s' % (val, total))
    # 涨跌停幅度识别
    check('板别识别: 主板10%/创业板20%/科创板20%',
          limit_pct_for_code('600000') == 0.10
          and limit_pct_for_code('300750') == 0.20
          and limit_pct_for_code('688981') == 0.20)


if __name__ == '__main__':
    for t in (test_fill_and_commission, test_t1, test_sealed_limit_up,
              test_sealed_limit_down, test_round_lot_short_odd,
              test_volume_cap, test_stamp_duty, test_suspension,
              test_limit_day_only, test_end_to_end):
        try:
            t()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            FAIL.append('%s(EXCEPTION:%s)' % (t.__name__, exc))
    print('\n' + '=' * 60)
    print('结果: %d 通过 / %d 失败' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  失败: %s' % f)
        sys.exit(1)
    print('全部通过 ✅')
