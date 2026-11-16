# -*- coding: utf-8 -*-
"""单选裁决数据(只读): 10只候选近120日持20日的期望收益/赔率/回撤, qfq真源实算."""
import sqlite3

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
STOCKS = [
    ("601939", "建设银行"), ("601398", "工商银行"), ("601225", "陕西煤业"),
    ("600901", "江苏金租"), ("002292", "奥飞娱乐"), ("600229", "城市传媒"),
    ("601288", "农业银行"), ("601098", "中南传媒"), ("000719", "中原传媒"),
    ("002376", "新北洋"),
]
WINDOW = 120
HOLD = 20

con = sqlite3.connect(QFQ, uri=True)
out = []
for code, name in STOCKS:
    closes = [r[0] for r in con.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (code,))]
    i = len(closes) - 1
    rets = []
    for t in range(max(0, i - WINDOW + 1), i + 1):
        if t + HOLD <= i:
            rets.append(closes[t + HOLD] / closes[t] - 1)
    n = len(rets)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    p_win = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    exp = p_win * avg_win + (1 - p_win) * avg_loss
    pl_ratio = avg_win / abs(avg_loss) if avg_loss else float("inf")
    worst = min(rets)
    out.append((code, name, exp, p_win, avg_win, avg_loss, pl_ratio, worst))
con.close()

out.sort(key=lambda x: -x[2])
print(f"近{WINDOW}日任意日买入持{HOLD}日 | 排序: 期望收益降序")
print(f"{'代码':<8}{'名称':<8}{'期望收益':<9}{'胜率':<8}{'均盈':<8}{'均亏':<9}{'盈亏比':<7}{'最差单次':<8}")
for code, name, exp, p_win, aw, al, plr, worst in out:
    print(f"{code:<8}{name:<8}{exp:<9.2%}{p_win:<8.1%}{aw:<+8.2%}{al:<+9.2%}{plr:<7.2f}{worst:<+8.2%}")
