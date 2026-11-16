#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sell_lifecycle_reconcile.py — E19 卖出单生命周期闸 fixture 回归（2026-10-16）。

覆盖 fill_confirm_monitor 新增 --reconcile 模式与 cmd_expire 卖出侧 fail-closed 加固：
  T1  EXPIRED_READ 卖单命中成交 → FILLED + executor 台账传播
  T2  命中撤单 → CANCELLED 双侧（open_orders + executor_orders）
  T3  无匹配未了结行维持原状态（不擅断）
  T4  EXPIRED_READ 买单 → FILLED + trade_records 回填，复跑幂等不双记
  T5  fills 数据源失败 → exit 3 fail-closed（不改判任何状态）
  T6  expire 卖出侧：fills 可得时命中成交改 FILLED、无匹配才 EXPIRED_READ；
      fills 失败时 SELL 保持 OPEN（只豁免卖出，买入照常过期）
  T7  无委托号历史行走五元组兜底匹配

运行: python tests/test_sell_lifecycle_reconcile.py   （全绿 exit 0）
零真实外呼：westock/notify/subprocess 全部打桩，台账与库落 tmp。
"""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, r"D:\zcodesj")

import executor_order_ledger as eol          # noqa: E402
import fill_confirm_monitor as fcm           # noqa: E402

TMP = tempfile.mkdtemp(prefix="e19_lifecycle_")
OPEN_P = os.path.join(TMP, "open_orders.jsonl")
EXEC_P = os.path.join(TMP, "executor_orders.jsonl")
ATTR_DB = os.path.join(TMP, "attribution.db")
DQ_DB = os.path.join(TMP, "dragon_quant.db")
REPORT_DIR = os.path.join(TMP, "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

TD = "20261015"


def _init_dbs():
    con = sqlite3.connect(ATTR_DB)
    con.execute("""CREATE TABLE trade_records (code TEXT, name TEXT, trade_date TEXT,
        action TEXT, price REAL, volume INTEGER, amount REAL, strategy_version TEXT,
        signal_type TEXT, subsystems TEXT, market_env TEXT, sector TEXT, notes TEXT,
        list_membership TEXT, created_at TEXT)""")
    con.commit(), con.close()
    con = sqlite3.connect(DQ_DB)
    con.execute("""CREATE TABLE buy_tracker (code TEXT, name TEXT, buy_date TEXT,
        buy_price REAL, buy_qty INTEGER, signal_source TEXT, stop_loss_pct REAL,
        status TEXT, verdict TEXT, updated_at TEXT, UNIQUE(code, buy_date))""")
    con.commit(), con.close()


def _write_open(rows):
    with open(OPEN_P, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_exec(rows):
    with open(EXEC_P, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_open():
    out = []
    with open(OPEN_P, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return {r["order_id"] or r["code"] + r["action"]: r for r in out}


def _read_exec():
    out = {}
    with open(EXEC_P, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                out[r["order_id"]] = r
    return out


def _base_row(oid, code, name, action, price, qty, status, td=TD, confirmed=0):
    return {"code": code, "name": name, "action": action, "price": price, "qty": qty,
            "base_qty": qty, "confirmed": confirmed, "status": status, "order_id": oid,
            "trade_date": td, "submitted_at": f"{td[:4]}-{td[4:6]}-{td[6:8]}T09:35:00",
            "c6_note": "", "source": "test"}


def _fill(oid, code, action, price, qty, status, at):
    return {"code6": code, "action": action, "price": price, "volume": qty,
            "filled_at": at, "status": status, "order_id": oid, "name": "t"}


class _Patches:
    def __init__(self, fills, raise_on_fetch=False):
        self.fills = fills
        self.raise_on_fetch = raise_on_fetch
        self._old = {}

    def __enter__(self):
        self._old = {
            eol: dict(EXEC_ORDERS=eol.EXEC_ORDERS, OPEN_ORDERS=eol.OPEN_ORDERS),
            fcm: dict(ATTR_DB=fcm.ATTR_DB, DQ_DB=fcm.DQ_DB, REPORT_DIR=fcm.REPORT_DIR),
        }
        eol.EXEC_ORDERS, eol.OPEN_ORDERS = EXEC_P, OPEN_P
        fcm.ATTR_DB, fcm.DQ_DB, fcm.REPORT_DIR = ATTR_DB, DQ_DB, REPORT_DIR

        class _FakeSub:
            @staticmethod
            def run(*a, **k):
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return R()

        self._old_sub = fcm.subprocess
        fcm.subprocess = _FakeSub
        self._old_notify = fcm._naked_notify
        fcm._naked_notify = lambda *a, **k: None

        def _fetch_today():
            if self.raise_on_fetch:
                raise RuntimeError("stub: westock down")
            return self.fills

        def _fetch_history(limit=500):
            if self.raise_on_fetch:
                raise RuntimeError("stub: westock down")
            return self.fills

        self._old_ft, self._old_fh = fcm._fetch_today_fills, fcm._fetch_history_fills
        fcm._fetch_today_fills, fcm._fetch_history_fills = _fetch_today, _fetch_history
        return self

    def __exit__(self, *exc):
        eol.EXEC_ORDERS, eol.OPEN_ORDERS = self._old[eol]["EXEC_ORDERS"], self._old[eol]["OPEN_ORDERS"]
        for k, v in self._old[fcm].items():
            setattr(fcm, k, v)
        fcm.subprocess = self._old_sub
        fcm._naked_notify = self._old_notify
        fcm._fetch_today_fills, fcm._fetch_history_fills = self._old_ft, self._old_fh


def test_t1_expired_sell_refilled():
    _write_open([_base_row("OID1", "002513", "蓝丰生化", "SELL", 5.24, 6000, "EXPIRED_READ")])
    _write_exec([{"order_id": "OID1", "code": "002513", "action": "SELL",
                  "status": "OPEN", "trade_date": TD}])
    fills = [_fill("OID1", "002513", "SELL", 5.24, 6000, "已成交", "2026-10-15 09:38:46")]
    with _Patches(fills):
        res = fcm.cmd_reconcile()
    row = _read_open()["OID1"]
    assert row["status"] == "FILLED" and row["confirmed"] == 6000, row
    assert _read_exec()["OID1"]["status"] == "FILLED"
    assert res["corrections"] and res["corrections"][0]["new"] == "FILLED"
    print("T1 PASS: EXPIRED_READ 卖单改判 FILLED + executor 传播")


def test_t2_cancelled():
    _write_open([_base_row("OID2", "600966", "博汇纸业", "SELL", 4.70, 3000, "EXPIRED_READ")])
    _write_exec([{"order_id": "OID2", "code": "600966", "action": "SELL",
                  "status": "OPEN", "trade_date": TD}])
    fills = [_fill("OID2", "600966", "SELL", 4.70, 3000, "系统撤单", "2026-10-14 15:32:06")]
    with _Patches(fills):
        fcm.cmd_reconcile()
    assert _read_open()["OID2"]["status"] == "CANCELLED"
    assert _read_exec()["OID2"]["status"] == "CANCELLED"
    print("T2 PASS: 真撤单双侧改判 CANCELLED")


def test_t3_unmatched_kept():
    _write_open([_base_row("OID3", "600000", "浦发银行", "SELL", 9.99, 100, "OPEN")])
    _write_exec([{"order_id": "OID3", "code": "600000", "action": "SELL",
                  "status": "OPEN", "trade_date": TD}])
    with _Patches([_fill("OIDX", "600000", "SELL", 9.99, 100, "已成交",
                         "2026-10-15 10:00:00")]):
        res = fcm.cmd_reconcile()
    assert _read_open()["OID3"]["status"] == "OPEN"
    assert len(res["unmatched"]) == 1
    print("T3 PASS: 无匹配行维持 OPEN 且如实列示")


def test_t4_buy_backfill_idempotent():
    _write_open([_base_row("OID4", "600380", "健康元", "BUY", 9.98, 1200, "EXPIRED_READ")])
    _write_exec([{"order_id": "OID4", "code": "600380", "action": "BUY",
                  "status": "OPEN", "trade_date": TD}])
    fills = [_fill("OID4", "600380", "BUY", 9.98, 1200, "已成交", "2026-10-15 09:31:15")]
    with _Patches(fills):
        fcm.cmd_reconcile()
        fcm.cmd_reconcile()  # 复跑幂等
    assert _read_open()["OID4"]["status"] == "FILLED"
    con = sqlite3.connect(ATTR_DB)
    n = con.execute("SELECT COUNT(*) FROM trade_records WHERE code='sh600380'").fetchone()[0]
    con.close()
    assert n == 1, f"trade_records 双记: {n}"
    print("T4 PASS: 买单回填 FILLED + trade_records 幂等不双记")


def test_t5_failclosed():
    _write_open([_base_row("OID5", "600000", "浦发银行", "SELL", 9.65, 1200, "EXPIRED_READ")])
    _write_exec([{"order_id": "OID5", "code": "600000", "action": "SELL",
                  "status": "OPEN", "trade_date": TD}])
    try:
        with _Patches([], raise_on_fetch=True):
            fcm.cmd_reconcile()
        raise AssertionError("fail-closed 未触发")
    except SystemExit as e:
        assert e.code == 3, e.code
    assert _read_open()["OID5"]["status"] == "EXPIRED_READ"  # 未被擅改
    print("T5 PASS: 数据源失败 exit 3 且不改判")


def test_t6_expire_sell_paths():
    # 6a: fills 可得——命中成交改 FILLED、无匹配标 EXPIRED_READ
    _write_open([
        _base_row("OID6", "603982", "泉峰汽车", "SELL", 6.25, 5100, "OPEN"),
        _base_row("OID7", "600000", "浦发银行", "SELL", 9.99, 100, "OPEN"),
        _base_row("OID8", "600000", "浦发银行", "BUY", 9.65, 1200, "OPEN"),
    ])
    _write_exec([{"order_id": f"OID{i}", "code": "x", "action": "SELL", "status": "OPEN",
                  "trade_date": TD} for i in (6, 7, 8)])
    fills = [_fill("OID6", "603982", "SELL", 6.25, 5100, "已成交", "2026-10-15 09:38:35")]
    with _Patches(fills):
        res = fcm.cmd_expire(TD)
    rows = _read_open()
    assert rows["OID6"]["status"] == "FILLED", rows["OID6"]
    assert rows["OID7"]["status"] == "EXPIRED_READ"
    assert rows["OID8"]["status"] == "EXPIRED_READ"  # 买入维持原过期语义
    assert res["sells_kept_open"] == 0
    # 6b: fills 失败——SELL 保持 OPEN，买入照常过期
    _write_open([
        _base_row("OID9", "603982", "泉峰汽车", "SELL", 6.25, 5100, "OPEN"),
        _base_row("OID10", "600000", "浦发银行", "BUY", 9.65, 1200, "OPEN"),
    ])
    _write_exec([{"order_id": "OID9", "code": "x", "action": "SELL", "status": "OPEN",
                  "trade_date": TD},
                 {"order_id": "OID10", "code": "x", "action": "BUY", "status": "OPEN",
                  "trade_date": TD}])
    with _Patches([], raise_on_fetch=True):
        res = fcm.cmd_expire(TD)
    rows = _read_open()
    assert rows["OID9"]["status"] == "OPEN", "SELL 行在数据源失败时被误标过期"
    assert rows["OID10"]["status"] == "EXPIRED_READ"
    assert res["sells_kept_open"] == 1
    print("T6 PASS: expire 卖出侧 对账改判/无匹配过期/数据源失败保持 OPEN 三路径")


def test_t7_fivetuple_fallback():
    _write_open([_base_row("", "600000", "浦发银行", "BUY", 9.65, 1200, "EXPIRED_READ")])
    _write_exec([])
    fills = [_fill("", "600000", "BUY", 9.65, 1200, "已成交", "2026-10-15 09:30:03")]
    with _Patches(fills):
        fcm.cmd_reconcile()
    assert _read_open()["600000BUY"]["status"] == "FILLED"
    print("T7 PASS: 无委托号历史行五元组兜底匹配")


def main() -> int:
    _init_dbs()
    test_t1_expired_sell_refilled()
    test_t2_cancelled()
    test_t3_unmatched_kept()
    test_t4_buy_backfill_idempotent()
    test_t5_failclosed()
    test_t6_expire_sell_paths()
    test_t7_fivetuple_fallback()
    print(f"\n全部 7 用例 PASS（tmp={TMP}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
