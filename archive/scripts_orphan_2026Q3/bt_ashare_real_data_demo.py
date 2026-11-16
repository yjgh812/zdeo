# -*- coding: utf-8 -*-
"""
bt_ashare_real_data_demo — 真实A股日线上的约束层验证
==================================================
数据: dragon_quant_meta.db stock_daily_meta (不复权,至20260814)
标的: 002167东方锆业 / 300319麦捷科技 / 603989艾华集团 (名称均经stock_info核实)

验证内容:
  1) 扫描真实一字板 → 证明数据含真实封板日
  2) 靶向测试: 一字板前一日收盘出买单 → 真实封板日被拦截(DAYFAIL)
  3) 同策略 SMA(5,20)轮动 × 同数据: 理想化(默认broker零成本) vs 真实约束层 对照
运行: python D:/zcodesj/bt_ashare_real_data_demo.py
"""
import sqlite3
import sys

sys.path.insert(0, r'D:\zcodesj')

import pandas as pd
import backtrader as bt

from bt_ashare_realistic import (
    build_cerebro, add_ashare_data, align_dataframes, limit_pct_for_code,
)

DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
NAMES = {'002167': '东方锆业', '300319': '麦捷科技', '603989': '艾华集团'}
START, END = 20230101, 20260814
CASH = 1_000_000.0


def load_raw(code):
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        'SELECT date, open, high, low, close, volume FROM stock_daily_meta '
        'WHERE code=? AND date BETWEEN ? AND ? ORDER BY date',
        con, params=(code, START, END))
    con.close()
    df['date'] = pd.to_datetime(df['date'].astype(str), format='%Y%m%d')
    df = df.set_index('date')
    return df.astype(float)


def scan_sealed(df, code):
    """真实一字板: 四 OHLC 相等且涨幅达到板别幅度(按分舍入容差)。"""
    pct = limit_pct_for_code(code)
    out = []
    c = df['close'].values
    o, h, l = df['open'].values, df['high'].values, df['low'].values
    for i in range(1, len(df)):
        if c[i - 1] <= 0:
            continue
        same = (abs(o[i] - h[i]) < 1e-9 and abs(h[i] - l[i]) < 1e-9
                and abs(l[i] - c[i]) < 1e-9)
        if not same:
            continue
        lim = round(c[i - 1] * (1 + pct), 2)
        if abs(c[i] - lim) < 0.006:          # 真实一字涨停(分舍入容差)
            out.append((df.index[i].date(), round(c[i - 1], 2), round(c[i], 2)))
    return out


# ------------------------------------------------ 一字板前夜买入靶向测试
def sealed_intercept_test(dfs, sealed_map):
    print('\n[靶向] 一字板前一日收盘出买单 → 执行日应被拦截')
    code = '002167'
    days = sealed_map[code]
    if not days:
        print('  ⚠️ 002167无真实一字板,跳过')
        return False
    d0 = days[0][0]                       # 第一处真实一字板
    idx = list(dfs[code].index)
    pos = idx.index(pd.Timestamp(d0))
    if pos == 0:
        d0, pos = days[1][0], idx.index(pd.Timestamp(days[1][0]))
    sig_day = idx[pos - 1].date()         # 信号日=前一日收盘

    hit = {}

    class S(bt.Strategy):
        def next(self):
            if str(self.data.datetime.date(0)) == str(sig_day):
                hit['order'] = self.buy(data=self.datas[0], size=2000)

    c = build_cerebro(cash=CASH, slippage_perc=0.0)
    for cd in ('002167', '300319', '603989'):
        add_ashare_data(c, dfs[cd], cd)
    c.addstrategy(S)
    st = c.run()[0]
    events = [e for e in st.broker.event_log
              if e['code'] == '002167' and '封死涨停' in e['reason']]
    ok_date = events and events[0]['date'] == str(d0)
    pos_after = st.broker.getposition(st.datas[0]).size
    print('  一字板日=%s(前收%.2f→一字%.2f) 信号日=%s' % (d0, days[0][1], days[0][2], sig_day))
    print('  拦截事件=%s 持仓=%s' % (events, pos_after))
    print('  ✅ 真实一字板拦截成功' if (ok_date and pos_after == 0)
          else '  ❌ 未拦截或日期不符')
    return bool(ok_date and pos_after == 0)


