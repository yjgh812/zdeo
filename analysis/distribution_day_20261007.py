# -*- coding: utf-8 -*-
"""趋势波段补遗: 欧奈尔派发日聚簇(distribution day cluster)验证 — 2026-10-07 研究侧零接线
欧奈尔卖出侧市场择时: 数周内3-5个放量大跌日(distribution day)=市场转弱应减仓。
FTD(§八,右侧买入确认,四门全过)的对称卖出侧信号。

判据跑前冻结:
  派发日 = 上证单日跌幅<=-2.0% 且 当日成交量>前一日
  聚簇事件 = 滚动20交易日内派发日计数>=3 的首个达成日(欧奈尔"数周内3-5个"A股紧凑化)
  冷却 = 上一聚簇事件后20交易日不记新
  fwd20/60; 对照臂=全体派发日(非聚簇内当日); 分年+2015后
  判定: fwd20中位<0且win<45=卖出有效(SELL_VALID); >0=卖飞(SELL_FLY); 中间=无增量
先例参照: 跌停潮42轮(fwd+1.03%强于基线=跌停潮后不悲观)/G3空间板跌停/破线卖飞——
  A股「大跌事件后不悲观」已三次复现, 本验证检验放量大跌日聚簇是否例外。
"""
import json
import struct
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
OUT = Path(r"D:\zcodesj\tmp\distribution_day_result_20261007.json")
DD_TH = -2.0
CLUSTER_N, CLUSTER_WIN, COOLDOWN = 3, 20, 20
FWD = [5, 10, 20, 60]


def med_win(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {"n": len(vals), "med": round(s[len(s) // 2], 2),
            "win": round(sum(1 for v in vals if v > 0) * 100.0 / len(vals), 1)}


def main():
    t0 = time.time()
    rows = []
    with open(VIPDOC_DAY, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _r = struct.unpack("<IIIIIfII", b)
            rows.append((str(d), c / 100.0, vol))
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    vols = [r[2] for r in rows]
    n = len(rows)
    rets = [0.0] + [closes[i] / closes[i - 1] - 1 for i in range(1, n)]

    dd_days = [i for i in range(1, n) if rets[i] * 100.0 <= DD_TH and vols[i] > vols[i - 1]]
    print(f"distribution days (跌<=-2%且放量): {len(dd_days)}")

    clusters = []
    last_c = -999
    for i in dd_days:
        if i - last_c <= COOLDOWN:
            continue
        cnt = sum(1 for j in dd_days if i - CLUSTER_WIN < j <= i)
        if cnt >= CLUSTER_N:
            clusters.append(i)
            last_c = i
    print(f"cluster events (20日内>=3个派发日): {len(clusters)}")

    def enrich(idxs):
        out = []
        for i in idxs:
            r = {"date": dates[i]}
            for h in FWD:
                j = i + h
                r[f"fwd{h}"] = round((closes[j] / closes[i] - 1) * 100.0, 2) if j < n else None
            out.append(r)
        return out

    ev_cl, ev_all = enrich(clusters), enrich(dd_days)
    s_cl = {f"fwd{h}": med_win([e[f"fwd{h}"] for e in ev_cl]) for h in FWD}
    s_all = {f"fwd{h}": med_win([e[f"fwd{h}"] for e in ev_all]) for h in FWD}
    # 非聚簇内孤立派发日对照
    in_cluster = set()
    for i in clusters:
        for j in dd_days:
            if i - CLUSTER_WIN < j <= i:
                in_cluster.add(j)
    lone = [i for i in dd_days if i not in in_cluster]
    s_lone = {f"fwd{h}": med_win([e[f"fwd{h}"] for e in enrich(lone)]) for h in FWD}

    by = {}
    for e in ev_cl:
        if e.get("fwd20") is not None:
            by.setdefault(e["date"][:4], []).append(e["fwd20"])
    yearly = {y: med_win(v) for y, v in sorted(by.items())}
    recent = [e for e in ev_cl if e["date"] >= "20150101"]
    s_recent = med_win([e["fwd20"] for e in recent])

    s20 = s_cl["fwd20"]
    gate = ("SELL_VALID" if (s20["n"] >= 20 and s20["med"] < 0 and s20["win"] < 45)
            else ("SELL_FLY" if s20["med"] > 0 else "NO_EDGE"))

    print(f"[CLUSTER] n={s20['n']} fwd20 med={s20['med']} win={s20['win']} fwd60 med={s_cl['fwd60']['med']}")
    print(f"[ALL-DD ] n={s_all['fwd20']['n']} fwd20 med={s_all['fwd20']['med']} win={s_all['fwd20']['win']}")
    print(f"[LONE   ] n={s_lone['fwd20']['n']} fwd20 med={s_lone['fwd20']['med']} win={s_lone['fwd20']['win']}")
    print(f"[2015后 ] n={s_recent['n']} med={s_recent['med']} win={s_recent['win']}")
    print(f"[GATE] {gate}")

    result = {"meta": {"script": "analysis/distribution_day_20261007.py",
                       "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "locked": {"dd_th": DD_TH, "cluster": [CLUSTER_N, CLUSTER_WIN], "cooldown": COOLDOWN}},
              "dd_days_total": len(dd_days), "clusters": len(clusters),
              "cluster_stats": s_cl, "all_dd_stats": s_all, "lone_stats": s_lone,
              "recent_2015": s_recent, "yearly_fwd20": yearly, "gate": gate,
              "cluster_dates": [e["date"] for e in ev_cl],
              "worst5": sorted([e for e in ev_cl if e.get("fwd20") is not None], key=lambda x: x["fwd20"])[:5]}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved -> {OUT} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
