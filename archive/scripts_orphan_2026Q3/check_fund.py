#!/usr/bin/env python3
import sqlite3
c = sqlite3.connect("D:/workbuddyspace/factor_engine/data/dragon_quant.db", timeout=10)
cols = [x[1] for x in c.execute("PRAGMA table_info(stock_factors_fundamental)").fetchall()]
print("stock_factors_fundamental cols:", cols)
print("total:", len(cols))
has_droe = [x for x in cols if "droe" in x.lower()]
has_roa = [x for x in cols if "roa" in x.lower()]
has_ep = [x for x in cols if x.lower() in ["ep", "ep_ttm"]]
has_bp = [x for x in cols if x.lower() in ["bp", "bp_lr"]]
print("droe:", has_droe, "roa:", has_roa, "ep:", has_ep, "bp:", has_bp)
# Check factor_daily too
cols2 = [x[1] for x in c.execute("PRAGMA table_info(factor_daily)").fetchall()]
print("\\nfactor_daily cols (first 30):", cols2[:30])
has = [x for x in cols2 if x.lower() in ["ep", "bp", "droe", "roe"]]
print("has ep/bp/droe/roe:", has)
c.close()