# -*- coding: utf-8 -*-
import sqlite3, sys
from collections import defaultdict
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
ICONDB = "file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro"


def main():
    rng = np.random.default_rng(7)
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
    icon = sqlite3.connect(ICONDB, uri=True)
    indmap = {str(k)[-6:]: l1 for k, _nm, l1 in icon.execute("select code,name,l1 from l1_member").fetchall()}
    icon.close()
    g = defaultdict(lambda: {"d": [], "o": [], "c": [], "v": [], "p": []})
    for code, d, o, c, v, p in rows:
        s = g[code]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["c"].append(float(c or 0))
        s["v"].append(float(v or 0)); s["p"].append(float(p or 0) if p is not None else 0.0)
    sig = defaultdict(list)  # date -> list of dicts
    mkt_dn = defaultdict(int)
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
            sig[d[i]].append({"r20": r20, "code": code, "o1": o[i + 1], "c6": c[i + 6],
                              "dev": dev, "volr": volr, "ind": indmap.get(c6, "未知")})
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        p, d = s["p"], s["d"]
        for i, dd in enumerate(d):
            if p[i] / 100.0 <= -0.07:
                mkt_dn[dd] += 1

    def rets_of(pick):
        rs = [(x["c6"] / x["o1"] - 1.0) for x in pick if x["o1"] > 0]
        if not rs:
            return None
        return float(np.mean(rs)), float(np.median(rs)), float(np.mean([x > 0 for x in rs]))

    K = 20
    methods = {"random20": [], "deepdev20": [], "lowvolr20": [], "inddiv20": []}
    for T, lst in sig.items():
        if not lst:
            continue
        gname = ("normal" if mkt_dn.get(T, 0) <= 15 else
                 "caution" if mkt_dn.get(T, 0) <= 30 else
                 "reduced" if mkt_dn.get(T, 0) <= 50 else "crash")
        # A random20: sample 30 times average
        acc = []
        for _ in range(30):
            if len(lst) <= K:
                acc.extend(lst)
            else:
                acc.extend(rng.choice(lst, size=K, replace=False).tolist())
        rr = rets_of(acc)
        if rr:
            methods["random20"].append((gname, *rr))
        # B deepest dev (deepest pullback)
        b = sorted(lst, key=lambda x: x["dev"])[:K]
        rr = rets_of(b)
        if rr:
            methods["deepdev20"].append((gname, *rr))
        # C lowest volr
        c2 = sorted(lst, key=lambda x: x["volr"])[:K]
        rr = rets_of(c2)
        if rr:
            methods["lowvolr20"].append((gname, *rr))
        # D industry-diversified: round-robin across industries, random order inside ind
        byind = defaultdict(list)
        for x in lst:
            byind[x["ind"]].append(x)
        inds = sorted(byind.keys(), key=lambda k: rng.random())
        picked = []
        round_ = 0
        while len(picked) < K and round_ < 10:
            for k in inds:
                if round_ < len(byind[k]) and len(picked) < K:
                    picked.append(byind[k][round_])
            round_ += 1
        rr = rets_of(picked)
        if rr:
            methods["inddiv20"].append((gname, *rr))
    for mn, arr in methods.items():
        print("==== %s ====" % mn)
        for gname in ("normal", "caution", "reduced", "crash"):
            sub = [a for a in arr if a[0] == gname]
            if not sub:
                print("%-8s no samples" % gname)
                continue
            allr = np.array([a[1] for a in sub])
            med = np.array([a[2] for a in sub])
            wr = np.array([a[3] for a in sub])
            print("%-8s days=%-4d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
                gname, len(sub), 100 * allr.mean(), 100 * med.mean(), 100 * wr.mean()))


if __name__ == "__main__":
    main()
