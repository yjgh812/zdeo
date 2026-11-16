# -*- coding: utf-8 -*-
"""add_mh.py — 补MASTER/HIST臂6笔(老4+新6=10); 幂等守卫: 计划内已有mh臂则拒跑。"""
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

# 幂等守卫(上轮add_dcontrol重跑翻车教训)
if any(p["source"] == "revival_master_hist" for p in plan):
    print("已有MH臂, 拒绝重复添加")
    sys.exit(0)

taken = {p["code"] for p in plan}
W23_SKIP = {"001318", "600928", "000001", "601528", "002839", "600908", "000415"}
OLD_MH = {"601988", "600019", "601816", "600027"}  # 中国银行/宝钢/京沪高铁/华电国际
quota = 10 - len(OLD_MH)

added = []
for s in sel["groups"]["revival_master_hist"]["stocks"]:
    code = s["code"]
    if code in taken or code in W23_SKIP or code in OLD_MH:
        continue
    px = float(s["close"])
    added.append({
        "code": code, "name": get_name(code) or "?",
        "source": "revival_master_hist",
        "volume": 100, "limit_price": round(px * 1.005, 2),
        "note": "主人令20261101盈利模式扩容[MASTER/HIST双模型]老4+新6之新#%d; 高挂0.5%%; 信号日%d; %s" % (
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
print("计划:", len(plan), "笔 | 预算≈%.0f元" % tot)
for a in added:
    print("  +", a["code"], a["name"], a["limit_price"])
