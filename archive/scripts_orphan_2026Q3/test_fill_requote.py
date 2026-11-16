# -*- coding: utf-8 -*-
"""test_fill_requote.py — fill_requote_executor 分类逻辑单测（只读，不触真实文件/不撤单）"""
import sys
import importlib.util

spec = importlib.util.spec_from_file_location("fqe", r"D:\zcodesj\fill_requote_executor.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CASES = [
    # (row, cur, ref, 期望status, 说明)
    ({"code": "sz002445", "price": 3.69, "qty": 2000, "confirmed": 0, "requoted": False}, 3.67, 3.71, "HOLD", "现价<挂单价: 市场可成交等自然成交"),
    ({"code": "sz002379", "price": 20.10, "qty": 1000, "confirmed": 0, "requoted": False}, 18.70, 20.10, "HOLD", "现价<挂单价: 低于计划价买入=更优, 不撤"),
    ({"code": "sh601009", "price": 11.54, "qty": 3000, "confirmed": 0, "requoted": False}, 11.62, 11.72, "REQ", "挂单价≤现价<ref: 改价贴近现价"),
    ({"code": "sh601009", "price": 11.54, "qty": 3000, "confirmed": 0, "requoted": True}, 11.62, 11.72, "HOLD", "已改过一次: 不再改"),
    ({"code": "sh600000", "price": 10.00, "qty": 1000, "confirmed": 0, "requoted": False}, 12.50, 10.00, "DROP", "追高: 现价>ref×1.03, 撤单"),
    ({"code": "sh600000", "price": 10.00, "qty": 1000, "confirmed": 0, "requoted": False}, 9.50, 10.00, "HOLD", "现价<挂单价: 等自然成交"),
    ({"code": "sh600000", "price": 10.00, "qty": 1000, "confirmed": 0, "requoted": False}, None, 10.00, "NO_QUOTE", "无实时价: fail-closed"),
    ({"code": "sh600000", "price": 3.69, "qty": 2000, "confirmed": 0, "requoted": False}, 3.70, 3.72, "REQ", "REQ新价约束抽查"),
    ({"code": "sh601009", "price": 11.54, "qty": 3000, "confirmed": 0, "requoted": False}, 11.62, 11.60, "REQ", "现价>ref但<ref×1.03: 非追高, 改价到ref(获批价)"),
    ({"code": "sh601009", "price": 11.54, "qty": 3000, "confirmed": 0, "requoted": False}, 11.95, 11.60, "DROP", "现价>ref×1.03: 真追高撤单"),
    ({"code": "sh601009", "price": 11.54, "qty": 3000, "confirmed": 0, "requoted": False}, 11.50, 11.60, "HOLD", "现价=挂单价附近, 改价无收益"),
]

ok = True
for row, cur, ref, want, note in CASES:
    got = m.classify_row(row, cur, ref)["status"]
    flag = "✅" if got == want else "❌"
    if got != want:
        ok = False
    print(f"{flag} cur={cur} ref={ref} -> {got} (期望 {want}) | {note}")

# REQ 新价 ≤ ref 且 > 挂单价 的约束抽查
r = m.classify_row({"code": "sh600000", "price": 3.69, "qty": 2000, "confirmed": 0, "requoted": False}, 3.70, 3.72)
np_ = r.get("new_price")
assert np_ and 3.69 < np_ <= 3.72, f"REQ新价越界: {np_}"
r2 = m.classify_row({"code": "sh601009", "price": 11.54, "qty": 3000, "confirmed": 0, "requoted": False}, 11.62, 11.60)
assert r2.get("new_price") == 11.60, f"ref封顶未生效: {r2}"
print(f"REQ 新价约束 ✅ new_price={np_} / ref封顶 {r2.get('new_price')} (=ref11.60)")
print("ALL PASS" if ok else "HAS FAIL")
sys.exit(0 if ok else 1)
