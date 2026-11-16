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

    def is_lim(code6, r):
        return r >= (0.199 if code6[:3] in ("300", "301") else 0.099)

    # A: first-board next-day promotion rate
    fb_total = 0
    fb_promo = 0
    # B: consolidation-quality entry vs chase-first-board
    chase_rets = []
    cons_rets = []
    cons_low_dev = []   # entry-day dev < 5%
    cons_hi_dev = []    # dev >= 5%
    cons_vlow_volr = []  # entry-day volr < 0.8
    cons_wait_days = []
    # C: high-board(>=3) break-day next-day distribution
    hb_next = []
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, c, v, p, o = s["d"], s["c"], s["v"], s["p"], s["o"]
        n = len(d)
        lim_flags = [is_lim(c6, p[i] / 100.0) if c[i] > 0 and abs(p[i]) < 35 else False
                     for i in range(n)]
        i = 25
        while i < n - 8:
            if not lim_flags[i] or lim_flags[i - 1]:
                i += 1
                continue
            # first board at day i
            fb_total += 1
            if i + 1 < n and lim_flags[i + 1]:
                fb_promo += 1
            # chase ret: buy next open, sell close of i+6
            if o[i + 1] > 0 and c[i + 6] > 0:
                chase_rets.append(c[i + 6] / o[i + 1] - 1.0)
            # B: consolidation scan: days i+1 .. i+9
            fb_close = c[i]
            fb_vol = v[i]
            entered = False
            for k in range(i + 1, min(i + 10, n - 6)):
                if lim_flags[k]:
                    break  # second board = different path, not consolidation
                if c[k] < fb_close * 0.98:
                    break  # broke the board = failed
                vseg = [x for x in v[max(0, k - 20):k] if x > 0]
                volr = v[k] / float(np.mean(vseg)) if vseg and v[k] > 0 else 9.9
                if volr < 1.2:  # shrunk volume = quality confirmed
                    ma20 = float(np.mean(c[max(0, k - 20):k])) if k >= 20 else 0
                    dev = c[k] / ma20 - 1.0 if ma20 > 0 else 0
                    if o[k + 1] > 0 and c[k + 6] > 0:
                        cons_rets.append(c[k + 6] / o[k + 1] - 1.0)
                        (cons_low_dev if dev < 0.05 else cons_hi_dev).append(
                            c[k + 6] / o[k + 1] - 1.0)
                        if volr < 0.8:
                            cons_vlow_volr.append(c[k + 6] / o[k + 1] - 1.0)
                        cons_wait_days.append(k - i)
                        entered = True
                    break
            i += 1
        # C: consecutive run >= 3 boards, break day -> next day return
        run = 0
        for i in range(1, n - 2):
            if lim_flags[i]:
                run += 1
                continue
            if run >= 3 and i + 1 < n:
                if o[i + 1] > 0 and c[i + 1] > 0 and abs(p[i + 1]) < 35:
                    hb_next.append(p[i + 1] / 100.0)
            run = 0
    if fb_total:
        print("A. 首板次日晋级率: %d个首板, 次日再涨停%d个 = %.1f%%" % (
            fb_total, fb_promo, 100.0 * fb_promo / fb_total))
    arr = np.array(chase_rets)
    print("B1. 追首板(次日开盘买,持5日): n=%d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
        len(arr), 100 * arr.mean(), 100 * np.median(arr), 100 * float(np.mean(arr > 0))))
    arr2 = np.array(cons_rets)
    if len(arr2):
        print("B2. 横盘质量介入(首板后缩量+不破板再买,持5日): n=%d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            len(arr2), 100 * arr2.mean(), 100 * np.median(arr2), 100 * float(np.mean(arr2 > 0))))
        print("    等待天数中位: %.0f天" % float(np.median(cons_wait_days)))
        for lbl, arrx in (("  子集: 介入时位置<5%(低位横盘)", cons_low_dev),
                          ("  子集: 位置>=5%(高位横盘)", cons_hi_dev),
                          ("  子集: 介入日量比<0.8(极度缩量)", cons_vlow_volr)):
            a3 = np.array(arrx)
            if len(a3) >= 100:
                print("%s: n=%d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
                    lbl, len(a3), 100 * a3.mean(), 100 * np.median(a3),
                    100 * float(np.mean(a3 > 0))))
    arr3 = np.array(hb_next)
    if len(arr3):
        big = float(np.mean(arr3 <= -0.07))
        print("C. 高标(>=3连板)断板次日: n=%d mean=%+.2f%% median=%+.2f%% win=%.1f%% 跌<=-7%%占比=%.1f%%" % (
            len(arr3), 100 * arr3.mean(), 100 * np.median(arr3),
            100 * float(np.mean(arr3 > 0)), 100 * big))


if __name__ == "__main__":
    main()
