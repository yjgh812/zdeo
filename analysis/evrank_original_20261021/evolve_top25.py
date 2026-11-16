# -*- coding: utf-8 -*-
"""全宇宙top25补算持20日期望收益(只读): score+EV双维择优, qfq实算."""
import json
import sqlite3

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
WINDOW, HOLD = 120, 20

top = json.load(open("universe_top25.json", encoding="utf-8"))
codes = [r["code"] for r in top]
names = {r["code"]: r["name"] for r in top}
l1s = {r["code"]: r["l1"] for r in top}
scores = {r["code"]: r["score"] for r in top}
win5s = {r["code"]: r["win5"] for r in top}

con = sqlite3.connect(QFQ, uri=True)
out = []
for code in codes:
    closes = [r[0] for r in con.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (code,))]
    i = len(closes) - 1
    rets = [closes[t + HOLD] / closes[t] - 1
            for t in range(max(0, i - WINDOW + 1), i + 1) if t + HOLD <= i]
    n = len(rets)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    p_win = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    exp = p_win * avg_win + (1 - p_win) * avg_loss
    plr = avg_win / abs(avg_loss) if avg_loss else float("inf")
    worst = min(rets)
    out.append(dict(code=code, name=names[code], l1=l1s[code], score=scores[code],
                    win5=win5s[code], exp=exp, p_win=p_win, avg_win=avg_win,
                    plr=plr, worst=worst))
con.close()

out.sort(key=lambda x: -x["exp"])
print(f"{'排名':<4}{'代码':<8}{'名称':<8}{'score':<7}{'胜率20':<8}{'期望20':<9}{'均盈':<8}{'盈亏比':<7}{'最差':<8}")
for k, r in enumerate(out, 1):
    print(f"{k:<4}{r['code']:<8}{r['name']:<8}{r['score']:<7.1f}{r['p_win']:<8.1%}"
          f"{r['exp']:<+9.2%}{r['avg_win']:<+8.2%}{r['plr']:<7.2f}{r['worst']:<+8.2%}")
