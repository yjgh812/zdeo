# -*- coding: utf-8 -*-
import sqlite3, sys
from collections import defaultdict
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
FUND = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
STDB = "D:/zcodesj/data/st_history.db"


def main():
    con = sqlite3.connect(QFQ, uri=True)
    rows = con.execute(
        "select code,date,open,high,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=20230101 order by code,date").fetchall()
    con.close()
    scon = sqlite3.connect("file:%s?mode=ro" % STDB, uri=True)
    st_set = {str(c)[-6:] for c, f in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)"
    ).fetchall() if f == 1}
    scon.close()
    g = defaultdict(lambda: {"d": [], "o": [], "h": [], "c": [], "v": [], "p": []})
    for code, d, o, h, c, v, p in rows:
        s = g[code]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["h"].append(float(h or 0))
        s["c"].append(float(c or 0)); s["v"].append(float(v or 0))
        s["p"].append(float(p or 0) if p is not None else 0.0)
    # market equal-weight daily return (attribution-pipeline universe)
    mkt = defaultdict(list)
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        for i, dd in enumerate(s["d"]):
            mkt[dd].append(s["p"][i] / 100.0)
    mkt_ret = {dd: float(np.mean(a)) for dd, a in mkt.items() if len(a) > 200}

    # A: LHB decay path
    fcon = sqlite3.connect(FUND, uri=True)
    ev = fcon.execute(
        "select DISTINCT ts_code, trade_date from dragon_tiger_new where trade_date>=20230101").fetchall()
    fcon.close()
    horizons = (1, 3, 5, 10)
    lhb_rets = {k: [] for k in horizons}
    dmap = {}
    for ts, td in ev:
        c6 = ts[:6]
        s = g.get(c6)
        if not s:
            continue
        d = s["d"]
        try:
            i = d.index(int(td))
        except ValueError:
            continue
        dmap[c6] = (s, i)
    for c6, (s, i) in dmap.items():
        c, d = s["c"], s["d"]
        for k in horizons:
            j = i + k
            if j < len(c) and c[i] > 0 and c[j] > 0 and dd_ok(d, j, k):
                stock = c[j] / c[i] - 1.0
                mr = cum_mkt(mkt_ret, d, i, j)
                lhb_rets[k].append(stock - mr)
    print("==== A. 龙虎榜上榜股 超额衰减路径(n=%d事件) ====" % len(dmap))
    for k in horizons:
        arr = np.array(lhb_rets[k])
        if len(arr):
            print("T+%d  n=%-6d 超额=%+.3f%% 中位=%+.3f%% 胜率=%.1f%%" % (
                k, len(arr), 100 * arr.mean(), 100 * np.median(arr),
                100 * float(np.mean(arr > 0))))

    # B: auction-gap sweet zone (open gap bucket x day volume ratio bucket)
    sweet = {"in": [], "out": [], "hi6": []}   # in=2.8-3.5%&volr>5, hi6=gap>6%
    day_stats = {"in": [0, 0, 0], "out": [0, 0, 0], "hi6": [0, 0, 0]}  # [n, red, seal]
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, o, h, c, v, p = s["d"], s["o"], s["h"], s["c"], s["v"], s["p"]
        n = len(d)
        for i in range(25, n - 7):
            if c[i] <= 0 or c[i - 1] <= 0 or o[i] <= 0 or not (0.5 <= c[i] <= 500):
                continue
            gap = o[i] / c[i - 1] - 1.0
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 0
            prev_p = p[i - 1] / 100.0
            lim = 0.19 if c6[:3] in ("300", "301") else 0.09
            limpx = c[i - 1] * (1 + lim)
            touched = h[i] >= limpx * 0.998
            red = c[i] > o[i]
            ret5 = (c[i + 6] / o[i + 1] - 1.0) if o[i + 1] > 0 and c[i + 6] > 0 else None
            key = None
            if 0.028 <= gap <= 0.035 and volr > 5:
                key = "in"
            elif gap >= 0.06:
                key = "hi6"
            else:
                key = "out"
            st3 = day_stats[key]
            st3[0] += 1
            st3[1] += 1 if red else 0
            st3[2] += 1 if touched else 0
            if ret5 is not None:
                sweet[key].append(ret5)
    print("")
    print("==== B. 集合竞价高开分档(全天量比近似口径) ====")
    for key, lab in (("in", "高开2.8-3.5%+量比>5(文中sweet区)"),
                     ("out", "其余高开"),
                     ("hi6", "高开>=6%(文中陷阱区)")):
        st3 = day_stats[key]
        arr = np.array(sweet[key])
        print("%s: n=%-6d 当日收红%.1f%% 当日冲板%.1f%% | 次日买持5日 mean=%+.3f%% win=%.1f%%" % (
            lab, st3[0], 100.0 * st3[1] / st3[0] if st3[0] else 0,
            100.0 * st3[2] / st3[0] if st3[0] else 0,
            100 * arr.mean(), 100 * float(np.mean(arr > 0))))

    # C: sector resonance (same-industry limit-up count on the day)
    icon = sqlite3.connect("file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro", uri=True)
    indmap = {str(k)[-6:]: l1 for k, _nm, l1 in icon.execute("select code,name,l1 from l1_member").fetchall()}
    icon.close()
    day_ind_lim = defaultdict(lambda: defaultdict(int))
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        for i, dd in enumerate(s["d"]):
            p = s["p"][i] / 100.0
            if p >= (0.19 if c6[:3] in ("300", "301") else 0.09) and abs(p) < 35:
                day_ind_lim[dd][indmap.get(c6, "?")] += 1
    iso, reso = [], []
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, o, c, p = s["d"], s["o"], s["c"], s["p"]
        for i in range(25, len(d) - 7):
            pp = p[i] / 100.0
            if not (abs(pp) < 35 and pp >= (0.19 if c6[:3] in ("300", "301") else 0.09)):
                continue
            if c[i] <= 0 or not (0.5 <= c[i] <= 500):
                continue
            k = day_ind_lim.get(d[i], {}).get(indmap.get(c6, "?"), 0)
            if o[i + 1] > 0 and c[i + 6] > 0:
                r5 = c[i + 6] / o[i + 1] - 1.0
                (iso if k <= 1 else reso).append(r5)
    print("")
    print("==== C. 板块共振(同行业当日涨停家数) ====")
    for lab, arr in (("孤立(同行业仅自己)", iso), ("共振(同行业>=2只涨停)", reso)):
        a = np.array(arr)
        print("%s: n=%-7d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            lab, len(a), 100 * a.mean(), 100 * np.median(a), 100 * float(np.mean(a > 0))))


def dd_ok(d, j, k):
    return j < len(d) and (d[j] >= d[j - k])


def cum_mkt(mkt_ret, d, i, j):
    tot = 1.0
    for x in range(i + 1, j + 1):
        if d[x] in mkt_ret:
            tot *= (1 + mkt_ret[d[x]])
    return tot - 1.0


if __name__ == "__main__":
    main()
