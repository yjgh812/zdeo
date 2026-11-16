# -*- coding: utf-8 -*-
"""revival_wave23_batch_plan_gen.py — Wave23 名单 → 官方 buy-batch 执行计划生成器

输入: data/revival_wave23_picks_20261028.json (md5 4cc8093082e82e00f7cbe5345ca1bce1, 冻结名单)
输出: data/revival_wave23_batch_plan_20261028.json (trade_executor buy-batch argparse 字段, 同 Wave1 通道)
字段语义: sector=申万L1(industry_group_map_snapshot), prev_close=T前一交易日(20261026)qfq收盘,
note=方法+规则摘要, execute=true。生成物只读, 名单本体不改动(价格/股数原样透传)。
"""
from __future__ import annotations
import json, sys, os, sqlite3

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
IND_DB = os.path.join(ROOT, "data", "industry_group_map_snapshot.db")
SRC = os.path.join(ROOT, "data", "revival_wave23_picks_20261028.json")
OUT = os.path.join(ROOT, "data", "revival_wave23_batch_plan_20261028.json")

doc = json.load(open(SRC, encoding="utf-8"))
T = int(doc["signal_date"])
assert T == 20261027, f"名单信号日漂移: {T}"

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates = [r[0] for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date<? order by date desc limit 2", (T,))]
prev_d = dates[0]
ind_con = sqlite3.connect(f"file:{IND_DB}?mode=ro", uri=True)
ind = dict(ind_con.execute("select code,l1 from l1_member"))
ind_con.close()

plan = []
for src, m in doc["methods"].items():
    for p in m["picks"]:
        code6 = p["code"]
        pc = con.execute(
            "select close from stock_daily_meta_qfq where code=? and date=?",
            (code6, prev_d)).fetchone()
        plan.append({
            "code": code6,
            "name": p["name"],
            "source": src,
            "price": p["price"],
            "volume": p["volume"],
            "sector": ind.get(code6, "未知"),
            "prev_close": round(float(pc[0]), 2) if pc and pc[0] else None,
            "note": f"revival W23 {src} {p['rule'][:40]}",
            "execute": True,
        })
con.close()

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False, indent=1)

n = len(plan)
cost = sum(p["price"] * p["volume"] for p in plan)
miss_sector = sum(1 for p in plan if p["sector"] == "未知")
miss_pc = sum(1 for p in plan if p["prev_close"] is None)
print(f"plan={OUT}\nitems={n} budget={cost:.0f} sector缺失={miss_sector} prev_close缺失={miss_pc} prev_date={prev_d}")
import hashlib
print("md5:", hashlib.md5(open(OUT, "rb").read()).hexdigest())
