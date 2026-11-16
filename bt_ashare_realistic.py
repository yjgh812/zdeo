# -*- coding: utf-8 -*-
"""
bt_ashare_realistic — backtrader A股真实交易约束层
================================================

把 backtrader 的回测口径贴近 A股实盘，已按屠龙系统历史教训逐条对齐：

约束                | 实现方式                                  | 对应教训
--------------------|------------------------------------------|------------------------------
T+1                 | broker 强制：当日买入份额当日不可卖       | T+0回测口径错误闭环(判死清单)
成交价口径          | 信号T日收盘产生 → T+1开盘价成交(默认)     | 持有期必须≥2交易日
涨跌停封板          | 全天封死涨停买单不成交/跌停卖单不成交     | R1涨停追入十年回测WRONG
停牌                | NaN行/主时钟双检，停牌日订单作废          | V4.3停牌天数bug(+52%假象)
整手                | 买入须100整数倍；零股只能一次性全卖       |
禁止卖空            | 卖出>持仓直接废单                         |
真实费用            | 佣金万2.5(最低5元)+过户费万0.1双向        |
                    | +印花税0.05%仅卖出                       | V7:滑点>3bps即亏损
成交量上限          | 成交量≤当日volume×vol_cap(默认25%)        |
当日有效            | 市价/限价单当日未成交作废(止损族除外)     | A股委托当日有效
滑点                | 默认0.1%不利方向(bar范围内匹配)           |

快速上手::

    import backtrader as bt
    from bt_ashare_realistic import build_cerebro, add_ashare_data, AShareSizer

    cerebro = build_cerebro(cash=1_000_000, slippage_perc=0.001)
    add_ashare_data(cerebro, df, '600519')          # df: open/high/low/close/volume
    cerebro.addstrategy(MyStrategy)
    cerebro.addsizer(AShareSizer, percents=95)
    results = cerebro.run()
    results[0].broker.print_event_summary()         # 废单/封板/停牌统计

多股票必须先对齐日历(停牌=NaN行)::

    from bt_ashare_realistic import align_dataframes
    dfs = align_dataframes({'600519': df1, '000001': df2, ...})
    for code, df in dfs.items(): add_ashare_data(cerebro, df, code)

注意：build_cerebro 默认 runonce=False —— runonce 模式下多股票按索引而非
日期同步，停牌会错位；runnext 模式按主时钟同步，是贴近真实的默认。

止损单(Stop/StopTrail族)默认跨日有效：语义等同"券商条件单每日自动重挂"，
符合实盘止损体系；可用 day_only=False 统一改为全单当日有效。
"""

import collections
import math

import backtrader as bt
from backtrader.order import Order

import market_rules_config as mrc
import minute_fill_engine

__all__ = [
    'AShareCommission', 'AShareVolumeFiller', 'AShareBroker', 'AShareSizer',
    'align_dataframes', 'add_ashare_data', 'build_cerebro',
    'limit_pct_for_code',
]


# ---------------------------------------------------------------- 费用结构

class AShareCommission(bt.CommInfoBase):
    """A股费用：佣金(最低5元,双向) + 过户费(万0.1,双向) + 印花税(0.05%,仅卖出)。

    backtrader 调用 getcommission(size, price) 时 size 恒为正、不区分买卖，
    印花税方向由 AShareBroker._execute 在成交前通过 set_sell_flag 注入。
    """
    params = (
        ('commission', 0.00025),    # 佣金 万2.5
        ('min_commission', 5.0),    # 单笔最低5元(按每次成交计,偏保守)
        ('stamp_duty', 0.0005),     # 印花税 千0.5 仅卖出
        ('transfer_fee', 0.00001),  # 过户费 万0.1 双向
        ('stocklike', True),
        ('commtype', bt.CommInfoBase.COMM_PERC),
        ('percabs', True),
    )

    _is_sell = False
    _trade_date = None

    def set_sell_flag(self, is_sell, trade_date=None):
        """注入方向与成交日期；印花税按日期分段（2023-08-28 减半）由配置模块裁决。"""
        self._is_sell = bool(is_sell)
        self._trade_date = trade_date

    def _getcommission(self, size, price, pseudoexec):
        value = abs(size) * price
        if value <= 0:
            return 0.0
        comm = max(value * self.p.commission, self.p.min_commission)
        comm += value * self.p.transfer_fee
        if self._is_sell:
            rate = (mrc.stamp_duty_rate(self._trade_date)
                    if self._trade_date else self.p.stamp_duty)
            comm += value * rate
        return comm


# ---------------------------------------------------------------- 成交量上限

