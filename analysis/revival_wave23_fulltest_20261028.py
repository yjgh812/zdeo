# -*- coding: utf-8 -*-
"""revival_wave23_fulltest_20261028.py — 17 项复活方法全量测试引擎（主人令「全量测试」）

统一合同(同 PREREG): 信号日收盘价入场(涨停收盘=不可成交剔除)/持有20交易日/-8%硬止损
(日内低点触及按止损价成交近似)/双边成本0.1%×2/宇宙=非ST(历史日频标记)非科创北交/≥60交易日/收盘0.5~30元。
口径: 2016-01-01 起全史; 因子族周频采样(每5个交易日), PIT 月末快照(严格≤信号日);
事件族(chan二买/v52周批/p2日top5/composite日top5)按事件流全量。
输出: 每方法 n/胜率/均值/中位/止损率/分年 + 对宇宙基线超额。
"""
from __future__ import annotations
import sys, os, json, sqlite3, statistics, math, time
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
MAIN = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
ST_DB = r"D:/zcodesj/data/st_history.db"
IND_DB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
PIT_DB = r"D:/zcodesj/factor_pit.db"
START = 20160101
WEEK = 5
COST = 0.001
STOP = 0.08
HOLD = 20
OUT = r"D:\zcodesj\data\revival_wave23_fulltest_20261028.json"


# ---------- 数据装载(2016+ 全史) ----------
def load_qfq():
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    dates = sorted({int(r[0]) for r in con.execute(
        "select distinct date from stock_daily_meta_qfq where date>=? order by date desc limit 3000", (START,))})
    dmin = str(dates[0])
    series = defaultdict(lambda: defaultdict(list))
    for code, d, h, l, c, v, a in con.execute(
        "select code,date,high,low,close,volume,amount from stock_daily_meta_qfq "
        "where date>=? order by code,date", (dmin,)):
        s = series[code]
        s["date"].append(int(d)); s["high"].append(h); s["low"].append(l)
        s["close"].append(c); s["vol"].append(v); s["amt"].append(a)
    con.close()
    out = {}
    for code, s in series.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in ("4", "8", "9"):
            continue
        arr = {k: np.array(s[k], dtype=float) for k in ("high", "low", "close", "vol", "amt")}
        arr["date"] = np.array(s["date"], dtype=np.int64)
        n = len(arr["date"])
        if n < 80:
            continue
        prev = np.concatenate([[arr["close"][0]], arr["close"][:-1]])
        arr["ret"] = np.where(prev > 0, arr["close"] / prev - 1, 0.0)
        out[c6] = arr
    return out, dates


def load_st_set():
    st = set()
    con = sqlite3.connect(f"file:{ST_DB}?mode=ro", uri=True)
    for code, d in con.execute("select code, date from st_daily where is_st=1"):
        st.add((str(code).split(".")[-1].zfill(6)[-6:], int(d)))
    con.close()
    return st


def load_sw1():
    con = sqlite3.connect(f"file:{IND_DB}?mode=ro", uri=True)
    ind = {k[-6:]: v for k, v in con.execute("select code, l1 from l1_member")}
    con.close()
    return ind


