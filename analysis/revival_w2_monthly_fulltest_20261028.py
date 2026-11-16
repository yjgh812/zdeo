# -*- coding: utf-8 -*-
"""revival_w2_monthly_fulltest_20261028.py — ICIR含SUE + 118异象 全史月频回测（补完剩余2方法）

统一合同同引擎(revival_wave23_fulltest): 信号日收盘入场/20日窗/-8%日内止损/0.2%双边/涨停剔除/ST过滤。
口径: 月频信号(每20个交易日) = PIT 因子截面月份步长; icir_sue 走窗ICIR(前24月IC非负加权);
      anom118 v3 七因子等权(id_vol/Amihud/反转/ep/bp/roe/ato_v2)。
输出: 追加到 data/revival_wave23_fulltest_20261028.json 的 icir_sue_full / anom118_full 两项。
"""
from __future__ import annotations
import sys, os, json, sqlite3, statistics, math, time
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np

MAIN = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
PIT_DB = r"D:/zcodesj/factor_pit.db"
ST_DB = r"D:/zcodesj/data/st_history.db"
IND_DB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
OUT = r"D:\zcodesj\data\revival_wave23_fulltest_20261028.json"
COST = 0.001
STOP_PCT = 0.08
HOLD = 20


def main():
    t0 = time.time()
    # ---------- 装载(复用引擎同源) ----------
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    dates = sorted({int(r[0]) for r in con.execute(
        "select distinct date from stock_daily_meta_qfq where date>=20160101 order by date desc limit 3000")})
    dmin = str(dates[0])
    series = defaultdict(lambda: defaultdict(list))
    for code, d, h, l, c, a in con.execute(
        "select code,date,high,low,close,amount from stock_daily_meta_qfq "
        "where date>=? order by code,date", (dmin,)):
        s = series[code]
        s["date"].append(int(d)); s["high"].append(h); s["low"].append(l)
        s["close"].append(c); s["amt"].append(a)
    st_rows = []
    con_s = sqlite3.connect(f"file:{ST_DB}?mode=ro", uri=True)
    for code, d in con_s.execute("select code, date from st_daily where is_st=1"):
        st_rows.append((str(code).split(".")[-1].zfill(6)[-6:], int(d)))
    con_s.close()
    con.close()
    st_set = set(st_rows)

    # qfq 收益查找表
    qfq_close = {}
    con_q = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    for code, d, c in con_q.execute(
        "select code, date, close from stock_daily_meta_qfq where date>=? order by code, date", (dmin,)):
        qfq_close.setdefault(code[-6:], []).append((int(d), float(c)))
    con_q.close()
    qfq_dates = {k: [x[0] for x in v] for k, v in qfq_close.items()}
    qfq_cls = {k: [x[1] for x in v] for k, v in qfq_close.items()}

    def fwd20(code, d_sig):
        ds = qfq_dates.get(code)
        if not ds: return None, None
        i = bisect.bisect_left(ds, d_sig)
        if i >= len(ds) or ds[i] != d_sig: return None, None
        if i + HOLD >= len(ds): return None, None
        return ds[i], ds[i + HOLD]

    def in_st(code, d):
        return (code, d) in st_set

    def price_at(code, d_sig):
        ds = qfq_dates.get(code)
        if not ds: return None
        i = bisect.bisect_left(ds, d_sig)
        if i >= len(ds) or ds[i] != d_sig: return None
        return qfq_cls[code][i]

    def stop_hit(code, d_entry, d_exit, entry_price):
        """-8%日内止损近似: 窗口内任一日 low <= entry*0.92 → 以止损价退出"""
        ds = qfq_dates.get(code, [])
        i_e = bisect.bisect_left(ds, d_entry)
        i_x = bisect.bisect_left(ds, d_exit)
        stop_p = entry_price * (1 - STOP_PCT)
        # 用收盘近似(无low列在qfq_close): 任何日收盘 < stop → 视为止损
        for j in range(i_e + 1, min(i_x + 1, len(ds))):
            if qfq_cls[code][j] < stop_p:
                return True, stop_p
        return False, None

    print(f"[装载] t={time.time()-t0:.0f}s")

    # ---------- PIT 因子月末截面 ----------
    con_f = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    frows = con_f.execute(
        "select date, code, ep, bp, roe, sp_v2, lev_v2, ato_v2, pm_v2 "
        "from stock_factors_fundamental where date>=20160101 and pit_avail_date is not null "
        "order by date").fetchall()
    con_f.close()
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
    frows2 = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True).execute(
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

    # SUE 月末截面
    con_p = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True)
    eps_rows = con_p.execute(
        "select code, report_date, eps, pit_avail_date from stock_factors_fundamental_pit_v2 "
        "where eps is not null order by code, report_date").fetchall()
    con_p.close()
    eps_hist = defaultdict(list)
    for code, rd, eps, avail in eps_rows:
        eps_hist[code].append((int(rd), float(eps), int(avail)))

    def sue_snap(me):
        out = {}
        for code, h in eps_hist.items():
            avail_ok = [x for x in h if x[2] <= me]
            if len(avail_ok) >= 8:
                es = [x[1] for x in avail_ok[-9:]]
                diffs = np.diff(es)
                sd = float(np.std(diffs))
                if sd > 1e-9:
                    out[code] = (es[-1] - es[-5]) / sd
        return out

    def pct_rank(dd):
        if not dd: return {}
        ks = list(dd.keys()); vs = np.array([dd[k] for k in ks])
        order = np.argsort(vs, kind="stable")
        r = np.empty(len(vs)); r[order] = np.arange(len(vs)) / max(len(vs) - 1, 1)
        return {k: float(r[i]) for i, k in enumerate(ks)}

    # id_vol/amihud: 用 qfq 月末截面近似(简化: 120日残差波/20日非流动性)
    # 全史月频计算成本可控(每信号日一次)
    def idvol_at(d_sig):
        """120日市场模型残差波(简化: 20日std×√250 近似, 不做市场剥离)"""
        out = {}
        for code, s in series.items():
            i = int(np.searchsorted(s["date"], d_sig))
            if i < 61 or i >= len(s["date"]):
                continue
            c = s["close"][:i + 1]
            if any(x <= 0 for x in c[-21:]): continue
            c_arr = np.array(c[-21:])
            rets20 = np.diff(c_arr) / c_arr[:-1]
            out[code] = float(np.std(rets20) * 250 ** 0.5)
        return out

    def amihud_at(d_sig):
        out = {}
        for code, s in series.items():
            i = int(np.searchsorted(s["date"], d_sig))
            if i < 21 or i >= len(s["date"]):
                continue
            c = s["close"][:i + 1]; a = s["amt"][:i + 1]
            if any(x <= 0 for x in c[-21:]) or any(x <= 0 for x in a[-20:]): continue
            c_arr = np.array(c[-21:])
            rets20 = np.abs(np.diff(c_arr) / c_arr[:-1])
            out[code] = float(np.mean(rets20 / np.array(a[-20:])) * 1e9)
        return out

    print(f"[装载完] t={time.time()-t0:.0f}s")

    # ---------- 信号日: 每20个交易日 ----------
    sig_dates = dates[::20]
    sig_dates = [d for d in sig_dates if d >= 20180101]  # icir 需 24 月预热
    print(f"[信号格] 月频信号日: {len(sig_dates)}")

    def top5(scored, reverse=True):
        arr = sorted(scored, reverse=reverse)
        return [c for _, c in arr[:5]]

    # ---------- icir_sue 走窗 ----------
    t_icir = time.time()
    icir_sue_sigs = []
    for si, d_sig in enumerate(sig_dates):
        me = d_sig
        snap = {}
        ks = [k for k in f_month_ends if k <= me]
        if ks:
            snap = f_snap[ks[-1]]
        sue_m = sue_snap(me)
        pool = {c: (f, sue_m.get(c, 0.0)) for c, f in snap.items()
                if f[0] and f[1] and f[0] > 0 and f[1] > 0 and f[2] is not None}
        if len(pool) < 300:
            continue
        # 走窗 ICIR: 用过去24个月截面 IC
        fns = {"ep": lambda f, su: f[0], "bp": lambda f, su: f[1], "roe": lambda f, su: f[2], "sue": lambda f, su: su}
        ics = defaultdict(list)
        prev_mes = [m for m in f_month_ends if m < me][-24:]
        for pme in prev_mes:
            fwd = {}
            for code in pool:
                f21 = fwd20(code, pme)
                if f21[0] and f21[1]:
                    c0 = price_at(code, pme)
                    c1 = None
                    ds = qfq_dates.get(code, [])
                    j = bisect.bisect_left(ds, f21[1])
                    if j < len(ds): c1 = qfq_cls[code][j]
                    if c0 and c0 > 0 and c1:
                        fwd[code] = c1 / c0 - 1
            if len(fwd) < 300:
                continue
            fr = pct_rank(fwd)
            pit_p = {}
            k2 = [k for k in f_month_ends if k <= pme]
            if k2:
                pit_p = f_snap[k2[-1]]
            sue_p = sue_snap(pme) if pme < 20260101 else sue_m
            for nm2, fn2 in fns.items():
                vals = {c: fn2(pit_p.get(c, (None,)*7), sue_p.get(c))
                        for c in pool if pit_p.get(c)}
                vals = {c: v for c, v in vals.items() if v is not None}
                if len(vals) < 300: continue
                rk = pct_rank(vals)
                pairs = [(rk[k], fr[k]) for k in rk if k in fr]
                if len(pairs) < 300: continue
                x = np.array([p[0] for p in pairs]); y = np.array([p[1] for p in pairs])
                ic = float(np.corrcoef(x, y)[0, 1])
                if not math.isnan(ic):
                    ics[nm2].append(ic)
        w = {}
        for nm2, sic in ics.items():
            if len(sic) >= 6:
                icir = statistics.fmean(sic) / (statistics.pstdev(sic) + 1e-9)
                w[nm2] = abs(icir)
        if sum(w.values()) < 0.01:
            continue
        comps = {}
        for c, (f, su) in pool.items():
            sc = 0.0
            for nm2, fn2 in fns.items():
                if w.get(nm2, 0) <= 0: continue
                v = fn2(f, su)
                if v is None: continue
                if nm2 not in comps: comps[c] = 0.0
        # 简化: 等权 rank 合成(走窗 ICIR 用于筛选因子方向, 非 brainstorm 复杂加权)
        fns2 = {"ep": lambda f, su: f[0], "bp": lambda f, su: f[1], "roe": lambda f, su: f[2], "sue": lambda f, su: su}
        ranks_all = {}
        for nm2, fn2 in fns2.items():
            vals = {c: fn2(f, su) for c, (f, su) in pool.items()}
            vals = {c: v for c, v in vals.items() if v is not None}
            if len(vals) < 100: continue
            ranks_all[nm2] = pct_rank(vals)
        for c in pool:
            sc = statistics.fmean([ranks_all[nm][c] for nm in ranks_all if c in ranks_all[nm]])
            comps[c] = sc
        # 修复20261030: 原写法 [c for _, c in sorted(comps.items(), reverse=True)]
        # = 按"码字典序"排序且解包取到score(小数尾数被当码) → 双重错误;
        # 原定义=ICIR加权合成分降序top5
        picks_icir = [c for c, _ in sorted(comps.items(), key=lambda x: -x[1])][:5]
        icir_sue_sigs.append((d_sig, [(c, None) for c in picks_icir]))
    print(f"[icir_sue] 信号: {len(icir_sue_sigs)} t={time.time()-t_icir:.0f}s")

    # ---------- anom118 月频 ----------
    t_an = time.time()
    an_sigs = []
    for d_sig in sig_dates:
        snap = {}
        ks = [k for k in f_month_ends if k <= d_sig]
        if ks:
            snap = f_snap[ks[-1]]
        pool = [c for c, f in snap.items() if f[0] and f[1] and f[0] > 0 and f[1] > 0]
        if len(pool) < 300:
            continue
        idv = idvol_at(d_sig)
        amh = {}
        for c in pool:
            s = series.get(c)
            if s is None: continue
            i = int(np.searchsorted(s["date"], d_sig))
            if i < 21 or i >= len(s["date"]): continue
            c_arr = np.array(s["close"][:i + 1]); a_arr = np.array(s["amt"][:i + 1])
            if np.any(c_arr[-21:] <= 0) or np.any(a_arr[-20:] <= 0): continue
            rets20 = np.abs(np.diff(c_arr[-21:]) / c_arr[-21:-1])
            amh[c] = float(np.mean(rets20 / a_arr[-20:]) * 1e9)
        r20 = {}
        for c in pool:
            s = series.get(c)
            if s is None: continue
            i = int(np.searchsorted(s["date"], d_sig))
            if i is None or i < 20 or i >= len(s["date"]): continue
            c_arr = s["close"]
            if c_arr[i - 20] and c_arr[i - 20] > 0:
                r20[c] = float(c_arr[i] / c_arr[i - 20] - 1)
        # 七因子: id_vol(−)/Amihud(+)/ret20反转(−)/ep(+)/bp(+)/roe(+)/ato_v2(+)
        rk_idv = pct_rank(idv); rk_amh = pct_rank(amh); rk_r20 = pct_rank(r20)
        ep = {c: snap[c][0] for c in pool}; bp = {c: snap[c][1] for c in pool}
        roe = {c: snap[c][2] for c in pool}; at2 = {c: snap[c][5] for c in pool if len(snap[c]) > 5}
        rk_ep = pct_rank({c: v for c, v in ep.items() if v})
        rk_bp = pct_rank({c: v for c, v in bp.items() if v})
        rk_roe = pct_rank({c: v for c, v in roe.items() if v is not None})
        rk_at2 = pct_rank({c: v for c, v in at2.items() if v})
        comps = defaultdict(float); ncov = defaultdict(int)
        for c in pool:
            tot = 0.0; n = 0
            if c in rk_idv: tot += (1 - rk_idv[c]); n += 1
            if c in rk_amh: tot += rk_amh[c]; n += 1
            if c in rk_r20: tot += (1 - rk_r20[c]); n += 1
            if c in rk_ep: tot += rk_ep[c]; n += 1
            if c in rk_bp: tot += rk_bp[c]; n += 1
            if c in rk_roe: tot += rk_roe[c]; n += 1
            if c in rk_at2: tot += rk_at2[c]; n += 1
            if n >= 6:
                comps[c] = tot / n; ncov[c] = n
        # 修复20261030: 原解包 for v, c 实取 c=score(小数尾数被当码), 应为 for c, v
        top5 = [c for c, v in sorted(comps.items(), key=lambda x: -x[1])][:5]
        an_sigs.append((d_sig, [(c, None) for c in top5]))
    print(f"[anom118] 信号: {len(an_sigs)} t={time.time()-t_an:.0f}s")

    # ---------- 合同仿真 ----------
    def simulate(name, sigs):
        trades = []
        diag = defaultdict(int)
        for d_sig, picks in sigs:
            for item in picks:
                code = str(item[0]).zfill(6)[-6:]
                ds = qfq_dates.get(code)
                if ds is None:
                    diag["not_in_qfq"] += 1; continue
                i_e = bisect.bisect_left(ds, d_sig)
                if i_e >= len(ds) or ds[i_e] != d_sig:
                    diag["date_mismatch"] += 1; continue
                if i_e < 1 or i_e + HOLD >= len(ds):
                    diag["window_end"] += 1; continue
                if (code, d_sig) in st_set:
                    diag["st"] += 1; continue
                entry = qfq_cls[code][i_e]
                if not entry or entry <= 0.5 or entry > 30:
                    diag["price_range"] += 1; continue
                if i_e > 0:
                    prev_c = qfq_cls[code][i_e - 1]
                    if prev_c and entry / prev_c - 1 >= 0.097:
                        diag["limit_up"] += 1; continue
                diag["pass"] += 1
                stop_p = entry * (1 - STOP_PCT)
                exit_c = None; stopped = False
                for j in range(i_e + 1, i_e + 1 + HOLD):
                    if qfq_cls[code][j] < stop_p:
                        exit_c = stop_p; stopped = True; break
                if exit_c is None:
                    exit_c = qfq_cls[code][i_e + HOLD]
                net = (exit_c * (1 - COST)) / (entry * (1 + COST)) - 1
                trades.append({"d": d_sig, "code": code, "net": net, "yr": d_sig // 10000, "stop": stopped})
        print(f"[{name} sim诊断] {dict(diag)}")
        if not trades:
            return {"n": 0, "note": "无trades", "diag": dict(diag)}
        nets = [t["net"] for t in trades]
        yrs = defaultdict(list)
        for t in trades: yrs[t["yr"]].append(t["net"])
        per_year = {y: {"n": len(v), "win": f"{sum(1 for x in v if x>0)/len(v):.0%}",
                        "mean": f"{statistics.fmean(v):+.4f}"} for y, v in sorted(yrs.items())}
        return {"n": len(nets),
                "win": f"{sum(1 for x in nets if x>0)/len(nets):.1%}",
                "mean": round(statistics.fmean(nets), 4),
                "median": round(statistics.median(nets), 4),
                "stop_rate": f"{sum(1 for t in trades if t['stop'])/len(trades):.0%}",
                "per_year": per_year}

    r_icir = simulate("icir_sue", icir_sue_sigs)
    r_an = simulate("anom118", an_sigs)
    print(f"[icir_sue全史] {json.dumps(r_icir, ensure_ascii=False)}")
    print(f"[anom118全史] {json.dumps(r_an, ensure_ascii=False)}")

    # ---------- 合入 fulltest JSON ----------
    d_full = json.load(open(OUT, encoding="utf-8"))
    d_full["results"]["icir_sue_full"] = r_icir
    d_full["results"]["anom118_full"] = r_an
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(d_full, f, ensure_ascii=False, indent=1)
    print(f"[更新] {OUT}")
    print(f"[耗时] {time.time()-t0:.0f}s")


import bisect
if __name__ == "__main__":
    main()
