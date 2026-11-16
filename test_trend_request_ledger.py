#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trend_checker S16 提请台账测试(临时路径, 禁碰真实 data/trend_request_ledger.jsonl)。

覆盖四用例:
  a) append_trend_request_ledger 追加一行, 字段符合契约(6位裸码/sell_request)
  b) 同日同 code 幂等: 第二次返回 False 且不重复追加
  c) 不同日同 code: 正常追加
  d) main() 接线 dry-run: check_trend 返回 BEARISH → 台账落行;
     返回 BEARISH_D1(只观察) → 台账不落行

运行: python test_trend_request_ledger.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, r"D:/zcodesj")
import trend_checker as tc  # noqa: E402


def _tmp_ledger() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    os.unlink(f.name)
    return f.name


def _read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


class T1_AppendBasics(unittest.TestCase):
    """a) 追加一行, 字段契约正确。"""

    def test_append_and_fields(self):
        p = _tmp_ledger()
        try:
            ok = tc.append_trend_request_ledger(
                "sh600519", "贵州茅台", "🔴 空头排列（趋势走坏成立）",
                ledger_path=p, request_date="20260923")
            self.assertTrue(ok)
            rows = _read(p)
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(set(r.keys()),
                             {"request_date", "code", "name",
                              "action", "reason"})
            self.assertEqual(r["request_date"], "20260923")
            self.assertEqual(r["code"], "600519")  # 台账统一 6 位裸码
            self.assertEqual(r["name"], "贵州茅台")
            self.assertEqual(r["action"], "sell_request")
            self.assertIn("走坏", r["reason"])
        finally:
            if os.path.exists(p):
                os.unlink(p)


class T2_Idempotent(unittest.TestCase):
    """b) 同日同 code 幂等 / c) 跨日正常追加。"""

    def test_same_day_duplicate(self):
        p = _tmp_ledger()
        try:
            self.assertTrue(tc.append_trend_request_ledger(
                "600519", "贵州茅台", "走坏", ledger_path=p,
                request_date="20260923"))
            self.assertFalse(tc.append_trend_request_ledger(
                "sh600519", "贵州茅台", "走坏", ledger_path=p,
                request_date="20260923"))
            self.assertEqual(len(_read(p)), 1)
        finally:
            if os.path.exists(p):
                os.unlink(p)

    def test_next_day_appends(self):
        p = _tmp_ledger()
        try:
            tc.append_trend_request_ledger("600519", "贵州茅台", "走坏",
                                           ledger_path=p,
                                           request_date="20260923")
            self.assertTrue(tc.append_trend_request_ledger(
                "600519", "贵州茅台", "走坏", ledger_path=p,
                request_date="20260924"))
            self.assertEqual(len(_read(p)), 2)
        finally:
            if os.path.exists(p):
                os.unlink(p)


class T3_MainWiring(unittest.TestCase):
    """d) main() 接线 dry-run: BEARISH 落行 / BEARISH_D1 不落行。"""

    def _run_main(self, verdict: str, p: str):
        fake = {"code": "sh600711", "name": "厦门钨业", "verdict": verdict,
                "label": ("🔴 空头排列（趋势走坏成立·连续2日+破位≥1%）"
                          if verdict == "BEARISH"
                          else "🟠 走坏第1日（只观察，不构成卖出理由）")}
        argv = sys.argv
        sys.argv = ["trend_checker.py", "--code", "sh600711"]
        try:
            with mock.patch.object(tc, "check_trend", return_value=fake), \
                 mock.patch.object(tc, "TREND_LEDGER_PATH", p):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = tc.main()
        finally:
            sys.argv = argv
        return rc, buf.getvalue()

    def test_bearish_writes_ledger(self):
        p = _tmp_ledger()
        try:
            rc, out = self._run_main("BEARISH", p)
            self.assertEqual(rc, 0)
            self.assertIn("提请落盘", out)
            rows = _read(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["code"], "600711")
            self.assertEqual(rows[0]["action"], "sell_request")
        finally:
            if os.path.exists(p):
                os.unlink(p)

    def test_bearish_d1_no_write(self):
        p = _tmp_ledger()
        try:
            rc, out = self._run_main("BEARISH_D1", p)
            self.assertEqual(rc, 0)
            self.assertNotIn("提请落盘", out)
            self.assertEqual(_read(p), [])
        finally:
            if os.path.exists(p):
                os.unlink(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
