r"""reversal_weekly_top_backtest.py — 周频反转实测（PREREG_REVERSAL_WEEKLY_V5，指纹958f50c70e0c6660）。
T1=Top30主判据, T2=Top10/T3=Top50描述(降序=最强者)。每5个交易日按5日超额大盘涨幅升序取最弱者持下5日。零交易权。用法: [run2]
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
PREREG = r"D:\zcodesj\data\PREREG_REVERSAL_WEEKLY_TOP_V6_20261114.json"
START_DATA, SIG_END = "20100101", "20261113"
SPLIT = 20160000
COST = 0.003
PERIOD = 5
EXCL = ("4", "8", "9", "68")
OUT1 = r"D:\zcodesj\reports\reversal_weekly_top_bt_20100101_20261113.json"
OUT2 = r"D:\zcodesj\reports\reversal_weekly_top_bt_20100101_20261113_run2.json"


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


def next_k(d, bd):
    i = bisect.bisect_left(bd, d)
    return i


def main(run_id="run1"):
    t0 = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace(('  "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b'  "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = fp == prereg["fingerprint"]
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))

    m = sqlite3.connect("file:%s?mode=ro" % META_DB, uri=True)
    irows = m.execute("SELECT date,close FROM index_daily_meta WHERE code='000001' AND date>=? ORDER BY date",
                      (START_DATA,)).fetchall()
    m.close()
    idt = [int(r[0]) for r in irows]
    icl = [r[1] for r in irows]
    idx5 = {}
    for i in range(5, len(idt)):
        idx5[idt[i]] = icl[i] / icl[i - 5] - 1.0

    is_st = st_loader()
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)

    bench = defaultdict(lambda: [0.0, 0])
    cur = con.execute("SELECT code,date,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
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
    sel_idx = list(range(24, len(bd) - PERIOD, PERIOD))
    sel_set = set(sel_idx)
    sel_dates = [bd[i] for i in sel_idx]
    period_end_date = {}
    for k, si in enumerate(sel_idx):
        period_end_date[k] = bd[si + PERIOD]
    print("pass1完成 %.0fs 基准日%d 周期数%d" % (time.time() - t0, len(bd), len(sel_idx)))

    snap = defaultdict(dict)
    cur = con.execute("SELECT code,date,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        closes = [(int(d), c) for _, d, c in grp]
        for i in range(5, len(closes)):
            d, c = closes[i]
            if d not in bidx:
                continue
            bi = bidx[d]
            if bi not in sel_set:
                continue
            if c is None or c <= 0:
                continue
            if not (2.0 <= c <= 30.0):
                continue
            if d >= SPLIT and is_st(code, d):
                continue
            p5 = closes[i - 5][1]
            ir = idx5.get(d)
            if p5 is None or p5 <= 0 or ir is None:
                continue
            snap[d][code] = c / p5 - 1.0 - ir
    print("pass2完成 %.0fs 截面周期数%d" % (time.time() - t0, len(snap)))

    arms = {"T1": (30, False), "T2": (10, False), "T3": (50, False)}
    results = {}
    for arm, (topn, _desc) in arms.items():
        member_sets = {}
        prev_set = set()
        costs = []
        for k, D in enumerate(sel_dates):
            cand = snap.get(D, {})
            if len(cand) < topn:
                continue
            ranked = sorted(cand.items(), key=lambda kv: kv[1], reverse=True)  # 降序=最强者
            sel = {kk for kk, _ in ranked[:topn]}
            member_sets[k] = sel
            chg = len(sel - prev_set) + len(prev_set - sel)
            costs.append((k, chg / float(topn)))
            prev_set = sel
        per_code = defaultdict(list)
        for k, sel in member_sets.items():
            for code in sel:
                per_code[code].append(k)
        psum = defaultdict(lambda: [0.0, 0])
        for code, ks in per_code.items():
            rows_ = con.execute("SELECT date,close FROM stock_daily_meta_qfq "
                                "WHERE code=? AND date>=? AND date<=? ORDER BY date",
                                (code, START_DATA, SIG_END)).fetchall()
            prev = None
            for d, c in rows_:
                d = int(d)
                if c is None or prev is None or prev <= 0:
                    prev = c if c is not None else prev
                    continue
                r = c / prev - 1.0
                bi = bidx.get(d)
                if bi is None:
                    prev = c
                    continue
                ksel = bisect.bisect_right(sel_dates, d) - 1
                if ksel < 0:
                    prev = c
                    continue
                kset = member_sets.get(ksel)
                if kset is not None and code in kset and sel_idx[ksel] < bi <= sel_idx[ksel] + PERIOD:
                    psum[(arm, ksel, d)][0] += r
                    psum[(arm, ksel, d)][1] += 1
                prev = c
        period_ex = []
        for k, D in enumerate(sel_dates):
            dates = [bd[j] for j in range(sel_idx[k] + 1, min(sel_idx[k] + 1 + PERIOD, len(bd)))]
            pr = 1.0
            ok = False
            for d in dates:
                s, c2 = psum.get((arm, k, d), [0.0, 0])
                if c2:
                    ok = True
                    pr *= (1 + s / c2)
            if not ok:
                continue
            cost = next((cf for ck, cf in costs if ck == k), 0.0)
            pr *= (1 - COST * cost)
            j1 = min(sel_idx[k] + PERIOD, len(bd) - 1)
            b = bcum[j1] / bcum[sel_idx[k]] - 1.0
            period_ex.append({"pd": D, "ex": (pr - 1.0 - b) * 100.0})
        stats = win_stats(period_ex)
        results[arm] = stats
        print("%s: 主窗周均=%s 年化=%s t=%s 正年=%s | 留出窗周均=%s | 判定=%s" % (
            arm, stats["primary"].get("mean_p"), stats["primary"].get("annualized"),
            stats["primary"].get("t"), stats["primary"].get("pos_years"), stats["holdout"].get("mean_p"),
            stats["primary"].get("verdict")))
    con.close()
    out = {"prereg_fp": fp, "fp_ok": fp_ok, "run_id": run_id,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "window": [START_DATA, SIG_END], "split": SPLIT, "period": PERIOD, "arms": results}
    path = OUT2 if run_id == "run2" else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", path)


def win_stats(period_ex):
    def seg(lo, hi):
        xs = [m["ex"] for m in period_ex if lo <= m["pd"] < hi]
        if len(xs) < 26:
            return {"n": len(xs)}
        mu = sum(xs) / len(xs)
        sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
        t = mu / (sd / len(xs) ** 0.5) if sd > 0 else None
        yrs = defaultdict(list)
        for m in period_ex:
            if lo <= m["pd"] < hi:
                yrs[str(m["pd"])[:4]].append(m["ex"])
        yr_mean = {y: sum(v) / len(v) for y, v in yrs.items()}
        pos = sum(1 for v in yr_mean.values() if v > 0)
        return {"n": len(xs), "mean_p": round(mu, 3), "annualized": round(mu * 52, 2),
                "t": None if t is None else round(t, 2),
                "pos_years": "%d/%d" % (pos, len(yr_mean)),
                "verdict": ("PASS" if (mu > 0 and t is not None and t >= 2.0
                                       and pos / len(yr_mean) >= 0.5) else "FAIL")}
    return {"primary": seg(20160000, 20990000), "holdout": seg(20100000, 20160000)}


if __name__ == "__main__":
    rid = sys.argv[1] if len(sys.argv) > 1 else "run1"
    main(rid)
