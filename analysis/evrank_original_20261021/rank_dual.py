# -*- coding: utf-8 -*-
"""复合排名(只读): 上涨概率(p_win)排名 + 上涨空间(EV)排名 相加, 双条件综合最优."""
import json
import sqlite3

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
WINDOW, HOLD = 120, 20

top = json.load(open("universe_top25.json", encoding="utf-8"))
codes = [r["code"] for r in top]
meta = {r["code"]: r for r in top}

con = sqlite3.connect(QFQ, uri=True)
stats = {}
for code in codes:
    closes = [r[0] for r in con.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (code,))]
    i = len(closes) - 1
    rets = [closes[t + HOLD] / closes[t] - 1
            for t in range(max(0, i - WINDOW + 1), i + 1) if t + HOLD <= i]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    pw = len(wins) / len(rets)
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(losses) / len(losses) if losses else 0
    exp = pw * aw + (1 - pw) * al
    worst = min(rets)
    stats[code] = dict(p_win=pw, exp=exp, avg_win=aw, plr=aw / abs(al) if al else 99, worst=worst)
con.close()

rows = []
for code in codes:
    m = meta[code]
    r = stats[code]
    rows.append(dict(code=code, name=m["name"], l1=m["l1"], score=m["score"],
                     p_win=r["p_win"], exp=r["exp"], avg_win=r["avg_win"],
                     plr=r["plr"], worst=r["worst"]))
rows.sort(key=lambda x: -x["p_win"])
for k, r in enumerate(rows, 1):
    r["rank_p"] = k
rows.sort(key=lambda x: -x["exp"])
for k, r in enumerate(rows, 1):
    r["rank_e"] = k
for r in rows:
    r["composite"] = round(r["rank_p"] + r["rank_e"], 1)
rows.sort(key=lambda x: x["composite"])

pd_desc = """  -- 说明: 复合分=上涨概率排名+期望收益排名(越小越优); p_win=近120日持20日上涨概率;
-- exp=同窗口持20日期望收益; 空间维度=概率加权后的净期望(胜率x盈亏比已内嵌)"""
print(pd_desc)
print(f"{'#':<3}{'代码':<8}{'名称':<8}{'行业':<6}{'score':<7}{'概率':<7}{'期望':<8}{'均盈':<8}{'盈亏比':<7}{'最差':<8}{'复合':<5}")
for k, r in enumerate(rows[:12], 1):
    print(f"{k:<3}{r['code']:<8}{r['name']:<8}{r['l1']:<6}{r['score']:<7.1f}"
          f"{r['p_win']:<7.1%}{r['exp']:<+8.2%}{r['avg_win']:<+8.2%}{r['plr']:<7.2f}"
          f"{r['worst']:<+8.2%}{r['composite']:<5.1f}")
