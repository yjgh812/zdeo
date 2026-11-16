#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_t7_t8_min_fixes.py — T7低价废单前置校验 + T8 trade_records幂等查重 验证(2026-09-23)。

全部用内存sqlite / dry-run路径 / 假ws桩, 不读写真实 attribution.db, 不下单。跑法:
  cd /d/zcodesj && PYTHONUTF8=1 python test_t7_t8_min_fixes.py
用例:
  a) 0.05元挂价 → do_buy REJECT(低价废单熔断)
  b) 4.91元挂价 → 通过T7校验(dry-run正常走到预览)
  c) 相同五元组第二次写入 → 跳过(duplicate skipped)
  d) 不同五元组 → 正常写入
"""
import inspect
import sqlite3
import sys
from argparse import Namespace

sys.path.insert(0, r"D:/zcodesj")
import trade_executor as te

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" | {detail}" if detail and not cond else ""))


# ---------- T7: 纯函数 ----------
print("== T7 price_below_floor 纯函数 ==")
check("t7.pure 0.05 低于下限", te.price_below_floor(0.05) is True)
check("t7.pure 4.91 正常", te.price_below_floor(4.91) is False)
check("t7.pure 0.10 边界放行", te.price_below_floor(0.10) is False)
check("t7.pure 0.099 拒绝", te.price_below_floor(0.099) is True)
check("t7.pure None 放行", te.price_below_floor(None) is False)

# ---------- T7: do_buy 入口集成(dry-run + 假ws桩, 不触网不下单) ----------
print("== T7 do_buy 入口(假ws + 闸门桩, dry-run) ==")
te.query_signal_quality = None  # 归因反馈桩(不读真实库)
te.run_gate = lambda a, holdings, total_cash, quote, ws=None: {
    "verdict": "PASS", "sizing": {"shares": 100}, "rejects": [], "warnings": []}


class FakeWs:
    def positions(self):
        return {"data": {"positions": []}}

    def portfolio(self):
        return {"data": {"portfolio": {"totalAssets": 100000}}}

    def quote(self, code):
        return {"data": {code: {"price": 4.91, "prev_close": 4.88}}}


def mk_args(price):
    return Namespace(code="601398", name="工商银行", source="manual_confirmed",
                     sector="银行", price=price, volume=None, amount=None, budget=None,
                     execute=False, prev_close=None, pct_60d=None, amplitude_40d=None,
                     sector_breadth=None, intent=None, allow_add_position=False,
                     limit_price=None)


r = te.do_buy(mk_args(0.05), FakeWs())
check("t7.a 0.05被拒 REJECT", r.get("gate_verdict") == "REJECT" and r.get("executed") is False,
      str(r.get("reason")))
check("t7.a 理由含低价废单熔断", "低价废单" in str(r.get("reason", "")))

r2 = te.do_buy(mk_args(4.91), FakeWs())
check("t7.b 4.91通过校验(dry-run预览)", r2.get("gate_verdict") == "PASS"
      and "dry-run" in str(r2.get("reason", "")), str(r2.get("reason")))

_src = inspect.getsource(te.do_buy)
check("t7.struct 校验位于ws.buy之前", 0 <= _src.find("price_below_floor") < _src.find("ws.buy("))

# ---------- T8: 幂等查重(内存sqlite) ----------
print("== T8 trade_records 幂等查重(内存sqlite) ==")
conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE trade_records (id INTEGER PRIMARY KEY, code TEXT, name TEXT, "
             "trade_date TEXT, action TEXT, price REAL, volume INTEGER, amount REAL, created_at TEXT)")


def record_like_do_buy(code, tday, action, price, vol):
    """与 trade_executor do_buy 落库分支同构的写前查重模式(共用同一helper)。"""
    if te._trade_records_duplicate(conn, code, tday, action, price, vol):
        print(f"  duplicate skipped: {code} {tday} {action} {price} x{vol}")
        return False
    conn.execute("INSERT INTO trade_records (code,trade_date,action,price,volume) "
                 "VALUES (?,?,?,?,?)", (code, tday, action, price, vol))
    conn.commit()
    return True


def count():
    return conn.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0]


check("t8.空表查重False", te._trade_records_duplicate(
    conn, "sh601398", "20260923", "BUY", 4.91, 100) is False)
check("t8.c 首笔写入", record_like_do_buy("sh601398", "20260923", "BUY", 4.91, 100) is True
      and count() == 1)
check("t8.c 相同五元组第二次跳过", record_like_do_buy("sh601398", "20260923", "BUY", 4.91, 100) is False
      and count() == 1)
check("t8.d 不同volume正常写入", record_like_do_buy("sh601398", "20260923", "BUY", 4.91, 200) is True
      and count() == 2)
check("t8.d 不同price正常写入", record_like_do_buy("sh601398", "20260923", "SELL", 4.85, 100) is True
      and count() == 3)
check("t8.d 不同code正常写入", record_like_do_buy("sh601939", "20260923", "BUY", 4.91, 100) is True
      and count() == 4)
check("t8.do_sell分支同helper", "_trade_records_duplicate" in inspect.getsource(te.do_sell))

conn.close()

print(f"\n== 结果: PASS={len(PASS)} FAIL={len(FAIL)} ==")
if FAIL:
    print("失败用例: " + ", ".join(FAIL))
    sys.exit(1)
print("ALL PASS")
