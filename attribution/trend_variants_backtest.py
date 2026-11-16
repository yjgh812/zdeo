r"""trend_variants_backtest.py — 教程变体批量实测（PREREG_TREND_VARIANTS_V2，指纹2fbad6f373079310）。
买臂A1-A6(T+1开盘买,持有10/20日收盘卖) + 卖臂S1a/b/c(信号后10/20日前瞻超额,反向判据) + R1月频动量Top30。零交易权。用法: [run2]
"""
import bisect
import hashlib
import json
import sqlite3
import sys
import time
from collections import deque, defaultdict
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
PREREG = r"D:\zcodesj\data\PREREG_TREND_VARIANTS_V2_20261114.json"
START_DATA, SIG_START, SIG_END = "20140101", "20160101", "20261113"
H1, H2 = 10, 20
COST = 0.003
TOPN = 30
GEM = ("300", "301", "302")
EXCL = ("4", "8", "9", "68")
OUT1 = r"D:\zcodesj\reports\trend_variants_bt_20160101_20261113.json"
OUT2 = r"D:\zcodesj\reports\trend_variants_bt_20160101_20261113_run2.json"

BUY_ARMS = ("A1", "A2", "A3", "A4", "A5", "A6")
SELL_ARMS = ("S1a", "S1b", "S1c")


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


def prev_ext(arr, w, mode):
    """R[t]=arr[t-w..t-1]极值(不含当前bar); 窗口有效值<w时None。O(n)单调队列。"""
    n = len(arr)
    R = [None] * n
    dq = deque()
    valid = 0
    for t in range(n):
        if t >= w + 1 and arr[t - w - 1] is not None:
            valid -= 1
        while dq and dq[0][0] < t - w:
            dq.popleft()
        if t >= w and valid >= w and dq:
            R[t] = dql(dq)
        v = arr[t]
        if v is not None:
            if mode == "max":
                while dq and dq[-1][1] <= v:
                    dq.pop()
            else:
                while dq and dq[-1][1] >= v:
                    dq.pop()
            dq.append((t, v))
            valid += 1
    return R


def dql(dq):
    return dq[0][1]


def nw_t(dmeans):
    T = len(dmeans)
    if T < 30:
        return None, T
    mu = sum(dmeans) / T
    u = [x - mu for x in dmeans]
    g0 = sum(x * x for x in u) / T
    lr = g0
    for l in range(1, 6):
        s = sum(u[i] * u[i - l] for i in range(l, T))
        lr += 2.0 * (1 - l / 6.0) * (s / T)
    if lr <= 0:
        return 99.0, T
    return mu / (lr / T) ** 0.5, T


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
    return dt, cl, idx20