class AShareVolumeFiller(object):
    """单bar成交量上限：成交股数 ≤ 当日volume × cap，且按手取整。"""

    def __init__(self, cap=0.25, lot=100):
        self.cap = float(cap)
        self.lot = int(lot)

    def __call__(self, order, price, ago):
        try:
            vol = order.data.volume[ago]
        except Exception:
            return abs(order.executed.remsize)
        if vol is None or vol != vol or vol <= 0:   # NaN/无量 → 无法成交
            return 0
        maxq = int(vol * self.cap) // self.lot * self.lot
        # 卖单的 remsize 为负，此处必须返回正数数量(broker会自行取负)
        return min(abs(order.executed.remsize), maxq)


# ---------------------------------------------------------------- 涨跌停幅度

def limit_pct_for_code(code, trade_date=None):
    """按6位代码×交易日期识别涨跌停幅度：主板10% / 创业板20%（2020-08-24前10%）/
    科创板20%（开市即20%）/ 北交所30%。分段规则唯一来源=market_rules_config。"""
    return mrc.limit_pct(code, trade_date)


# ---------------------------------------------------------------- 真实Broker

class AShareBroker(bt.brokers.BackBroker):
    """A股约束Broker：T+1 / 涨跌停封板 / 停牌 / 整手 / 禁卖空 / 当日有效。

    只重写四个稳定入口，不碰易变的内部执行细节：
      submit()    —— 委托废单检查(整手/卖空/T+1可卖)
      _try_exec() —— 执行前闸门(停牌/封死涨跌停) + 当日有效作废
      _execute()  —— T+1买入登记 + 印花税方向注入(所有成交必经之路)
    """
    params = (
        ('t1', True),                # T+1
        ('enforce_limit', True),     # 涨跌停封板拒成交
        ('enforce_round_lot', True), # 整手
        ('forbid_short', True),      # 禁止卖空
        ('day_only', True),          # 市价/限价单当日有效(止损族除外)
        ('lot_size', 100),
        ('limit_pcts', None),        # {代码: 幅度} 覆盖，如 {'600519':0.10, 'xxx':0.05(ST)}
        ('minute_fill', False),      # 分钟撮合模式: T+1执行走walk_fill盘中撮合(数据缺失回退日级)
    )

    def __init__(self):
        super().__init__()
        self._t1_buys = {}          # data -> (买入日期, 当日买入股数)
        self.event_log = []         # 废单/作废明细
        self.event_stats = collections.Counter()
        self._minute_stats = collections.Counter()  # 分钟模式: filled/expired/fallback/cap_short

    # ---- 工具 ----

    def _log_event(self, order, kind, reason):
        d = order.data
        self.event_log.append({
            'date': str(d.datetime.date(0)),
            'code': d._name or '?',
            'side': 'BUY' if order.isbuy() else 'SELL',
            'kind': kind, 'reason': reason,
        })
        self.event_stats['%s:%s' % (kind, reason)] += 1

    def _reject_order(self, order, reason):
        order.reject()
        self._log_event(order, 'REJECT', reason)
        self.notify(order)
        return order

    def _dayfail(self, order, reason):
        """当日有效：作废剩余委托(已成交部分保留)。"""
        order.cancel()
        self._log_event(order, 'DAYFAIL', reason)
        self.notify(order)
        self._ococheck(order)
        self._bracketize(order, cancel=True)

    def _limit_pct(self, data):
        pcts = self.p.limit_pcts or {}
        name = getattr(data, '_name', None) or ''
        if name in pcts:
            return pcts[name]
        d = data.datetime.date(0)
        tds = int(d.strftime('%Y%m%d')) if d else None
        return limit_pct_for_code(name, tds)

    def _is_suspended(self, data):
        # 停牌三检：NaN行(对齐缺失) / 无量(bar成交量≤0) / 主时钟不同步(runnext)
        o = data.open[0]
        if o is None or o != o:
            return True
        v = data.volume[0]
        if v is None or v != v or v <= 0:
            return True
        m = getattr(self.cerebro, '_dtmaster', None)
        if m is not None and data.datetime.date(0) != m.date():
            return True
        return False

    def _sealed_sides(self, data):
        """返回(封死涨停, 封死跌停)：全天最低价钉在涨停价上方/最高价钉在跌停价下方。"""
        prev = data.close[-1]
        if prev is None or prev != prev or prev <= 0:
            return False, False
        pct = self._limit_pct(data)
        lim_up = prev * (1.0 + pct)
        lim_dn = prev * (1.0 - pct)
        tol = max(lim_up * 1e-3, 1e-6)   # 容差：覆盖涨跌停价四舍五入到分的舍入误差(低价股~9e-4)+前复权幅度近似
        lo, hi = data.low[0], data.high[0]
        if lo is None or hi is None or lo != lo or hi != hi:
            return False, False
        return lo >= lim_up - tol, hi <= lim_dn + tol

    # ---- T+1 ----

    def _t1_add(self, data, qty):
        d0 = data.datetime.date(0)
        rec = self._t1_buys.get(data)
        if rec and rec[0] == d0:
            self._t1_buys[data] = (d0, rec[1] + qty)
        else:
            self._t1_buys[data] = (d0, qty)

    def _t1_locked(self, data):
        if not self.p.t1:
            return 0
        rec = self._t1_buys.get(data)
        if not rec or rec[0] != data.datetime.date(0):
            return 0    # 跨日自动解锁
        return rec[1]

    # ---- 重写入口 ----

    def submit(self, order, check=True):
        size = order.created.size or 0
        pos = self.getposition(order.data)
        if size > 0:
            if self.p.enforce_round_lot and size % self.p.lot_size:
                return self._reject_order(
                    order, '买入%d股非%d整数倍' % (size, self.p.lot_size))
        elif size < 0:
            ssize = -size
            if self.p.forbid_short and ssize > pos.size:
                return self._reject_order(
                    order, '卖出%d股>持仓%d股(禁止卖空)' % (ssize, pos.size))
            locked = self._t1_locked(order.data)
            if self.p.t1 and ssize > pos.size - locked:
                return self._reject_order(
                    order, 'T+1限制:可卖%d股<委托%d股' % (pos.size - locked, ssize))
            if (self.p.enforce_round_lot
                    and ssize % self.p.lot_size and ssize != pos.size):
                return self._reject_order(
                    order, '卖出%d股非整手且非全仓(零股须一次性卖出)' % ssize)
        return super().submit(order, check)

    def _minute_exec(self, order, data):
        """分钟撮合分支（v3.1, 预注册 minute_fill_engine_v31_prereg.json）。
        返回 'fallback'(无分钟数据,回退日级) / 'expired'(当日无可成交) / 成交均价。"""
        code6 = ''.join(ch for ch in (getattr(data, '_name', '') or '')
                        if ch.isdigit())[:6]
        dd = data.datetime.date(0)
        d_int = int(dd.strftime('%Y%m%d')) if dd else 0
        act = 'BUY' if order.isbuy() else 'SELL'
        qty = abs(int(order.created.size or 0))
        if order.exectype == Order.Limit and order.created.price:
            lim = float(order.created.price)
        else:
            lim = 1e9 if act == 'BUY' else 1e-4
        w = minute_fill_engine.walk_fill(code6, d_int, act, lim, qty)
        if w is None:
            self._minute_stats['fallback'] += 1
            return 'fallback'
        if w['filled'] <= 0:
            self._minute_stats['expired'] += 1
            return 'expired'
        if w['filled'] < qty:
            self._minute_stats['cap_short'] += 1
        self._minute_stats['filled'] += 1
        return w['avg']

    def _try_exec(self, order):
        data = order.data
        stop_family = order.exectype in (
            Order.Stop, Order.StopTrail, Order.StopLimit, Order.StopTrailLimit)
        ttl = self.p.day_only and not stop_family
        # 首个可执行bar = 创建bar的下一根(信号T收盘 → T+1执行)
        first_opp = data.datetime[0] > order.created.dt

        if first_opp:
            if self._is_suspended(data):
                if ttl:
                    return self._dayfail(order, '停牌无法成交')
                return    # 条件单继续挂单等待复牌
            if self.p.enforce_limit:
                sealed_up, sealed_dn = self._sealed_sides(data)
                if order.isbuy() and sealed_up:
                    if ttl:
                        return self._dayfail(order, '全天封死涨停')
                    return
                if not order.isbuy() and sealed_dn:
                    if ttl:
                        return self._dayfail(order, '全天封死跌停')
                    return
            # ── v3.1 分钟撮合分支(市价/限价单; 止损族维持原语义) ──
            if self.p.minute_fill and not stop_family:
                r = self._minute_exec(order, data)
                if r == 'fallback':
                    pass                  # 无分钟数据→回退日级撮合(计数已记)
                elif r == 'expired':
                    if ttl:
                        return self._dayfail(order, '分钟撮合:当日无可成交(量帽/未触达)')
                    return
                else:
                    return self._execute(order, ago=0, price=r)

        super()._try_exec(order)

        if ttl and first_opp and order.alive():
            self._dayfail(order, '当日有效(收盘未成交作废)')

    def _execute(self, order, ago=None, price=None, cash=None,
                 position=None, dtcoc=None):
        comminfo = self.getcommissioninfo(order.data)
        set_flag = getattr(comminfo, 'set_sell_flag', None)
        if set_flag:
            d = order.data.datetime.date(0)
            set_flag(not order.isbuy(), d.strftime('%Y%m%d') if d else None)
        pre_rem = order.executed.remsize if ago is not None else 0
        try:
            return super()._execute(order, ago=ago, price=price, cash=cash,
                                    position=position, dtcoc=dtcoc)
        finally:
            if set_flag:
                set_flag(False)
            if ago is not None and order.isbuy() and self.p.t1:
                filled = pre_rem - order.executed.remsize
                if filled > 0:
                    self._t1_add(order.data, filled)

    def print_event_summary(self):
        if sum(self._minute_stats.values()):
            print('[bt_ashare] 分钟模式统计: %s' % dict(self._minute_stats))
        if not self.event_stats:
            print('[bt_ashare] 无废单/作废事件')
            return
        print('[bt_ashare] 委托事件统计:')
        for k, v in self.event_stats.most_common():
            print('  %-4d × %s' % (v, k))


