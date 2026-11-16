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
        s["d"].append(int(d))
        s["o"].append(float(o or 0)); s["c"].append(float(c or 0))
        s["v"].append(float(v or 0)); s["p"].append(float(p or 0) if p is not None else 0.0)
    # per-stock per-day precompute: valid signal + features
    sig = defaultdict(list)  # date -> [(r20, code, on_next, c6_close_plus6)]
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, c, v, p, o = s["d"], s["c"], s["v"], s["p"], s["o"]
        n = len(d)
        for i in range(25, n - 7):
            px = c[i]
            if px <= 0 or not (2.0 <= px <= 30.0):
                continue
            rt = p[i] / 100.0
            lim = 0.19 if c6[:3] in ("300", "301") else 0.09
            if rt >= lim:
                continue
            ma20 = float(np.mean(c[i - 20:i]))
            dev = px / ma20 - 1.0
            if not (-0.03 <= dev <= 0.05):
                continue
            if c[i - 21] <= 0:
                continue
            r20 = c[i - 1] / c[i - 21] - 1.0
            if r20 <= 0:
                continue
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 99.0
            if volr >= 1.5:
                continue
            sig[d[i]].append((r20, code, o[i + 1], c[i + 6]))
    # market-wide down>=7% counts per day (same universe)
    mkt_dn = defaultdict(int)
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        p, d = s["p"], s["d"]
        for i, dd in enumerate(d):
            if p[i] / 100.0 <= -0.07:
                mkt_dn[dd] += 1
    # daily groups by density level
    groups = {"normal": [], "caution": [], "reduced": [], "crash": []}
    groups_all = {"normal": [], "caution": [], "reduced": [], "crash": []}
    groups_low = {"normal": [], "caution": [], "reduced": [], "crash": []}
    for T, lst in sig.items():
        k = mkt_dn.get(T, 0)
        if not lst:
            continue
        gname = ("normal" if k <= 15 else "caution" if k <= 30 else "reduced" if k <= 50 else "crash")
        lst.sort(key=lambda x: -x[0])
        top = lst[:30]
        rets = [(c6 / on - 1.0) if on > 0 else None for r20, code, on, c6 in top]
        rets = [x for x in rets if x is not None]
        if not rets:
            continue
        groups[gname].append((float(np.mean(rets)), float(np.median(rets)),
                              float(np.mean([x > 0 for x in rets]))))
        # all-signal equal weight
        rets_all = [(c6 / on - 1.0) if on > 0 else None for r20, code, on, c6 in lst]
        rets_all = [x for x in rets_all if x is not None]
        groups_all[gname].append((float(np.mean(rets_all)), float(np.median(rets_all)),
                                  float(np.mean([x > 0 for x in rets_all]))))
        # weakest-momentum bottom30
        low = lst[-30:]
        rets_low = [(c6 / on - 1.0) if on > 0 else None for r20, code, on, c6 in low]
        rets_low = [x for x in rets_low if x is not None]
        groups_low[gname].append((float(np.mean(rets_low)), float(np.median(rets_low)),
                                  float(np.mean([x > 0 for x in rets_low]))))
    def rep(label, gr):
        print("==== %s ====" % label)
        for name in ("normal", "caution", "reduced", "crash"):
            arr = gr[name]
            if not arr:
                print("%-8s no samples" % name)
                continue
            allr = np.array([r for r, m, w in arr])
            med = np.array([m for r, m, w in arr])
            wr = np.array([w for r, m, w in arr])
            print("%-8s days=%-4d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
                name, len(arr), 100 * allr.mean(), 100 * med.mean(), 100 * wr.mean()))
    rep("选法A: r20最强的30只", groups)
    rep("选法B: 全部信号等权", groups_all)
    rep("选法C: r20最弱的30只", groups_low)
    crash_days = sorted(t for t in sig if mkt_dn.get(t, 0) > 50 and sig[t])
    print("crash signal days:", len(crash_days))


if __name__ == "__main__":
    main()
