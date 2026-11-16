# -*- coding: utf-8 -*-
import sqlite3, sys
from collections import defaultdict
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "D:/zcodesj/data/st_history.db"


def main():
    con = sqlite3.connect(QFQ, uri=True)
    rows = con.execute(
        "select code,date,open,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=20230101 order by code,date").fetchall()
    con.close()
    scon = sqlite3.connect("file:%s?mode=ro" % STDB, uri=True)
    st_set = {str(c)[-6:] for c, f in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)"
    ).fetchall() if f == 1}
    scon.close()
    g = defaultdict(lambda: {"d": [], "o": [], "c": [], "v": [], "p": []})
    for code, d, o, c, v, p in rows:
        s = g[code]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["c"].append(float(c or 0))
        s["v"].append(float(v or 0)); s["p"].append(float(p or 0) if p is not None else 0.0)

    def is_lim(c6, r):
        return r >= (0.199 if c6[:3] in ("300", "301") else 0.09)

    byr20 = defaultdict(list)
    byhold = defaultdict(list)
    byma60 = defaultdict(list)
    bydev = defaultdict(list)
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, o, c, v, p = s["d"], s["o"], s["c"], s["v"], s["p"]
        n = len(d)
        for i in range(65, n - 22):
            if c[i] <= 0 or not (0.5 <= c[i] <= 500) or is_lim(c6, p[i] / 100.0):
                continue
            ma20 = float(np.mean(c[i - 20:i]))
            dev = c[i] / ma20 - 1.0 if ma20 > 0 else 9
            if not (-0.03 <= dev <= 0.05):
                continue
            if c[i - 21] <= 0:
                continue
            r20 = c[i - 1] / c[i - 21] - 1.0
            if r20 <= 0:
                continue
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 9.9
            if not (1.0 <= volr <= 2.5):
                continue
            ma60 = float(np.mean(c[i - 60:i]))
            ma60p = float(np.mean(c[i - 65:i - 5]))
            if o[i + 1] <= 0 or c[i + 6] <= 0:
                continue
            ret5 = c[i + 6] / o[i + 1] - 1.0
            rbk = ("a涨0-5%" if r20 < 0.05 else "b5-15%" if r20 < 0.15 else
                   "c15-30%" if r20 < 0.30 else "d涨>30%")
            byr20[rbk].append(ret5)
            mbk = ("a MA60向上" if c[i] > ma60 > ma60p else "b MA60其他")
            byma60[mbk].append(ret5)
            dbk = ("a贴线-3~-1%" if dev < -0.01 else "b-1~+2%" if dev < 0.02 else
                   "c+2~+5%")
            bydev[dbk].append(ret5)
            for hb in (5, 10, 20):
                j = i + 1 + hb
                if j < n and c[j] > 0:
                    byhold[hb].append(c[j] / o[i + 1] - 1.0)
    print("==== A. 回踩族 x 前20日涨幅档 (次日买持5日) ====")
    for bk in sorted(byr20):
        a = np.array(byr20[bk])
        print("%-10s n=%-8d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))
    print("")
    print("==== B. 回踩族持有期对比 (次日开盘买) ====")
    for hb in (5, 10, 20):
        a = np.array(byhold[hb])
        if len(a):
            print("持有%d日: n=%-8d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
                hb, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))
    print("")
    print("==== C. 回踩族 x MA60趋势质量 ====")
    for bk in sorted(byma60):
        a = np.array(byma60[bk])
        print("%-12s n=%-8d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))
    print("")
    print("==== D. 回踩族 x 偏离MA20细分 ====")
    for bk in sorted(bydev):
        a = np.array(bydev[bk])
        print("%-12s n=%-8d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))


if __name__ == "__main__":
    main()
