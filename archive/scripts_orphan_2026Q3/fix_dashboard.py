#!/usr/bin/env python3
"""Fix the _pct_distribution function in app.py — prev_close from stock_daily_hsj self-join."""
import re

p = r"D:\zcodesj\tulong_dashboard\app.py"
with open(p, "r", encoding="utf-8") as f:
    code = f.read()

old = """        prev_d = con.execute(
            \"SELECT MAX(date) FROM stock_daily_hsj WHERE date<?\", (maxd,)
        ).fetchone()[0]
        prevs = dict(
            con.execute(
                \"SELECT code, close FROM stock_daily_hsj WHERE date=?\", (prev_d,)
            ).fetchall()
        )
        rows = con.execute(
            \"SELECT code, close FROM stock_daily_hsj WHERE date=? AND vol>0\", (maxd,)
        ).fetchall()"""

new = """        prevs = dict(
            con.execute(
                \"SELECT a.code, a.close FROM stock_daily_hsj a \"\"
                \"WHERE a.date = (SELECT MAX(b.date) FROM stock_daily_hsj b \"\"
                \"         WHERE b.code = a.code AND b.date < ? AND b.vol > 0)\",
                (maxd,)
            ).fetchall()
        )
        rows = con.execute(
            \"SELECT code, close FROM stock_daily_hsj WHERE date=? AND vol>0\", (maxd,)
        ).fetchall()"""

code = code.replace(old, new)
with open(p, "w", encoding="utf-8") as f:
    f.write(code)
print("done")