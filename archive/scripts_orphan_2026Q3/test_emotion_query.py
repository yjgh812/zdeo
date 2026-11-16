import sqlite3, sys
c = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
maxd = c.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0]
print("maxd:", maxd)
prev = dict(c.execute(
    "SELECT a.code, a.close FROM stock_daily_hsj a "
    "WHERE a.date = (SELECT MAX(b.date) FROM stock_daily_hsj b "
    "         WHERE b.code = a.code AND b.date < ? AND b.vol > 0)",
    (maxd,)
).fetchall())
print("prev count:", len(prev))
if prev:
    codes, closes = zip(*list(prev.items())[:3])
    print("samples:", codes, closes)
else:
    print("prev EMPTY!")
    # test with hardcoded date
    prev2 = dict(c.execute("SELECT code, close FROM stock_daily_hsj WHERE date=20260813").fetchall())
    print("test with 20260813:", len(prev2))
c.close()