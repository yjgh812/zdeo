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

    by_chg = defaultdict(list)     # today-change bucket -> ret5
    by_volr = defaultdict(list)    # volr bucket -> ret5 (within chg>=3%)
    cross = defaultdict(list)      # volr x chg buckets
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, o, c, v, p = s["d"], s["o"], s["c"], s["v"], s["p"]
        n = len(d)
        for i in range(25, n - 7):
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
            rt = p[i] / 100.0
            if abs(rt) > 0.35:
                continue
            if o[i + 1] <= 0 or c[i + 6] <= 0:
                continue
            ret = c[i + 6] / o[i + 1] - 1.0
            cbk = ("a跌<0%" if rt < 0 else "b0-3%" if rt < 0.03 else
                   "c3-5%" if rt < 0.05 else "d5%-涨停下")
            by_chg[cbk].append(ret)
            if rt >= 0.03:
                vbk = "量比1-1.5" if volr < 1.5 else "量比1.5-2.5"
                by_volr[vbk].append(ret)
                cross[(vbk, cbk)].append(ret)
    print("==== 回踩框架(r20>0+位置带+量比1-2.5) x 当日涨幅档, 次日买持5日 ====")
    for bk in sorted(by_chg):
        a = np.array(by_chg[bk])
        print("%-12s n=%-8d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))
    print("")
    print("==== 当日涨幅>=3% 子集 x 量比细分 ====")
    for bk in sorted(by_volr):
        a = np.array(by_volr[bk])
        print("%-12s n=%-8d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))
    print("")
    print("==== 共振交叉表(量比 x 当日涨幅) ====")
    for (vb, cb), lst in sorted(cross.items()):
        a = np.array(lst)
        print("%-10s x %-12s n=%-7d mean=%+.3f%% win=%.1f%%" % (
            vb, cb, len(a), 100 * a.mean(), 100 * float(np.mean(a > 0))))


if __name__ == "__main__":
    main()
