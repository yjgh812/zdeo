#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guarded_paper_trade.py 测试(临时库, 禁碰真实 attribution.db)。

覆盖四用例:
  a) precheck: 688 开头(科创板)买入 → REJECT(no_log=True, 不写真实 guard_precheck_log);
     另附 sell 方向最小参数 → PASS 冒烟
  b) record: 相同五元组第二次写入被跳过(duplicate skipped)
  c) record 写入字段与 trade_executor.py 口径一致(INSERT 列名比对 + 行值断言)
  d) 无参数用法输出正常

运行: python test_guarded_paper_trade.py
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, r"D:/zcodesj")
import guarded_paper_trade as g  # noqa: E402

# 真实库 trade_records 建表 DDL(列名/约束与 attribution.db 同构, 仅用于临时库)
DDL = """
CREATE TABLE trade_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    trade_date TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('BUY','SELL','PARTIAL_SELL')),
    price REAL NOT NULL,
    volume INTEGER NOT NULL,
    amount REAL,
    strategy_version TEXT DEFAULT 'V23',
    signal_type TEXT CHECK(signal_type IN ('probability_threshold','top_n_ranking','manual')),
    subsystems TEXT,
    factors TEXT,
    pred_proba REAL,
    pred_class INTEGER,
    stop_loss REAL,
    take_profit REAL,
    trailing_profit REAL,
    expected_return_5d REAL,
    expected_return_20d REAL,
    market_env TEXT,
    sector TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    list_membership TEXT DEFAULT 'unknown')
"""

# 真实库 engine_attribution_log 建表 DDL(与 attribution/attribution.db 同构, 仅用于临时库)
ATTRIBUTION_DDL = """
CREATE TABLE engine_attribution_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT, code TEXT, name TEXT, event TEXT,
    main_attribution TEXT, sub_attribution TEXT,
    confidence REAL, conflict_ratio REAL,
    ar REAL, stock_pct REAL, benchmark_pct REAL,
    dimension_scores TEXT, contribution_pct TEXT,
    predictability INTEGER, controllability INTEGER,
    feature_tags TEXT,
    fwd3 REAL, fwd5 REAL, outcome_updated_at TEXT,
    created_at TEXT
)
"""


def _tmp_conn() -> tuple[sqlite3.Connection, str]:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.execute(DDL)
    conn.execute(ATTRIBUTION_DDL)
    conn.commit()
    return conn, f.name


def _ns(**kw) -> argparse.Namespace:
    """构造 run_precheck 所需的全量 Namespace(买卖两侧字段并集)。"""
    base = dict(
        precheck=True, record=False, code="sh600519", direction="buy",
        price=14.09, quantity=1000, name="", source="manual_confirmed",
        prev_close=None, sector="", total_cash=None, holdings=None,
        amount=None, is_st=False, allow_add_position=False,
        loss_pct=None, stop_loss=False, pe=None, pb=None,
        dividend_yield=None, earnings_yoy=None, net_inflow_5d=None,
        sector_pct=None, no_log=True,  # 测试默认不写真实 guard_precheck_log
        trade_date="", fill_time="", reason="",
    )
    base.update(kw)
    return argparse.Namespace(**base)


class T1_Precheck688Reject(unittest.TestCase):
    """a) 688 科创板自动检测 → REJECT(守卫真实判定链, no_log 不污染真实留痕表)。"""

    def test_688_buy_reject(self):
        verdict, r, exit_code = g.run_precheck(_ns(code="sh688001", direction="buy",
                                                  price=25.0))
        self.assertEqual(verdict, "REJECT")
        self.assertEqual(exit_code, 2)
        self.assertTrue(r["verdict"] == "REJECT")
        joined = " | ".join(r.get("rejects", []))
        self.assertIn("科创板", joined)

    def test_sell_smoke_pass(self):
        verdict, r, exit_code = g.run_precheck(_ns(code="sz000001", direction="sell",
                                                  price=10.0))
        self.assertEqual(verdict, "PASS")
        self.assertEqual(exit_code, 0)
        self.assertEqual(r["verdict"], "SELL_OK")


class T2_RecordDedup(unittest.TestCase):
    """b) 相同五元组第二次写入被跳过。"""

    def test_duplicate_skipped(self):
        conn, path = _tmp_conn()
        try:
            kw = dict(code="sh600519", direction="buy", price=14.09, quantity=1000,
                      trade_date="20260923", fill_time="09:35:12", source="owner_order",
                      name="贵州茅台")
            id1 = g.record_fill(conn, **kw)
            self.assertIsNotNone(id1)
            buf = io.StringIO()
            with redirect_stdout(buf):
                id2 = g.record_fill(conn, **kw)
            self.assertIsNone(id2)
            self.assertIn("duplicate skipped", buf.getvalue())
            n = conn.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0]
            self.assertEqual(n, 1)
        finally:
            conn.close()
            os.unlink(path)