def main(run_id="run1"):
    t0 = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace(('  "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b'  "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = fp == prereg["fingerprint"]
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))

    idt, icl, idx20 = load_index()
    is_st = st_loader()
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)

    # ---- Pass1: 全池基准 + 月末交易日集合 ----
    bench = defaultdict(lambda: [0.0, 0])
    cur = con.execute("SELECT code,date,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    ncode = 0
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        ncode += 1
        if ncode % 800 == 0:
            print("pass1 %d码 %.0fs" % (ncode, time.time() - t0))
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
    last_ym = None
    for d in bd:
        ym = str(d)[:6]
        if last_ym is not None and ym != last_ym:
            me_set.add(bd_prev)
        bd_prev = d
        last_ym = me_ym = ym
    me_set.add(bd_prev)
    print("pass1完成 %.0fs 基准日%d 月末日%d" % (time.time() - t0, len(bd), len(me_set)))

    # ---- Pass2: 信号检测 + 事件测量 ----
    ev = {a: [] for a in BUY_ARMS}
    sv = {a: [] for a in SELL_ARMS}
    snap = defaultdict(dict)  # 月末日 -> {code: ret20}
    cnt = {a: 0 for a in list(BUY_ARMS) + list(SELL_ARMS)}

    def bench_w(d0, d1):
        i0, i1 = bidx.get(d0), bidx.get(d1)
        if i0 is None or i1 is None or i1 < i0:
            return None
        return bcum[i1] / bcum[i0] - 1.0

    def hold_excess(bars, t, horizon, thr):
        """T+1开盘买→T+horizon收盘卖 的净超额; 不可测/开盘涨停跳过返回None"""
        if t + horizon + 1 >= len(bars):
            return None
        eo = bars[t + 1][1]
        xc = bars[t + 1 + horizon][4]
        d0, d1 = bars[t + 1][0], bars[t + 1 + horizon][0]
        if eo is None or xc is None or eo <= 0:
            return None
        c_sig = bars[t][4]
        if eo / c_sig - 1.0 >= thr:
            return None  # 开盘涨停不可买
        gross = xc / eo - 1.0
        net = gross - COST
        b = bench_w(d0, d1)
        if b is None:
            return None
        return (net - b) * 100.0

    def fwd_excess(bars, t, horizon):
        """信号日收盘→horizon日后收盘 超额(卖点口径); 不可测返回None"""
        if t + horizon >= len(bars):
            return None
        c0, c1 = bars[t][4], bars[t + horizon][4]
        d0, d1 = bars[t][0], bars[t + horizon][0]
        if c0 is None or c1 is None or c0 <= 0:
            return None
        gross = c1 / c0 - 1.0
        b = bench_w(d0, d1)
        if b is None:
            return None
        return (gross - b) * 100.0

    def add_event(arm, bars, t, thr):
        e10 = hold_excess(bars, t, H1, thr)
        e20 = hold_excess(bars, t, H2, thr)
        if e10 is None or e20 is None:
            cnt[arm] += 1
            return
        d_ent = bars[t + 1][0]
        ev[arm].append({"d": d_ent, "y": str(d_ent)[:4], "e10": e10, "e20": e20})

    def add_sell(arm, bars, t):
        f10 = fwd_excess(bars, t, H1)
        f20 = fwd_excess(bars, t, H2)
        if f10 is None or f20 is None:
            cnt[arm] += 1
            return
        sv[arm].append({"d": bars[t][0], "y": str(bars[t][0])[:4], "f10": f10, "f20": f20})

    cur = con.execute("SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
                      "SGN? AND date<=? ORDER BY code,date", (SIG_START, SIG_END)).fetchall() if False else con.execute(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    ncode = 0
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        ncode += 1
        if ncode % 500 == 0:
            print("pass2 %d码 %d事件 %.0fs" % (ncode, sum(len(v) for v in ev.values()), time.time() - t0))
        if code.startswith(EXCL):
            continue
        gem = code.startswith(GEM)
        thr = 0.198 if gem else 0.098
        bars = [(int(d), o, h, l, c, v) for _, d, o, h, l, c, v in grp]
        n = len(bars)
        if n < 80:
            continue
        cs = [b[4] for b in bars]
        vs = [b[5] for b in bars]
        hs = [b[2] for b in bars]
        ls = [b[3] for b in bars]
        ma5, ma10, ma20, ma34, ma60 = [None] * n, [None] * n, [None] * n, [None] * n, [None] * n
        s5 = s10 = s20 = s34 = s60 = 0.0
        for i in range(n):
            c = cs[i]
            if c is not None:
                s5 += c
                s10 += c
                s20 += c
                s34 += c
                s60 += c
            if i >= 5 and cs[i - 5] is not None:
                s5 -= cs[i - 5]
            if i >= 10 and cs[i - 10] is not None:
                s10 -= cs[i - 10]
            if i >= 20 and cs[i - 20] is not None:
                s20 -= cs[i - 20]
            if i >= 34 and cs[i - 34] is not None:
                s34 -= cs[i - 34]
            if i >= 60 and cs[i - 60] is not None:
                s60 -= cs[i - 60]
            if i >= 4:
                ma5[i] = s5 / 5.0
            if i >= 9:
                ma10[i] = s10 / 10.0
            if i >= 19:
                ma20[i] = s20 / 20.0
            if i >= 33:
                ma34[i] = s34 / 34.0
            if i >= 59:
                ma60[i] = s60 / 60.0
        hi55 = prev_ext(hs, 55, "max")
        dif = dea = None
        e12 = e26 = cs[0]
        difs = [None] * n
        deas = [None] * n
        for i in range(n):
            c = cs[i]
            if c is None:
                difs[i] = difs[i - 1] if i else None
                deas[i] = deas[i - 1] if i else None
                continue
            e12 = e12 + (c - e12) * 2.0 / 13.0
            e26 = e26 + (c - e26) * 2.0 / 27.0
            dif = e12 - e26
            dea = dif if dea is None else dea + (dif - dea) * 2.0 / 10.0
            difs[i] = dif
            deas[i] = dea
        for t in range(60, n - H2 - 1):
            d = bars[t][0]
            if d < int(SIG_START) or d > int(SIG_END):
                continue
            c, o, v = cs[t], bars[t][1], vs[t]
            if c is None or o is None or c <= 0:
                continue
            if is_st(code, d):
                continue
            if not (2.0 <= c <= 30.0):
                continue
            if v is None:
                v = -1.0
            m5, m10, m20, m34, m60 = ma5[t], ma10[t], ma20[t], ma34[t], ma60[t]
            if None in (m5, m10, m20, m34, m60):
                continue
            p5, p10, p20, p60 = ma5[t - 1], ma10[t - 1], ma20[t - 1], ma60[t - 1]
            if None in (p5, p10, p20, p60):
                continue
            vm5 = vs[t - 4:t + 1]
            if any(x is None for x in vm5):
                vm5 = None
            else:
                vm5 = sum(vm5) / 5.0
            if vm5 is None:
                continue
            df, dfa = difs[t], deas[t]
            if df is None or dfa is None:
                continue
            # ---- A1 ----
            if (m5 > m10 > m20 > m60 and m5 > p5 and m10 > p10 and m20 > p20 and m60 > p60
                    and c > m5 and c < m20 * 1.15):
                add_event("A1", bars, t, thr)
            # ---- A2 ----
            if t >= 20 and cs[t - 20] not in (None,) and cs[t - 20]:
                r20 = c / cs[t - 20] - 1.0
                ir = idx20.get(d)
                if ir is not None and (m5 > m10 > m20 > m60 and m5 > p5 and m10 > p10 and m20 > p20 and m60 > p60
                                       and c > m5 and c < m20 * 1.15 and r20 > ir and r20 > 0):
                    add_event("A2", bars, t, thr)
            # ---- A3 ----
            h55 = hi55[t]
            if h55 is not None and c > h55 and (m60 > p60 or c > m60) and vm5 is not None and v > vm5:
                add_event("A3", bars, t, thr)
            # ---- A4 ----
            vp = vs[t - 1]
            if (m20 > m60 and m20 > p20 and m60 > p60 and ls[t] is not None
                    and ls[t] <= m20 * 1.02 and c >= m20 * 0.99
                    and v < vm5 and vp is not None and v < vp
                    and (c > o or (cs[t - 1] is not None and c > cs[t - 1]))
                    and df > 0 and dfa is not None and df >= dfa * 0.95):
                add_event("A4", bars, t, thr)
            # ---- A5 ----
            gold = df > dfa and difs[t - 1] is not None and difs[t - 1] <= deas[t - 1]
            if gold and df > 0 and dfa > 0:
                death = False
                for k in range(max(1, t - 15), t):
                    if difs[k] is not None and deas[k] is not None and difs[k - 1] is not None and deas[k - 1] is not None:
                        if difs[k] < deas[k] and difs[k - 1] >= deas[k - 1]:
                            death = True
                            break
                if death and v > vm5:
                    add_event("A5", bars, t, thr)
            # ---- A6 ----
            if m60 > p60:
                cross20_60 = False
                for k in range(max(1, t - 2), t + 1):
                    if (ma20[k] is not None and ma60[k] is not None and ma20[k - 1] is not None and ma60[k - 1] is not None
                            and ma20[k] > ma60[k] and ma20[k - 1] <= ma60[k - 1]):
                        cross20_60 = True
                        break
                hold60 = (cs[t] is not None and cs[t - 1] is not None and cs[t - 2] is not None
                          and ma60[t - 1] is not None and ma60[t - 2] is not None)
                if hold60 and cs[t] > m60 and cs[t - 1] > ma60[t - 1] and cs[t - 2] > ma60[t - 2]:
                    dif_up0 = False
                    for k in range(max(1, t - 2), t + 1):
                        if difs[k] is not None and difs[k - 1] is not None and difs[k] > 0 and difs[k - 1] <= 0:
                            dif_up0 = True
                            break
                    if dif_up0 and v > vm5:
                        add_event("A6", bars, t, thr)
            # ---- 卖点 ----
            pm34 = ma34[t - 1]
            if pm34 is not None:
                if c < m34 and cs[t - 1] is not None and cs[t - 1] >= pm34:
                    add_sell("S1a", bars, t)
            pp60 = ma60[t - 1]
            p2c = cs[t - 2]
            p2m60 = ma60[t - 2]
            if (pp60 is not None and p2c is not None and p2m60 is not None
                    and c < m60 and cs[t - 1] is not None and cs[t - 1] < pp60 and p2c >= p2m60):
                add_sell("S1b", bars, t)
            p10v = ma10[t - 1]
            if (p10v is not None and m20 <= p20 and m5 < m20 and c < m20 * 0.985
                    and m5 < p5 and c < m5 and (df < dfa or df < 0)):
                add_sell("S1c", bars, t)
            # ---- R1 月末截面快照 ----
            if d in me_set and t >= 20 and cs[t - 20] not in (None,) and cs[t - 20] > 0:
                ir = idx20.get(d)
                if ir is not None:
                    snap[d][code] = c / cs[t - 20] - 1.0
    print("pass2完成 %.0fs 事件A:%s 卖点:%s 月末快照月数%d" % (
        time.time() - t0, {a: len(ev[a]) for a in BUY_ARMS}, {a: len(sv[a]) for a in SELL_ARMS}, len(snap)))

    # ---- R1 月频组合 ----
    me_dates = sorted(snap)
    member_months = defaultdict(set)  # ym -> codes
    prev_set = set()
    costs = []
    for D in me_dates:
        cand = snap[D]
        if len(cand) < TOPN:
            continue
        ir = idx20.get(D, 0.0)
        ranked = sorted(cand.items(), key=lambda kv: (kv[1] - ir), reverse=True)
        sel = {k for k, _ in ranked[:TOPN]}
        ym_next = next_month(str(D)[:6])
        member_months[ym_next] = sel
        chg = len(sel - prev_set) + len(prev_set - sel)
        costs.append((ym_next, chg / float(TOPN)))
        prev_set = sel
    del snap
    # Phase C: 组合月收益
    mret_daily = defaultdict(lambda: [0.0, 0])  # (ym,date)->[sum,cnt]
    cur = con.execute("SELECT code,date,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    want = defaultdict(set)  # ym -> codes
    for ym, s in member_months.items():
        want[ym] = s
    ncode = 0
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        ncode += 1
        if code.startswith(EXCL):
            continue
        ms = [ym for ym, s in want.items() if code in s]
        if not ms:
            continue
        prev = None
        for _, d, c in grp:
            d = int(d)
            ym = str(d)[:6]
            if c is not None and prev not in (None,) and prev > 0 and ym in member_months and code in member_months[ym]:
                mret_daily[(ym, d)][0] += c / prev - 1.0
                mret_daily[(ym, d)][1] += 1
            prev = c
    con.close()
    month_ex = []
    for ym in sorted(member_months):
        dates = sorted(k[1] for k in mret_daily if k[0] == ym)
        if not dates:
            continue
        pr = 1.0
        for d in dates:
            s, c2 = mret_daily[(ym, d)]
            if c2:
                pr *= (1 + s / c2)
        cost = 0.0
        for cy, cf in costs:
            if cy == ym:
                cost = cf
        pr *= (1 - COST * cost)
        i0 = bisect.bisect_left(bd, int(ym + "00"))
        i1 = bisect.bisect_left(bd, int(next_month(ym) + "00"))
        if i0 >= len(bd) or i1 <= i0:
            continue
        b = bcum[i1 - 1] / bcum[max(i0 - 1, 0)] - 1.0
        month_ex.append({"ym": ym, "ex": (pr - 1.0 - b) * 100.0, "pr": (pr - 1.0) * 100.0})
    out = build_out(run_id, fp, fp_ok, ev, sv, month_ex)
    path = OUT2 if run_id == "run2" else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", path)


def next_month(ym):
    y, m = int(ym[:4]), int(ym[4:])
    m += 1
    if m > 12:
        y += 1
        m = 1
    return "%04d%02d" % (y, m)


def build_out(run_id, fp, fp_ok, ev, sv, month_ex):
    def agg_evs(lst, key):
        if not lst:
            return {"n": 0}
        xs = [r[key] for r in lst]
        wins = [x for x in xs if x > 0]
        dm = defaultdict(list)
        for r in lst:
            dm[r["d"]].append(r[key])
        tstat, nd = nw_t([sum(v) / len(v) for v in dm.values()])
        years = defaultdict(list)
        for r in lst:
            years[r["y"]].append(r[key])
        by_year = {y: round(sum(v) / len(v), 3) for y, v in sorted(years.items())}
        ycnt = {y: len(v) for y, v in sorted(years.items())}
        return {"n": len(xs), "win_rate": round(len(wins) / len(xs), 4),
                "mean": round(sum(xs) / len(xs), 3),
                "median": round(sorted(xs)[len(xs) // 2], 3),
                "nw5_t": None if tstat is None else round(tstat, 2), "dates": nd,
                "by_year": by_year, "year_n": ycnt}

    def judge_buy(m10, m20):
        if m10["n"] < 1 or m20["n"] < 1:
            return {"verdict": "INSUFFICIENT"}
        def p(x):
            return (x["mean"] is not None and x["mean"] > 0 and x["nw5_t"] is not None
                    and x["nw5_t"] >= 2.0 and x["dates"] >= 30 and year_ratio(x) >= 0.5)
        ok = p(m10) and p(m20)
        return {"P1": m10["mean"] > 0 and m20["mean"] > 0,
                "P2": (m10["nw5_t"] or 0) >= 2 and (m20["nw5_t"] or 0) >= 2,
                "P3": year_ratio(m10) >= 0.5 and year_ratio(m20) >= 0.5,
                "verdict": "PASS" if ok else "FAIL"}

    def year_ratio(m):
        ys = [y for y, v in m["by_year"].items() if m["year_n"][y] >= 30 and v > 0]
        tot = [y for y, v in m["year_n"].items() if v >= 30]
        return (len(ys) / len(tot)) if tot else 0.0

    out = {"prereg_fp": fp, "fp_ok": fp_ok, "run_id": run_id,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "window": [SIG_START, SIG_END],
           "cost": COST, "horizons": [H1, H2], "buy_arms": {}, "sell_arms": {}, "R1": {}}
    for arm in BUY_ARMS:
        m10, m20 = agg_evs(ev[arm], "e10"), agg_evs(ev[arm], "e20")
        out["buy_arms"][arm] = {"h10": m10, "h20": m20, "verdict": judge_buy(m10, m20)}
    for arm in SELL_ARMS:
        m10, m20 = agg_evs(sv[arm], "f10"), agg_evs(sv[arm], "f20")
        ok10 = m10["n"] > 0 and m10["mean"] < 0 and (m10["nw5_t"] or 0) <= -2.0
        ok20 = m20["n"] > 0 and m20["mean"] < 0 and (m20["nw5_t"] or 0) <= -2.0
        out["sell_arms"][arm] = {"f10": m10, "f20": m20,
                                 "verdict": "PASS(卖点有效)" if (ok10 and ok20) else "FAIL(卖点反向或无效)"}
    if month_ex:
        xs = [m["ex"] for m in month_ex]
        mu = sum(xs) / len(xs)
        sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
        t = mu / (sd / len(xs) ** 0.5) if sd > 0 else None
        by_year = defaultdict(list)
        for m in month_ex:
            by_year[m["ym"][:4]].append(m["ex"])
        yr = {y: round(sum(v) / len(v), 2) for y, v in sorted(by_year.items())}
        pos_years = sum(1 for v in yr.values() if v > 0)
        out["R1"] = {"months": len(xs), "mean_month_excess": round(mu, 3),
                     "annualized_excess": round(mu * 12, 2), "monthly_t": None if t is None else round(t, 2),
                     "by_year_mean_excess": yr, "pos_years": "%d/%d" % (pos_years, len(yr)),
                     "verdict": ("PASS" if (mu > 0 and t is not None and t >= 2 and pos_years / len(yr) >= 0.5) else "FAIL")}
    return out


if __name__ == "__main__":
    rid = sys.argv[1] if len(sys.argv) > 1 else "run1"
    main(rid)
