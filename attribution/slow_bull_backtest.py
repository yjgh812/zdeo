r"""slow_bull_backtest.py — 慢牛爬升+FIP信息离散度月频实测（PREREG_SLOWBULL_FIP_V1，指纹f97c0568d104cf12）。
S1=慢牛七判据主判据, S2=S1且ID≤0(描述), S3=60日涨幅[10%,60%]且ID>0(对照, 预期负)。
每月末最后交易日收盘判定, 命中者等权持至下月末收盘。零交易权。用法: python -I slow_bull_backtest.py [run2]
判形函数严格按预注册冻结定义实现, 禁止事后调参。
"""
import bisect
import hashlib
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
PREREG = r"D:\zcodesj\data\PREREG_SLOWBULL_FIP_V1_20261116.json"
START_DATA, SIG_END = "20100101", "20261113"
SPLIT = 20160000
COST = 0.003
MINBARS = 130
EXCL = ("4", "8", "9", "68")
ARMS = ("S1", "S2", "S3")
OUT1 = r"D:\zcodesj\reports\slow_bull_bt_20100101_20261113.json"
OUT2 = r"D:\zcodesj\reports\slow_bull_bt_20100101_20261113_run2.json"


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


def linreg(ys):
    n = len(ys)
    mt = (n - 1) / 2.0
    my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for t, y in zip(range(n), ys):
        dx = t - mt
        dy = y - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    slope = sxy / sxx if sxx > 0 else 0.0
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    return slope, r2


def slow_bull7(c, c121, vals61, vals70, chgs):
    """S1七判据, 输入: 信号日收盘c, c[-121]收盘, close[-61:]共61值, close[-70:]共70值, 近60根bar的change_pct."""
    ma20 = sum(vals61[-20:]) / 20.0
    ma60 = sum(vals61[-60:]) / 60.0
    if not (c > ma20 > ma60):
        return False
    if not (ma60 > sum(vals70[:-10]) / 60.0):
        return False
    if not (c / c121 - 1.0 > 0.0):
        return False
    r60 = c / vals61[0] - 1.0
    if not (0.10 <= r60 <= 0.60):
        return False
    peak = vals61[0]
    dd = 0.0
    for v in vals61:
        if v > peak:
            peak = v
        d0 = 1.0 - v / peak
        if d0 > dd:
            dd = d0
    if not (dd < 0.12):
        return False
    for x in chgs:
        if x is None or x > 9.5 or x < -6.0:
            return False
    if sum(1 for x in chgs if abs(x) > 5.0) > 3:
        return False
    if c / max(vals61) < 0.97:
        return False
    slope, r2 = linreg([math.log(v) for v in vals61])
    if r2 < 0.75:
        return False
    dpct = (math.exp(slope) - 1.0) * 100.0
    ang = math.degrees(math.atan(dpct))
    if not (18.0 <= ang <= 45.0):
        return False
    return True


