#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_manual_buy_note_guard.py — manual 特批买入留痕闸回归套件(2026-10-15)

固化 20261015 四维复盘执行缺陷②修复(主人「继续」令):
  ① manual_confirmed + --execute + 空/空白 --note → REJECT(留痕闸, 归因可追溯宪法§9)
  ② dry-run 不拦(允许试算) / 非 manual 源不拦(系统源有 source 落台账溯源)
  ③ do_buy 调用点位于任何 ws 调用之前(REJECT 零副作用) + 台账 log_submitted 带 note
用法: python tests/test_manual_buy_note_guard.py   (无 --log, 不污染 guard_precheck_log)
退出码: 0=全过 / 1=有失败
"""
from __future__ import annotations

import argparse
import inspect
import sys
import unittest
from unittest import mock

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace")

import trade_executor as te


class FakeWS:
    """最小 mock(同 test_poa_source_gate): 不触网。"""

    def positions(self):
        return {"data": {"positions": []}}

    def portfolio(self):
        return {"data": {"portfolio": {"totalAssets": 100000}}}

    def quote(self, code):
        return {"data": {}}


class ManualNoteGuardTests(unittest.TestCase):

    def _mk_args(self, source="manual_confirmed", execute=True, note="", **kw):
        a = argparse.Namespace(
            code="000975", name="山金国际", source=source, sector="", price=None,
            volume=None, amount=5000.0, execute=execute, note=note,
            prev_close=None, pct_60d=None, amplitude_40d=None, sector_breadth=None,
            intent="", allow_add_position=False, limit_price=None,
            # 2026-11-05 ZONE-GATE(买卖区域闸)上线后, 本套件用例票不在位置池会被先拦,
            # 到不了留痕闸层 → 用例统一携带 owner_approve_zone 豁免(与本套件单测定位一致:
            # 只测留痕闸层, 显式绕过与留痕闸层无关的闸; 同 owner_override_circuit 先例)
            owner_approve_zone=True,
            # 2026-11-07: PAPER-OBS-GATE(模拟盘观察闸)登记上线后, manual_confirmed 源会被
            # 先拦到不了留痕闸层 → 本套件只测留痕闸层, 携带 paper-obs 特批豁免
            # (与 owner_approve_zone 同先例: 显式绕过与本套件无关的闸)
            owner_approve_paper_obs=True,
            # 测试豁免通道(代码注释明示): 本套件只测留痕闸层, 显式绕过 PD-037 熔断/冷却期
            # (2026-10-15 实锤: S-5≥2笔次日冷却期会拦一切 execute 用例, 环境态脆弱)
            owner_override_circuit=True)
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_manual_execute_blank_note_rejected(self):
        # mock 时段硬闸(留痕闸位于其后, 单测不依赖运行时刻)
        with mock.patch.object(te, "_trading_session_gate", return_value=None):
            r = te.do_buy(self._mk_args(note=""), FakeWS())
        self.assertEqual(r.get("gate_verdict"), "REJECT")
        self.assertIn("manual特批留痕", str(r.get("gate_rejects")))

    def test_manual_execute_whitespace_note_rejected(self):
        with mock.patch.object(te, "_trading_session_gate", return_value=None):
            r = te.do_buy(self._mk_args(note="   "), FakeWS())
        self.assertEqual(r.get("gate_verdict"), "REJECT")
        self.assertIn("manual特批留痕", str(r.get("gate_rejects")))

    def test_guard_passes_with_note(self):
        self.assertIsNone(te._manual_note_guard(self._mk_args(note="主人令防守单")))

    def test_guard_passes_dry_run(self):
        self.assertIsNone(te._manual_note_guard(self._mk_args(execute=False, note="")))

    def test_guard_ignores_non_manual_source(self):
        self.assertIsNone(te._manual_note_guard(
            self._mk_args(source="breakout_scout", note="")))

    def test_guard_tolerates_missing_note_attr(self):
        a = self._mk_args()
        del a.note  # 旧调用方不传 --note 时 getattr 默认兜底, 不得 AttributeError
        self.assertIsNotNone(te._manual_note_guard(a))

    def test_dry_run_not_blocked_by_guard(self):
        with mock.patch.object(te, "_trading_session_gate", return_value=None), \
             mock.patch.object(te, "run_gate", return_value={
                 "verdict": "PASS", "rejects": [], "warnings": [],
                 "sizing": {"shares": 100}}):
            r = te.do_buy(self._mk_args(execute=False, note=""), FakeWS())
        self.assertNotEqual(r.get("gate_verdict"), "REJECT")
        self.assertIn("dry-run", str(r.get("reason")))

    def test_source_anchor_and_ledger_note(self):
        # 防漂移: 守卫调用点在 do_buy 内 + 台账 log_submitted 买侧携带 note=
        src = inspect.getsource(te)
        self.assertIn("_manual_note_guard(a)", src)
        self.assertIn("note=(getattr(a, \"note\", \"\") or \"\")", src)
        self.assertIn('sp.add_argument("--note"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
