# -*- coding: utf-8 -*-
"""枚举申万 L1/L2 指数代码, 确认 index_hist_sw 字段"""
import akshare as ak

for fn, lv in [("sw_index_first_info", "L1"), ("sw_index_second_info", "L2")]:
    try:
        df = getattr(ak, fn)()
        print(f"=== {fn} ({lv}) 行数={len(df)} 列={list(df.columns)} ===")
        print(df.head(3).to_string())
        print()
    except Exception as e:
        print(f"{fn}: ❌ {str(e)[:150]}")
