# -*- coding: utf-8 -*-
"""add_evrank5.py — 主人令11/2「量化综合排名第一,选5只股票」: 原版EV排名前5中剔除计划已覆盖, 补5只新票入计划。"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")
os.chdir(r"D:\zcodesj")
from stock_name_lookup import get_name

# fresh(20261030) EV排名 + 10/30收盘: 来源 evrank_fresh_out.json / evrank_fresh_pool10.json
CAND = [
    ("601598", 7.16),   # 中国外运 EV#2
    ("600377", 13.83),  # 宁沪高速 EV#3
    ("601288", 7.48),   # 农业银行 EV#5
    ("601528", 5.44),   # 瑞丰银行 EV#6
    ("002332", 8.89),   # 仙琚制药 EV#8
]
plan = json.load(open(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json", encoding="utf-8"))
in_plan = {p["code"] for p in plan}
out = []
for code, px in CAND:
    nm = get_name(code)
    if code in in_plan:
        print("跳过(已在计划):", code, nm)
        continue
    if not nm:
        print("名称解析失败, 拒绝入计划:", code)
        sys.exit(2)
    out.append({"code": code, "name": nm, "source": "quant_rank_original",
                "volume": 100, "limit_price": round(px * 1.005, 2),
                "note": "主人令20261102[量化综合排名第一(原版)选5只]; 高挂0.5%%; 信号日20261030; EV近120日持20日口径"})
    print("  +", code, nm, "@%.2f" % (px * 1.005))

RP = os.path.realpath(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json")
assert RP == r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json", "路径越界"
plan.extend(out)
json.dump(plan, open(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("计划: %d 笔 | 预算≈%.0f元" % (len(plan), sum(x["limit_price"] * x["volume"] for x in plan)))