def main():
    t0 = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace(('  "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b'  "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = fp == prereg["fingerprint"]
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))

    m = sqlite3.connect("file:%s?mode=ro" % META_DB, uri=True)
    irows = m.execute("SELECT date,close FROM index_daily_meta WHERE code='000001' AND date>=? AND date<=? "
                      "ORDER BY date", (int(START_DATA), int(SIG_END))).fetchall()
    m.close()
    icl = {int(d): c for d, c in irows}

    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    all_dates = [int(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? AND date<=? ORDER BY date",
        (START_DATA, SIG_END))]
    month_ends = []
    prev_d, cur_m = None, None
    for d in all_dates:
        ym = d // 100
        if cur_m is not None and ym != cur_m:
            month_ends.append(prev_d)
        cur_m = ym
        prev_d = d

    is_st = st_loader()

    # pass1 基准: 全池(剔前缀, 按日剔ST)等权日收益累乘
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
    print("pass1完成 %.0fs 基准日%d 信号月数%d" % (time.time() - t0, len(bd), len(month_ends) - 1))

    # pass2 判形: 每股在每个月末信号日按冻结判据判定
    arm_members = {a: {} for a in ARMS}
    me_idx = {d: i for i, d in enumerate(month_ends)}
    cur = con.execute("SELECT code,date,close,change_pct FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        closes = [(int(d), c, chg) for _, d, c, chg in grp]
        for i in range(MINBARS - 1, len(closes)):
            d, c, _ = closes[i]
            if d not in me_idx:
                continue
            if c is None or c <= 0 or not (2.0 <= c <= 30.0):
                continue
            if d >= SPLIT and is_st(code, d):
                continue
            vals61 = [closes[j][1] for j in range(i - 60, i + 1)]
            if any(v is None or v <= 0 for v in vals61):
                continue
            c121 = closes[i - 120][1]
            if c121 is None or c121 <= 0:
                continue
            vals70 = [closes[j][1] for j in range(i - 69, i + 1)]
            if any(v is None or v <= 0 for v in vals70):
                continue
            chgs = [closes[j][2] for j in range(i - 59, i + 1)]
            r60 = c / vals61[0] - 1.0
            rets = [vals61[j] / vals61[j - 1] - 1.0 for j in range(1, 61)]
            ppos = sum(1 for r in rets if r > 0) / 60.0
            pneg = sum(1 for r in rets if r < 0) / 60.0
            cg = 1.0 if r60 > 0 else (-1.0 if r60 < 0 else 0.0)
            idf = cg * (ppos - pneg)
            mi = me_idx[d]
            if slow_bull7(c, c121, vals61, vals70, chgs):
                arm_members["S1"].setdefault(mi, set()).add(code)
                if idf <= 0.0:
                    arm_members["S2"].setdefault(mi, set()).add(code)
            if 0.10 <= r60 <= 0.60 and idf > 0.0:
                arm_members["S3"].setdefault(mi, set()).add(code)
    print("pass2完成 %.0fs" % (time.time() - t0))

    results = {}
    for arm in ARMS:
        member_sets = {}
        prev_set = set()
        costs = []
        for k in range(len(month_ends) - 1):
            sel = arm_members[arm].get(k)
            if not sel:
                continue
            member_sets[k] = sel
            chg = len(sel - prev_set) + len(prev_set - sel)
            costs.append((k, chg / float(len(sel))))
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
                k2 = bisect.bisect_left(month_ends, d) - 1
                if 0 <= k2 < len(month_ends) - 1 and d > month_ends[k2] and code in member_sets.get(k2, ()):
                    psum[(k2, d)][0] += r
                    psum[(k2, d)][1] += 1
                prev = c
        monthly = []
        for k, D in enumerate(month_ends[:-1]):
            if k not in member_sets or D not in bidx:
                continue
            E = month_ends[k + 1]
            if E not in bidx:
                continue
            pr = 1.0
            ok = False
            for j in range(bidx[D] + 1, bidx[E] + 1):
                d = bd[j]
                s, c2 = psum.get((k, d), [0.0, 0])
                if c2:
                    ok = True
                    pr *= (1 + s / c2)
            if not ok:
                continue
            cost = next((cf for ck, cf in costs if ck == k), 0.0)
            pr *= (1 - COST * cost)
            b = bcum[bidx[E]] / bcum[bidx[D]] - 1.0
            iv = None
            if D in icl and E in icl:
                iv = icl[E] / icl[D] - 1.0
            monthly.append({"pd": D, "hits": len(member_sets[k]),
                            "port": round(pr - 1.0, 6), "bench": round(b, 6),
                            "ex": round((pr - 1.0 - b) * 100.0, 4),
                            "idx": None if iv is None else round(iv, 6)})
        stats = mstats(monthly)
        results[arm] = {"months": monthly, "months_with_hits": len(member_sets), "stats": stats}
        p, h = stats["primary"], stats["holdout"]
        print("%s: 主窗 月均=%s 年化净超额=%s t=%s 正年=%s 平均命中=%s | 留出窗 月均=%s | 判定=%s / %s" % (
            arm, p.get("mean_p"), p.get("annualized"), p.get("t"), p.get("pos_years"),
            p.get("hit_mean"), h.get("mean_p"), p.get("verdict"), h.get("verdict")))
    con.close()
    print("机械判定(S1主窗): %s" % results["S1"]["stats"]["primary"].get("verdict"))
    out = {"prereg_fp": fp, "fp_ok": fp_ok, "window": [START_DATA, SIG_END],
           "split": SPLIT, "cost": COST, "min_bars": MINBARS,
           "n_signal_months": len(month_ends) - 1, "arms": results}
    path = OUT2 if len(sys.argv) > 1 and sys.argv[1] == "run2" else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", path, " %.0fs" % (time.time() - t0))


def mstats(monthly):
    def seg(lo, hi, judge):
        xs = [mm for mm in monthly if lo <= mm["pd"] < hi]
        if len(xs) < 26:
            return {"n": len(xs), "verdict": "样本不足"}
        exs = [mm["ex"] for mm in xs]
        mu = sum(exs) / len(exs)
        sd = (sum((x - mu) ** 2 for x in exs) / len(exs)) ** 0.5
        t = mu / (sd / len(exs) ** 0.5) if sd > 0 else None
        yrs = defaultdict(list)
        for mm in xs:
            yrs[str(mm["pd"])[:4]].append(mm["ex"])
        yr_mean = {y: sum(v) / len(v) for y, v in yrs.items()}
        pos = sum(1 for v in yr_mean.values() if v > 0)
        if not judge:
            vd = "仅报告"
        elif mu > 0 and t is not None and t >= 2.0 and pos / len(yr_mean) >= 0.5:
            vd = "PASS_candidate"
        elif mu < 0 and t is not None and t <= -2.0:
            vd = "FAIL"
        else:
            vd = "观察不采信"
        return {"n": len(xs), "mean_p": round(mu, 3), "annualized": round(mu * 12, 2),
                "t": None if t is None else round(t, 2),
                "pos_years": "%d/%d" % (pos, len(yr_mean)),
                "hit_mean": round(sum(mm["hits"] for mm in xs) / len(xs), 1),
                "verdict": vd}
    return {"primary": seg(20160000, 20990000, True),
            "holdout": seg(20100000, 20160000, False)}


if __name__ == "__main__":
    main()
