r"""trend_system_backtest.py — 趋势选股体系全历史回测（PREREG_TREND_SYSTEM_V1，指纹b5fe90358042ca73）。
三臂: A=完整管线+三三确认(主判据) / B=管线无确认 / C=纯共振。零交易权。用法: python trend_system_backtest.py [run2]
"""
import bisect
import hashlib
import json
import sqlite3
import sys
import time
from collections import deque, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
PREREG = r"D:\zcodesj\data\PREREG_TREND_SYSTEM_V1_20261114.json"
START_DATA, SIG_START, SIG_END = "20140101", "20160101", "20261113"
MAX_HOLD, COST, COST5 = 60, 0.003, 0.005
GEM = ("300", "301", "302")
EXCL = ("4", "8", "9", "68")
OUT1 = r"D:\zcodesj\reports\trend_system_bt_20160101_20261113.json"
OUT2 = r"D:\zcodesj\reports\trend_system_bt_20160101_20261113_run2.json"


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
    """R[t]=arr[t-w..t-1]的极值(不含当前bar); 窗口有效值<w时=None。O(n)单调队列。"""
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
            R[t] = dq[0][1]
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
    sec_map = {cd[-6:]: sn for cd, sn in m.execute("SELECT code,sector_name FROM stock_sector_sw1")}
    irows = m.execute("SELECT date,close FROM index_daily_meta WHERE code='000001' AND date>=? ORDER BY date",
                      ("20140601",)).fetchall()
    m.close()
    ic = [r[1] for r in irows]
    idt = [int(r[0]) for r in irows]
    idx_state = {idt[i]: ic[i] > sum(ic[i - 60:i]) / 60.0 for i in range(60, len(ic))}

    is_st = st_loader()
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)

    # ---- 第一遍: 板块聚合 + 全池基准 ----
    sec = defaultdict(lambda: [0.0, 0, 0.0, 0, 0, 0])  # (板块,日期)->[sum_ret,n_ret,sum_vr,n_vr,n_trend,n_data]
    bench = defaultdict(lambda: [0.0, 0])
    cur = con.execute("SELECT code,date,close,volume FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    ncode = 0
    for code, grp in ((k, list(g)) for k, g in __import__("itertools").groupby(cur, key=lambda r: r[0])):
        ncode += 1
        if ncode % 800 == 0:
            print("pass1 %d码 %.0fs" % (ncode, time.time() - t0))
        if code.startswith(EXCL):
            continue
        sector = sec_map.get(code)
        closes, vols, dates = [], [], []
        for _, d, c, v in grp:
            dates.append(int(d))
            closes.append(c)
            vols.append(v)
        # MA20
        ma20 = [None] * len(closes)
        s = 0.0
        for i, c in enumerate(closes):
            if c is None:
                ma20[i] = ma20[i - 1] if i else None
                continue
            s += c
            if i >= 20:
                pc = closes[i - 20]
                if pc is not None:
                    s -= pc
            if i >= 19:
                ma20[i] = s / 20.0
        for i in range(1, len(closes)):
            c = closes[i]
            pc = closes[i - 1]
            if c is None or pc is None or c <= 0 or pc <= 0:
                continue
            ret = c / pc - 1.0
            d = dates[i]
            if not is_st(code, d):
                bench[d][0] += ret
                bench[d][1] += 1
            if sector is None:
                continue
            sec[sector, d][0] += ret
            sec[sector, d][1] += 1
            sec[sector, d][5] += 1
            if ma20[i] is not None and ma20[i - 1] is not None:
                if c > ma20[i] and ma20[i] > ma20[i - 1]:
                    sec[sector, d][4] += 1
            w = vols[max(0, i - 4):i + 1]
            if vols[i] and all(x for x in w) and len(w) == 5:
                vr = vols[i] / (sum(w) / 5.0)
                sec[sector, d][2] += vr
                sec[sector, d][3] += 1

    # 板块状态表
    sec_ok = {}
    by_sec = defaultdict(list)
    for (sector, d) in sec:
        by_sec[sector].append(d)
    for sector, ds in by_sec.items():
        ds.sort()
        vals, cum = [], 1.0
        for d in ds:
            s, n = sec[sector, d][0], sec[sector, d][1]
            r = s / n if n else 0.0
            cum *= (1 + r)
            vals.append(cum)
        for j, d in enumerate(ds):
            if j < 20 or vals[j] is None:
                continue
            ma_prev = sum(vals[j - 20:j]) / 20.0
            rising = j >= 21 and sum(vals[j - 21:j - 1]) / 20.0 < ma_prev
            sret, nret, svr, nvr, ntrend, ndata = sec[sector, d]
            vr_mean = svr / nvr if nvr else None
            trend_ratio = ntrend / ndata if ndata else 0.0
            ok = (ma_prev is not None and vals[j] > ma_prev and rising
                  and vr_mean is not None and vr_mean >= 1.0
                  and ntrend >= 3 and trend_ratio > 0.2)
            sec_ok[(sector, d)] = ok
    del sec, by_sec

    # 基准累乘
    bd = sorted(bench)
    bcum, c = [], 1.0
    for d in bd:
        s, n = bench[d]
        c *= (1 + s / n if n else 0.0)
        bcum.append(c)
    del bench
    print("pass1完成 %.0fs 板块日状态%d 基准日%d" % (time.time() - t0, len(sec_ok), len(bd)))

    # ---- 第二遍: 信号+三臂+离场模拟 ----
    stats = {a: dict(sig=0, lskip=0, cfail=0, miss=0, traded=0) for a in "ABC"}
    trades = {"A": [], "B": [], "C": []}

    cur = con.execute("SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    ncode = 0
    for code, grp in ((k, list(g)) for k, g in __import__("itertools").groupby(cur, key=lambda r: r[0])):
        ncode += 1
        if ncode % 500 == 0:
            print("pass2 %d码 %d笔 %.0fs" % (ncode, stats["A"]["traded"] + stats["B"]["traded"] + stats["C"]["traded"], time.time() - t0))
        if code.startswith(EXCL):
            continue
        sector = sec_map.get(code)
        gem = code.startswith(GEM)
        thr = 0.198 if gem else 0.098
        bars = [(int(d), o, h, l, c, v) for _, d, o, h, l, c, v in grp]
        n = len(bars)
        if n < 80:
            continue
        cs = [b[4] for b in bars]
        vs = [b[5] for b in bars]
        ls = [b[3] for b in bars]
        ma5, ma10, ma20 = [None] * n, [None] * n, [None] * n
        s5 = s10 = s20 = 0.0
        for i in range(n):
            c = cs[i]
            if c is not None:
                s5 += c
                s10 += c
                s20 += c
                if i >= 5 and cs[i - 5] is not None:
                    s5 -= cs[i - 5]
                if i >= 10 and cs[i - 10] is not None:
                    s10 -= cs[i - 10]
                if i >= 20 and cs[i - 20] is not None:
                    s20 -= cs[i - 20]
            if i >= 4:
                ma5[i] = s5 / 5.0
            if i >= 9:
                ma10[i] = s10 / 10.0
            if i >= 19:
                ma20[i] = s20 / 20.0
        hi20 = prev_ext(cs, 20, "max")
        tlow = prev_ext(ls, 20, "min")
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
        # 结构关: 近60bar内存在历史20日新高
        newhi = [bool(hi20[i] is not None and cs[i] is not None and cs[i] > hi20[i]) for i in range(n)]
        struct = [False] * n
        cnt = 0
        for i in range(n):
            if i >= 60:
                if newhi[i - 1]:
                    cnt += 1
                if newhi[i - 60]:
                    cnt -= 1
                struct[i] = cnt >= 1
        for t in range(60, n):
            d = bars[t][0]
            if d < int(SIG_START) or d > int(SIG_END):
                continue
            c, o = cs[t], bars[t][1]
            if c is None or o is None or c <= 0:
                continue
            m5, m10, m20 = ma5[t], ma10[t], ma20[t]
            if m5 is None or m10 is None or m20 is None:
                continue
            p5, p10, p20 = ma5[t - 1], ma10[t - 1], ma20[t - 1]
            if p5 is None or p10 is None or p20 is None:
                continue
            if is_st(code, d):
                continue
            if not (2.0 <= c <= 30.0):
                continue
            ma_up = m5 > p5 and m10 > p10 and m20 > p20
            ma_bull = m5 > m10 > m20
            if not (ma_bull and ma_up and c > m20):
                continue
            # 量能关: 近5bar上涨日均量>=下跌日均量
            wv = vs[t - 4:t + 1]
            wc = cs[t - 4:t + 1]
            upv = dnv = 0.0
            nun = ndn = 0
            okvol = True
            for j in range(5):
                if wv[j] is None or wc[j] is None or (j and wc[j - 1] is None):
                    okvol = False
                    break
                if j == 0:
                    continue
                if wc[j] > wc[j - 1]:
                    upv += wv[j]
                    nun += 1
                elif wc[j] < wc[j - 1]:
                    dnv += wv[j]
                    ndn += 1
            if not okvol:
                continue
            if ndn and not nun:
                continue
            if nun and ndn and upv / nun < dnv / ndn:
                continue
            # 回调关
            low10 = ls[t - 9:t + 1]
            if any(x is None for x in low10) or m20 is None:
                continue
            if min(low10) <= m20:
                continue
            # 结构关
            if not struct[t]:
                continue
            # 共振③量 ④新高收阳 ②MACD
            vm = vs[t - 5:t]
            if any(x is None or x <= 0 for x in vm) or vs[t] is None:
                continue
            if vs[t] <= 1.3 * (sum(vm) / 5.0):
                continue
            if hi20[t] is None or c <= hi20[t] or c <= o:
                continue
            df = difs[t]
            dfa = deas[t]
            if df is None or dfa is None:
                continue
            if not (df > dfa and df > 0):
                continue
            stats["C"]["sig"] += 1
            cond_gates_sector = sector is not None and sec_ok.get((sector, d), False)
            sig_rec = (t, d, c, o)
            # C臂: D+1开盘
            if t + 1 < n and bars[t + 1][1] is not None:
                po = bars[t + 1][1]
                if not (po / c - 1 >= thr):
                    add_trade("C", bars, t + 1, po, code, d, gem, cs, tlow, bd, bcum, trades, stats, is_st)
                else:
                    stats["C"]["lskip"] += 1
            else:
                stats["C"]["miss"] += 1
            if not cond_gates_sector:
                continue
            stats["A"]["sig"] += 1
            stats["B"]["sig"] += 1
            if t + 1 < n and bars[t + 1][1] is not None:
                po = bars[t + 1][1]
                if not (po / c - 1 >= thr):
                    add_trade("B", bars, t + 1, po, code, d, gem, cs, tlow, bd, bcum, trades, stats, is_st)
                else:
                    stats["B"]["lskip"] += 1
            else:
                stats["B"]["miss"] += 1
            # A臂三三确认
            if t + 3 >= n or any(cs[t + k] is None for k in (1, 2, 3)):
                stats["A"]["cfail"] += 1
                continue
            P = c
            if not all(cs[t + k] >= P for k in (1, 2, 3)):
                stats["A"]["cfail"] += 1
                continue
            if max(cs[t + 1], cs[t + 2], cs[t + 3]) < P * 1.03:
                stats["A"]["cfail"] += 1
                continue
            if t + 4 >= n or bars[t + 4][1] is None:
                stats["A"]["miss"] += 1
                continue
            po = bars[t + 4][1]
            prev_c = cs[t + 3]
            if po / prev_c - 1 >= thr:
                stats["A"]["lskip"] += 1
                continue
            add_trade("A", bars, t + 4, po, code, d, gem, cs, tlow, bd, bcum, trades, stats, is_st)
    con.close()
    print("pass2完成 %.0fs" % (time.time() - t0))
    out = build_output(run_id, fp, fp_ok, stats, trades, bd, bcum, idx_state)
    path = OUT2 if run_id == "run2" else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", path)


def sim_exit(bars, ei, entry_price, tlow):
    """离场模拟: close触发条件->次日开盘卖; 触发后等待首个有开盘价的bar; 尾部按最后收盘强平。"""
    n = len(bars)
    pending = None  # (理由, 触发bar) 触发bar次日开盘卖出
    for k in range(ei + 1, n):
        d, o, h, l, cl, v = bars[k]
        if o is None:
            continue
        if pending is None:
            if cl is None:
                continue
            held = k - ei
            if cl <= entry_price * 0.92:
                pending = ("stop8", k)
            elif tlow[k] is not None and cl < tlow[k]:
                pending = ("trail20", k)
            elif held >= MAX_HOLD:
                pending = ("cap", k)
            if pending is None:
                continue
        if pending[1] < k:
            return d, o, pending[0], False, k - ei
    last = None
    for k in range(n - 1, ei, -1):
        if bars[k][4] is not None:
            last = k
            break
    if last is None:
        return None
    return bars[last][0], bars[last][4], "censored", True, last - ei


def add_trade(arm, bars, ei, entry_price, code, sig_d, gem, cs, tlow, bd, bcum, trades, stats, is_st):
    r = sim_exit(bars, ei, entry_price, tlow)
    if r is None:
        stats[arm]["miss"] += 1
        return
    xd, xo, reason, cens, held = r
    gross = xo / entry_price - 1.0
    net = gross - COST
    ie = bisect.bisect_left(bd, sig_d2(bars, ei))
    ie2 = bisect.bisect_left(bd, xd)
    bench_r = (bcum[ie2] / bcum[ie] - 1.0) if 0 <= ie < len(bd) and 0 <= ie2 < len(bcum) and ie2 >= ie else 0.0
    trades[arm].append({
        "code": code, "sig": str(sig_d), "entry": str(bars[ei][0]), "exit": str(xd),
        "year": str(sig_d)[:4], "net": round(net * 100, 4),
        "net5": round((gross - COST5) * 100, 4),
        "excess": round((net - bench_r) * 100, 4),
        "hold": held, "reason": reason, "censored": cens})


def sig_d2(bars, ei):
    return bars[ei][0]


def build_output(run_id, fp, fp_ok, stats, trades, bd, bcum, idx_state):
    def agg(ts):
        if not ts:
            return {"n": 0}
        nets = [t["net"] for t in ts]
        ex = [t["excess"] for t in ts]
        wins = [x for x in nets if x > 0]
        loss = [x for x in nets if x <= 0]
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(loss) / len(loss) if loss else 0.0
        return {"n": len(ts),
                "win_rate": round(len(wins) / len(ts), 4),
                "mean_net": round(sum(nets) / len(ts), 3),
                "median_net": round(sorted(nets)[len(nets) // 2], 3),
                "mean_excess": round(sum(ex) / len(ts), 3),
                "median_excess": round(sorted(ex)[len(ex) // 2], 3),
                "pl_ratio": round(abs(aw / al), 2) if al else None,
                "mean_hold": round(sum(t["hold"] for t in ts) / len(ts), 1),
                "censored": sum(1 for t in ts if t["censored"]),
                "reasons": {r: sum(1 for t in ts if t["reason"] == r) for r in ("stop8", "trail20", "cap", "censored")}}

    out = {"prereg_fp": fp, "fp_ok": fp_ok, "run_id": run_id,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "window": [SIG_START, SIG_END], "cost": [COST, COST5],
           "arm_stats": stats, "arms": {}}
    for arm in "ABC":
        ts = trades[arm]
        years = sorted({t["year"] for t in ts})
        ent_dates = defaultdict(list)
        for t in ts:
            ent_dates[t["entry"]].append(t["excess"] / 100.0)
        dm = [sum(v) / len(v) for v in ent_dates.values()]
        tstat, ndates = nw_t(dm)
        verdict = {}
        if arm == "A":
            mean_ex = agg(ts).get("mean_excess")
            p1 = mean_ex is not None and mean_ex > 0
            p2 = tstat is not None and tstat >= 2.0
            ys = {}
            for y in years:
                sub = [t for t in ts if t["year"] == y]
                if len(sub) >= 30:
                    ys[y] = sum(t["excess"] for t in sub) / len(sub)
            pos = [y for y in ys if ys[y] > 0]
            p3 = bool(ys) and len(pos) / len(ys) >= 0.5
            verdict = {"P1_excess_gt0": p1, "P2_nw5_t": None if tstat is None else round(tstat, 2),
                       "P2_dates": ndates, "P2_pass": p2,
                       "yearly_excess": {y: round(v, 3) for y, v in ys.items()},
                       "P3_pos_years": "%d/%d" % (len(pos), len(ys)),
                       "P3_pass": p3,
                       "verdict": "PASS" if (p1 and p2 and p3) else "FAIL"}
        out["arms"][arm] = {
            "overall": agg(ts),
            "by_year": {y: {k: agg([t for t in ts if t["year"] == y])[k]
                            for k in ("n", "mean_net", "mean_excess", "win_rate")} for y in years},
            "by_idx": {"above_ma60": agg([t for t in ts if idx_state.get(int(t["sig"])) is True]),
                       "below_ma60": agg([t for t in ts if idx_state.get(int(t["sig"])) is False])},
            "verdict": verdict}
    return out


if __name__ == "__main__":
    rid = sys.argv[1] if len(sys.argv) > 1 else "run1"
    main(rid)