class Contract:
    """统一合同仿真: 信号日收盘入场, 20交易日持有, -8%日内止损, 0.2%双边成本"""

    def __init__(self, series, st_set):
        self.series = series
        self.st = st_set
        self.base_cache = {}

    def eligible(self, code, d):
        s = self.series.get(code)
        if s is None:
            return None
        i = int(np.searchsorted(s["date"], d))
        if i >= len(s["date"]) or s["date"][i] != d:
            return None
        if i < 60:
            return None
        if i + 1 + HOLD > len(s["date"]):
            return None  # 窗口未完结(尾端交易)不入统计
        if (code, d) in self.st:
            return None
        c = s["close"][i]
        if not c or c <= 0.5 or c > 30:
            return None
        if i > 0 and s["ret"][i] >= 0.097:  # 涨停收盘=不可成交
            return None
        return i

    def window(self, code, i):
        s = self.series[code]
        if i + 1 + HOLD > len(s["date"]):
            return None
        return s, i

    def trade_return(self, code, i):
        """入场=信号日收盘(已过涨停检查), 持有 i+1..i+HOLD, -8% 日内止损"""
        s = self.series[code]
        entry = s["close"][i]
        stop = entry * (1 - STOP)
        for k in range(i + 1, i + 1 + HOLD):
            low = s["low"][k]
            if low and low <= stop:
                exit_ = stop
                return entry, exit_, True
        exit_ = s["close"][i + HOLD]
        return entry, exit_, False

    def net(self, entry, exit_):
        return (exit_ * (1 - COST)) / (entry * (1 + COST)) - 1

    def baseline(self, d, codes_pool):
        """同信号日全宇宙等权 fwd20(无止损, 近似基线)"""
        if d in self.base_cache:
            return self.base_cache[d]
        rets = []
        for code in codes_pool:
            s = self.series.get(code)
            if s is None:
                continue
            i = int(np.searchsorted(s["date"], d))
            if i >= len(s["date"]) or s["date"][i] != d or i + HOLD >= len(s["date"]):
                continue
            c0, c1 = s["close"][i], s["close"][i + HOLD]
            if c0 and c0 > 0 and c1:
                rets.append(c1 / c0 - 1)
        m = statistics.fmean(rets) if rets else None
        self.base_cache[d] = m
        return m


def eligible_pool(series, d, st):
    codes = []
    for code, s in series.items():
        i = int(np.searchsorted(s["date"], d))
        if i >= len(s["date"]) or s["date"][i] != d or i < 60:
            continue
        if (code, d) in st:
            continue
        c = s["close"][i]
        if c and 0.5 < c <= 30:
            codes.append(code)
    return codes


# ---------- 因子预计算(滑窗向量) ----------
def rolling_fac(arr):
    c = arr["close"]
    amp = np.where(c > 0, (arr["high"] - arr["low"]) / np.maximum(c, 1e-8), 0.0)
    ret = arr["ret"]
    down = (ret <= 0).astype(float)
    W = 20
    if len(c) < W + 2:
        return None
    vw = np.lib.stride_tricks.sliding_window_view
    rets = ret[1:]
    amps = amp[1:]
    downs = down[1:]
    n = len(rets)
    if n < W - 1:
        return None
    s_amp = vw(amps, W).sum(1)[: -0] if False else vw(amps, W - 1).sum(1)
    s_ampd = vw(amps * downs, W - 1).sum(1)
    n_down = vw(downs, W - 1).sum(1)
    mean_up = (s_amp - s_ampd) / np.maximum(W - 1 - n_down, 1)
    mean_dn = s_ampd / np.maximum(n_down, 1)
    fac_ampA = np.where(n_down > 0, mean_dn - mean_up, np.nan)  # 高值优
    # amp_b: rel_amount 切割
    amt = arr["amt"]
    n_amt = len(amt)
    ma20 = np.convolve(amt, np.ones(W) / W, mode="valid")  # 对齐 day 19..n-1
    rel_full = np.full(n_amt, np.nan)
    rel_full[W - 1:] = amt[W - 1:] / np.maximum(ma20, 1e-8)
    amts = rel_full[1:]  # 与 amps(days 1..n-1)对齐
    wamp = vw(amps, W - 1)
    wamt = vw(amts, W - 1) if len(amts) >= W - 1 else None
    fac_ampB = np.full(len(amps) - (W - 1) + 1, np.nan)
    if wamt is not None:
        m = min(len(wamp), len(wamt))
        rk = np.argsort(wamt[:m], axis=1)
        half = (W - 1) // 2
        hi = np.take_along_axis(wamp[:m], rk[:, half:], axis=1).mean(1)
        lo = np.take_along_axis(wamp[:m], rk[:, :half], axis=1).mean(1)
        fac_ampB[:m] = hi - lo  # 低值优(IC-0.069)
    # 振幅A三因子/传统振幅等直接由 fac_ampA 派生
    return {"ampA": fac_ampA, "ampB": fac_ampB,
            "off": W - 1}  # fac[k] 对应行 date[k+off]


