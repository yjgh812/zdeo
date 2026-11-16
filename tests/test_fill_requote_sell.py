# -*- coding: utf-8 -*-
"""test_fill_requote_sell.py — fill_requote 卖单 pegging 合同测试（2026-11-11 主人批准扩展）。
运行: python -I tests/test_fill_requote_sell.py（纯本地零 I/O，不污染 guard_precheck_log）
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "fre", os.path.join(os.path.dirname(HERE), "fill_requote_executor.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _row(price, qty=100, confirmed=0, requoted=False):
    return {"code": "sh603619", "name": "中曼石油", "price": price,
            "qty": qty, "confirmed": confirmed, "requoted": requoted}


def _check(label, price, cur, floor, want, want_price=None):
    r = m.classify_sell_row(_row(price), cur, floor)
    assert r["status"] == want, f"{label}: {r}"
    if want_price is not None:
        assert r["new_price"] == want_price, f"{label}: price={r.get('new_price')}"
    print(f"PASS {label}")


_check("中曼重演 REQ@18.52", 18.75, 18.56, 18.50, "REQ", 18.52)
_check("市价达挂价 HOLD", 10.00, 10.01, 9.00, "HOLD")
_check("无下限 NO_FLOOR", 10.00, 9.80, 0, "NO_FLOOR")
_check("深度跌破 DROP", 10.00, 9.00, 9.30, "DROP")
_check("封底clamp REQ@9.30", 10.00, 9.31, 9.30, "REQ", 9.30)
_check("常规REQ@9.33", 10.00, 9.35, 9.30, "REQ", 9.33)

_r = m.classify_sell_row(_row(10.00, requoted=True), 9.80, 9.00)
assert _r["status"] == "HOLD" and "已改过一次" in _r["reason"], _r
print("PASS requoted HOLD")

_r = m.classify_sell_row(_row(10.00, confirmed=100), 9.80, 9.00)
assert _r["status"] == "HOLD", _r
print("PASS rem0 HOLD")

_r = m.classify_sell_row(_row(10.00), None, 9.00)
assert _r["status"] == "NO_QUOTE", _r
print("PASS NO_QUOTE")
print("UNIT_PASS 10/10")
