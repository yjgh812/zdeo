# -*- coding: utf-8 -*-
"""证伪复核批次5 · 独立重实现三件

M) TIMING-PANIC-STAGE1 (T1市场级)
   原档: 恐慌日=宇宙日收益中位<=-2%(208/2593天); 恐慌日D+1开盘买等权宇宙 vs 无条件基线(非重叠采样):
   main N5 +0.549%/t1.40未达2, recent5y +0.105%/t0.17, N20转负
N) BREAKOUT-SCOUT-TRADEABLE (首板蓄势二波)
   原档口径(脚本逐字): 首板=涨停&前10日无板&60日涨幅<25%&量>=2x vma20&c<=30;
   蓄势=地板0.97x首板开盘价且回撤<15%; 间隔>=3日再板=信号; 入场=二板次日收盘; f20 含费0.3%;
   原档: 5,950笔 mean-3.48% vs 同宇宙基线+0.64%, 超额-4.12pp, 胜率32.0% vs 47.2%, 9/9年负
O) BREAKOUT-CLUSTER-FILTER (同日同细分行业>=3只首板)
   原档: limit_up_patterns口径 主线组(42只)R1 -2.04%/31.0% vs 孤立组(331只)+0.50%/42.9%, Δ=-2.55pp
   本检: 同表复算(数据已延至20261019, n更大)

数据: wb 单一真源 + attribution/attribution.db limit_up_patterns (只读)
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LUP = r"D:\zcodesj\attribution\attribution.db"
PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")


def load_panel(start, cols):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
    ph = ",".join("?" * len(PREFIXES))
    df = pd.read_sql(
        "SELECT %s FROM stock_daily_meta_qfq WHERE date>=? AND substr(code,1,3) IN (%s)" % (cols, ph),
        con, params=(start,) + PREFIXES)
    con.close()
    df["date"] = df["date"].astype(str)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def jdump(obj, name):
    p = os.path.join(HERE, name)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", p)


def part_m():
    df = load_panel("20160101", "code,date,open,close,change_pct")
    med = df.groupby("date")["change_pct"].median()
    cal = np.array(sorted(med.index))
    panic = [d for d in cal if med[d] <= -2.0]
    # 透视矩阵一次建好, 查 O(1)
    op = df.pivot(index="date", columns="code", values="open")
    cl = df.pivot(index="date", columns="code", values="close")
    dpos = {d: i for i, d in enumerate(cal)}

    def window_ret(entry_day, hold):
        j = dpos.get(entry_day)
        if j is None or j + hold >= len(cal):
            return np.nan
        d1, d2 = cal[j + 1], cal[j + hold]
        if d1 not in op.index or d2 not in cl.index:
            return np.nan
        r = (cl.loc[d2] / op.loc[d1] - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
        r = r[op.loc[d1] > 0]
        return float(r.mean()) if len(r) else np.nan

    def run(panics, hold, base_stride):
        keep, last_j = [], -10**9
        for d0 in panics:
            j = dpos.get(d0)
            if j is None or j - last_j <= hold:
                continue
            last_j = j
            r = window_ret(d0, hold)
            if not np.isnan(r):
                keep.append((d0, r))
        base = []
        for j in range(hold, len(cal) - hold - 1, base_stride):
            r = window_ret(cal[j], hold)
            if not np.isnan(r):
                base.append(r)
        pv = np.array([r for _, r in keep]); bv = np.array(base)
        pv = pv[~np.isnan(pv)]
        se = np.sqrt(pv.var(ddof=1) / len(pv) + bv.var(ddof=1) / len(bv))
        t = (pv.mean() - bv.mean()) / se if se > 0 else float("nan")
        return {"n_panic": int(len(pv)), "panic_mean_pct": round(float(pv.mean()) * 100, 3),
                "base_mean_pct": round(float(bv.mean()) * 100, 3),
                "excess_pct": round(float(pv.mean() - bv.mean()) * 100, 3), "welch_t": round(float(t), 2)}

    panic5 = [d for d in panic if d >= cal[max(0, len(cal) - 5 * 243)]]
    res = {
        "id": "TIMING-PANIC-STAGE1", "mode": "independent_reimpl",
        "n_panic_days_total": len(panic), "n_days_total": len(cal),
        "main_N5": run(panic, 5, 5), "recent5y_N5": run(panic5, 5, 5),
        "main_N20": run(panic, 20, 20),
        "caveats": ["非重叠=贪婪跳过窗口重叠恐慌日; 基线=无条件日等步长抽样; 原档208/2593天"],
    }
    jdump(res, "reaudit_panic_timing_result.json")
    print("M) main_N5=%s" % json.dumps(res["main_N5"]))
    print("M) recent5y_N5=%s" % json.dumps(res["recent5y_N5"]))
    print("M) main_N20=%s" % json.dumps(res["main_N20"]))
    return res


def part_n():
    df = load_panel("20170601", "code,date,open,close,volume,change_pct")
    df = df[df["date"] <= "20260904"].copy()
    allowed = ("600", "601", "603", "605", "000", "001", "002", "003", "300")
    sig_rows, base_rows = [], []
    for code, g in df.groupby("code", sort=False):
        if code[:3] not in allowed:
            continue
        n = len(g)
        if n < 300:
            continue
        o = g["open"].to_numpy(float); c = g["close"].to_numpy(float)
        v = g["volume"].to_numpy(float); d = g["date"].to_numpy()
        pct = g["change_pct"].to_numpy(float)
        thr = 19.8 if code[:3] == "300" else 9.8
        s = pd.Series
        is_lim = s(pct) >= thr
        prior10 = is_lim.shift(1).rolling(10, min_periods=10).sum().to_numpy()
        gain60 = c / s(c).shift(60).to_numpy() - 1.0
        vma20 = s(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            board = (is_lim.to_numpy() & (prior10_lim_ok(prior10)) & (gain60 < 0.25)
                     & (v >= 2.0 * vma20) & (c <= 30.0))
        elig = (c <= 30.0) & ~np.isnan(vma20) & ~np.isnan(s(c).shift(60).to_numpy())
        f20_from = np.full(n, np.nan)
        for i in range(n - 21):
            e = i + 1
            if o[e] > 0:
                f20_from[i] = c[i + 20] / c[e] - 1.0 - 0.003  # 次日收盘入场
        b_idx = np.where(board)[0]
        seen = set()
        for i in b_idx:
            floor = o[i] * 0.97
            for j in range(i + 1, min(i + 21, n)):
                if pct[j] >= thr and (j - i) >= 3:
                    if j not in seen:
                        seen.add(j)
                    break
                if c[j] < floor or (c[j] / c[i] - 1.0) < -0.15:
                    break
        for j in seen:
            if d[j] >= "20180101" and j + 21 < n and np.isfinite(f20_from[j]):
                sig_rows.append((d[j], f20_from[j]))
        for i in np.where(elig)[0]:
            if d[i] >= "20180101" and i % 5 == 0 and np.isfinite(f20_from[i]):
                base_rows.append((d[i], f20_from[i]))
    sig = pd.DataFrame(sig_rows, columns=["date", "f20"])
    base = pd.DataFrame(base_rows, columns=["date", "f20"])
    mg = sig.groupby("date")["f20"].mean()
    mb = base.groupby("date")["f20"].mean()
    t_day = float(mg.mean() / (mg.std(ddof=1) / np.sqrt(len(mg)))) if len(mg) > 2 else float("nan")
    sig["year"] = sig["date"].str[:4]
    yearly = sig.groupby("year")["f20"].mean()
    bmean = float(base["f20"].mean())
    res = {
        "id": "BREAKOUT-SCOUT-TRADEABLE", "mode": "independent_reimpl",
        "n_signals": int(len(sig)), "sig_mean_pct": round(float(sig["f20"].mean()) * 100, 3),
        "sig_win": round(float((sig["f20"] > 0).mean()), 4),
        "base_mean_pct": round(bmean * 100, 3),
        "excess_pp": round(float(sig["f20"].mean() - bmean) * 100, 3),
        "day_cluster_t": round(t_day, 2),
        "n_months": int(mg.shape[0]),
        "yearly_mean_pct": {k: round(float(v) * 100, 3) for k, v in yearly.items()},
        "caveats": ["同宇宙基线=elig(c<=30&指标齐)每5日抽样次收入场同费率; ST未剔(原档用cache)"],
    }
    jdump(res, "reaudit_scout_result.json")
    print("N) n=%d sig=%+.2f%% win=%.3f base=%+.2f%% excess=%+.2fpp t=%.1f years_negative=%d/%d" % (
        len(sig), sig["f20"].mean() * 100, (sig["f20"] > 0).mean(), bmean * 100,
        (sig["f20"].mean() - bmean) * 100, t_day,
        int((yearly < 0).sum()), len(yearly)))
    return res


def prior10_lim_ok(x):
    return x == 0


def part_o():
    con = sqlite3.connect("file:%s?mode=ro" % LUP, uri=True)
    lup = pd.read_sql("SELECT trade_date,code,industry,limit_count FROM limit_up_patterns WHERE limit_count=1", con)
    con.close()
    df = load_panel("20260801", "code,date,close")
    px = df.set_index(["code", "date"])["close"]
    cal = np.array(sorted(df["date"].unique()))
    lup["code"] = lup["code"].astype(str).str.zfill(6)
    cnt = lup.groupby(["trade_date", "industry"])["code"].count().rename("cnt")
    lup = lup.merge(cnt, left_on=["trade_date", "industry"], right_index=True, how="left")
    rows = []
    for _, r in lup.iterrows():
        d = str(r["trade_date"])
        j = int(np.searchsorted(cal, d))
        if j + 1 >= len(cal) or cal[j] != d:
            continue
        d1 = cal[j + 1]
        c0 = px.get((r["code"], d)); c1 = px.get((r["code"], d1))
        if c0 and c1 and c0 > 0:
            rows.append((d, r["code"], r["industry"], int(r["cnt"]), c1 / c0 - 1.0))
    ev = pd.DataFrame(rows, columns=["date", "code", "industry", "cnt", "r1"])
    ev["grp"] = np.where(ev["cnt"] >= 3, "mainline", "isolated")

    def summarize(e):
        g = e.groupby("grp")["r1"].agg(["count", "mean", "median", lambda s: float((s > 0).mean())])
        g.columns = ["n", "mean", "median", "win"]
        a = e[e["grp"] == "mainline"]["r1"]; b = e[e["grp"] == "isolated"]["r1"]
        return {k2: {"n": int(v["n"]), "r1_mean_pct": round(float(v["mean"]) * 100, 3),
                     "r1_median_pct": round(float(v["median"]) * 100, 3),
                     "win": round(float(v["win"]), 4)} for k2, v in g.iterrows()}, stats_t(a, b)

    g, wt = summarize(ev)
    orig_win = ev[ev["date"] <= "20260909"]
    g_orig, wt_orig = summarize(orig_win)
    res = {
        "id": "BREAKOUT-CLUSTER-FILTER", "mode": "independent_reimpl",
        "window": [ev["date"].min(), ev["date"].max()], "n_events": int(len(ev)),
        "groups": g, "delta_pp_welch_t": wt,
        "orig_window_le_20260909": {"groups": g_orig, "delta_pp_welch_t": wt_orig,
                                    "n": int(len(orig_win))},
        "caveats": ["limit_up_patterns 20260812起; 原档9/9时点数据窗更短(42/331只), 本检窗口更长n更大",
                    "附原窗口(<=20260909)对拍用于归因: 若原窗口方向亦不复现则为口径差异而非时间衰减"],
    }
    jdump(res, "reaudit_cluster_result.json")
    print("O full)", json.dumps(g, ensure_ascii=False), "t=", wt)
    print("O orig_win)", json.dumps(g_orig, ensure_ascii=False), "t=", wt_orig, "n=", len(orig_win))
    return res


def stats_t(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return round(float((a.mean() - b.mean()) / se), 2) if se > 0 else float("nan")


if __name__ == "__main__":
    part_m()
    part_n()
    part_o()
    print("done")
