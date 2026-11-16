# -*- coding: utf-8 -*-
"""add_dcontrol.py — 补齐中枢对照臂8笔至挂单计划(主人令20261101: 每种盈利方式补足至10只各100股)。
读取sel11_out(三源验证过)的type_sample_D_control名单, 跳W23/已收; 输出v2计划(56+8=64笔)。"""
import os, sys, json, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from stock_name_lookup import get_name

sel = json.load(open(os.path.join(HERE, "sel11_out.json"), encoding="utf-8"))
plan_path = os.path.join(ROOT, "data", "winner_methods_topup_plan_20261101.json")
plan = json.load(open(plan_path, encoding="utf-8"))
taken = {p["code"] for p in plan}
W23_SKIP = {"001318", "600928", "000001", "601528", "002839", "600908", "000415"}

OLD_DC = {"601166", "000560"}  # 兴业银行/我爱我家 = 中枢对照现持2只
quota = 10 - len(OLD_DC)

added = []
for s in sel["groups"]["type_sample_D_control"]["stocks"]:
    code = s["code"]
    if code in taken or code in W23_SKIP or code in OLD_DC:
        continue
    px = float(s["close"])
    added.append({
        "code": code, "name": get_name(code) or "?",
        "source": "type_sample_D_control",
        "volume": 100, "limit_price": round(px * 1.005, 2),
        "note": "主人令20261101盈利模式扩容[中枢对照]老2+新8之新#%d; 高挂0.5%%; 信号日%d; %s" % (
            len(added) + 1, int(sel["signal_date"]), s["reason"][:50]),
    })
    taken.add(code)
    if len(added) >= quota:
        break

plan.extend(added)
out = os.path.realpath(plan_path)
assert out == os.path.realpath(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json"), "路径越界"
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tot = sum(p["limit_price"] * p["volume"] for p in plan)
print("v2计划:", len(plan), "笔 | 预算≈%.0f元" % tot)
for a in added:
    print("  +", a["code"], a["name"], a["limit_price"])
