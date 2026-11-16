# -*- coding: utf-8 -*-
"""test_c6_halve.py — decide_c6_halve 纯函数单测（R-20260828-01，只读，不触执行链）"""
import importlib.util

spec = importlib.util.spec_from_file_location("te", r"D:\zcodesj\trade_executor.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
f = m.decide_c6_halve

c6_hot = {"found": True, "sector": "电力设备", "pct_1d": 4.2, "pct_5d": 2.0}
c6_mild = {"found": True, "sector": "银行", "pct_1d": 2.5, "pct_5d": 1.0}
c6_none = {"found": False, "sector": None, "pct_1d": None, "pct_5d": None}

CASES = [
    # (c6, is_stop_loss, pnl_pct, no_c6_halve, 期望, 说明)
    (c6_hot, False, None, False, True, "板块>3%趋势卖→减半"),
    (c6_hot, True, None, False, False, "S-5止损豁免不减半"),
    (c6_hot, False, -0.09, False, False, "已破-8%硬止损不减半"),
    (c6_hot, False, None, True, False, "no_c6_halve关闭"),
    (c6_mild, False, None, False, False, "板块2.5%不触发"),
    (c6_none, False, None, False, False, "数据缺失fail-open"),
    (c6_hot, False, -0.05, False, True, "板块>3%且亏损未达-8%仍减半(防踏空语义)"),
]

ok = True
for c6, stop, pnl, noh, want, note in CASES:
    got = f(c6, stop, pnl, noh)
    flag = "✅" if got == want else "❌"
    if got != want:
        ok = False
    print(f"{flag} {note}: got={got} want={want}")
print("ALL PASS" if ok else "HAS FAIL")
import sys
sys.exit(0 if ok else 1)
