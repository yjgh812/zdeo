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

    # buckets
    auction = defaultdict(list)      # open-gap bucket -> [ret5]
    auction_promo = defaultdict(lambda: [0, 0])  # bucket -> [promo, total]
    fb_volr = defaultdict(list)      # first-board-day volr bucket -> [ret5]
    fb_volr_promo = defaultdict(lambda: [0, 0])
    dip_volr = defaultdict(list)     # pullback-signal volr bucket -> [ret5]
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, c, v, p, o = s["d"], s["c"], s["v"], s["p"], s["o"]
        n = len(d)
        lim_flags = [is_lim(c6, p[i] / 100.0) if c[i] > 0 and abs(p[i]) < 35 else False
                     for i in range(n)]
        for i in range(25, n - 7):
            # --- pullback signal (r20>0, dev in [-3%,+5%], NOT limit-up today) ---
            if c[i] > 0 and 0.5 <= c[i] <= 500 and not lim_flags[i]:
                ma20 = float(np.mean(c[i - 20:i]))
                dev = c[i] / ma20 - 1.0 if ma20 > 0 else 9
                if -0.03 <= dev <= 0.05 and c[i - 21] > 0:
                    r20 = c[i - 1] / c[i - 21] - 1.0
                    if r20 > 0:
                        vseg = [x for x in v[i - 20:i] if x > 0]
                        volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 9.9
                        if o[i + 1] > 0 and c[i + 6] > 0:
                            ret = c[i + 6] / o[i + 1] - 1.0
                            bk = ("a<0.6" if volr < 0.6 else "b0.6-1" if volr < 1.0 else
                                  "c1-1.5" if volr < 1.5 else "d1.5-2.5" if volr < 2.5 else "e>2.5")
                            dip_volr[bk].append(ret)
            # --- first-board events ---
            if not lim_flags[i] or (i >= 1 and lim_flags[i - 1]):
                continue
            if not (0.5 <= c[i] <= 500):
                continue
            vseg = [x for x in v[i - 20:i] if x > 0]
            fbv = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 9.9
            promo = 1 if lim_flags[i + 1] else 0
            bk2 = ("a<0.3" if fbv < 0.3 else "b0.3-1" if fbv < 1.0 else
                   "c1-1.5" if fbv < 1.5 else "d1.5-2.5" if fbv < 2.5 else "e>2.5")
            fb_volr_promo[bk2][0] += promo
            fb_volr_promo[bk2][1] += 1
            if o[i + 1] > 0 and c[i + 6] > 0:
                fb_volr[bk2].append(c[i + 6] / o[i + 1] - 1.0)
                # auction gap next open vs first-board close
                gap = o[i + 1] / c[i] - 1.0
                bk3 = ("a<0%(lowopen)" if False else
                       "f<0" if gap < 0 else
                       "g0-3" if gap < 0.03 else
                       "h3-7" if gap < 0.07 else
                       "i7-9.5" if gap < 0.095 else "j>=9.5")
                auction[bk3].append(c[i + 6] / o[i + 1] - 1.0)
                auction_promo[bk3][0] += promo
                auction_promo[bk3][1] += 1
    def rep(title, dmap, dmap_promo=None):
        print("==== %s ====" % title)
        for bk in sorted(dmap.keys()):
            arr = np.array(dmap[bk])
            line = "%-10s n=%-6d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
                bk, len(arr), 100 * arr.mean(), 100 * np.median(arr),
                100 * float(np.mean(arr > 0)))
            if dmap_promo is not None and bk in dmap_promo:
                pr, tt = dmap_promo[bk]
                if tt:
                    line += " 晋级率%.1f%%" % (100.0 * pr / tt)
            print(line)
    rep("首板日量比分档 x 次日买入持5日", fb_volr, fb_volr_promo)
    print("")
    rep("首板次日开盘涨幅分档 x 追入持5日", auction, auction_promo)
    print("")
    rep("回踩信号量比分档 x 次日买入持5日(裁决缩量悖论)", dip_volr)


if __name__ == "__main__":
    main()
