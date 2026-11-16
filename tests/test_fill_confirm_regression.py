#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_fill_confirm_regression.py — fill_confirm_monitor 回归套件(唯一权威版)

背景（2026-08-27 E61，2026-08-28 准则38；同日债务清偿：本文件合并原
test_fill_confirm_monitor.py 的 5 用例与原 regression 4 用例，重复场景去重、
S1/S2 纯语言语义演示移除——其防护价值已由 T1/T2 端到端覆盖）：
  ① rows 与 all_rows 非同源对象 → `x is r` 身份比较永不匹配 → next() 抛 StopIteration 首跑即崩
  ② 持仓映射键=裸6位(norm6)，行内 code 带 sh/sz 前缀 → 恒查 0 股零成交
  ③ _record_fill 曾生成 `szsh600928` 垃圾代码（前缀未剥离即拼接）
  ④ --expire 幂等重跑会把此前已过期行从复评段 md 中丢失（同晚发现）
  ⑤ SELL 行 c6_note 随台账携带落 trade_records.notes（准则37/43）
  ⑥ 旧台账行无 c6_note 向后兼容

隔离：ATTR_DB/DQ_DB/REPORT_DIR/OPEN_ORDERS 全部 mock.patch 到 tempfile 临时目录
（修复原 regression 版 tearDown 把生产路径还原成临时常量的缺陷），SQL 全参数绑定。
用法: python tests/test_fill_confirm_regression.py   （无 --log，不触碰 guard_precheck_log）
退出码：0=全过 / 1=有失败
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace")

import executor_order_ledger as eol  # noqa: E402
import fill_confirm_monitor as fcm   # noqa: E402

TRADE_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS trade_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT, trade_date TEXT,
  action TEXT, price REAL, volume INTEGER, amount REAL, strategy_version TEXT,
  signal_type TEXT, subsystems TEXT, market_env TEXT, sector TEXT, notes TEXT,
  list_membership TEXT, created_at TEXT)
"""
BUY_TRACKER_DDL = """
CREATE TABLE IF NOT EXISTS buy_tracker (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT, buy_date TEXT,
  buy_price REAL, buy_qty INTEGER, signal_source TEXT, stop_loss_pct REAL,
  status TEXT, verdict TEXT, updated_at TEXT, UNIQUE(code, buy_date))