class T3_FieldCalibre(unittest.TestCase):
    """c) 写入字段与 trade_executor.py 口径一致(列名比对 + 行值断言)。"""

    def test_columns_match_executor(self):
        with open(r"D:/zcodesj/trade_executor.py", encoding="utf-8") as f:
            src = f.read()
        cols_sets = re.findall(
            r"INSERT (?:OR IGNORE )?INTO trade_records \(([^)]+)\)", src)
        self.assertTrue(cols_sets, "trade_executor.py 未找到 trade_records INSERT")
        executor_cols = tuple(
            c.replace('"', "").strip() for c in cols_sets[0].split(","))
        self.assertEqual(g._RECORD_COLUMNS, executor_cols)
        for s in cols_sets:  # do_buy 与 do_sell 两处口径也须一致
            self.assertEqual(
                tuple(c.replace('"', "").strip() for c in s.split(",")),
                executor_cols)

    def test_row_values(self):
        conn, path = _tmp_conn()
        try:
            new_id = g.record_fill(conn, "600519", "buy", 14.09, 1000,
                                   "20260923", "09:35:12", "owner_order",
                                   name="贵州茅台")
            cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_records)")]
            row = dict(zip(cols, conn.execute(
                "SELECT * FROM trade_records WHERE id=?", (new_id,)).fetchone()))
            self.assertEqual(row["code"], "sh600519")          # norm 归一化
            self.assertEqual(row["action"], "BUY")
            self.assertEqual(row["trade_date"], "20260923")
            self.assertEqual(row["price"], 14.09)
            self.assertEqual(row["volume"], 1000)
            self.assertEqual(row["amount"], round(14.09 * 1000, 2))  # executor 口径
            self.assertEqual(row["strategy_version"], "20260923")    # executor: =trade_date
            self.assertEqual(row["signal_type"], "manual")           # 白名单外归 manual
            self.assertEqual(row["subsystems"], "guarded_paper_trade")
            self.assertEqual(row["market_env"], "")                  # executor: 空串
            self.assertRegex(row["created_at"],
                             r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")  # isoformat(秒)
        finally:
            conn.close()
            os.unlink(path)


class T4_Usage(unittest.TestCase):
    """d) 无参数用法输出正常。"""

    def test_usage(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = g.main([])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("用法", out)
        self.assertIn("--precheck", out)
        self.assertIn("--record", out)

    def test_source_whitelist(self):
        conn, path = _tmp_conn()
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = g.main(["--record", "--code", "sh600519", "--direction", "buy",
                             "--price", "1", "--quantity", "100",
                             "--trade-date", "20260923", "--fill-time", "09:00:00",
                             "--source", "bad_source"])
            self.assertEqual(rc, 1)  # 非法 source 拒绝落账
        finally:
            conn.close()
            os.unlink(path)


class T5_TradeAttribution(unittest.TestCase):
    """e) M6 前向落地: record 后归因简行存在/幂等/分析字段为空。

    e1) record 后 engine_attribution_log 存在一行, 关键字段正确
    e2) 重复 record(不同价格, 归因四元组相同)→ 归因行不重复
    e3) 归因分析字段确为空(留待归因引擎回填, 不编造数值)
    """

    def test_attribution_row_written(self):
        conn, path = _tmp_conn()
        try:
            new_id = g.record_fill(conn, "600519", "buy", 14.09, 1000,
                                   "20260923", "09:35:12", "owner_order",
                                   name="贵州茅台")
            self.assertIsNotNone(new_id)
            row = conn.execute(
                "SELECT event_date, code, name, event, main_attribution, "
                "sub_attribution, created_at FROM engine_attribution_log"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "20260923")
            self.assertEqual(row[1], "sh600519")
            self.assertEqual(row[2], "贵州茅台")
            self.assertEqual(row[3], "trade_executed")
            self.assertEqual(row[4], "owner_order")
            self.assertEqual(row[5], "buy")
            self.assertRegex(row[6], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
            # sell 方向 main/sub 对齐
            g.record_fill(conn, "sz000993", "sell", 12.0, 500,
                          "20260924", "10:00:00", "s5", name="闽东电力")
            row2 = conn.execute(
                "SELECT main_attribution, sub_attribution, event "
                "FROM engine_attribution_log WHERE code='sz000993'"
            ).fetchone()
            self.assertEqual(row2, ("s5", "sell", "trade_executed"))
        finally:
            conn.close()
            os.unlink(path)

    def test_attribution_row_idempotent(self):
        conn, path = _tmp_conn()
        try:
            base = dict(trade_date="20260923", fill_time="09:35:12",
                        source="owner_order", name="贵州茅台")
            g.record_fill(conn, "600519", "buy", 14.09, 1000, **base)
            # 同日同方向不同价格: T8 五元组不重复(会写入第二条 trade_records),
            # 但归因 (code,event_date,event,sub_attribution) 已存在 → 跳过
            buf = io.StringIO()
            with redirect_stdout(buf):
                id2 = g.record_fill(conn, "600519", "buy", 15.00, 1000, **base)
            self.assertIsNotNone(id2)
            self.assertIn("duplicate skipped: attribution", buf.getvalue())
            n = conn.execute(
                "SELECT COUNT(*) FROM engine_attribution_log WHERE code='sh600519'"
            ).fetchone()[0]
            self.assertEqual(n, 1)
        finally:
            conn.close()
            os.unlink(path)

    def test_attribution_analysis_fields_empty(self):
        conn, path = _tmp_conn()
        try:
            g.record_fill(conn, "600519", "buy", 14.09, 1000,
                          "20260923", "09:35:12", "owner_order", name="贵州茅台")
            row = conn.execute(
                "SELECT confidence, conflict_ratio, ar, stock_pct, benchmark_pct, "
                "dimension_scores, contribution_pct, predictability, "
                "controllability, feature_tags, fwd3, fwd5, outcome_updated_at "
                "FROM engine_attribution_log"
            ).fetchone()
            self.assertEqual(tuple(row), (None,) * 13)
        finally:
            conn.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
