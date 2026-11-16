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
        "select code,date,open,high,low,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=20230101 order by code,date").fetchall()
    con.close()
    scon = sqlite3.connect("file:%s?mode=ro" % STDB, uri=True)
    st_set = {str(c)[-6:] for c, f in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)"
    ).fetchall() if f == 1}
    scon.close()
    g = defaultdict(lambda: {"d": [], "o": [], "h": [], "l": [], "c": [], "v": [], "p": []})
    for code, d, o, h, l, c, v, p in rows:
        s = g[code]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["h"].append(float(h or 0))
        s["l"].append(float(l or 0)); s["c"].append(float(c or 0)); s["v"].append(float(v or 0))
        s["p"].append(float(p or 0) if p is not None else 0.0)

    def is_lim(c6, r):
        return r >= (0.199 if c6[:3] in ("300", "301") else 0.099)

    H = 20          # hold horizon days
    SL = -0.08      # stop loss
    TP = 0.20       # take profit (the rule being judged)
    out_tp = []     # with 20% TP + 8% SL
    out_sl = []     # SL only, hold to T+20
    out_hold = []   # pure hold T+20 (no SL, reference)
    out_trail = []  # trailing: peak-drop 8% exit (owner trailing rule) + 8% SL
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, o, h, l, c, p, v = (s["d"], s["o"], s["h"], s["l"], s["c"], s["p"], s["v"])
        n = len(d)
        for i in range(25, n - H - 1):
            if c[i] <= 0 or not (0.5 <= c[i] <= 500) or is_lim(c6, p[i] / 100.0):
                continue
            ma20 = float(np.mean(c[i - 20:i]))
            dev = c[i] / ma20 - 1.0 if ma20 > 0 else 9
            if not (-0.03 <= dev <= 0.05):
                continue
            if c[i - 21] <= 0 or c[i - 1] / c[i - 21] - 1.0 <= 0:
                continue
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = (v[i] / float(np.mean(vseg))) if vseg and v[i] > 0 else 9.9
            if not (1.0 <= volr <= 2.5):
                continue
            ent = o[i + 1]
            if ent <= 0:
                continue
            # path simulation from day i+1 .. i+H
            slpx = ent * (1 + SL)
            tppx = ent * (1 + TP)
            r_tp = None
            peak = ent
            r_trail = None
            for k in range(i + 1, i + H + 1):
                if l[k] > 0 and h[k] > 0:
                    if l[k] <= slpx:            # stop hit first (conservative)
                        r_tp = SL
                        r_trail = SL
                        break
                    peak = max(peak, h[k])
                    trailpx = peak * 0.92
                    if l[k] <= trailpx:         # trailing 8% off peak
                        r_trail = trailpx / ent - 1.0
                        if r_tp is None:
                            pass
                else:
                    continue
            if h[i + H] > 0 and h[i + H] >= tppx and r_tp is None:
                r_tp = TP
            if r_trail is None and c[i + H] > 0:
                r_trail = c[i + H] / ent - 1.0
            # SL-only: keep scanning for stop, else exit at T+H close
            r_sl2 = None
            done = False
            for k in range(i + 1, i + H + 1):
                if l[k] > 0 and l[k] <= slpx:
                    r_sl2 = SL
                    done = True
                    break
            if not done and c[i + H] > 0:
                r_sl2 = c[i + H] / ent - 1.0
            if r_tp is None and c[i + H] > 0:
                r_tp = c[i + H] / ent - 1.0     # never hit TP -> hold to horizon
            r_hold = (c[i + H] / ent - 1.0) if c[i + H] > 0 else None
            if r_tp is not None:
                out_tp.append(r_tp)
            if r_sl2 is not None:
                out_sl.append(r_sl2)
            if r_trail is not None:
                out_trail.append(r_trail)
            if r_hold is not None:
                out_hold.append(r_hold)
    for lab, arr in (("A 20%硬止盈+8%止损", out_tp), ("B 仅8%止损持到20日", out_sl),
                     ("C 波动止盈(峰值回撤8%)+8%止损", out_trail),
                     ("D 无止盈无止损纯持20日(参考)", out_hold)):
        a = np.array(arr)
        print("%s: n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            lab, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))
    a_tp = np.array(out_tp)
    a_sl = np.array(out_sl)
    # right-tail comparison
    if len(a_tp) and len(a_sl):
        q = 95
        print("右尾对比: 95分位 止盈版=%+.1f%% vs 无止盈版=%+.1f%%" % (
            100 * np.percentile(a_tp, q), 100 * np.percentile(a_sl, q)))


if __name__ == "__main__":
    main()
