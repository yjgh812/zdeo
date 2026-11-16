# -*- coding: utf-8 -*-
"""
W5 退潮事件族描述研究 (B站学习第42轮, 2026-10-06)

立项依据(prereg_preflight 20261006 CONFLICT已论证): 命中SENTIMENT-CYCLE证伪族(日频择时)/
G3(空间板跌停后市场走强,n=46个股级)/PD-034(恐慌事件买入时机)。差异:
①跌停潮=市场级广度事件(跌停家数激增), G3=个股级单票事件——不同层, G3结论作本研究对照组
(若跌停潮后效也走强=两层级同构; 若走弱=个股级与市场级分层有效, 广度才是风险触发器——
正是第25轮G3结论"regime用广度指标非单票事件"的跌停维度补证);
②PD-034部分=其26事件内部按次日反弹质量分层的家族注脚(第40轮W3遗留), 非新信号。
全部描述性fwd统计, 不立择时规则, 零交易权。v2台账只读。

判据 LOCKED (写死于看结果前):
- 数据: qfq库全市场日线, 起点19970101(1996-12-16涨跌停制度前跌停家数无意义, 写死)
- 涨停/跌停: 主板chg>=9.8/<=-9.8, 创业板(300/301)>=19.8/<=-19.8, 剔688/北交所/每股首日
- 跌停潮事件: mkt_dt >= max(25, 2*mean(mkt_dt[T-5:T])), 事件间隔>=5日去重
- fwd: 当日收盘起市场等权f1/f5/f20(等权代理, 无成本, 描述层)
- 分年段: 1997-2014/2015-2026; 相位交叉: 2025-2026子集挂v2台账相位
- PD-034分层: 26事件按ret1_pct>=0(有反弹)/<0(续弱)两组, fwd5/fwd20对比
"""
import json
import os
import sqlite3
from collections import defaultdict

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = r"D:\zcodesj\data\emotion_phase_v2_ledger.jsonl"
PD034 = r"D:\zcodesj\data\panic_overshoot_shadow.jsonl"
OUT = r"D:\zcodesj\tmp\ebbing_event_family_20261006.json"

START = "19970101"
DT_ABS, DT_MULT, GAP = 25, 2.0, 5


def med_win(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0}
    n = len(xs)
    med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    return {"n": n, "med": round(med, 3), "mean": round(sum(xs) / n, 3),
            "win": round(100 * sum(1 for x in xs if x > 0) / n, 1)}


def skip_code(c):
    return c.startswith(("688", "689", "8", "4", "920"))


def is_gem(c):
    return c.startswith(("300", "301"))


def main():
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date", (START,))]
    didx = {d: i for i, d in enumerate(dates)}
    nd = len(dates)
    first_day = dict(conn.execute("SELECT code, MIN(date) FROM stock_daily_meta_qfq GROUP BY code"))
    mkt_zt = [0] * nd
    mkt_dt = [0] * nd
    day_sum = [0.0] * nd
    day_cnt = [0] * nd
    cur = conn.execute("SELECT code, date, change_pct FROM stock_daily_meta_qfq WHERE date>=? AND change_pct IS NOT NULL", (START,))
    for code, d, chg in cur:
        if skip_code(code) or first_day.get(code) == d:
            continue
        i = didx[d]
        th = 19.8 if is_gem(code) else 9.8
        if chg >= th:
            mkt_zt[i] += 1
        elif chg <= -th:
            mkt_dt[i] += 1
        day_sum[i] += chg
        day_cnt[i] += 1
    conn.close()
    # 逐日等权chg前缀和(百分数): ew[i]=sum(day_ew[0..i])
    ew = [0.0] * (nd + 1)
    for i in range(nd):
        ew[i + 1] = ew[i] + (day_sum[i] / day_cnt[i] if day_cnt[i] else 0.0)
    print(f"dates={nd} {dates[0]}..{dates[-1]}")

    def fwd(i, k):
        return ew[i + 1 + k] - ew[i + 1] if i + 1 + k <= nd else None

    # 跌停潮事件
    events = []
    last = -99
    for i in range(5, nd):
        base = sum(mkt_dt[i - 5:i]) / 5.0
        if mkt_dt[i] >= max(DT_ABS, DT_MULT * max(base, 1.0)) and i - last >= GAP:
            events.append(i)
            last = i
    print(f"dt-surge events={len(events)}")

    ev_rows, by_segment = [], defaultdict(lambda: defaultdict(list))
    for i in events:
        rec = {"date": dates[i], "dt": mkt_dt[i], "zt": mkt_zt[i],
               "f1": fwd(i, 1), "f5": fwd(i, 5), "f20": fwd(i, 20)}
        ev_rows.append(rec)
        seg = "1997-2014" if dates[i] <= "20141231" else "2015-2026"
        for k in ("f1", "f5", "f20"):
            by_segment[seg][k].append(rec[k])

    result = {
        "meta": {"locked": {"start": START, "dt_abs": DT_ABS, "dt_mult": DT_MULT, "gap": GAP,
                             "note": "等权代理无成本描述层; 1996-12-16涨跌停制度前不纳入"},
                  "dates": [dates[0], dates[-1]], "n": nd,
                  "baseline_f": {"f1": med_win([fwd(i, 1) for i in range(nd - 20)]),
                                  "f5": med_win([fwd(i, 5) for i in range(nd - 20)]),
                                  "f20": med_win([fwd(i, 20) for i in range(nd - 20)])}},
        "dt_surge_all": {k: med_win([r[k] for r in ev_rows]) for k in ("f1", "f5", "f20")},
        "dt_surge_by_segment": {s: {k: med_win(v) for k, v in d.items()} for s, d in by_segment.items()},
        "top10_worst": sorted(ev_rows, key=lambda r: r["f20"] if r["f20"] is not None else 0)[:10],
    }

    # 相位交叉(2025-2026子集)
    rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
    phase_of = {}
    for r in rows:
        phase_of.setdefault(r["phase_date"], r["phase"])
    sub = defaultdict(lambda: defaultdict(list))
    for r in ev_rows:
        if r["date"] >= "20250101" and r["date"] in phase_of:
            sub[phase_of[r["date"]]]["f5"].append(r["f5"])
            sub[phase_of[r["date"]]]["f20"].append(r["f20"])
    result["dt_surge_by_phase_2025_26"] = {p: {k: med_win(v) for k, v in d.items()} for p, d in sub.items()}

    # PD-034 26事件按次日反弹质量分层
    pd_rows = [json.loads(l) for l in open(PD034, encoding="utf-8") if l.strip()]
    g_reb, g_weak = [], []
    for r in pd_rows:
        r1 = r.get("ret1_pct")
        try:
            r1 = float(r1)
        except (TypeError, ValueError):
            continue
        (g_reb if r1 >= 0 else g_weak).append(r)
    def grp(rows_, key):
        return med_win([float(r[key]) for r in rows_ if r.get(key) not in (None, "None", "")])
    result["pd034_rebound_split"] = {
        "n_total": len(pd_rows), "n_rebound": len(g_reb), "n_weak": len(g_weak),
        "rebound_ret1>=0": {"f5": grp(g_reb, "fwd5_pct"), "f20": grp(g_reb, "fwd20_pct")},
        "weak_ret1<0": {"f5": grp(g_weak, "fwd5_pct"), "f20": grp(g_weak, "fwd20_pct")},
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"result": result, "events": ev_rows}, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
