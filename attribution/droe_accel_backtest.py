r"""droe_accel_backtest.py — 月频反转镜像实测（PREREG_EARNINGS_ACCEL_V4，指纹1e99373cb52dc51b）。
E1=droe Top30主判据, E2=Top10/E3=Bottom30描述。因子=PIT droe(pit_avail_date<=月末)。主窗2016-2026(判据窗)。零交易权。用法: [run2]
"""
import bisect
import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
PREREG = r"D:\zcodesj\data\PREREG_EARNINGS_ACCEL_V4_20261114.json"
START_DATA = "20140101"
SPLIT = 20160000          # 主窗(样本内推导) vs 留出窗分界
SIG_END = "20261113"
COST = 0.003
EXCL = ("4", "8", "9", "68")
OUT1 = r"D:\zcodesj\reports\droe_accel_bt_20160101_20261113.json"
OUT2 = r"D:\zcodesj\reports\droe_accel_bt_20160101_20261113_run2.json"


def st_loader():
    con = sqlite3.connect("file:%s?mode=ro" % ST_DB, uri=True)
    cache = {}

    def state(code, date):
        if code not in cache:
            pfx = "sh" if code.startswith("6") else "sz"
            rows = con.execute("SELECT date,is_st FROM st_daily WHERE code=? ORDER BY date",
                               (pfx + "." + code,)).fetchall()
            cache[code] = rows
        rows = cache[code]
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] <= date:
                lo = mid + 1
            else:
                hi = mid
        return rows[lo - 1][1] if lo > 0 else 0
    return state


def load_index():
    m = sqlite3.connect("file:%s?mode=ro" % META_DB, uri=True)
    rows = m.execute("SELECT date,close FROM index_daily_meta WHERE code='000001' AND date>=? ORDER BY date",
                     (START_DATA,)).fetchall()
    m.close()
    dt = [int(r[0]) for r in rows]
    cl = [r[1] for r in rows]
    idx20 = {}
    for i in range(20, len(rows)):
        idx20[dt[i]] = cl[i] / cl[i - 20] - 1.0
    return idx20


def load_factor():
    pit = sqlite3.connect("file:%s?mode=ro" % PIT_DB, uri=True)
    f = defaultdict(list)
    for code, avail, droe in pit.execute(
            "SELECT code, pit_avail_date, droe FROM stock_factors_fundamental_pit_v2 "
            "WHERE droe IS NOT NULL AND pit_avail_date>=20140601 ORDER BY code, pit_avail_date"):
        f[code[-6:]].append((avail, droe))
    pit.close()
    return f


def next_month(ym):
    y, m = int(ym[:4]), int(ym[4:])
    m += 1
    if m > 12:
        y += 1
        m = 1
    return "%04d%02d" % (y, m)


def month_stats(month_ex, lo, hi):
    xs = [m["ex"] for m in month_ex if lo <= int(m["ym"] + "00") < hi]
    if len(xs) < 12:
        return {"months": len(xs)}
    mu = sum(xs) / len(xs)
    sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
    t = mu / (sd / len(xs) ** 0.5) if sd > 0 else None
    yrs = defaultdict(list)
    for m in month_ex:
        if lo <= int(m["ym"] + "00") < hi:
            yrs[m["ym"][:4]].append(m["ex"])
    yr_mean = {y: sum(v) / len(v) for y, v in yrs.items()}
    pos_years = sum(1 for v in yr_mean.values() if v > 0)
    return {"months": len(xs), "mean_month_excess": round(mu, 3),
            "annualized_excess": round(mu * 12, 2),
            "monthly_t": None if t is None else round(t, 2),
            "by_year_mean_excess": {y: round(v, 2) for y, v in sorted(yr_mean.items())},
            "pos_years": "%d/%d" % (pos_years, len(yr_mean))}


