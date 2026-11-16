r"""rise_fall_anatomy_20261116.py — 全宇宙涨跌段解剖（PREREG_RISEFALL_ANATOMY_V1，指纹0498f242fea68b50）。

四问拆解(主人2026-11-16令): 什么股票能涨(Q1)/什么时间段能涨(Q2)/涨什么样(Q3)/跌什么样(Q4)。
数据=D层库stock_daily(逐股逐日21维)+market_daily(pct_above20大盘状态)。
拆段=zigzag θ=25%(往返阈值), 段最短3交易日, 仅已确认段。零交易权, 禁作选股公式。
用法: python -I rise_fall_anatomy_20261116.py [run2]
判定口径严格按预注册冻结定义实现, 禁止事后调参。
"""
import hashlib
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC_DB = r"D:\zcodesj\data\universe_portrait_fullseries.db"
PREREG = r"D:\zcodesj\data\PREREG_RISEFALL_ANATOMY_V1_20261116.json"
OUT1 = r"D:\zcodesj\reports\RISEFALL_ANATOMY_20261116.json"
OUT2 = OUT1.replace(".json", "_run2.json")
THETA = 0.25
MIN_DAYS = 3
BIG = 50.0


def zigzag(closes):
    """θ=25% zigzag → 已确认段列表[(kind,si,ei)]。kind='U'(谷→峰)/'D'(峰→谷)。相邻枢轴连线，末段未确认不计。"""
    n = len(closes)
    legs = []
    if n == 0:
        return legs
    ext = closes[0]
    ext_i = 0
    direction = 1
    prev_i = None
    prev_kind = None
    for i in range(1, n):
        c = closes[i]
        if direction == 1:
            if c > ext:
                ext = c
                ext_i = i
            elif c <= ext * (1.0 - THETA):
                if prev_kind == "L":
                    legs.append(("U", prev_i, ext_i))
                prev_i = ext_i
                prev_kind = "H"
                direction = -1
                ext = c
                ext_i = i
        else:
            if c < ext:
                ext = c
                ext_i = i
            elif c >= ext * (1.0 + THETA):
                if prev_kind == "H":
                    legs.append(("D", prev_i, ext_i))
                prev_i = ext_i
                prev_kind = "L"
                direction = 1
                ext = c
                ext_i = i
    return legs


