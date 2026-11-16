#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R34脉冲次日资金/R35卖后回补冷却闸 单元测试（PD-018②③, sqlite路由桩, 零真实数据依赖）。

R34覆盖: 脉冲→warn / 非脉冲→clear / 映射缺失→fail_open / 库异常→fail_open
R35覆盖: 无当日卖出→clear / 间隔不足→reject / 价格反向T→reject / 双条件满足→clear /
         特批→warn / created_at坏→fail_open / 无买入价→fail_open

运行: python test_r34_r35_gates.py
"""
from __future__ import annotations

import sqlite3
import sys
import types
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, r"D:/zcodesj")
import pre_trade_check_v2 as ptc  # noqa: E402


class _StubCursor:
    def __init__(self, one=None, rows=None):
        self._one, self._rows = one, rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _StubCon:
    def __init__(self, one=None, rows=None):
        self._one, self._rows = one, rows or []

    def execute(self, sql, *a, **kw):
        return _StubCursor(self._one, self._rows)

    def close(self):
        pass


def _route(meta_one=None, meta_rows=None, flow_one=None, flow_rows=None,
           attr_one=None, attr_rows=None):
    meta = _StubCon(one=meta_one, rows=meta_rows or [])
    flow = _StubCon(one=flow_one, rows=flow_rows or [])
    attr = _StubCon(one=attr_one, rows=attr_rows or [])

    def fake_connect(path, *a, **kw):
        if "dragon_quant_meta" in path:
            return meta
        if "em_flow_snapshot" in path:
            return flow
        if "attribution" in path:
            return attr
        raise AssertionError(f"unexpected db: {path}")
    return fake_connect


class R34Tests(unittest.TestCase):
    def _meta(self):
        # 601866→交通运输; 成员 sh601866/sh600018
        return _StubCon(one=("交通运输",), rows=[("sh601866", "交通运输"), ("sh600018", "交通运输")])

    def test_pulse_warn(self):
        flow = _StubCon(one=("20260930",), rows=[("601866", 7e8), ("600018", 6e8)])  # 13亿
        fc = _route(meta_one=("交通运输",), meta_rows=[("sh601866", "交通运输"), ("sh600018", "交通运输")],
                    flow_one=("20260930",), flow_rows=[("601866", 7e8), ("600018", 6e8)])
        with mock.patch.object(sqlite3, "connect", side_effect=fc):
            r = ptc.check_r34_pulse_nextday("601866")
        self.assertEqual(r["status"], "warn")
        self.assertIn("脉冲", r["note"])

    def test_no_pulse_clear(self):
        fc = _route(meta_one=("交通运输",), meta_rows=[("sh601866", "交通运输")],
                    flow_one=("20260930",), flow_rows=[("601866", 3e8)])
        with mock.patch.object(sqlite3, "connect", side_effect=fc):
            r = ptc.check_r34_pulse_nextday("601866")
        self.assertEqual(r["status"], "clear")

    def test_no_mapping_fail_open(self):
        fc = _route(meta_one=(None,), meta_rows=[], flow_one=("20260930",), flow_rows=[])
        with mock.patch.object(sqlite3, "connect", side_effect=fc):
            r = ptc.check_r34_pulse_nextday("601866", sector="")
        self.assertEqual(r["status"], "fail_open")

    def test_db_error_fail_open(self):
        def boom(path, *a, **kw):
            raise RuntimeError("db locked")
        with mock.patch.object(sqlite3, "connect", side_effect=boom):
            r = ptc.check_r34_pulse_nextday("601866")
        self.assertEqual(r["status"], "fail_open")


class R35Tests(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 14, 0, 0)

    def _attr(self, rows):
        return _route(attr_one=("_",), attr_rows=rows)

    def test_no_sell_clear(self):
        with mock.patch.object(sqlite3, "connect", side_effect=self._attr([])):
            r = ptc.check_r35_resell_cooldown("300977", price=20.5, now=self.NOW)
        self.assertEqual(r["status"], "clear")

    def test_gap_and_price_reject(self):
        rows = [(20.89, "2026-10-08T13:53:00")]  # 距14:00仅7分钟
        with mock.patch.object(sqlite3, "connect", side_effect=self._attr(rows)):
            r = ptc.check_r35_resell_cooldown("300977", price=20.95, now=self.NOW)
        self.assertEqual(r["status"], "reject")

    def test_price_reverse_t_reject(self):
        rows = [(20.89, "2026-10-08T13:00:00")]  # 间隔60分钟够, 但买价≥卖价
        with mock.patch.object(sqlite3, "connect", side_effect=self._attr(rows)):
            r = ptc.check_r35_resell_cooldown("300977", price=20.95, now=self.NOW)
        self.assertEqual(r["status"], "reject")
        self.assertIn("反向T", r["note"])

    def test_compliant_clear(self):
        rows = [(20.89, "2026-10-08T13:00:00")]  # 60分钟 + 买价20.50<20.89
        with mock.patch.object(sqlite3, "connect", side_effect=self._attr(rows)):
            r = ptc.check_r35_resell_cooldown("300977", price=20.50, now=self.NOW)
        self.assertEqual(r["status"], "clear")

    def test_owner_approve_warn(self):
        rows = [(20.89, "2026-10-08T13:53:00")]
        with mock.patch.object(sqlite3, "connect", side_effect=self._attr(rows)):
            r = ptc.check_r35_resell_cooldown("300977", price=20.95, now=self.NOW, owner_approve=True)
        self.assertEqual(r["status"], "warn")

    def test_bad_created_at_fail_open(self):
        rows = [(20.89, "not-a-time")]
        with mock.patch.object(sqlite3, "connect", side_effect=self._attr(rows)):
            r = ptc.check_r35_resell_cooldown("300977", price=20.95, now=self.NOW)
        self.assertEqual(r["status"], "fail_open")

    def test_no_price_fail_open(self):
        rows = [(20.89, "2026-10-08T13:53:00")]
        with mock.patch.object(sqlite3, "connect", side_effect=self._attr(rows)):
            r = ptc.check_r35_resell_cooldown("300977", price=None, now=self.NOW)
        self.assertEqual(r["status"], "fail_open")


if __name__ == "__main__":
    unittest.main(verbosity=2)