"""


class FillConfirmRegressionTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self.open_orders = os.path.join(tmp, "open_orders.jsonl")
        self.attr_db = os.path.join(tmp, "attr.db")
        self.dq_db = os.path.join(tmp, "dq.db")
        self.report_dir = os.path.join(tmp, "reports")
        os.makedirs(self.report_dir)
        for path, ddl in ((self.attr_db, TRADE_RECORDS_DDL), (self.dq_db, BUY_TRACKER_DDL)):
            con = sqlite3.connect(path)
            con.execute(ddl)
            con.commit()
            con.close()
        # OPEN_ORDERS 在两命名空间均可解析；ATTR_DB/DQ_DB/REPORT_DIR 在 fcm 命名空间
        self._patches = [
            mock.patch.object(eol, "OPEN_ORDERS", self.open_orders),
            mock.patch.object(fcm, "OPEN_ORDERS", self.open_orders),
            mock.patch.object(fcm, "ATTR_DB", self.attr_db),
            mock.patch.object(fcm, "DQ_DB", self.dq_db),
            mock.patch.object(fcm, "REPORT_DIR", self.report_dir),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write_open(self, rows: list[dict]):
        with open(self.open_orders, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _read_trades(self) -> list[dict]:
        con = sqlite3.connect(self.attr_db)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT code,name,action,price,volume,subsystems,notes FROM trade_records")]
        con.close()
        return rows

    # ── ①+② 端到端: cmd_poll 全链路（身份同源 + 持仓键归一） ──
    def test_t1_poll_no_stopiteration_on_identity(self):
        """E61①: 待决行回写必须命中 all_rows 同一对象, 不得抛 StopIteration。"""
        self._write_open([
            {"code": "600928", "name": "西安银行", "action": "BUY", "price": 3.61,
             "qty": 1600, "base_qty": 0, "confirmed": 0, "status": "OPEN",
             "order_id": "T1-1", "trade_date": "20260828"},
            {"code": "sz000617", "name": "中油资本", "action": "BUY", "price": 7.31,
             "qty": 2000, "base_qty": 0, "confirmed": 0, "status": "OPEN",
             "order_id": "T1-2", "trade_date": "20260828"},
        ])
        with mock.patch.object(fcm, "_positions_map",
                               return_value={"600928": 1600, "000617": 2000}):
            res = fcm.cmd_poll()  # 修复前在此抛 StopIteration
        self.assertEqual(res["rows"], 2)
        self.assertIn("600928", res["filled_codes"])

    def test_t2_position_key_normalization(self):
        """E61②: 持仓键为裸6位, 行内 code 带前缀必须归一后命中, 不得恒判零成交。"""
        self._write_open([
            {"code": "sh600928", "name": "西安银行", "action": "BUY", "price": 3.61,
             "qty": 1600, "base_qty": 0, "confirmed": 0, "status": "OPEN",
             "order_id": "T2-1", "trade_date": "20260828"},
        ])
        with mock.patch.object(fcm, "_positions_map", return_value={"600928": 1600}):
            res = fcm.cmd_poll()
        self.assertEqual(res.get("filled_codes", {}).get("600928"), 1600,
                         "带前缀码持仓必须被归一匹配（修复前恒为0股零成交）")
        rows = fcm.load_open_rows(("OPEN", "PARTIAL", "FILLED"))
        self.assertEqual(rows[0]["status"], "FILLED")

    # ── ③+⑤ _record_fill: 规范前缀码 + c6_note 落 notes ──
    def test_t3_record_fill_prefixed_code_and_notes(self):
        """E61③+准则43: 落库代码必须 to_prefixed 规范前缀码; c6_note/order 随 notes 落库。"""
        row = {"code": "sh600928", "name": "西安银行", "action": "SELL",
               "price": 3.61, "qty": 1600, "trade_date": "20260828",
               "order_id": "TESTOID001", "c6_note": "C6板块判定=中性(机械设备 +2.4%1d/+2.3%5d,T-1)"}
        fcm._record_fill(row, 1600, "2026-08-28T10:00:00")
        trades = self._read_trades()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["code"], "sh600928", "禁止生成 szsh600928 垃圾代码")
        self.assertNotIn("szsh", trades[0]["code"])
        self.assertEqual(trades[0]["subsystems"], "trade_executor_async")
        self.assertIn("order=TESTOID001", trades[0]["notes"])
        self.assertIn("C6板块判定=中性", trades[0]["notes"])

    def test_t4_old_row_without_c6_note_backward_compat(self):
        """⑥: 旧台账行无 c6_note → notes 保持原 async 串, 不报错。"""
        row = {"code": "000001", "name": "平安银行", "action": "SELL", "price": 11.6,
               "qty": 500, "base_qty": 500, "confirmed": 0, "status": "OPEN",
               "order_id": "T4-1", "trade_date": "20260828"}
        fcm._record_fill(row, 500, "2026-08-28T15:00:00")
        trades = self._read_trades()
        self.assertEqual(trades[0]["notes"], "async回报确认 order=T4-1")

    # ── ④ expire 幂等: 复评段 md 不丢此前已过期行 ──
    def test_t5_expire_md_keeps_prior_expired_rows(self):
        prior = {"code": "sz000833", "name": "粤桂股份", "action": "BUY", "price": 20.6,
                 "qty": 900, "base_qty": 0, "confirmed": 0, "status": "EXPIRED_READ",
                 "order_id": "TESTOID002", "trade_date": "20260828"}
        fresh = {"code": "sh600989", "name": "宝丰能源", "action": "BUY", "price": 22.99,
                 "qty": 600, "base_qty": 0, "confirmed": 0, "status": "OPEN",
                 "order_id": "TESTOID003", "trade_date": "20260828"}
        self._write_open([prior, fresh])
        res = fcm.cmd_expire("20260828")
        self.assertEqual(res["expired"], 1)  # 仅 OPEN 的 fresh 被本次标注
        md_path = os.path.join(self.report_dir, "pending_review_20260828.md")
        with open(md_path, encoding="utf-8") as f:
            md1 = f.read()
        self.assertIn("sz000833", md1, "此前已过期行不得在幂等重跑中丢失")
        self.assertIn("粤桂股份", md1)
        self.assertIn("sh600989", md1)
        # 幂等重跑: md 仍含两行, 且不再把 prior 计入 expired
        res2 = fcm.cmd_expire("20260828")
        self.assertEqual(res2["expired"], 0)
        with open(md_path, encoding="utf-8") as f:
            self.assertIn("sz000833", f.read())


if __name__ == "__main__":
    ok = unittest.main(verbosity=2, exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)
