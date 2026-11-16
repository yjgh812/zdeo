#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_poa_source_gate.py — P0-A 信号源准入闸回归套件(2026-08-29 立; 2026-11-03 合同更新)

固化信号源准入行为(唯一真源 _common.py):
  ① 生产买入 source 必须 ∈ METHOD_ADMISSION_INTERIM_SOURCES ∪ {manual_confirmed}
    (2026-11-03 主人令「七领跑组暂行实买, 其余影子观察」= 暂行方法准入合同;
     原 PROVEN∪EXPERIMENTAL 全放行合同退役, 档案=SOP 236)
  ② 空串/未登记任意串 → REJECT"P0-A"
  ③ 退役源(smc_s2_roe/hotspot_breakout)→ REJECT
  ④ 白名单七组 + manual_confirmed → 通过 P0-A(进入后续闸门/下单链)
  ⑤ 白名单外的已登记源(如 master_hist/mass_scan)→ REJECT(暂行影子观察语义)
  ⑥ buy-batch: manual_confirmed 禁入批量(既有,回归)
  ⑦ buy-batch: 计划条目携带主人批准键(owner_approve_*/owner_override_session)
     → 整条 ValueError 拒绝(2026-11-03 断根令: 批准不得写进计划文件)

隔离: do_buy 的 P0-A 检查位于函数首部(早于 ws 调用)——非法 source 用例无需 mock ws;
合法 source 用例 mock ws(positions/portfolio/quote 空结构) + run_gate 固定 PASS + dry-run。
用法: PYTHONUTF8=1 python tests/test_poa_source_gate.py   (无 --log; GBK 控制台须 UTF8 模式)
退出码: 0=全过 / 1=有失败
"""
from __future__ import annotations

import argparse
import sys
import unittest
from unittest import mock

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace")

import trade_executor as te
from _common import METHOD_ADMISSION_INTERIM_SOURCES, PROVEN_EDGE_SOURCES, EXPERIMENTAL_SOURCES

# 2026-11-03 合同: 准入=暂行方法准入 ∪ 主人特批通道
_ALLOWED = METHOD_ADMISSION_INTERIM_SOURCES | {"manual_confirmed"}


class FakeWS:
    """最小 mock: positions/portfolio/quote 返回空, 不触网。"""

    def positions(self):
        return {"data": {"positions": []}}

    def portfolio(self):
        return {"data": {"portfolio": {"totalAssets": 100000}}}

    def quote(self, code):
        return {"data": {}}


class P0ASourceGateTests(unittest.TestCase):

    def _mk_args(self, source, **kw):
        a = argparse.Namespace(
            code="000975", name="山金国际", source=source, sector="", price=5.0,
            # 20261027: volume=None+amount路径在10/26 sizing废除重构后do_buy int(None)崩溃(存量债务)——
            # 本套件对齐「显式--volume或--amount」契约改为显式volume, amount路径修复另行立项
            volume=100, amount=None, execute=False, prev_close=None, pct_60d=None,
            amplitude_40d=None, sector_breadth=None, intent="", allow_add_position=False,
            limit_price=None,
            # 2026-11-05: ZONE-GATE(买卖区域闸)上线后用例票000975不在位置池会被先拦——
            # 本套件只测 P0-A 准入层, 统一携带位置池特批豁免(与 owner_override_circuit 同先例)
            owner_approve_zone=True,
            # 2026-10-15: 测试豁免通道显式绕过 PD-037 熔断/冷却期闸——本套件只测 P0-A 层,
            # 否则 S-5≥2笔次日(冷却期)环境下全部用例被前置闸拦截(当日实锤, 环境态脆弱)
            owner_override_circuit=True)
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_blank_source_rejected(self):
        r = te.do_buy(self._mk_args(""), FakeWS())
        self.assertEqual(r.get("gate_verdict"), "REJECT")
        self.assertIn("P0-A", str(r.get("gate_rejects")))

    def test_ungated_arbitrary_rejected(self):
        r = te.do_buy(self._mk_args("ungated_待标注"), FakeWS())
        self.assertEqual(r.get("gate_verdict"), "REJECT")
        self.assertIn("P0-A", str(r.get("gate_rejects")))

    def test_retired_sources_rejected(self):
        for src in ("smc_s2_roe", "hotspot_breakout"):
            with self.subTest(src=src):
                r = te.do_buy(self._mk_args(src), FakeWS())
                self.assertEqual(r.get("gate_verdict"), "REJECT")
                self.assertIn("P0-A", str(r.get("gate_rejects")))

    def test_priority_sources_pass_gate(self):
        """④ 白名单七组通过 P0-A。
        2026-11-07 更新: manual_confirmed 已入模拟盘观察区(paper_trading_observations,
        实盘证据 29笔T+5 -6.56%/胜率10.3%), 默认被 PAPER-OBS-GATE 拒绝, 移入独立用例;
        本用例只测暂行准入七组(剔除 manual_confirmed)。"""
        for src in sorted(METHOD_ADMISSION_INTERIM_SOURCES):
            if src == "manual_confirmed":
                continue
            with self.subTest(src=src):
                self.assertIn(src, _ALLOWED)  # 真源集合内
                with mock.patch.object(te, "run_gate", return_value={
                        "verdict": "PASS", "rejects": [], "warnings": [],
                        "sizing": {"shares": 100}}), \
                     mock.patch.object(te, "_tencent_quote_fallback", return_value=None):
                    # 2026-10-15: mock F2腾讯降级链——否则触网取真价改变sizing折算(环境态隔离)
                    r = te.do_buy(self._mk_args(src), FakeWS())
                    self.assertNotEqual(r.get("gate_verdict"), "REJECT")
                    self.assertEqual(r.get("reason"), "dry-run（未加 --execute）。将买 100股 @5.0")

    def test_manual_confirmed_paper_obs_gate(self):
        """⑤b 2026-11-07 新增: manual_confirmed 默认被 PAPER-OBS-GATE 拒绝(模拟盘实盘
        观察无优势区, 证据=29笔T+5 -6.56%/胜率10.3%); 主人 --owner-approve-paper-obs
        特批后放行(通道未删除, 逐笔留痕)。"""
        self.assertIn("manual_confirmed", _ALLOWED)  # 仍在暂行准入集(通道语义不变)
        with mock.patch.object(te, "run_gate", return_value={
                "verdict": "PASS", "rejects": [], "warnings": [],
                "sizing": {"shares": 100}}), \
             mock.patch.object(te, "_tencent_quote_fallback", return_value=None):
            r = te.do_buy(self._mk_args("manual_confirmed"), FakeWS())
            self.assertEqual(r.get("gate_verdict"), "REJECT")
            self.assertIn("PAPER-OBS-GATE", str(r.get("gate_rejects")))
        # 特批放行
        a = self._mk_args("manual_confirmed")
        a.owner_approve_paper_obs = True
        with mock.patch.object(te, "run_gate", return_value={
                "verdict": "PASS", "rejects": [], "warnings": [],
                "sizing": {"shares": 100}}), \
             mock.patch.object(te, "_tencent_quote_fallback", return_value=None):
            r = te.do_buy(a, FakeWS())
            self.assertNotEqual(r.get("gate_verdict"), "REJECT")
            self.assertEqual(r.get("reason"), "dry-run（未加 --execute）。将买 100股 @5.0")

    def test_non_priority_registered_sources_rejected(self):
        """⑤ 白名单外的已登记源(PROVEN/EXPERIMENTAL 成员)→ REJECT(暂行影子观察语义)。
        2026-11-05 主人令更新: revival_momentum 已入暂行准入(METHOD_ADMISSION扩容),
        从本拒绝名单移除(合同随准入令同步)。"""
        for src in ("master_hist", "mass_scan", "quant_rank_original"):
            with self.subTest(src=src):
                with mock.patch.object(te, "run_gate", return_value={
                        "verdict": "PASS", "rejects": [], "warnings": [],
                        "sizing": {"shares": 100}}), \
                     mock.patch.object(te, "_tencent_quote_fallback", return_value=None):
                    r = te.do_buy(self._mk_args(src), FakeWS())
                    self.assertEqual(r.get("gate_verdict"), "REJECT")
                    self.assertIn("暂行方法准入", str(r.get("gate_rejects")))

    def test_batch_manual_confirmed_blocked(self):
        # buy-batch 既有拦截: manual_confirmed 不得批量(回归)——该行在 do_buy 前被 skipped
        import json as _json
        import os as _os
        plan = [{"code": "000975", "name": "山金国际", "source": "manual_confirmed",
                 "amount": 5000.0}]
        _tmp = _os.path.join(_os.environ.get("TEMP", "."), "poa_test_plan.json")
        with open(_tmp, "w", encoding="utf-8") as f:
            _json.dump(plan, f)
        try:
            with mock.patch.object(te, "do_buy") as mdb:
                mdb.return_value = {"executed": True}
                out = te.do_buy_batch(_tmp, FakeWS(), execute=False)
        finally:
            if _os.path.exists(_tmp):
                _os.remove(_tmp)
        # manual_confirmed 行被 skip, do_buy 未被调用
        mdb.assert_not_called()
        self.assertEqual(out.get("total"), 1)
        self.assertGreaterEqual(out.get("rejected_or_skipped", 0), 1)

    def test_batch_owner_approve_keys_rejected(self):
        """⑦ 断根合同: 计划条目携带主人批准键 → 整条 ValueError 拒绝, do_buy 不被调用。"""
        import json as _json
        import os as _os
        for keys in ({"owner_approve_ddgate": True},
                     {"owner_approve_ddgate": False},
                     {"owner_override_session": True}):
            with self.subTest(keys=sorted(keys)):
                plan = [{"code": "000975", "name": "山金国际", "source": "tandi",
                         "amount": 5000.0, **keys}]
                _tmp = _os.path.join(_os.environ.get("TEMP", "."), "poa_test_plan_owner.json")
                with open(_tmp, "w", encoding="utf-8") as f:
                    _json.dump(plan, f)
                try:
                    with mock.patch.object(te, "do_buy") as mdb:
                        out = te.do_buy_batch(_tmp, FakeWS(), execute=False)
                finally:
                    if _os.path.exists(_tmp):
                        _os.remove(_tmp)
                mdb.assert_not_called()
                errs = [r for r in out.get("results", []) if r.get("error")]
                self.assertEqual(len(errs), 1)
                self.assertIn("主人批准键", errs[0]["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
