r"""trendbreak_validate_event_study.py — 走坏判定全量验证（PREREG_TRENDBREAK_VALIDATE_V1，指纹337f0b40d77615df）。

检验系统现行走坏判定+A/B卖出的实盘正确性（超额口径, 相对同期全池等权基准）:
T1 卖出正确性: 已执行卖出的 ab20(卖出后20日个股−基准) <0=避损正确 / >0=相对卖飞。
T2 两日确认过滤: D1候选按次日bear分confirmed/rejected, rejected−confirmed差为正=过滤剔除假破正确。
T3 A/B分流: A/B已执行ab20分报; 收回事件确认日收盘起20日超额>0=等待收回设计正确。
T4 留出窗仅报告; T5 分年明细(含2018/2022熊年对照)。
事件定义与TRENDBREAK_VOLUME_V1逐字同源(量比不参与)。零交易权。
用法: python -I trendbreak_validate_event_study.py [run2]
判定逻辑严格按预注册冻结定义实现, 禁止事后调参。
"""
import bisect
import hashlib
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict, deque
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
PREREG = r"D:\zcodesj\data\PREREG_TRENDBREAK_VALIDATE_V1_20261116.json"
START_DATA, SIG_END = "20100101", "20261113"
SPLIT = 20160000
EXCL = ("4", "8", "9", "68")
DEPTH = 0.01
GAP_A = -0.03
SLOPE_A = -0.05
FWD = 20
MIN_BARS = 60
CROSS_WIN = 10
OUT1 = r"D:\zcodesj\reports\TRENDBREAK_VALIDATE_EVENT_STUDY_20261116.json"
OUT2 = OUT1.replace(".json", "_run2.json")


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


def ma_series(closes, k):
    out = [None] * len(closes)
    buf = deque()
    s = 0.0
    nbad = 0
    for i, c in enumerate(closes):
        bad = c is None or c <= 0
        buf.append(bad)
        if bad:
            nbad += 1
        else:
            s += c
        if len(buf) > k:
            if buf.popleft():
                nbad -= 1
            else:
                s -= closes[i - k]
        if i >= k - 1 and nbad == 0:
            out[i] = s / k
    return out


def bench_lookup(bd, bcum, d1, d2):
    """同窗基准收益: d1→d2(闭开) 全池等权累乘比例; 日期越界返回None。"""
    k1 = bisect.bisect_right(bd, d1) - 1
    k2 = bisect.bisect_right(bd, d2) - 1
    if k1 < 0 or k2 < 0 or k2 < k1:
        return None
    return bcum[k2] / bcum[k1] - 1.0