# ---------------------------------------------------------------- Sizer

class AShareSizer(bt.Sizer):
    """按可用资金比例计算整手买入量，预留费用与滑点缓冲；卖出=全部持仓。"""
    params = (
        ('percents', 95.0),
        ('lot', 100),
        ('fee_buffer', 0.002),      # 费用+滑点缓冲
    )

    def _getsizing(self, comminfo, cash, data, isbuy):
        if not isbuy:
            return self.broker.getposition(data).size
        price = data.close[0]
        if price is None or price != price or price <= 0:
            return 0
        budget = min(cash, self.broker.getvalue() * self.p.percents / 100.0)
        unit_cost = price * (1.0 + self.p.fee_buffer)
        lots = int(budget / (unit_cost * self.p.lot))
        return lots * self.p.lot


# ---------------------------------------------------------------- 数据

def align_dataframes(dfs):
    """多股票日历对齐：缺失日(停牌)插入NaN行。dict[code -> df] -> dict[code -> df]。"""
    idx = None
    for df in dfs.values():
        idx = df.index if idx is None else idx.union(df.index)
    return {code: df.reindex(idx.sort_values()) for code, df in dfs.items()}


def add_ashare_data(cerebro, df, name):
    """接入一只股票：df 需含 open/high/low/close/volume 列(大小写均可)。"""
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    missing = {'open', 'high', 'low', 'close', 'volume'} - set(df.columns)
    if missing:
        raise ValueError('df缺少列: %s' % missing)
    data = bt.feeds.PandasData(dataname=df, name=name)
    cerebro.adddata(data, name=name)
    return data


