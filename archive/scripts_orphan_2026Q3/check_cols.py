#!/usr/bin/env python3
import sqlite3
c = sqlite3.connect("D:/workbuddyspace/factor_engine/data/dragon_quant.db", timeout=10)
cols = [x[1] for x in c.execute("PRAGMA table_info(stock_factors)").fetchall()]
print("stock_factors columns:", cols[:30])
print("total cols:", len(cols))
has_ep = [x for x in cols if "ep" in x.lower() and x not in ("eps",)]
has_bp = [x for x in cols if "bp" in x.lower() and x not in ("bps",)]
has_droe = [x for x in cols if "droe" in x.lower()]
print("ep-like:", has_ep, "bp-like:", has_bp, "droe:", has_droe)
c.close()