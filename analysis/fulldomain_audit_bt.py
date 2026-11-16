#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_audit_bt.py — c1 之 bt 引擎抽查（30笔填充同规则replay）。"""
from __future__ import annotations

import csv
import os
import random
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import backtrader as bt
import pandas as pd

OUT = r"D:\zcodesj\data\fulldomain"
MODELS = ("M1_box_breakout", "M2_trend_pullback",
          "M3_value_monthly", "M4_momentum_weekly")


def _read(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    fills = []
    for m in MODELS:
        for t in _read(os.path.join(OUT, f"trades_{m}.csv")):
            if 20240101 <= int(t["entry_d"]) <= 20241231:
                fills.append(t)
    random.seed(7)
    sample = random.sample(fills, min(30, len(fills)))
    devs, dates_ok = [], 0
    for t in sample:
        code = t["code"]
        want_px = float(t["entry_px"])
        want_d = int(t["entry_d"])
        import sqlite3
        conn = sqlite3.connect(
            "file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
            + "?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM stock_daily_raw "
            "WHERE code=? AND date<=? ORDER BY date DESC LIMIT 3",
            (code, want_d)).fetchall()
        conn.close()
        rows.reverse()
        if len(rows) < 2:
            continue
        # 目标成交bar=rows[-1](=want_d); 提交bar=rows[-2]
        if int(rows[-1][0]) != want_d:
            continue
        idx = [datetime.strptime(str(r[0]), "%Y%m%d") for r in rows]
        df = pd.DataFrame([[r[1], r[2], r[3], r[4], r[5]] for r in rows],
                          index=idx,
                          columns=["open", "high", "low", "close", "volume"])
        cerebro = _cerebro(df, code)
        px = _run_once(cerebro)
        if px is None:
            continue
        if abs(px - want_px) <= max(0.005, want_px * 3e-4):
            dates_ok += 1
        devs.append(abs(px - want_px) / want_px * 100)
    med = sorted(devs)[len(devs) // 2] if devs else None
    print(f"[AUDIT-BT] 抽查{len(devs)}/{len(sample)} 日期+价格匹配={dates_ok} "
          f"价格偏差中位={med if med is None else round(med, 3)}pp")
    return 0


def _cerebro(df, code):
    from bt_ashare_realistic import build_cerebro
    c = build_cerebro(cash=200_000.0, minute_fill=False,
                      slippage_perc=0.001)
    import backtrader as bt
    import pandas as pd
    data = bt.feeds.PandasData(dataname=df, name=code)
    c.adddata(data, name=code)
    return c


def _run_once(cerebro):
    import backtrader as bt
    box = {}

    class S(bt.Strategy):
        def __init__(self):
            self.done = False

        def next(self):
            if not self.done and len(self.data) >= 2:
                self.buy(size=100)
                self.done = True

        def notify_order(self, order):
            if order.status == order.Completed and "px" not in box:
                box["px"] = order.executed.price
    cerebro.addstrategy(S)
    cerebro.run()
    return box.get("px")


if __name__ == "__main__":
    raise SystemExit(main())