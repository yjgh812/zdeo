import sqlite3, sys, numpy as np

c = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
maxd = c.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0]
print("maxd:", maxd)

# find previous trading day
prev_d = c.execute("SELECT MAX(date) FROM stock_daily_hsj WHERE date < ? AND vol > 0", (maxd,)).fetchone()[0]
print("prev_d:", prev_d)

prevs = dict(c.execute("SELECT code, close FROM stock_daily_hsj WHERE date = ? AND vol > 0", (prev_d,)).fetchall())
rows = c.execute("SELECT code, close FROM stock_daily_hsj WHERE date = ? AND vol > 0", (maxd,)).fetchall()
print("prevs:", len(prevs), "rows:", len(rows))

pcts = []
for code, close in rows:
    p = prevs.get(code)
    if p and p > 0:
        pcts.append((close - p) / p * 100)

pcts = [p for p in pcts if abs(p) < 50]
print("pcts:", len(pcts))
if pcts:
    print("avg:", round(float(np.mean(pcts)), 2), "median:", round(float(np.median(pcts)), 2))
    print("up:", sum(1 for p in pcts if p > 0), "down:", sum(1 for p in pcts if p < 0))
else:
    print("EMPTY pcts!")
c.close()