def main(run_id="run1"):
    t0 = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace(('  "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b'  "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = fp == prereg["fingerprint"]
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))

    factor = load_factor()
    is_st = st_loader()
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)

    # ---- Pass1: 全池基准 + 月末集 ----
    bench = defaultdict(lambda: [0.0, 0])
    cur = con.execute("SELECT code,date,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    ncode = 0
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        ncode += 1
        if code.startswith(EXCL):
            continue
        prev = None
        for _, d, c in grp:
            d = int(d)
            if c is None or prev is None or prev <= 0:
                prev = c if c is not None else prev
                continue
            if not is_st(code, d):
                bench[d][0] += c / prev - 1.0
                bench[d][1] += 1
            prev = c
    bd = sorted(bench)
    bcum, cc = [], 1.0
    for d in bd:
        s, n = bench[d]
        cc *= (1 + s / n if n else 0.0)
        bcum.append(cc)
    del bench
    bidx = {d: i for i, d in enumerate(bd)}
    me_set = set()
    bd_prev = None
    last_ym = None
    for d in bd:
        ym = str(d)[:6]
        if last_ym is not None and ym != last_ym:
            me_set.add(bd_prev)
        bd_prev = d
        last_ym = ym
    me_set.add(bd_prev)
    print("pass1完成 %.0fs 基准日%d 月末日%d" % (time.time() - t0, len(bd), len(me_set)))

    # ---- Pass2: 月末截面快照(含资格判定) ----
    snap = defaultdict(dict)
    cur = con.execute("SELECT code,date,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        frows = factor.get(code, [])
        fdates = [x[0] for x in frows]
        closes = [(int(d), c) for _, d, c in grp]
        for (d, c) in closes:
            if d not in me_set:
                continue
            if c is None or c <= 0:
                continue
            if not (2.0 <= c <= 30.0):
                continue
            if d >= SPLIT and is_st(code, d):
                continue
            j = bisect.bisect_right(fdates, d) - 1
            if j < 0:
                continue
            snap[d][code] = frows[j][1]
    print("pass2完成 %.0fs 截面月数%d" % (time.time() - t0, len(snap)))

    # ---- 成员与月收益(三臂) ----
    me_dates = sorted(snap)
    arms = {"E1": (30, True), "E2": (10, True), "E3": (30, False)}
    results = {}
    for arm, (topn, desc) in arms.items():
        member_months = {}
        prev_set = set()
        costs = []
        for D in me_dates:
            cand = snap[D]
            if len(cand)< topn:
                continue
            ranked = sorted(cand.items(), key=lambda kv: kv[1], reverse=desc)
            sel = {k for k, _ in ranked[:topn]}
            ym_next = next_month(str(D)[:6])
            member_months[ym_next] = sel
            chg = len(sel - prev_set) + len(prev_set - sel)
            costs.append((ym_next, chg / float(topn)))
            prev_set = sel
        mret = defaultdict(lambda: [0.0, 0])
        for code in set().union(*member_months.values()) if member_months else set():
            rows_ = con.execute("SELECT date,close FROM stock_daily_meta_qfq "
                                "WHERE code=? AND date>=? AND date<=? ORDER BY date",
                                (code, START_DATA, SIG_END)).fetchall()
            yms = [ym for ym, s in member_months.items() if code in s]
            prev = None
            for d, c in rows_:
                d = int(d)
                ym = str(d)[:6]
                if c is not None and prev is not None and prev > 0 and ym in member_months and code in member_months[ym]:
                    mret[(ym, d)][0] += c / prev - 1.0
                    mret[(ym, d)][1] += 1
                prev = c
        month_ex = []
        for ym in sorted(member_months):
            dates = sorted(k[1] for k in mret if k[0] == ym)
            if not dates:
                continue
            pr = 1.0
            for d in dates:
                s, c2 = mret[(ym, d)]
                if c2:
                    pr *= (1 + s / c2)
            cost = next((cf for cy, cf in costs if cy == ym), 0.0)
            pr *= (1 - COST * cost)
            i0 = bisect.bisect_left(bd, int(ym + "00"))
            i1 = bisect.bisect_left(bd, int(next_month(ym) + "00"))
            if i0 >= len(bd) or i1 <= i0:
                continue
            b = bcum[i1 - 1] / bcum[max(i0 - 1, 0)] - 1.0
            month_ex.append({"ym": ym, "ex": (pr - 1.0 - b) * 100.0, "pr": (pr - 1.0) * 100.0})
        holdout = month_stats(month_ex, 20100000, SPLIT)
        primary = month_stats(month_ex, SPLIT, 20990000)
        if primary.get("months", 0) >= 12 and primary["monthly_t"] is not None:
            pos_n, pos_tot = primary["pos_years"].split("/")
            verdict = ("PASS" if (primary["annualized_excess"] > 0 and primary["monthly_t"] >= 2.0
                                  and int(pos_n) / int(pos_tot) >= 0.5) else "FAIL")
        else:
            verdict = "INSUFFICIENT"
        results[arm] = {"holdout_2010_2015": holdout, "primary_2016_2026": primary, "verdict": verdict}
        print("%s: 主窗月均超额=%s 年化=%s t=%s 正年=%s | 留出窗%s | 判定=%s" % (
            arm, primary.get("mean_month_excess"), primary.get("annualized_excess"),
            primary.get("monthly_t"), primary.get("pos_years"), holdout.get("mean_month_excess"), verdict))
    con.close()
    out = {"prereg_fp": fp, "fp_ok": fp_ok, "run_id": run_id,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "window": [START_DATA, SIG_END], "split": SPLIT, "arms": results}
    path = OUT2 if run_id == "run2" else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", path)


if __name__ == "__main__":
    rid = sys.argv[1] if len(sys.argv) > 1 else "run1"
    main(rid)
