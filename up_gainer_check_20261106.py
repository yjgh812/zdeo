# -*- coding: utf-8 -*-
import sqlite3, sys
from collections import defaultdict
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"


def main():
    con = sqlite3.connect(QFQ, uri=True)
    rows = con.execute(
        "select code,date,open,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=20230101 order by code,date").fetchall()
    con.close()
    scon = sqlite3.connect(STDB, uri=True)
    st_set = {str(c)[-6:] for c, f in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)"
    ).fetchall() if f == 1}
    scon.close()
    g = defaultdict(lambda: {"d": [], "o": [], "c": [], "v": [], "p": []})
    for code, d, o, c, v, p in rows:
        s = g[code]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["c"].append(float(c or 0))
        s["v"].append(float(v or 0)); s["p"].append(float(p or 0) if p is not None else 0.0)
    # per-day up5 pool with features; per-day market down7 count
    up5 = defaultdict(list)   # date -> list of dict
    mkt_dn = defaultdict(int)
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, c, v, p, o = s["d"], s["c"], s["v"], s["p"], s["o"]
        for i, dd in enumerate(d):
            if p[i] / 100.0 <= -0.07:
                mkt_dn[dd] += 1
        for i in range(25, len(d) - 7):
            r = p[i] / 100.0
            if r < 0.05 or r > 0.35 or not (0.5 <= c[i] <= 500):
                continue  # hygiene: absurd moves/prices are data garbage
            ma20 = float(np.mean(c[i - 20:i]))
            dev = c[i] / ma20 - 1.0 if ma20 > 0 else 0
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 0
            lim = 0.19 if c6[:3] in ("300", "301") else 0.09
            up5[d[i]].append({"code": code, "r": r, "px": c[i], "dev": dev, "volr": volr,
                            "sealed": r >= lim, "o1": o[i + 1], "c6c": c[i + 6]})
    def grp(name, sel):
        rets = []
        for T, lst in up5.items():
            pick = [x for x in lst if sel(x) and x["o1"] > 0]
            if not pick:
                continue
            k = mkt_dn.get(T, 0)
            gname = ("normal" if k <= 15 else "caution" if k <= 30 else
                     "reduced" if k <= 50 else "crash")
            if gname != name:
                continue
            rets.extend((x["c6c"] / x["o1"] - 1.0) for x in pick)
        if not rets:
            print("%-8s %s no samples" % (name, label))
            return
        arr = np.array(rets)
        print("%-8s %s n=%-6d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            name, label, len(arr), 100 * arr.mean(), 100 * np.median(arr),
            100 * float(np.mean(arr > 0))))
    tests = [
        ("全部涨5%", lambda x: True),
        ("未封板(5%~涨停线下)", lambda x: not x["sealed"]),
        ("封板(含连板)", lambda x: x["sealed"]),
        ("低价<15元", lambda x: x["px"] < 15),
        ("放量volr>=2", lambda x: x["volr"] >= 2.0),
        ("温和量1-2", lambda x: 1.0 <= x["volr"] < 2.0),
        ("高位dev>10%", lambda x: x["dev"] > 0.10),
        ("平位dev<5%", lambda x: x["dev"] < 0.05),
    ]
    for label, sel in tests:
        for name in ("normal", "crash"):
            grp(name, sel)
        print("")
    print("---- crash日分档补充 ----")
    for label, sel in tests:
        for name in ("caution", "reduced"):
            grp(name, sel)
        print("")


if __name__ == "__main__":
    main()
