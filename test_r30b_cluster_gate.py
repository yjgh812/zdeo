#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_r30b_cluster 单元测试（PD-024②, westock 全 mock, 行业映射走真实库只读）。

覆盖:
  a) 簇占比 <20% → pass
  b) 20%<占比≤25% → warn(接近上限)
  c) 占比 >25% → reject(存量不动)
  d) 未超但拟买入名义推过 25% → reject(含拟买口径)
  e) owner_approve=True → reject 降 warn 留痕
  f) 代码无行业映射 → fail_open
  g) westock 真值源失败 → fail_open

运行: python test_r30b_cluster_gate.py
"""
from __future__ import annotations

import sqlite3
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, r"D:/zcodesj")
import pre_trade_check_v2 as ptc  # noqa: E402

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"


def _sector_and_members():
    con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    row = con.execute("SELECT sector_name FROM stock_sector_sw1 WHERE code='sh601866'").fetchone()
    if not row or not row[0]:
        con.close()
        return None, []
    sec = row[0]
    members = [r[0] for r in con.execute(
        "SELECT code FROM stock_sector_sw1 WHERE sector_name=? LIMIT 4", (sec,))]
    con.close()
    return sec, members


def _ws_factory(positions, total=1_000_000.0, fail=False):
    class FakeWS:
        def positions(self):
            if fail:
                raise RuntimeError("token 失效")
            return {"data": {"positions": positions}}

        def portfolio(self):
            return {"data": {"totalAssets": total}}
    return FakeWS


def _patch_ws(fake_cls):
    return mock.patch.dict(sys.modules, {"westock_client": types.SimpleNamespace(WeStockClient=fake_cls)})


class R30BTests(unittest.TestCase):
    def setUp(self):
        self.sec, self.members = _sector_and_members()
        if not self.sec or len(self.members) < 2:
            self.skipTest("行业映射库不可用或同簇成员不足")

    def _positions(self, each_value):
        pos = []
        for i, c in enumerate(self.members[:2]):
            c6 = c[-6:]
            pos.append({"code": c6, "quantity": each_value // 10, "currentPrice": 10.0})
        return pos

    def test_pass_below_20(self):
        ws = _ws_factory(self._positions(90_000))  # 2×9万=18万=18% <20%
        with _patch_ws(ws):
            r = ptc.check_r30b_cluster("601866", sector="", total_cash=1_000_000)
        self.assertEqual(r["status"], "pass")

    def test_warn_20_25(self):
        ws = _ws_factory(self._positions(110_000))  # 2×11万=22万 ∈ (20%,25%]
        with _patch_ws(ws):
            r = ptc.check_r30b_cluster("601866", sector="", total_cash=1_000_000)
        self.assertEqual(r["status"], "warn")

    def test_reject_over_25(self):
        ws = _ws_factory(self._positions(160_000))  # 32万 >25%
        with _patch_ws(ws):
            r = ptc.check_r30b_cluster("601866", sector="", total_cash=1_000_000)
        self.assertEqual(r["status"], "reject")
        self.assertIn("存量不动", r["note"])

    def test_reject_notional_push_over(self):
        ws = _ws_factory(self._positions(110_000))  # 22万, 加 4万拟买 → 26% >25%
        with _patch_ws(ws):
            r = ptc.check_r30b_cluster("601866", sector="", notional=40_000, total_cash=1_000_000)
        self.assertEqual(r["status"], "reject")
        self.assertIn("含拟买", r["note"])

    def test_owner_approve_downgrade(self):
        ws = _ws_factory(self._positions(160_000))
        with _patch_ws(ws):
            r = ptc.check_r30b_cluster("601866", sector="", total_cash=1_000_000, owner_approve=True)
        self.assertEqual(r["status"], "warn")
        self.assertIn("特批豁免", r["note"])

    def test_no_mapping_fail_open(self):
        ws = _ws_factory([])
        with _patch_ws(ws):
            r = ptc.check_r30b_cluster("000000", sector="", total_cash=1_000_000)
        self.assertEqual(r["status"], "fail_open")

    def test_westock_fail_open(self):
        ws = _ws_factory([], fail=True)
        with _patch_ws(ws):
            r = ptc.check_r30b_cluster("601866", sector=self.sec, total_cash=1_000_000)
        self.assertEqual(r["status"], "fail_open")


if __name__ == "__main__":
    unittest.main(verbosity=2)
