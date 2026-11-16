# -*- coding: utf-8 -*-
import sqlite3, sys
from collections import defaultdict
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
FUND = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
ICONDB = "file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
T = 20261106


def main():
    con = sqlite3.connect(QFQ, uri=True)
    rows = con.execute(
        "select code,date,open,close,volume,amount,change_pct from stock_daily_meta_qfq "
        "where date>=20260701 order by code,date").fetchall()
    con.close()
    g = defaultdict(lambda: {"d": [], "o": [], "c": [], "v": [], "a": [], "p": []})
    for code, d, o, c, v, amt, p in rows:
        s = g[code]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["c"].append(float(c or 0))
        s["v"].append(float(v or 0)); s["a"].append(float(amt or 0))
        s["p"].append(float(p or 0) if p is not None else 0.0)
    icon = sqlite3.connect(ICONDB, uri=True)
    nind = {str(k)[-6:]: (nm, l1) for k, nm, l1 in icon.execute("select code,name,l1 from l1_member").fetchall()}
    icon.close()
    scon = sqlite3.connect(STDB, uri=True)
    st_set = {str(c)[-6:] for c, f in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)"
    ).fetchall() if f == 1}
    scon.close()
    ups = []
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        if T not in s["d"]:
            continue
        i = s["d"].index(T)
        if i < 25 or s["c"][i] <= 0:
            continue
        r = s["p"][i] / 100.0
        if r < 0.05:
            continue
        ma20 = float(np.mean(s["c"][i - 20:i]))
        dev = s["c"][i] / ma20 - 1.0 if ma20 > 0 else 0
        r20 = s["c"][i - 1] / s["c"][i - 21] - 1.0 if s["c"][i - 21] > 0 else 0
        vseg = [x for x in s["v"][i - 20:i] if x > 0]
        volr = s["v"][i] / float(np.mean(vseg)) if vseg and s["v"][i] > 0 else 0
        lim = 0.19 if c6[:3] in ("300", "301") else 0.09
        sealed = r >= lim
        prev_lim = (i >= 2 and s["p"][i - 1] / 100.0 >= lim)
        nm, l1 = nind.get(c6, ("?", "?"))
        ups.append({"code": c6, "name": nm, "ind": l1, "px": s["c"][i], "r": r,
                    "sealed": sealed, "board2": sealed and prev_lim,
                    "dev": dev, "r20": r20, "volr": volr, "amt": s["a"][i]})
    print("大涨池(涨>=5%%): %d只 | 其中封板%d 连板%d" % (
        len(ups), sum(1 for u in ups if u["sealed"]), sum(1 for u in ups if u["board2"])))
    indc = defaultdict(int)
    for u in ups:
        indc[u["ind"]] += 1
    top_ind = sorted(indc.items(), key=lambda x: -x[1])[:6]
    print("行业分布:", ", ".join("%s%d" % (k, v) for k, v in top_ind))
    med = lambda key: float(np.median([u[key] for u in ups]))
    print("画像中位: 价%.1f元 偏离MA20=%+.1f%% r20=%+.1f%% 量比%.2f 成交%.1f亿" % (
        med("px"), med("dev") * 100, med("r20") * 100, med("volr"), med("amt") / 1e8))
    # price bands
    pb = defaultdict(int)
    for u in ups:
        pb["10元下" if u["px"] < 10 else "10-20" if u["px"] < 20 else "20-30" if u["px"] < 30 else "30+"] += 1
    print("价格带:", dict(sorted(pb.items())))
    # why-up evidence: LHB net buy + recent forecast
    fcon = sqlite3.connect(FUND, uri=True)
    lhb = defaultdict(float)
    for ts, net in fcon.execute(
            "select ts_code, SUM(net_amount) from dragon_tiger_new where trade_date=? group by ts_code", (str(T),)):
        lhb[ts[:6]] = net
    fc_hits = defaultdict(list)
    for ts, ad, typ in fcon.execute(
            "select ts_code, ann_date, type from forecast where ann_date>=20261031 and type in "
            "('预增','扭亏','略增','预减','首亏','略减')"):
        fc_hits[ts[:6]].append((int(ad), typ))
    fcon.close()
    lhb_up = [u for u in ups if u["code"] in lhb and lhb[u["code"]] > 0]
    lhb_dn = [u for u in ups if u["code"] in lhb and lhb[u["code"]] < 0]
    fc_up = [u for u in ups if any(t in ("预增", "扭亏", "略增") for _d, t in fc_hits.get(u["code"], []))]
    fc_dn = [u for u in ups if any(t in ("预减", "首亏", "略减") for _d, t in fc_hits.get(u["code"], []))]
    print("为什么涨(证据面): 龙虎榜净买%d只 净卖%d只 | 近7日正面预告%d只 负面预告%d只" % (
        len(lhb_up), len(lhb_dn), len(fc_up), len(fc_dn)))
    print("  龙虎榜净买前6:", ", ".join("%s(%.0f万)" % (u["name"], lhb[u["code"]] / 1e4) for u in
          sorted(lhb_up, key=lambda x: -lhb[x["code"]])[:6]))
    print("  正面预告前6:", ", ".join(u["name"] for u in fc_up[:6]))
    # non-sealed gainers (5-9%) = non-limit-up gainers
    ns = [u for u in ups if not u["sealed"]]
    print("未封板大涨(涨5%%~涨停线下): %d只 | 封板组量比中位%.2f vs 未封板%.2f" % (
        len(ns), med("volr") if not ns else float(np.median([u["volr"] for u in ups if u["sealed"]])),
        float(np.median([u["volr"] for u in ns])) if ns else 0))
    print("  未封板大涨代表10只:", ", ".join("%s%+.1f%%" % (u["name"], u["r"] * 100) for u in
          sorted(ns, key=lambda x: -x["r"])[:10]))
    out = {"T": T, "n": len(ups), "ups": sorted(ups, key=lambda x: -x["r"])}
    import json, os
    json.dump(out, open(os.path.join(r"D:\zcodesj\data", "up_profile_20261106.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("json: data/up_profile_20261106.json")


if __name__ == "__main__":
    main()
