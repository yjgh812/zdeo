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

    def is_lim(code6, r):
        return r >= (0.199 if code6[:3] in ("300", "301") else 0.099)

    bkout = defaultdict(list)    # breakout volr bucket -> ret5
    fb_pos = defaultdict(list)   # first-board prior20 bucket -> ret5
    cons_cap = defaultdict(list)     # consolidation: cap<=3% vs >3% -> ret5
    cons_volr = defaultdict(list)    # consolidation: kday-vol vs fb-vol ratio bucket -> ret5
    dip_only = []                # shrink-pullback WITHOUT r20 cond -> ret5
    dip_full = 0
    dip_full_pos = 0
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, c, v, p, o = s["d"], s["c"], s["v"], s["p"], s["o"]
        n = len(d)
        lim_flags = [is_lim(c6, p[i] / 100.0) if c[i] > 0 and abs(p[i]) < 35 else False
                     for i in range(n)]
        for i in range(25, n - 7):
            if not (0.5 <= c[i] <= 500):
                continue
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 9.9
            ma20 = float(np.mean(c[i - 20:i]))
            dev = c[i] / ma20 - 1.0 if ma20 > 0 else 9
            # A: breakout above prior-20d high, not limit-up
            hi20 = max(c[i - 20:i])
            if not lim_flags[i] and c[i] > hi20 and o[i + 1] > 0 and c[i + 6] > 0:
                bk = ("a<1" if volr < 1.0 else "b1-2" if volr < 2.0 else
                      "c2-3" if volr < 3.0 else "d>3")
                bkout[bk].append(c[i + 6] / o[i + 1] - 1.0)
            # D: shrink-pullback WITHOUT momentum condition (dev band + volr only)
            if not lim_flags[i] and -0.03 <= dev <= 0.05 and volr < 1.0 \
                    and o[i + 1] > 0 and c[i + 6] > 0:
                dip_only.append(c[i + 6] / o[i + 1] - 1.0)
            # first-board events
            if lim_flags[i] and i >= 1 and not lim_flags[i - 1]:
                prior20 = c[i - 1] / c[i - 21] - 1.0 if c[i - 21] > 0 else 0
                if o[i + 1] > 0 and c[i + 6] > 0:
                    pbk = ("a底反<-10%" if prior20 < -0.10 else "b-10~0" if prior20 < 0 else
                           "c0~+10%" if prior20 < 0.10 else "d高位>+10%")
                    fb_pos[pbk].append(c[i + 6] / o[i + 1] - 1.0)
                # consolidation-quality scan (entry day k)
                fb_close = c[i]
                fb_vol = v[i]
                for k in range(i + 1, min(i + 10, n - 6)):
                    if lim_flags[k]:
                        break
                    if c[k] < fb_close * 0.98:
                        break
                    vseg2 = [x for x in v[max(0, k - 20):k] if x > 0]
                    kv = v[k] / float(np.mean(vseg2)) if vseg2 and v[k] > 0 else 9.9
                    ratio = v[k] / fb_vol if fb_vol > 0 else 9.9
                    cap = c[k] / fb_close - 1.0
                    if kv < 1.2:
                        if o[k + 1] > 0 and c[k + 6] > 0:
                            r5 = c[k + 6] / o[k + 1] - 1.0
                            cons_cap["a上限<=3%" if cap <= 0.03 else "b上限>3%"].append(r5)
                            rb = ("a量<0.5倍" if ratio < 0.5 else
                                  "b0.5-1倍" if ratio < 1.0 else "c>1倍")
                            cons_volr[rb].append(r5)
                        break
    print("==== A. 放量突破20日新高 x 量比档 (次日开盘买持5日) ====")
    for bk in sorted(bkout):
        arr = np.array(bkout[bk])
        print("%-8s n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(arr), 100 * arr.mean(), 100 * np.median(arr),
            100 * float(np.mean(arr > 0))))
    print("")
    print("==== B. 首板 x 前20日涨跌档 (次日开盘买持5日) ====")
    for bk in sorted(fb_pos):
        arr = np.array(fb_pos[bk])
        print("%-10s n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(arr), 100 * arr.mean(), 100 * np.median(arr),
            100 * float(np.mean(arr > 0))))
    print("")
    print("==== C1. 横盘介入 x 回调上限(收盘/首板收盘) ====")
    for bk in sorted(cons_cap):
        arr = np.array(cons_cap[bk])
        print("%-10s n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(arr), 100 * arr.mean(), 100 * np.median(arr),
            100 * float(np.mean(arr > 0))))
    print("==== C2. 横盘介入 x 当日量/首板日量 比值 ====")
    for bk in sorted(cons_volr):
        arr = np.array(cons_volr[bk])
        print("%-10s n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(arr), 100 * arr.mean(), 100 * np.median(arr),
            100 * float(np.mean(arr > 0))))
    print("")
    arr = np.array(dip_only)
    print("==== D. 无动量条件的缩量回踩(仅位置带+量比<1) ====")
    print("n=%d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
        len(arr), 100 * arr.mean(), 100 * np.median(arr),
        100 * float(np.mean(arr > 0))))


if __name__ == "__main__":
    main()
