# -*- coding: utf-8 -*-
"""group_pnl_view_20261029.py — 持仓按信号源分组涨跌全景(主人令·westock真值)"""
import json, os
from collections import defaultdict

src_of = {}
for line in open(r'D:/zcodesj/data/executor_orders.jsonl', encoding='utf-8'):
    line = line.strip()
    if not line: continue
    try: r = json.loads(line)
    except: continue
    if str(r.get('action','')).upper() != 'BUY': continue
    code = str(r.get('code','')).zfill(6)
    src = r.get('source','?')
    td = str(r.get('trade_date',''))
    if code not in src_of or td < src_of[code][1]:
        src_of[code] = (src, td)

doc = json.load(open(r'D:/zcodesj/data/positions_latest.json', encoding='utf-8'))
groups = defaultdict(lambda: {"n":0, "sum_pr":0.0, "sum_profit":0.0, "up":[], "dn":[], "mval":0.0})
for p in doc["positions"]:
    code = str(p["code"]).replace("sh","").replace("sz","").replace("bj","").zfill(6)
    src, td = src_of.get(code, ("legacy遗留仓", ""))
    pr = float(p.get("profitRate", "0") or 0)
    profit = float(p.get("profit", "0") or 0)
    mv = float(p.get("marketValue", "0") or 0)
    g = groups[src]
    g["n"] += 1; g["sum_pr"] += pr; g["sum_profit"] += profit; g["mval"] += mv
    (g["up"] if pr >= 0 else g["dn"]).append((p["name"], code, pr, profit))

rank = sorted(groups.items(), key=lambda kv: -(kv[1]["sum_pr"]/max(kv[1]["n"],1)))
tp = doc.get("totalProfit") or doc.get("summary", {}).get("totalProfit", "N/A")
tmv = doc.get("totalMarketValue") or doc.get("summary", {}).get("totalMarketValue", "N/A")
print(f"=== 持仓分组全景 (共{len(doc['positions'])}只, 总浮盈{tp}, 总市值{tmv}) ===\n")
for src, g in rank:
    avg = g["sum_pr"]/max(g["n"],1)
    flag = "🟢" if avg > 1 else ("🔴" if avg < -1 else "⚪")
    print(f"{flag} {src:<26} {g['n']:>3}只 均值{avg:>+6.2f}% 浮盈{g['sum_profit']:>+9.0f}元 (涨{len(g['up'])}/跌{len(g['dn'])}) 市值{g['mval']:.0f}")
    for nm, code, pr, pf in sorted(g["up"], key=lambda x: -x[2])[:5]:
        print(f"      ↑ {nm:<6} {code} {pr:>+6.2f}% ({pf:+.0f}元)")
    for nm, code, pr, pf in sorted(g["dn"], key=lambda x: x[2])[:5]:
        print(f"      ↓ {nm:<6} {code} {pr:>+6.2f}% ({pf:+.0f}元)")
    print()