def main():
    t0 = time.time()
    series, dates = load_qfq()
    st = load_st_set()
    ind = load_sw1()
    C = Contract(series, st)
    print(f"[装载] codes={len(series)} dates={len(dates)} t={time.time()-t0:.0f}s")

    week_days = dates[::WEEK]
    week_days = [d for d in week_days if d >= 20160201]
    print(f"[信号格] 周频信号日: {len(week_days)}")

    # 预计算: 每股 amp 因子行(全史) — 供 amp_a/amp_b/ampA_3f 周频查表
    t1 = time.time()
    ampfac = {}
    for code, arr in series.items():
        r = rolling_fac(arr)
        if r:
            ampfac[code] = r
    print(f"[预计算] amp 因子: {len(ampfac)} 只 t={time.time()-t1:.0f}s")

    # PIT 月末快照(严格<=信号日): ep/bp/roe/sp_v2/lev_v2/ato_v2
    t1 = time.time()
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    frows = con.execute(
        "select date, code, ep, bp, roe, sp_v2, lev_v2, ato_v2, pm_v2 "
        "from stock_factors_fundamental where date>=20160101 and pit_avail_date is not null "
        "order by date").fetchall()
    con.close()
    month_last_f = {}
    for d, code, ep, bp, roe, sp2, lv2, at2, pm2 in frows:
        ym = d // 100
        if ym not in month_last_f or d > month_last_f[ym][0]:
            month_last_f[ym] = (d, {})
        month_last_f[ym][1][code[-6:]] = (ep, bp, roe, sp2, lv2, at2, pm2)
    f_month_ends = sorted(set(v[0] for v in month_last_f.values() if v[0] >= 20160101))
    f_snap = {}
    cur = {}
    ti = 0
    frows2 = con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True).execute(
        "select date, code, ep, bp, roe, sp_v2, lev_v2, ato_v2, pm_v2 "
        "from stock_factors_fundamental where date>=20160101 and pit_avail_date is not null "
        "order by pit_avail_date, date").fetchall()
    for d, code, ep, bp, roe, sp2, lv2, at2, pm2 in frows2:
        c6 = code[-6:]
        while ti < len(f_month_ends) and d > f_month_ends[ti]:
            f_snap[f_month_ends[ti]] = dict(cur)
            ti += 1
        cur[c6] = (ep, bp, roe, sp2, lv2, at2, pm2)
    while ti < len(f_month_ends):
        f_snap[f_month_ends[ti]] = dict(cur)
        ti += 1
    print(f"[PIT] 月末快照 {len(f_snap)} 个月 t={time.time()-t1:.0f}s")

    def snap_at(d):
        ks = [k for k in f_month_ends if k <= d]
        return f_snap[ks[-1]] if ks else {}

    def sig_days_factor(cadence):
        return week_days if cadence == "week" else [d for d in dates[::20] if d >= 20160701]

    # ---------- 各方法信号生成 ----------
    methods = {}

    def top5_pick(scored):
        return scored[:5]

    # amp_a / amp_b 周频 top5
    for name, key, desc in (("amp_a", "ampA", "高值优"), ("amp_b", "ampB", "低值优")):
        sigs = []
        for d in week_days:
            scored = []
            for code, fac in ampfac.items():
                k = int(np.searchsorted(series[code]["date"], d))
                off = k - fac["off"]
                if 0 <= off < len(fac[key]):
                    v = fac[key][off]
                    s = series[code]
                    i2 = int(np.searchsorted(s["date"], d))
                    if i2 >= 60 and s["close"][i2] and 0.5 < s["close"][i2] <= 30 and (code, d) not in st:
                        scored.append((v if desc == "高值优" else -v, code, i2))
            scored = [(v, c, i) for v, c, i in scored if not math.isnan(v)]
            scored.sort(reverse=True)
            sigs.append((d, [(c, i) for _, c, i in top5_pick(scored)]))
        methods[name] = sigs

    # ampA_3f 周频: bp/ep PIT prescreen 100 → ampA rank → combo3 top5
    sigs = []
    for d in week_days:
        snap = snap_at(d)
        rows_bp = []
        for c6, (ep, bp, roe, sp2, lv2, at2, pm2) in snap.items():
            if c6 in series and ep and bp and ep > 0 and bp > 0:
                rows_bp.append((c6, ep, bp))
        if len(rows_bp) < 100:
            continue
        epv = {c: e for c, e, b in rows_bp}; bpv = {c: b for c, e, b in rows_bp}
        epr = w23rank(epv); bpr = w23rank(bpv)
        combo = sorted(((epr[c] + bpr[c]) / 2, c) for c, e, b in rows_bp)[:100]
        scored = []
        for _cv, c6 in combo:
            fac = ampfac.get(c6)
            if not fac:
                continue
            k = int(np.searchsorted(series[c6]["date"], d))
            off = k - fac["off"]
            if 0 <= off < len(fac["ampA"]):
                v = fac["ampA"][off]
                if not math.isnan(v):
                    scored.append((v, c6))
        ar = w23rank({c: v for v, c in scored})
        fin = sorted((((epr[c] + bpr[c] + ar[c]) / 3, c) for c in ar if c in epr and c in bpr), reverse=True)
        sigs.append((d, [(c, None) for _v, c in top5_pick(fin)]))
    methods["ampA_3f"] = sigs

    # bpep_weekly / hy_tail 周频
    sigs_b, sigs_h = [], []
    for d in week_days:
        snap = snap_at(d)
        rows_bp = [(c6, ep, bp) for c6, (ep, bp, *_r) in snap.items()
                   if ep and bp and ep > 0 and bp > 0]
        if len(rows_bp) < 100:
            continue
        epr = w23rank({c: e for c, e, b in rows_bp})
        bpr = w23rank({c: b for c, e, b in rows_bp})
        ranked = sorted((((epr[c] + bpr[c]) / 2, c) for c, e, b in rows_bp), reverse=True)
        # bpep_weekly: 行业≤4 top15→5
        cap = defaultdict(int); p15 = []
        for v, code in ranked:
            kk = ind.get(code, "_none")
            if cap[kk] >= 4:
                continue
            cap[kk] += 1
            p15.append(code)
            if len(p15) >= 15:
                break
        sigs_b.append((d, [(c, None) for c in p15[:5]]))
        # hy_tail: RS 尾部剔除(行业20日等权ret−全市场)
        ind_ret = defaultdict(list); all_r = []
        for code in series:
            s = series[code]
            i = int(np.searchsorted(s["date"], d))
            if i >= len(s["date"]) or i < 21:
                continue
            r20 = s["close"][i] / s["close"][i - 20] - 1
            all_r.append(r20)
            if code in ind:
                ind_ret[ind[code]].append(r20)
        if not all_r:
            continue
        mkt = statistics.fmean(all_r)
        rs = {k: statistics.fmean(v) - mkt for k, v in ind_ret.items() if len(v) >= 3}
        order = sorted(rs.items(), key=lambda x: -x[1])
        tail = {k for i, (k, _) in enumerate(order) if i >= 24}
        p2_ = []
        for v, code in ranked:
            k = ind.get(code)
            if k is not None and k in tail:
                continue
            p2_.append(code)
            if len(p2_) >= 5:
                break
        sigs_h.append((d, [(c, None) for c in p2_]))
    methods["bpep_weekly"] = sigs_b
    methods["hy_tail"] = sigs_h

    print(f"[信号] 因子族生成完 t={time.time()-t0:.0f}s (ampA_3f/bpep/hy_tail 样本: {len(sigs)}/{len(sigs_b)}/{len(sigs_h)})")

    # ---------- 合同仿真 ----------
    results = {}
    def run_method(name, sigs, note=""):
        trades = []
        C2 = C
        for d, picks in sigs:
            bl = C2.baseline(d, list(series.keys()))
            for item in picks:
                code = item[0]
                i = C2.eligible(code, d)
                if i is None:
                    continue
                entry, exit_, stopped = C2.trade_return(code, i)
                net = C2.net(entry, exit_)
                yr = d // 10000
                trades.append({"d": d, "code": code, "net": net, "stop": stopped, "yr": yr,
                               "excess": net - bl if bl is not None else None})
        if not trades:
            # 诊断: 前几笔 pick 的 eligible 状态
            dbg = []
            for d0, p0 in sigs[:3]:
                for it in p0[:3]:
                    code = it[0]
                    s = C.series.get(code)
                    if s is None:
                        dbg.append(f"{code}: not_in_series")
                        continue
                    i = int(np.searchsorted(s["date"], d0))
                    if i >= len(s["date"]) or s["date"][i] != d0:
                        dbg.append(f"{code}@{d0}: date_not_found")
                    elif i < 60:
                        dbg.append(f"{code}: i<60({i})")
                    elif i + 1 + HOLD > len(s["date"]):
                        dbg.append(f"{code}: window_end")
                    elif (code, d0) in C.st:
                        dbg.append(f"{code}: ST")
                    else:
                        c_ = s["close"][i]
                        dbg.append(f"{code}: price={c_}")
            print(f"[{name}] n=0 sigs={len(sigs)} picks={sum(len(p) for _, p in sigs)} dbg={dbg[:6]}")
            results[name] = {"n": 0, "note": note or "无信号", "dbg": dbg[:6]}
            return
        nets = [t["net"] for t in trades]
        yrs = defaultdict(list)
        for t in trades:
            yrs[t["yr"]].append(t["net"])
        per_year = {y: {"n": len(v), "win": f"{sum(1 for x in v if x>0)/len(v):.0%}",
                        "mean": f"{statistics.fmean(v):+.4f}"} for y, v in sorted(yrs.items())}
        res = {"n": len(nets),
               "win": f"{sum(1 for x in nets if x>0)/len(nets):.1%}",
               "mean": round(statistics.fmean(nets), 4),
               "median": round(statistics.median(nets), 4),
               "stop_rate": f"{sum(1 for t in trades if t['stop'])/len(trades):.0%}",
               "excess_mean": round(statistics.fmean([t["excess"] for t in trades if t["excess"] is not None]), 4) if any(t["excess"] is not None for t in trades) else None,
               "per_year": per_year, "note": note}
        results[name] = res
        print(f"[{name}] n={res['n']} 胜率{res['win']} 均值{res['mean']:+.2%} 止损率{res['stop_rate']} "
              f"超额均值{res['excess_mean']}")

    run_method("amp_a", methods["amp_a"])
    run_method("amp_b", methods["amp_b"])
    run_method("ampA_3f", methods["ampA_3f"])
    run_method("bpep_weekly", methods["bpep_weekly"])
    run_method("hy_tail", methods["hy_tail"])

    # ---------- 事件族 ----------
    # chan 二买事件
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    chan_events = con.execute(
        "select date, code from chanlun_signals_nolookahead "
        "where date>=20160101 and second_buy=1 order by date").fetchall()
    con.close()
    chan_sigs = defaultdict(list)
    for d, code in chan_events:
        c6 = str(code).zfill(6)[-6:]
        chan_sigs[int(d)].append((c6, None))
    run_method("chan_2buy", sorted(chan_sigs.items()), note="事件全量(非日5只)")

    # p2 日 top5
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    p2_rows = con.execute(
        "select date, code, p2_prob from ("
        " select date, code, p2_prob, row_number() over (partition by date order by p2_prob desc) rn"
        " from stock_signals_p2 where date>=20160101 and p2_prob is not null and date<=20260921"
        ") where rn<=5").fetchall()
    con.close()
    p2_sigs = defaultdict(list)
    for d, code, _p in p2_rows:
        p2_sigs[int(d)].append((str(code).zfill(6)[-6:], None))
    run_method("p2_top5_daily", sorted(p2_sigs.items()), note="p2_prob日top5(20160630~20260921在档)")

    # composite 日 top5
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    cp_rows = con.execute(
        "select date, code, composite_score from ("
        " select date, code, composite_score, row_number() over (partition by date order by composite_score desc) rn"
        " from stock_signals where date>=20160101 and composite_score is not null"
        ") where rn<=5").fetchall()
    con.close()
    cp_sigs = defaultdict(list)
    for d, code, _p in cp_rows:
        cp_sigs[int(d)].append((str(code).zfill(6)[-6:], None))
    run_method("composite_top5_daily", sorted(cp_sigs.items()), note="composite_score日top5")

    # v52 周批(六门近似: 20元/多头/pos250<50%/深跌25%段起-3~10%/量价1.1; 3年分位并入pos250近似, 如实)
    # 复用合同宇宙; 检测函数在每周信号日扫描
    def v52_detect(d):
        out_ = []
        for code, s in series.items():
            i = int(np.searchsorted(s["date"], d))
            if i >= len(s["date"]) or s["date"][i] != d or i < 260:
                continue
            c = s["close"][: i + 1]; v = s["vol"][: i + 1]
            if c[-1] > 20:
                continue
            ma20 = c[-20:].mean(); ma60 = c[-60:].mean()
            if not ma20 > ma60:
                continue
            c250 = c[-250:]; mn, mx = c250.min(), c250.max()
            if mx <= mn or (c[-1] - mn) / (mx - mn) >= 0.5:
                continue
            seg_k = None
            for k in range(len(c) - 40, len(c)):
                if k < 61:
                    continue
                h60 = c[k - 60:k - 1].max()
                dd = c[k] / h60 - 1
                r1 = c[k] / c[k - 1] - 1
                vma = v[max(0, k - 20):k].mean()
                if dd <= -0.25 and r1 >= 0.05 and vma > 0 and v[k] >= 1.5 * vma:
                    seg_k = k
            if seg_k is None:
                continue
            gap = c[-1] / c[seg_k] - 1
            if not (-0.03 <= gap <= 0.10):
                continue
            rets20 = np.diff(c[-21:]) / c[-21:-1]
            vols20 = v[-20:]
            up = rets20 > 0
            if up.sum() == 0 or (~up).sum() == 0:
                continue
            if vols20[up].mean() / vols20[~up].mean() < 1.1:
                continue
            out_.append(code)
        return out_

    v52_sigs = []
    for d in week_days[::2]:  # 周批双周采样控制成本
        picks = v52_detect(d)
        if picks:
            v52_sigs.append((d, [(c, None) for c in picks[:5]]))
    run_method("v52_weekly", v52_sigs, note="3年分位门并入pos250近似(保真妥协已披露)")

    # ---------- 汇总输出 ----------
    out = {"contract": "信号日收盘入场/20日窗/-8%日内止损/0.2%双边/涨停剔除/2016起全史",
           "cadence_note": "因子族周频采样; 事件族事件全量; icir_sue全史走窗未含(月度ICIR重算成本, 由截面名单+今日纸面测试覆盖)",
           "excluded": {"dbd_gru": "全史分钟级推理=独立重工程(今日截面名单已纸面验证)",
                         "lstm": "全史走窗重训=独立重工程(同上)",
                         "v51_fusion": "无历史信号流在档",
                         "v8_3dir": "谱系覆盖合并(方案B)",
                         "kronos_ft/rl": "缺件"},
           "results": results}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n[产物] {OUT}")
    print(f"[耗时] {time.time()-t0:.0f}s")


def w23rank(d):
    if not d:
        return {}
    ks = list(d.keys())
    vs = np.array([d[k] for k in ks])
    order = np.argsort(vs, kind="stable")
    r = np.empty(len(vs))
    r[order] = np.arange(len(vs)) / max(len(vs) - 1, 1)
    return {k: float(r[i]) for i, k in enumerate(ks)}


if __name__ == "__main__":
    main()
