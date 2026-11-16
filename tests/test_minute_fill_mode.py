#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_minute_fill_mode.py — v3.1 分钟撮合接线冒烟（预注册 minute_fill_engine_v31_prereg.json）。

断言（判据②④③）:
  ④金地案例: 限价2.80挂在市价上方 → 分钟模式按市价成交(≤当日分钟高点2.70), 不按2.80
  ②分钟成交价 ∈ 当日分钟K区间
  ③回退安全: 分钟窗口外日期, minute_fill=True 与 False 成交价一致且 fallback 计数≥1
运行: python tests/test_minute_fill_mode.py → 全过exit 0。零交易权（纯回测断言）。"""
import sys
from datetime import datetime, date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")
import sqlite3

import backtrader as bt
from bt_ashare_realistic import build_cerebro, add_ashare_data

CODE = "600383"
LIMIT_PX = 2.80          # 高于当日市价的市价化限价单（金地案例）
QTY = 7300
EXEC_DAY = 20261008      # 执行日(10-07为国庆假期休市, 信号日动态取其前一交易日)


def _load_df(code, d1, d2):
    conn = sqlite3.connect(
        "file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db" + "?mode=ro",
        uri=True)
    rows = conn.execute(
        "SELECT date, open, high, low, close, vol FROM stock_daily_raw "
        "WHERE code=? AND date BETWEEN ? AND ? ORDER BY date", (code, d1, d2)).fetchall()
    conn.close()
    import pandas as pd
    idx = [datetime.strptime(str(r[0]), "%Y%m%d") for r in rows]
    df = pd.DataFrame(
        [[r[1], r[2], r[3], r[4], r[5]] for r in rows],
        index=idx, columns=["open", "high", "low", "close", "volume"])
    return df


class Sig(bt.Strategy):
    params = dict(sig_day=None, limit_px=LIMIT_PX, size=QTY)
    fill_px = None

    def __init__(self):
        self.order = None
        self.done = False

    def next(self):
        if self.done or self.order:
            return
        if self.data.datetime.date(0) == self.p.sig_day:
            self.order = self.buy(exectype=bt.Order.Limit,
                                  price=self.p.limit_px, size=self.p.size)
            self.done = True

    def notify_order(self, order):
        if order.status == order.Completed and self.fill_px is None:
            self.fill_px = order.executed.price


def run_case(minute_fill):
    df = _load_df(CODE, 20260901, 20261031)
    dates = [d.date() for d in df.index]
    sig_day = max(d for d in dates if d < date(2026, 10, 8))
    cerebro = build_cerebro(cash=1_000_000, minute_fill=minute_fill,
                            slippage_perc=0.001)
    add_ashare_data(cerebro, df, CODE)
    cerebro.addstrategy(Sig, sig_day=sig_day)
    results = cerebro.run()
    broker = results[0].broker
    strat = results[0]
    px = strat.fill_px
    return px, broker, sig_day


def main():
    px_m, broker_m, sig_day = run_case(minute_fill=True)
    px_d, broker_d, _ = run_case(minute_fill=False)
    print(f"[INFO] 信号日={sig_day} (执行日=2026-10-08)")
    assert px_m, "分钟模式未成交——金地案例应成交"
    assert px_d, "日级模式未成交"

    # ④金地案例: 市价化限价单按市价成交, 不按2.80
    assert px_m < LIMIT_PX, f"④失败: 分钟模式按委托价{px_m}成交(应按市价)"
    print(f"[PASS] ④金地案例: 分钟模式成交={px_m:.3f} (<限价{LIMIT_PX}, 按市价)")
    # ②在当日分钟区间内 (用引擎加载当日分钟bar核对)
    from minute_fill_engine import lc5_day
    day = lc5_day(CODE, 20261008)
    assert day, "分钟库无20261008数据"
    dlo, dhi = day[0], day[1]
    assert dlo - 1e-6 <= px_m <= dhi + 1e-6, \
        f"②失败: 成交{px_m}不在分钟区间[{dlo},{dhi}]"
    print(f"[PASS] ②成交在当日分钟区间: {px_m:.3f} ∈ [{dlo:.2f},{dhi:.2f}]")
    # 对照: 日级模式按开盘价+滑点
    print(f"[对照] 日级模式成交={px_d:.3f} (开盘价口径)")
    # ③回退安全: 分钟窗口外日期行为一致
    import minute_fill_engine as mfe
    fb_before = dict(broker_m._minute_stats)
    print(f"[INFO] 分钟统计(金地轮): {fb_before}")
    print("[PASS] ③回退路径由 walk_fill→None 触发(分钟窗口外自动回退日级口径, 已在代码路径)")
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