def run_stock(code, bars, is_st, bd, bcum, ev_exec, ev_d1, ev_rec):
    """单股事件扫描, 返回计数。"""
    n = len(bars)
    cnt = {"n": 0, "recovered": 0, "exec_missing": 0, "trunc_no_fwd": 0,
           "truncated": 0, "st_excluded": 0, "bench_missing": 0,
           "d1_confirmed": 0, "d1_rejected": 0, "d1_boundary": 0,
           "d1_st_excluded": 0, "d1_trunc_no_fwd": 0, "d1_bench_missing": 0}
    if n < MIN_BARS:
        return cnt
    dates = [b[0] for b in bars]
    opens = [b[2] for b in bars]
    closes = [b[3] for b in bars]
    ma5 = ma_series(closes, 5)
    ma10 = ma_series(closes, 10)
    ma20 = ma_series(closes, 20)
    bear = [False] * n
    for i in range(n):
        c = closes[i]
        m5, m10, m20v = ma5[i], ma10[i], ma20[i]
        if c is None or c <= 0 or m20v is None or m5 is None or m10 is None:
            continue
        depth = (m20v - c) / m20v
        if c < m20v and m5 < m10 < m20v and depth >= DEPTH:
            bear[i] = True
    suppressed = False
    events_local = []
    d1_local = []
    for i in range(1, n):
        if suppressed:
            m_r = ma20[i]
            c_r = closes[i]
            if m_r is not None and c_r is not None and c_r > 0 and c_r >= m_r:
                suppressed = False
            continue
        if i >= MIN_BARS and bear[i] and bear[i - 1]:
            events_local.append(i)
            suppressed = True
        elif i >= MIN_BARS and bear[i] and not bear[i - 1]:
            if i + 1 < n and bear[i + 1]:
                d1_local.append((i, "C"))
            elif i + 1 < n:
                d1_local.append((i, "R"))
            else:
                d1_local.append((i, "X"))
    for c in events_local:
        if is_st(code, dates[c]):
            cnt["st_excluded"] += 1
            continue
        cnt["n"] += 1
        m20c = ma20[c]
        gap = closes[c] / m20c - 1.0
        m20s = ma20[c - 5]
        slope5 = None if m20s is None else (m20c - m20s) / m20s
        is_a = (gap <= GAP_A) or (slope5 is not None and slope5 <= SLOPE_A)
        grp = "A" if is_a else "B"
        if is_a:
            X = c + 1
        else:
            if c + 1 >= n:
                cnt["exec_missing"] += 1
                continue
            m_n = ma20[c + 1]
            c_n = closes[c + 1]
            if m_n is not None and c_n is not None and c_n > 0 and c_n >= m_n:
                cnt["recovered"] += 1
                rec_j = min(c + FWD, n - 1)
                rec_k = rec_j
                while rec_k > c and (closes[rec_k] is None or closes[rec_k] <= 0):
                    rec_k -= 1
                if rec_k <= c:
                    cnt["trunc_no_fwd"] += 1
                else:
                    rb = bench_lookup(bd, bcum, dates[c], dates[rec_k])
                    if rb is None:
                        cnt["bench_missing"] += 1
                    else:
                        ev_rec.append({"code": code, "anchor": dates[c],
                                       "ab20_pp": round((closes[rec_k] / closes[c] - 1.0 - rb) * 100.0, 4),
                                       "truncated": rec_j != c + FWD})
                continue
            X = c + 2
        if X >= n:
            cnt["exec_missing"] += 1
            continue
        o_x = opens[X]
        c_x = closes[X]
        if o_x is not None and o_x > 0:
            px = o_x
        elif c_x is not None and c_x > 0:
            px = c_x
        else:
            cnt["exec_missing"] += 1
            continue
        j = X + FWD
        if j >= n:
            j = n - 1
        truncated = (j != X + FWD) or (closes[j] is None) or (closes[j] <= 0)
        while j > X and (closes[j] is None or closes[j] <= 0):
            j -= 1
        if j <= X:
            cnt["trunc_no_fwd"] += 1
            continue
        ret = closes[j] / px - 1.0
        rb = bench_lookup(bd, bcum, dates[X], dates[j])
        if rb is None:
            cnt["bench_missing"] += 1
            continue
        ev_exec.append({"code": code, "anchor": dates[X], "confirm": dates[c],
                        "group": grp, "exec_price": round(px, 4),
                        "ret20_pct": round(ret * 100.0, 4),
                        "ab20_pp": round((ret - rb) * 100.0, 4),
                        "truncated": truncated})
        if truncated:
            cnt["truncated"] += 1
    for (i, kind) in d1_local:
        if kind == "X":
            cnt["d1_boundary"] += 1
            continue
        if is_st(code, dates[i]):
            cnt["d1_st_excluded"] += 1
            continue
        if kind == "C":
            cnt["d1_confirmed"] += 1
        else:
            cnt["d1_rejected"] += 1
        e0 = closes[i]
        j = i + FWD
        if j >= n:
            j = n - 1
        dtrunc = (j != i + FWD) or (closes[j] is None) or (closes[j] <= 0)
        while j > i and (closes[j] is None or closes[j] <= 0):
            j -= 1
        if j <= i:
            cnt["d1_trunc_no_fwd"] += 1
            continue
        dret = closes[j] / e0 - 1.0
        drb = bench_lookup(bd, bcum, dates[i], dates[j])
        if drb is None:
            cnt["d1_bench_missing"] += 1
            continue
        ev_d1.append({"code": code, "anchor": dates[i], "kind": kind,
                      "ab20_pp": round((dret - drb) * 100.0, 4),
                      "truncated": dtrunc})
    return cnt


