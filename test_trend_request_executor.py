#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trend_request_executor 单元测试（PD-024①, 临时路径+mock, 禁碰真实台账/状态/真金白银）。

覆盖:
  a) pending 口径: 昨日落提请→入列 / 当日落提请→不入 / 已有终态行→不入 / 非 sell_request→不入 / 同键去重
  b) --list 输出含代码+名称+否决指引
  c) --veto: 匹配落 vetoed 行; 不匹配码返回1
  d) --execute 否决跳过 / 非持仓 skipped_not_held / 真值源失败 fail-closed rc=3 零卖出
  e) --execute 非交易日守卫 rc=0
  f) --execute 提交成功落 executed_submitted (mock subprocess)

运行: python test_trend_request_executor.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, r"D:/zcodesj")
import trend_request_executor as tre  # noqa: E402


def _tmp_pair():
    d = tempfile.mkdtemp(prefix="tre_test_")
    led, st = os.path.join(d, "led.jsonl"), os.path.join(d, "state.jsonl")
    return led, st


def _w(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _r(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _req(day, code, name="测试股"):
    return {"request_date": day, "code": code, "name": name,
            "action": "sell_request", "reason": "🔴 空头排列"}


class TRETests(unittest.TestCase):
    def setUp(self):
        self.led, self.state = _tmp_pair()
        self.patches = [
            mock.patch.object(tre, "LEDGER", self.led),
            mock.patch.object(tre, "STATE", self.state),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_a_pending_scope(self):
        today = time.strftime("%Y%m%d")
        yday = "20260930"
        _w(self.led, [
            _req(yday, "601866"),                 # 次晨语义 → 入列
            _req(today, "603637"),                # 当日提请 → 不入
            _req(yday, "600000"),                 # 已终态 → 不入
            {"request_date": yday, "code": "600001", "action": "executed_submitted"},  # 非提请行 → 不入
            _req(yday, "601866"),                 # 重复键 → 去重
        ])
        _w(self.state, [{"request_date": yday, "code": "600000", "action": "executed_submitted"}])
        pend = tre.pending_requests()
        self.assertEqual([(p["request_date"], p["code"]) for p in pend],
                         [(yday, "601866")])

    def test_b_list_output(self):
        _w(self.led, [_req("20260930", "601866", "中远海发")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = tre.cmd_list()
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("601866", out)
        self.assertIn("中远海发", out)
        self.assertIn("--veto", out)

    def test_c_veto(self):
        _w(self.led, [_req("20260930", "601866", "中远海发"),
                      _req("20260930", "603637", "镇海股份")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = tre.cmd_veto(["601866"])
        self.assertEqual(rc, 0)
        st = _r(self.state)
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]["action"], "vetoed")
        self.assertEqual(st[0]["code"], "601866")
        with redirect_stdout(io.StringIO()):
            rc2 = tre.cmd_veto(["999999"])
        self.assertEqual(rc2, 1)

    def test_d_execute_branches(self):
        _w(self.led, [_req("20260930", "601866", "中远海发"),   # 持仓中 → 提交
                      _req("20260930", "603637", "镇海股份")])  # 持仓中 → 先否决
        with redirect_stdout(io.StringIO()):
            tre.cmd_veto(["603637"])
        fake_held = {"601866": {"name": "中远海发", "qty": 5000}}
        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout="[OK] 提交\n", stderr="")

        with mock.patch.object(tre, "_westock_held", return_value=fake_held), \
             mock.patch.object(tre, "is_trading_day", create=True, return_value=True), \
             mock.patch("trade_day_guard.is_trading_day", return_value=True), \
             mock.patch.object(tre.subprocess, "run", side_effect=fake_run):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = tre.cmd_execute()
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("601866", out)
        self.assertIn("SKIP-VETOED", out)
        self.assertEqual(len(runs), 1)
        self.assertIn("sell", runs[0])
        acts = {s["code"]: s["action"] for s in _r(self.state)}
        self.assertEqual(acts["601866"], "executed_submitted")
        self.assertEqual(acts["603637"], "vetoed")

    def test_d3_pre_sell_block_true_label(self):
        """防线拦下时 executor rc=0 但 executed=false——必须记 blocked_pre_sell 而非伪记已提交(10/2彩排)"""
        _w(self.led, [_req("20260930", "600035", "楚天高速")])
        fake_held = {"600035": {"name": "楚天高速", "qty": 2000}}

        def fake_run(cmd, **kw):
            return types.SimpleNamespace(returncode=0, stdout='  "executed": false,\n  "reason": "防卖飞拦截(pre_sell HOLD): [C1低估值]"\n', stderr="")

        with mock.patch.object(tre, "_westock_held", return_value=fake_held), \
             mock.patch("trade_day_guard.is_trading_day", return_value=True), \
             mock.patch.object(tre.subprocess, "run", side_effect=fake_run):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = tre.cmd_execute()
        self.assertEqual(rc, 0)
        st = _r(self.state)
        self.assertEqual(st[0]["action"], "blocked_pre_sell")
        self.assertIn("防线拦下未卖", buf.getvalue())

    def test_d2_fail_closed(self):
        _w(self.led, [_req("20260930", "601866")])
        with mock.patch.object(tre, "_westock_held", side_effect=RuntimeError("token 失效")), \
             mock.patch("trade_day_guard.is_trading_day", return_value=True):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = tre.cmd_execute()
        self.assertEqual(rc, 3)
        self.assertEqual(_r(self.state), [])  # 零状态写入=零卖出

    def test_e_holiday_guard(self):
        _w(self.led, [_req("20260930", "601866")])
        with mock.patch("trade_day_guard.is_trading_day", return_value=False):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = tre.cmd_execute()
        self.assertEqual(rc, 0)
        self.assertIn("非交易日", buf.getvalue())
        self.assertEqual(_r(self.state), [])

    def test_f_not_held_skip(self):
        _w(self.led, [_req("20260930", "600999")])
        with mock.patch.object(tre, "_westock_held", return_value={}), \
             mock.patch("trade_day_guard.is_trading_day", return_value=True):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = tre.cmd_execute()
        self.assertEqual(rc, 0)
        st = _r(self.state)
        self.assertEqual(st[0]["action"], "skipped_not_held")


if __name__ == "__main__":
    unittest.main(verbosity=2)