def pctile(xs, q):
    """xs须已升序。线性插值百分位。"""
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return xs[0]
    pos = (n - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    f = pos - lo
    return xs[lo] + (xs[hi] - xs[lo]) * f


def med(xs):
    return pctile(sorted(xs), 50) if xs else None


def dist_bucket(items, keyfn, buckets):
    """items分桶计数→{bucket: (count, share_of_total)}。"""
    cnt = defaultdict(int)
    for it in items:
        cnt[keyfn(it)] += 1
    tot = len(items)
    return {k: [cnt.get(k, 0), round(cnt.get(k, 0) / tot * 100.0, 2)] for k in buckets} if tot else {}


def leg_anatomy_U(c, dims, si, ei):
    """涨段解剖。c=closes, dims=dict of per-bar lists。返回dict或None(段过短)。"""
    days = ei - si
    if days < MIN_DAYS:
        return None
    gain = (c[ei] / c[si] - 1.0) * 100.0
    mid = (si + ei) // 2
    tot = c[ei] / c[si] - 1.0
    front = ((c[mid] / c[si] - 1.0) / tot) if tot != 0 else None
    peak = c[si]
    mp = 0.0
    for k in range(si + 1, ei):
        if c[k] > peak:
            peak = c[k]
        d0 = c[k] / peak - 1.0
        if d0 < mp:
            mp = d0
    vleg = [dims["vr5"][k] for k in range(si, ei + 1) if dims["vr5"][k] is not None]
    vbase = [dims["vr5"][k] for k in range(max(0, si - 5), si) if dims["vr5"][k] is not None]
    volmult = (sum(vleg) / len(vleg)) / (sum(vbase) / len(vbase)) if vleg and vbase and sum(vbase) > 0 else None
    return {"gain": round(gain, 4), "days": days,
            "daily": round(gain / days, 4),
            "front": None if front is None else round(front, 4),
            "maxpull": round(mp * 100.0, 4),
            "volmult": None if volmult is None else round(volmult, 4),
            "ret60s": dims["ret60"][ei],
            "pos250s": dims["pos250"][si], "align": dims["ma_align"][si],
            "band": dims["band"][si], "bias": dims["bias20"][si],
            "vr5": dims["vr5"][si], "vola": dims["vola20"][si],
            "pct20": dims["pct20"][si], "y": dims["date"][si] // 10000,
            "yend": dims["date"][ei] // 10000}


def leg_anatomy_D(c, dims, si, ei):
    """跌段解剖。返回dict或None(段过短)。"""
    days = ei - si
    if days < MIN_DAYS:
        return None
    fall = (c[ei] / c[si] - 1.0) * 100.0
    broke = False
    for k in range(si + 1, ei + 1):
        b = dims["bias20"][k]
        if b is not None and b < -1.0:
            broke = True
            break
    return {"fall": round(fall, 4), "days": days,
            "daily": round(fall / days, 4),
            "start_below": 1 if (dims["bias20"][si] is not None and dims["bias20"][si] < 0) else 0,
            "broke": 1 if broke else 0,
            "posend": dims["pos250"][ei],
            "y": dims["date"][si] // 10000}


def process_stock(code, rows, pct20map, ulist, dlist, acc):
    """rows=该股D层行(升序)。跑zigzag→解剖→入全局表。"""
    c = [r[1] for r in rows]
    dims = {"date": [r[0] for r in rows],
            "pos250": [r[2] for r in rows], "ma_align": [r[3] for r in rows],
            "bias20": [r[4] for r in rows], "vr5": [r[5] for r in rows],
            "vola20": [r[6] for r in rows], "ret60": [r[7] for r in rows],
            "band": [r[8] for r in rows],
            "pct20": [pct20map.get(r[0]) for r in rows]}
    legs = zigzag(c)
    prev_u = None
    for (kind, si, ei) in legs:
        if ei - si < MIN_DAYS:
            continue
        if kind == "U":
            a = leg_anatomy_U(c, dims, si, ei)
            if a:
                a["code"] = code
                ulist.append(a)
                prev_u = (si, ei)
        else:
            a = leg_anatomy_D(c, dims, si, ei)
            if a:
                a["code"] = code
                dlist.append(a)
                if prev_u is not None and si == prev_u[1]:
                    gspan = c[ei] - c[prev_u[0]]
                    if gspan != 0:
                        rt = (c[prev_u[1]] - c[ei]) / gspan
                        acc["rt"] += 1 if rt >= 0.8 else 0
                        acc["rt_n"] += 1
                prev_u = None


def agg_stats(items, key):
    xs = [x[key] for x in items if x.get(key) is not None]
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(xs), "mean": round(sum(xs) / len(xs), 4),
            "median": round(med(xs), 4),
            "p25": round(pctile(s, 25), 4), "p75": round(pctile(s, 75), 4)}


def dist_pp(big, all_, keyfn, buckets):
    """大涨段起点 vs 全体谷日 分桶占比与pp差。"""
    d1 = dist_bucket(big, keyfn, buckets)
    d0 = dist_bucket(all_, keyfn, buckets)
    out = {}
    for k in buckets:
        out[k] = {"big": d1.get(k, [0, 0.0])[1], "all": d0.get(k, [0, 0.0])[1],
                  "pp": round(d1.get(k, [0, 0.0])[1] - d0.get(k, [0, 0.0])[1], 2)}
    return out


def main():
    t0 = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace((' "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b' "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = (fp == prereg["fingerprint"])
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))
    con = sqlite3.connect("file:%s?mode=ro" % SRC_DB, uri=True)
    pct20map = {r[0]: r[1] for r in con.execute("SELECT date, pct_above20 FROM market_daily")}
    ulist = []
    dlist = []
    acc = {"rt": 0, "rt_n": 0}
    nstock = 0
    q = con.execute("SELECT code,date,close,pos250,ma_align,bias20,vr5,vola20,ret60,band "
                    "FROM stock_daily ORDER BY code,date")
    for code, grp in ((k, list(g)) for k, g in groupby(q, key=lambda r: r[0])):
        rows = [(int(d), c, p, al, b, v, vo, r60, bd)
                for (_, d, c, p, al, b, v, vo, r60, bd) in grp]
        nstock += 1
        process_stock(code, rows, pct20map, ulist, dlist, acc)
    con.close()
    print("拆段完成 %.0fs 股票%d 涨段%d 跌段%d" % (time.time() - t0, nstock, len(ulist), len(dlist)))
    big = [x for x in ulist if x["gain"] >= BIG]
    bigp = [x for x in big if x["pos250s"] is not None]
    allp = [x for x in ulist if x["pos250s"] is not None]

    def pb(x):
        v = x["pos250s"]
        if v < 20:
            return "p<20"
        if v < 40:
            return "p20_40"
        if v < 60:
            return "p40_60"
        if v < 80:
            return "p60_80"
        return "p80_100"

    def bb(x):
        v = x["band"]
        return "b0" if v == 0 else "b1" if v == 1 else "b2" if v == 2 else "b3" if v == 3 else "b4"

    def ab(x):
        v = x["align"]
        return "a%d" % v if v is not None else "aNA"

    def sb(x):
        v = x["pct20"]
        if v is None:
            return "wNA"
        if v < 30:
            return "弱<30"
        if v < 60:
            return "中30-60"
        return "强>60"

    def rb(x):
        v = x["ret60s"]
        if v is None:
            return "rNA"
        if v < -20:
            return "r<-20"
        if v < 0:
            return "r-20_0"
        if v < 30:
            return "r0_30"
        return "r>30"
    q1 = {"n_big": len(big), "n_big_with_pos": len(bigp), "n_all_troughs": len(ulist),
          "missing_pos250_big": len(big) - len(bigp),
          "pos250": dist_pp(bigp, allp, pb, ["p<20", "p20_40", "p40_60", "p60_80", "p80_100"]),
          "band": dist_pp(bigp, allp, bb, ["b0", "b1", "b2", "b3", "b4"]),
          "align": dist_pp(bigp, allp, ab, ["a0", "a1", "a2", "a3"]),
          "pct20": dist_pp(bigp, allp, sb, ["弱<30", "中30-60", "强>60"]),
          "ret60": dist_pp(bigp, allp, rb, ["r<-20", "r-20_0", "r0_30", "r>30"])}
    ymap2 = defaultdict(list)
    for x in ulist:
        ymap2[x["y"]].append(x["gain"])
    q2_years = {str(y): {"n": len(v), "mean_gain": round(sum(v) / len(v), 2)}
                for y, v in sorted(ymap2.items())}
    smap = defaultdict(list)
    for x in ulist:
        s = sb(x)
        if s != "wNA":
            smap[s].append(x["gain"])
    q2_state = {s: {"n": len(v), "mean_gain": round(sum(v) / len(v), 2)}
                for s, v in sorted(smap.items())}
    q2 = {"by_year": q2_years, "by_mkt_state": q2_state}
    q3_anat = {}
    for k in ("gain", "days", "daily", "front", "maxpull", "volmult"):
        q3_anat[k] = agg_stats(ulist, k)

    def arch(x):
        p = x["pos250s"]
        if p is not None and p < 30 and x["days"] >= 40:
            return "低位长坡"
        if p is not None and p >= 70 and x["days"] < 25:
            return "高位加速"
        if x["ret60s"] is not None and x["ret60s"] < -20 and x["days"] < 20:
            return "超跌反抽"
        return "稳步爬升"
    q3_types = {}
    for name in ("低位长坡", "高位加速", "超跌反抽", "稳步爬升"):
        xs = [x for x in ulist if arch(x) == name]
        q3_types[name] = {"n": len(xs),
                          "share_pct": round(len(xs) / len(ulist) * 100.0, 2) if ulist else 0,
                          "mean_gain": round(sum(x["gain"] for x in xs) / len(xs), 2) if xs else None,
                          "median_days": med([x["days"] for x in xs])}
    q3 = {"anatomy": q3_anat, "archetypes": q3_types}
    def2 = {}
    for k in ("fall", "days", "daily"):
        def2[k] = agg_stats(dlist, k)
    n_d = len(dlist)
    q4 = {"anatomy": def2,
          "share_start_below20_pct": round(sum(1 for x in dlist if x["start_below"] == 1) / n_d * 100.0, 2) if n_d else None,
          "share_broke20_pct": round(sum(1 for x in dlist if x["broke"] == 1) / n_d * 100.0, 2) if n_d else None,
          "roundtrip": {"n_pairs": acc["rt_n"], "rt_ge80": acc["rt"],
                        "share_pct": round(acc["rt"] / acc["rt_n"] * 100.0, 2) if acc["rt_n"] else None},
          "by_year": {}}
    ymap3 = defaultdict(list)
    for x in dlist:
        ymap3[x["y"]].append(x["fall"])
    for y, v in sorted(ymap3.items()):
        q4["by_year"][str(y)] = {"n": len(v), "mean_fall": round(sum(v) / len(v), 2)}
    out = {"prereg_fp": fp, "fp_ok": fp_ok, "n_stock": nstock,
           "n_up_legs": len(ulist), "n_dn_legs": len(dlist),
           "Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4,
           "samples_u": ulist[:2], "samples_d": dlist[:2]}
    path = OUT2 if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("Q1大涨段=%d(有pos250=%d) 全体谷日=%d" % (q1["n_big"], q1["n_big_with_pos"], q1["n_all_troughs"]))
    print("Q2年份分布:", {y: q2["by_year"][y]["n"] for y in q2["by_year"]})
    print("Q2市场状态:", {s: q2["by_mkt_state"][s]["n"] for s in q2["by_mkt_state"]})
    print("Q3涨段解剖: 涨幅中位=%s%% 天数中位=%s 前半动量占比中位=%s" % (
        q3["anatomy"]["gain"]["median"], q3["anatomy"]["days"]["median"],
        q3["anatomy"]["front"]["median"]))
    print("Q3原型:", {k: (v["n"], v["share_pct"]) for k, v in q3["archetypes"].items()})
    print("Q4跌段: 中位跌幅=%s%% 中位天数=%s 起点已在20线下占比=%s%% 回吐≥80%%占比=%s%%" % (
        q4["anatomy"]["fall"]["median"], q4["anatomy"]["days"]["median"],
        q4["share_start_below20_pct"], q4["roundtrip"]["share_pct"]))
    print("已写入", path, "%.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
