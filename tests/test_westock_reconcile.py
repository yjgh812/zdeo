#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""westock_reconcile 契约测试（纯函数，不联网不写库）。

覆盖 9/12 立项合同：
  T1 filter_filled: 只认已成交（撤单/未成交/系统撤单/坏行剔除）+ 字段规范化
  T2 aggregate_qty: (code,action) 聚合
  T3 diff_day 键级缺口: 全对齐0缺口 / 整键缺失(tr_qty=0,可自动补) /
     部分缺失(tr_qty>0,须人工) shortfall / SELL 缺口
  T4 9/11 实弹口径回归: 3 BUY(中粮糖业两笔聚合10500+海南橡胶3000)缺口、4 SELL 无缺口
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\zcodesj")
from westock_reconcile import aggregate_qty, diff_day, filter_filled  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


WS = [
    {"orderId": "1", "code": "sh600737", "name": "中粮糖业", "direction": "buy", "quantity": "10000",
     "price": "16.21", "amount": "162142.14", "status": "已成交", "filledAt": "2026-09-11 14:16:48"},
    {"orderId": "2", "code": "sh600737", "name": "中粮糖业", "direction": "buy", "quantity": "500",
     "price": "16.22", "amount": "8115.08", "status": "已成交", "filledAt": "2026-09-11 14:16:32"},
    {"orderId": "3", "code": "sh601118", "name": "海南橡胶", "direction": "buy", "quantity": "3000",
     "price": "6.74", "amount": "20225.25", "status": "已成交", "filledAt": "2026-09-11 14:15:47"},
    {"orderId": "4", "code": "sh601866", "name": "中远海发", "direction": "buy", "quantity": "9900",
     "price": "2.52", "amount": "24954.48", "status": "已撤单", "filledAt": "2026-09-10 10:00:47"},
    {"orderId": "5", "code": "sz300588", "name": "熙菱信息", "direction": "sell", "quantity": "300",
     "price": "22.48", "amount": "6735.62", "status": "已成交", "filledAt": "2026-09-10 09:43:22"},
    {"orderId": "6", "code": "sz002481", "name": "双塔食品", "direction": "buy", "quantity": "10000",
     "price": "4.56", "amount": "45611.40", "status": "系统撤单", "filledAt": "2026-09-08 15:32:08"},
    {"orderId": "7", "code": "bad", "name": "坏行", "direction": "buy", "quantity": "x",
     "price": "1", "amount": "1", "status": "已成交", "filledAt": "2026-09-11 10:00:00"},
]

# T1 filter_filled（已成交4行=9/11三笔+9/10熙菱；剔2撤单+1坏行）
filled = filter_filled(WS)
check("T1a 只保留已成交(7行→4行)", len(filled) == 4, f"got {len(filled)}")
codes = {(r["code6"], r["action"]) for r in filled}
check("T1b code规范化去前缀+direction大写", ("600737", "BUY") in codes and ("300588", "SELL") in codes, str(codes))
check("T1c tdate由filledAt转换", all(r["tdate"] == "20260911" for r in filled if r["code6"] == "600737"))

# T2 aggregate_qty
agg = aggregate_qty(filled)
check("T2 同股同向聚合(600737 BUY=10500)", agg.get(("600737", "BUY")) == 10500, str(agg))

# T3 diff_day 键级
def tr(code, action, qty, tdate="20260911"):
    return {"code6": code, "action": action, "qty": qty, "price": 0, "filled_at": "",
            "tdate": tdate, "name": "", "amount": 0}

ws_0911 = [r for r in filled if r["tdate"] == "20260911"]
TR_FULL = [tr("600737", "BUY", 10500), tr("601118", "BUY", 3000), tr("300588", "SELL", 300)]
check("T3a 全对齐0缺口", diff_day(ws_0911, TR_FULL) == [])
gaps = diff_day(ws_0911, [tr("600737", "BUY", 10500), tr("300588", "SELL", 300)])
check("T3b 整键缺失(tr_qty=0)", len(gaps) == 1 and gaps[0]["code6"] == "601118"
      and gaps[0]["shortfall"] == 3000 and gaps[0]["tr_qty"] == 0,
      str([(g["code6"], g["shortfall"], g["tr_qty"]) for g in gaps]))
gaps2 = diff_day(ws_0911, [tr("600737", "BUY", 5000), tr("601118", "BUY", 3000), tr("300588", "SELL", 300)])
g2 = [g for g in gaps2 if g["code6"] == "600737"]
check("T3c 部分缺失(tr_qty>0) shortfall=5500 须人工", len(g2) == 1 and g2[0]["tr_qty"] == 5000
      and g2[0]["shortfall"] == 5500, str(g2))
ws_sell = ws_0911 + [dict(tr("000567", "SELL", 1400), filled_at="2026-09-11 11:20:46")]
gaps3 = diff_day(ws_sell, TR_FULL)
check("T3d SELL缺口同样识别", len(gaps3) == 1 and gaps3[0]["code6"] == "000567"
      and gaps3[0]["action"] == "SELL", str([(g["code6"], g["action"]) for g in gaps3]))

# T4 9/11 实弹口径：台账=当日 4 SELL（锦江在线/大烨/锦江航运/海德）、0 BUY
TR_0911 = [tr(c, "SELL", q) for c, q in (("600650", 300), ("300670", 14600),
                                         ("601083", 200), ("000567", 1400))]
gaps_0911 = diff_day(ws_0911, TR_0911)
check("T4a 缺口键=2(600737/601118 BUY)", sorted((g["code6"], g["action"]) for g in gaps_0911)
      == [("600737", "BUY"), ("601118", "BUY")], str([(g["code6"], g["action"]) for g in gaps_0911]))
check("T4b 原始笔数=3(中粮两笔+海南一笔)", sum(len(g["records"]) for g in gaps_0911) == 3)
check("T4c SELL侧4笔无缺口", all(g["action"] == "BUY" for g in gaps_0911))

print("=" * 40)
print(f"FAILED: {FAILS}" if FAILS else "全部 11 项 PASS")
sys.exit(1 if FAILS else 0)
