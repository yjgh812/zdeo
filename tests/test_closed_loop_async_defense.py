#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""20261027 async双写幻影防御回归 (三段式D层: 构造2026-09实锤场景验证C层折叠+A/B语义)

场景复刻(oid级实证见 data/attribution_hygiene_log.jsonl):
  1. async回报确认(提交日) + 盘后审计导入(成交日) 同键异日双写 → FIFO只产出真行
  2. async行尚无审计孪生(盘中新鲜成交) → 正常配对(不误杀)
  3. async归属标注的审计行(naked_trade_audit O20归属) → 保留
  4. 同键同日 async+audit 旧式脏对 → async折叠
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, r"D:\zcodesj")
from attribution.closed_loop_sync import load_fills, fifo_pair

SCHEMA = """
CREATE TABLE trade_records (
    id INTEGER PRIMARY KEY, code TEXT, name TEXT, trade_date TEXT, action TEXT,
    price REAL, volume REAL, amount REAL, strategy_version TEXT, signal_type TEXT,
    subsystems TEXT, factors TEXT, pred_proba REAL, pred_class TEXT, stop_loss REAL,
    take_profit REAL, trailing_profit REAL, expected_return_5d REAL,
    expected_return_20d REAL, market_env TEXT, sector TEXT, notes TEXT,
    created_at TEXT, list_membership TEXT);
"""


def mk_db(rows):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    con = sqlite3.connect(f.name)
    con.execute(SCHEMA)
    for r in rows:
        con.execute("INSERT INTO trade_records (id,code,name,trade_date,action,price,volume,"
                    "signal_type,subsystems,sector,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["id"], r["code"], r.get("name", ""), r["date"], r["action"], r["price"],
                     r["vol"], "manual", "", "", r["notes"], "2026-10-27T00:00:00"))
    con.commit()
    return f.name


def load(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    fills, dupes = load_fills(con)
    con.close()
    return fills, dupes


def cleanup(path):
    """Windows下sqlite句柄释放有延迟, 清理容错(临时目录兜底)"""
    try:
        os.unlink(path)
    except PermissionError:
        pass


def case1_dual_date():
    """async9/1+audit9/2同键异日 → 仅audit行入队, FIFO产出单真行"""
    rows = [
        dict(id=1, code="000001", date="20260901", action="SELL", price=11.92, vol=200,
             notes="async回报确认 order=X"),
        dict(id=2, code="000001", date="20260902", action="SELL", price=11.92, vol=200,
             notes="盘后审计导入 2026-09-02 09:32:10"),
        dict(id=3, code="000001", date="20260827", action="BUY", price=11.67, vol=500,
             notes="async回报确认 order=Y"),
    ]
    path = mk_db(rows)
    fills, _ = load(path)
    sells = [f for f in fills if f["action"] == "SELL"]
    assert len(sells) == 1 and sells[0]["date"] == "20260902", f"SELL应折叠为1条audit行: {sells}"
    closed, unpaired = fifo_pair(fills, "20260801")
    assert len(closed) == 1 and closed[0]["exit_date"] == "20260902", f"应单真行: {closed}"
    assert closed[0]["pnl_amount"] == 50.0
    cleanup(path)
    print("  case1 同键异日双写折叠 → PASS")


def case2_async_fresh():
    """async行无审计孪生(盘中新鲜) → 保留并正常配对(不误杀)"""
    rows = [
        dict(id=1, code="600000", date="20261027", action="BUY", price=10.0, vol=500,
             notes="async回报确认 order=Z"),
        dict(id=2, code="600000", date="20261027", action="SELL", price=10.2, vol=500,
             notes="async回报确认 order=W"),
    ]
    path = mk_db(rows)
    fills, _ = load(path)
    assert len(fills) == 2, f"新鲜async行不应被折叠: {len(fills)}"
    closed, _ = fifo_pair(fills, "20261001")
    assert len(closed) == 1 and closed[0]["pnl_amount"] == 100.0
    cleanup(path)
    print("  case2 新鲜async行保留配对 → PASS")


def case3_async_attributed():
    """async归属标注的审计行 → 视为canonical保留"""
    rows = [
        dict(id=1, code="600000", date="20260915", action="BUY", price=7.61, vol=2600,
             notes="盘后审计导入 2026-09-15 09:33:12 | async归属 order=Q"),
        dict(id=2, code="600000", date="20260930", action="SELL", price=7.66, vol=2600,
             notes="盘后审计导入 2026-09-30 09:58:39 | async归属 order=Q"),
    ]
    path = mk_db(rows)
    fills, _ = load(path)
    assert len(fills) == 2, f"canonical行不应折叠: {len(fills)}"
    closed, _ = fifo_pair(fills, "20260901")
    assert len(closed) == 1 and closed[0]["pnl_amount"] == 130.0
    cleanup(path)
    print("  case3 async归属审计行保留 → PASS")


def case4_same_day_legacy():
    """同键同日 async+audit 旧式脏对 → async折叠(FIFO不双计队列)"""
    rows = [
        dict(id=1, code="601668", date="20260904", action="BUY", price=4.4, vol=1100,
             notes="recon_backfill 20260925 westock唯一真值补录"),
        dict(id=2, code="601668", date="20260904", action="BUY", price=4.4, vol=1100,
             notes="async回报确认 order=V"),
        dict(id=3, code="601668", date="20260907", action="SELL", price=4.33, vol=1100,
             notes="盘后审计导入 2026-09-07"),
    ]
    path = mk_db(rows)
    fills, _ = load(path)
    buys = [f for f in fills if f["action"] == "BUY"]
    assert len(buys) == 1, f"同键async买入应折叠: {len(buys)}"
    closed, _ = fifo_pair(fills, "20260801")
    assert len(closed) == 1 and closed[0]["entry_price"] == 4.4
    cleanup(path)
    print("  case4 同键同日脏对折叠 → PASS")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    case1_dual_date()
    case2_async_fresh()
    case3_async_attributed()
    case4_same_day_legacy()
    print("4/4 PASS")