# ------------------------------------------------ 理想 vs 真实 对照
class SmaRotation(bt.Strategy):
    """close>SMA20 买入2000股, close<SMA20 清仓; NaN(停牌)跳过。"""

    def __init__(self):
        self.sma = {d._name: bt.ind.SMA(d.close, period=20) for d in self.datas}

    def next(self):
        for d in self.datas:
            c = d.close[0]
            if c != c:
                continue
            name = d._name
            pos = self.getposition(d)
            if pos.size == 0 and c > self.sma[name][0]:
                self.buy(data=d, size=2000)
            elif pos.size > 0 and c < self.sma[name][0]:
                self.sell(data=d, size=pos.size)


def run_compare(dfs):
    print('\n[对照] 同策略同数据: 理想化(默认broker/零成本/无约束) vs 真实约束层')
    common = dict(runonce=False, stdstats=False)

    # 理想化
    cn = bt.Cerebro(**common)
    for cd in NAMES:
        add_ashare_data(cn, dfs[cd], cd)
    cn.broker.setcash(CASH)
    cn.addstrategy(SmaRotation)
    cn.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
    cn.addanalyzer(bt.analyzers.DrawDown, _name='dd')
    sn = cn.run()[0]

    # 真实约束
    cr = build_cerebro(cash=CASH, **common)
    for cd in NAMES:
        add_ashare_data(cr, dfs[cd], cd)
    cr.addstrategy(SmaRotation)
    cr.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
    cr.addanalyzer(bt.analyzers.DrawDown, _name='dd')
    sr = cr.run()[0]

    def stat(s):
        tr = s.analyzers.trades.get_analysis()
        dd = s.analyzers.dd.get_analysis()
        return (s.broker.getvalue(),
                tr.get('total', {}).get('total', 0),
                dd.get('max', {}).get('drawdown', 0.0))

    vn, tn, ddn = stat(sn)
    vr, tr_, ddr = stat(sr)
    gap = (vn / vr - 1) * 100 if vr else float('nan')

    print('           理想化          真实约束')
    print('  终值    %12.0f  %12.0f' % (vn, vr))
    print('  收益率  %11.2f%%  %11.2f%%' % (vn / CASH * 100 - 100, vr / CASH * 100 - 100))
    print('  完成笔数 %9d  %12d' % (tn, tr_))
    print('  最大回撤 %10.2f%%  %11.2f%%' % (ddn, ddr))
    print('  理想化虚增收益 = %.2f%%' % gap)
    sr.broker.print_event_summary()
    ev = sr.broker.event_stats
    print('  [关键] 真实约束下被拦截而理想化能成交的单数: %d'
          % sum(v for k, v in ev.items() if k.startswith(('REJECT', 'DAYFAIL'))))
    return gap, ev


if __name__ == '__main__':
    print('加载数据: %s ~ %s' % (START, END))
    dfs = {}
    for cd in NAMES:
        df = load_raw(cd)
        print('  %s %s: %d bar (%s ~ %s)' % (
            cd, NAMES[cd], len(df), df.index[0].date(), df.index[-1].date()))
        dfs[cd] = df
    n_before = {k: len(v) for k, v in dfs.items()}
    dfs = align_dataframes(dfs)
    n_nan = {k: int(v['open'].isna().sum()) for k, v in dfs.items()}
    print('  对齐后停牌/缺bar: ' + ', '.join(
        '%s=%d天(原%dbar)' % (k, n_nan[k], n_before[k]) for k in n_nan))

    print('\n[扫描] 真实一字涨停日(四值相等+触及板别幅度):')
    sealed_map = {}
    for cd in NAMES:
        days = scan_sealed(dfs[cd].dropna(), cd)
        sealed_map[cd] = days
        show = ', '.join('%s(%.2f→%.2f)' % d for d in days[:5])
        print('  %s %s: %d个  %s%s' % (
            cd, NAMES[cd], len(days), show, ' ...' if len(days) > 5 else ''))

    ok = sealed_intercept_test(dfs, sealed_map)
    gap, ev = run_compare(dfs)

    print('\n' + '=' * 64)
    print('结论: 真实一字板拦截%s | 理想化回测虚增 %.2f%% | 约束层事件明细见上'
          % ('✅' if ok else '❌', gap))
