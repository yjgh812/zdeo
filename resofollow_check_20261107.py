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

    lag = defaultdict(list)      # buy-lag k days after start day -> ret5
    hold = defaultdict(list)     # hold horizon -> ret (buy T+1 open)
    rebn = defaultdict(list)     # rebound-recovery bucket -> ret5
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, o, c, v, p = s["d"], s["o"], s["c"], s["v"], s["p"]
        n = len(d)
        for i in range(25, n - 22):
            if c[i] <= 0 or not (0.5 <= c[i] <= 500):
                continue
            rt = p[i] / 100.0
            lim = 0.19 if c6[:3] in ("300", "301") else 0.09
            if not (0.05 <= rt < lim) or rt > 0.35:
                continue
            if c[i - 21] <= 0 or c[i - 1] / c[i - 21] - 1.0 >= -0.10:
                continue
            ma20 = float(np.mean(c[i - 20:i]))
            dev = c[i] / ma20 - 1.0 if ma20 > 0 else 9
            if dev >= 0.05:
                continue
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 9.9
            if not (1.0 <= volr <= 2.5):
                continue
            lo20 = min(c[i - 20:i + 1])
            if lo20 <= 0:
                continue
            rebr = c[i] / lo20 - 1.0
            # A: buy-lag (buy at T+k open, hold 5 days)
            for k in (1, 2, 3, 5):
                bi = i + k
                if bi + 5 < n and o[bi] > 0 and c[bi + 5] > 0:
                    lag[k].append(c[bi + 5] / o[bi] - 1.0)
            # B: hold horizon (buy T+1 open)
            for hb in (5, 10, 20):
                j = i + 1 + hb
                if j < n and o[i + 1] > 0 and c[j] > 0:
                    hold[hb].append(c[j] / o[i + 1] - 1.0)
            # C: recovery bucket (buy T+1 open hold 5)
            if o[i + 1] > 0 and c[i + 6] > 0:
                rbk = ("a修复<10%" if rebr < 0.10 else "b10-15%" if rebr < 0.15 else
                       "c15-25%" if rebr < 0.25 else "d修复>25%")
                rebn[rbk].append(c[i + 6] / o[i + 1] - 1.0)
    print("==== A. 启动后第k天开盘买(持5日): 时效衰减 ====")
    for k in (1, 2, 3, 5):
        a = np.array(lag[k])
        if len(a):
            print("滞后%d天买: n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
                k, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))
    print("")
    print("==== B. T+1开盘买, 持有期对比 ====")
    for hb in (5, 10, 20):
        a = np.array(hold[hb])
        if len(a):
            print("持有%d日: n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
                hb, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))
    print("")
    print("==== C. 启动日已从20日低点修复多少 x 追入持5日 ====")
    for bk in sorted(rebn):
        a = np.array(rebn[bk])
        if len(a):
            print("%-10s n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
                bk, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))


if __name__ == "__main__":
    main()
