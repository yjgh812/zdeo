#!/usr/bin/env python3
"""5只走坏候选 连续2日复核 + 002478/601369 名实核对（只读，qfq 生产主库）20260907"""
import sqlite3

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
# westock 现价用于名实/数据源交叉核对
TARGETS = [
    ("300296", "利亚德", 5.06),
    ("301370", "国科恒泰", 8.68),
    ("601369", "常宝?(trend_checker曾报8.53)", 6.86),
    ("600987", "航民股份", 6.53),
    ("002174", "游族网络", 12.22),
    ("002478", "westock双名票", 6.86),
    ("002481", "双塔食品?", 4.50),
]

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()

for code6, name, wstock_px in TARGETS:
    rows = cur.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code LIKE ? ORDER BY date DESC LIMIT 40",
        (f"%{code6}%",)).fetchall()
    if len(rows) < 25:
        print(f"\n=== {code6} {name}: qfq行数不足({len(rows)})，代码可能不存在")
        continue
    rows = rows[::-1]
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    print(f"\n=== {code6} {name} | qfq最新收盘={closes[-1]} ({dates[-1]}) | westock现价={wstock_px}")
    streak = 0
    verdicts = []
    for i in range(20, len(closes)):
        c = closes[i]
        ma5 = sum(closes[i-4:i+1]) / 5
        ma10 = sum(closes[i-9:i+1]) / 10
        ma20 = sum(closes[i-19:i+1]) / 20
        bear = (c < ma20) and (ma5 < ma10 < ma20)
        streak = streak + 1 if bear else 0
        verdicts.append((dates[i], c, ma5, ma10, ma20, bear))
    for d, c, m5, m10, m20, bear in [v for v in verdicts if v[0] >= "20260828"]:
        flag = "🔴走坏" if bear else "ok"
        print(f"  {d} close={c:<8.2f} ma5={m5:.2f} ma10={m10:.2f} ma20={m20:.2f} {flag}")
    last2 = [v[5] for v in verdicts[-2:]]
    print(f"  → 最近两交易日({verdicts[-2][0]},{verdicts[-1][0]})连续走坏={'是' if all(last2) else '否'} | 当前连续天数={streak}")
con.close()
