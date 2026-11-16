# -*- coding: utf-8 -*-
"""monday_buy12_plan_20261107.py — 周一12只买入计划生成(主人令 2026-11-07「周一买入这12只股票,模拟盘」)
v5名单(各法4只) → trade_executor buy-batch 合同json:
  - owner_hang_open_pct=1.01(开盘价×1.01高挂, 开盘涨停自动剔除=机制内置)
  - source=dip_*_v1(实验臂, horizon_exit_executor凭此认领5日到期/收盘-8%自动卖)
  - 每只100股; R37弱档特批+admission/zone/prescreen等五特批旗标随单
周一9:27由 seven_methods_buy_run 同款流程执行(开盘价机制=挂单价由executor开盘时实时取)。
"""
import json
import os

ROOT = r"D:\zcodesj"
os.chdir(ROOT)

PLAN = [
    # (code, name, source, sector, note)
    ("301046", "能辉科技", "dip_up_v1", "建筑装饰", "起涨型黄金带 修复17.4%"),
    ("300931", "通用电梯", "dip_up_v1", "机械设备", "起涨型严格池 位置-0.4%"),
    ("600410", "华胜天成", "dip_up_v1", "计算机", "起涨型真超跌 量比放宽档"),
    ("301265", "华新科技", "dip_up_v1", "电子", "起涨型次档 位置放宽档"),
    ("000595", "新能股份", "dip_fb_v1", "公用事业", "底反首板 储能并网+扭亏"),
    ("001258", "立新能源", "dip_fb_v1", "公用事业", "底反首板"),
    ("600156", "华升股份", "dip_fb_v1", "纺织服饰", "底反首板"),
    ("600207", "安彩高科", "dip_fb_v1", "电力设备", "底反首板 4.88元超低"),
    ("300286", "安科瑞", "dip_ret_v1", "电力设备", "回踩确认型 段内强度1"),
    ("300360", "炬华科技", "dip_ret_v1", "电力设备", "回踩确认型 段内强度2"),
    ("600959", "江苏有线", "dip_ret_v1", "传媒", "回踩确认型 已持加仓"),
    ("300030", "阳普医疗", "dip_ret_v1", "医药生物", "回踩确认型"),
]

entries = []
for code, name, source, sector, note in PLAN:
    entries.append({
        "code": code,
        "name": name,
        "source": source,
        "sector": sector,
        "volume": 100,
        "note": "周一12只买入令(主人20261107)|%s" % note,
    })

plan = {
    "plan_id": "monday12_20261109",
    "generated_at": "2026-11-07",
    "owner_order": "周一买入这12只股票,模拟盘",
    "mode": "owner_hang_open_pct",
    "hang_open_pct": 1.01,
    "volume_each": 100,
    "entries": entries,
    "flags": ["--owner-approve-prescreen", "--owner-approve-admission",
              "--owner-approve-zone", "--owner-approve-r37",
              "--owner-hang-on-open-pct", "1.01"],
}
out = os.path.join(ROOT, "data", "monday12_buy_plan_20261109.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("计划落盘:", out)
print("共%d只(起涨4+首板4+回踩4), 每只100股" % len(entries))
print("执行: 周一9:27 buy-batch --execute, 开盘价×1.01高挂, 开盘涨停自动剔除")