# ---------------------------------------------------------------- 工厂

def build_cerebro(cash=1_000_000,
                  commission=0.00025, min_commission=5.0,
                  stamp_duty=0.0005, transfer_fee=0.00001,
                  slippage_perc=0.001, vol_cap=0.25,
                  t1=True, enforce_limit=True, enforce_round_lot=True,
                  forbid_short=True, day_only=True, limit_pcts=None,
                  minute_fill=False, **cerebro_kwargs):
    """一键构建贴近A股实盘的 cerebro。

    slippage_perc=0 / vol_cap=0 可分别关闭滑点与成交量上限。
    minute_fill=True: T+1执行升级为分钟盘中撮合(walk_fill, v3.1)——成交价已含真实
    摩擦,不再叠加slippage_perc(防双计);分钟数据缺失自动回退日级口径并计数。
    其余 cerebro_kwargs 透传给 bt.Cerebro(如 stdstats=False)。
    """
    cerebro_kwargs.setdefault('runonce', False)   # runnext按日期同步,停牌不错位
    cerebro = bt.Cerebro(**cerebro_kwargs)

    broker = AShareBroker(
        t1=t1, enforce_limit=enforce_limit,
        enforce_round_lot=enforce_round_lot, forbid_short=forbid_short,
        day_only=day_only, limit_pcts=limit_pcts or {}, minute_fill=minute_fill)
    broker.set_cash(cash)
    if slippage_perc and slippage_perc > 0:
        broker.set_slippage_perc(perc=slippage_perc,
                                 slip_open=True, slip_match=True, slip_out=False)
    if vol_cap and vol_cap > 0:
        broker.set_filler(AShareVolumeFiller(cap=vol_cap))
    broker.addcommissioninfo(AShareCommission(
        commission=commission, min_commission=min_commission,
        stamp_duty=stamp_duty, transfer_fee=transfer_fee))

    cerebro.broker = broker
    return cerebro
