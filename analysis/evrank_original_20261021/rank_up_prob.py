# -*- coding: utf-8 -*-
"""上涨概率排名(只读): 10只候选, P_up=近120日 5/10/20日持有胜率均值, qfq真源实算."""
import sqlite3

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
STOCKS = [
    # code, name, batch(1=严档第一批, 2=次优档第二批)
    ("601225", "陕西煤业", 1), ("600901", "江苏金租", 1), ("000719", "中原传媒", 1),
    ("601098", "中南传媒", 1), ("002292", "奥飞娱乐", 1),
    ("601939", "建设银行", 2), ("601398", "工商银行", 2), ("600229", "城市传媒", 2),
    ("601288", "农业银行", 2), ("002376", "新北洋", 2),
]
WINDOW = 120

con = sqlite3.connect(QFQ, uri=True)
out = []
for code, name, batch in STOCKS:
    closes = [r[0] for r in con.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (code,))]
    i = len(closes) - 1
    stats = {}
    for k in (5, 10, 20):
        wins = tot = 0
        for t in range(max(0, i - WINDOW + 1), i + 1):
            if t + k <= i:
                tot += 1
                if closes[t + k] / closes[t] - 1 > 0:
                    wins += 1
        stats[f"win{k}"] = wins / tot if tot >= 30 else float("nan")
    p_up = sum(stats.values()) / 3
    out.append((code, name, batch, p_up, stats["win5"], stats["win10"], stats["win20"]))
con.close()

out.sort(key=lambda x: -x[3])
print(f"{'排名':<4}{'代码':<8}{'名称':<8}{'批次':<5}{'P_up':<8}{'win5':<8}{'win10':<8}{'win20':<8}")
for rank, (code, name, batch, p_up, w5, w10, w20) in enumerate(out, 1):
    print(f"{rank:<4}{code:<8}{name:<8}{batch:<5}{p_up:<8.1%}{w5:<8.1%}{w10:<8.1%}{w20:<8.1%}")