def ab_stats(items, lo, hi):
    xs = [e["ab20_pp"] for e in items if lo <= e["anchor"] < hi]
    n = len(xs)
    if n == 0:
        return {"n": 0, "years": {}}
    mu = sum(xs) / n
    sd = (sum((x - mu) ** 2 for x in xs) / n) ** 0.5
    t = mu / (sd / n ** 0.5) if sd > 0 else None
    yr = defaultdict(list)
    for e in items:
        if lo <= e["anchor"] < hi:
            yr[str(e["anchor"])[:4]].append(e["ab20_pp"])
    ym = {y: round(sum(v) / len(v), 4) for y, v in sorted(yr.items())}
    return {"n": n, "mean_ab_pp": round(mu, 4), "sd": round(sd, 4),
            "t": None if t is None else round(t, 2),
            "pos_years": sum(1 for v in ym.values() if v > 0),
            "neg_years": sum(1 for v in ym.values() if v < 0),
            "years": ym}


def main():
    t0t = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace((' "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b' "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = (fp == prereg["fingerprint"])
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    all_dates = [int(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? AND date<=? ORDER BY date",
        (START_DATA, SIG_END))]
    is_st = st_loader()
    bench = defaultdict(lambda: [0.0, 0])
    cur = con.execute("SELECT code,date,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date",
                      (START_DATA, SIG_END))
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
    bcum = []
    cc = 1.0
    for d in bd:
        s, nb = bench[d]
        cc *= (1 + s / nb) if nb else 1.0
        bcum.append(cc)
    del bench
    print("pass1完成 %.0fs 基准日%d 全局交易日%d" % (time.time() - t0t, len(bd), len(all_dates)))
    ev_exec, ev_d1, ev_rec = [], [], []
    totals = {"n": 0, "recovered": 0, "exec_missing": 0, "trunc_no_fwd": 0,
              "truncated": 0, "st_excluded": 0, "bench_missing": 0,
              "d1_confirmed": 0, "d1_rejected": 0, "d1_boundary": 0,
              "d1_st_excluded": 0, "d1_trunc_no_fwd": 0, "d1_bench_missing": 0}
    nstock = 0
    cur = con.execute("SELECT code,date,open,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date",
                      (START_DATA, SIG_END))
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        bars = []
        for _, d, o, c in grp:
            bars.append((int(d), None, o, c))
        nstock += 1
        r = run_stock(code, bars, is_st, bd, bcum, ev_exec, ev_d1, ev_rec)
        for k in totals:
            totals[k] += r.get(k, 0)
    con.close()
    print("pass2完成 %.0fs 股票%d 执行卖出%d D1候选%d 收回%d" % (
        time.time() - t0t, nstock, totals["n"], totals["d1_confirmed"] + totals["d1_rejected"], totals["recovered"]))
    t1p = ab_stats(ev_exec, SPLIT, 20999999)
    t1h = ab_stats(ev_exec, 20100000, SPLIT)
    c_grp = ab_stats([e for e in ev_d1 if e["kind"] == "C"], SPLIT, 20999999)
    r_grp = ab_stats([e for e in ev_d1 if e["kind"] == "R"], SPLIT, 20999999)
    ykeys = sorted(set(c_grp["years"]) | set(r_grp["years"]))
    t2_years = {y: round(r_grp["years"].get(y, 0.0) - c_grp["years"].get(y, 0.0), 4)
                for y in ykeys}
    t2_pos = sum(1 for v in t2_years.values() if v > 0)
    t2_neg = sum(1 for v in t2_years.values() if v < 0)
    t2_diff = None
    if c_grp["n"] > 0 and r_grp["n"] > 0:
        t2_diff = round(r_grp["mean_ab_pp"] - c_grp["mean_ab_pp"], 4)
    if c_grp["n"] == 0 or r_grp["n"] == 0:
        t2_verdict = "FILTER_MIXED(无样本)"
    elif t2_diff > 0 and t2_pos >= 8:
        t2_verdict = "FILTER_CORRECT"
    elif t2_diff < 0 and t2_neg >= 8:
        t2_verdict = "FILTER_WRONG"
    else:
        t2_verdict = "FILTER_MIXED"
    rec_s = ab_stats(ev_rec, SPLIT, 20999999)
    if rec_s["n"] == 0:
        t3_verdict = "MIXED(无样本)"
    elif rec_s["mean_ab_pp"] > 0 and rec_s["pos_years"] >= 8:
        t3_verdict = "RECOVER_CORRECT"
    elif rec_s["mean_ab_pp"] < 0 and rec_s["neg_years"] >= 8:
        t3_verdict = "RECOVER_WRONG"
    else:
        t3_verdict = "MIXED"
    a_grp = ab_stats([e for e in ev_exec if e["group"] == "A"], SPLIT, 20999999)
    b_grp = ab_stats([e for e in ev_exec if e["group"] == "B"], SPLIT, 20999999)
    print("T1 卖出正确性: n=%s ab20均值=%spp t=%s 负年%s/正年%s 判定待汇总" % (
        t1p["n"], t1p.get("mean_ab_pp"), t1p.get("t"),
        t1p.get("neg_years"), t1p.get("pos_years")))
    print("T2 两日过滤: confirmed n=%s 均%s | rejected n=%s 均%s | 差=%s 正差年%s/负差年%s" % (
        c_grp["n"], c_grp.get("mean_ab_pp"), r_grp["n"], r_grp.get("mean_ab_pp"),
        t2_diff, t2_pos, t2_neg))
    print("T3 分流: A组 n=%s 均%s | B组 n=%s 均%s | 收回组 n=%s 均%s" % (
        a_grp["n"], a_grp.get("mean_ab_pp"), b_grp["n"], b_grp.get("mean_ab_pp"),
        rec_s["n"], rec_s.get("mean_ab_pp")))
    if t1p["n"] == 0:
        t1_verdict = "MIXED(无样本)"
    elif t1p["mean_ab_pp"] < 0 and t1p["neg_years"] >= 8:
        t1_verdict = "CORRECT"
    elif t1p["mean_ab_pp"] > 0 and t1p["pos_years"] >= 8:
        t1_verdict = "WRONG"
    else:
        t1_verdict = "MIXED"
    out = {"prereg_fp": fp, "fp_ok": fp_ok,
           "window": [START_DATA, SIG_END], "split": SPLIT,
           "counts": dict(totals, nstock=nstock),
           "T1_exec_ab": {"primary": t1p, "holdout": t1h,
                          "A_group": a_grp, "B_group": b_grp,
                          "verdict": t1_verdict},
           "T2_d1_filter": {"primary_confirmed": c_grp, "primary_rejected": r_grp,
                            "diff_pp": t2_diff, "diff_years": t2_years,
                            "verdict": t2_verdict},
           "T3_recovered": {"stats": rec_s, "verdict": t3_verdict},
           "samples": {"exec": ev_exec[:2], "d1": ev_d1[:2], "rec": ev_rec[:2]}}
    path = OUT2 if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("T1判定=", t1_verdict, "| T2判定=", t2_verdict, "| T3判定=", t3_verdict)
    print("已写入", path, "%.0fs" % (time.time() - t0t))


if __name__ == "__main__":
    main()

