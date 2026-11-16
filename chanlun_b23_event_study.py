#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chanlun_b23_event_study.py — 缠论 buy2+buy3 共振信号 全市场事件研究（2026-09-02）
预注册: data/prereg_chanlun_b23_20260902.json (定义/判据/承诺已锁死, 只跑一次)

事件: 月末快照T往前64交易日窗口的ChanAnalyzer分解同时含 buy2+buy3
收益: T收市 → T+N交易日 (N=20/60), 无前视(分解仅用<=T数据)
基准: 同月非事件股 bootstrap 1000次等量抽样
判据: 两视野净超额中位数>0 且 p<0.05 且子区间方向一致 → usable, 否则 falsified
"""
from __future__ import annotations
import json, math, sqlite3, sys, time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, r"D:\zcodesj")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PREREG = r"D:\zcodesj\data\prereg_chanlun_b23_20260902.json"
OUT = Path(r"D:\zcodesj\reports\EVENT_STUDY_CHANLUN_B23_20260902.json")
COST = 0.001 * 2          # 双边成本
NBOOT = 1000
HORIZONS = (20, 60)
WIN = 64


def month_end_dates():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    dates = [int(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE change_pct IS NOT NULL ORDER BY date")]
    con.close()
    months = {}
    for d in dates:
        months[d // 100] = d          # yyyymm -> 该月最后交易日
    snaps = [d for ym, d in sorted(months.items()) if 201901 <= ym <= 202606]
    return dates, snaps


def worker(task):
    """子进程: 对单个快照T做全市场分解+事件收益。返回 (T, events{code:ret20/60}, pool{code:ret20/60})"""
    T, dates = task
    ti = dates.index(T)
    wi0 = max(0, ti - WIN + 1)
    win_dates = dates[wi0:ti + 1]
    if len(win_dates) < WIN:
        return T, None, None
    fut_end = {}
    for N in HORIZONS:
        j = ti + N
        fut_end[N] = dates[j] if j < len(dates) else None
    read_end = max([d for d in fut_end.values() if d] + [T])
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd_read(con, win_dates[0], read_end)
    con.close()
    sys.path.insert(0, r"D:\zcodesj")
    from chanlun_engine.analyzer import ChanAnalyzer
    an = ChanAnalyzer(pivot_source="stroke")
    events = {}     # code -> dict ret20/ret60
    pool = {}       # 非事件股同字段(基准池)
    for code, g in df.groupby("code"):
        g = g.sort_values("date")
        if len(g) < WIN - 4:
            continue
        win_g = g[g.date <= T]
        if len(win_g) < WIN - 4:     # prereg口径: 窗口内>=60根(允许4根内停牌缺失)
            continue
        try:
            r = an.analyze(win_g.rename(columns={"volume": "vol"})[["date", "open", "high", "low", "close", "vol"]])
        except Exception:
            continue
        bps = {b.type for b in r.buy_sell_points}
        is_evt = ("buy2" in bps) and ("buy3" in bps)
        closes = dict(zip(g.date, g.close))
        rets = {}
        ok = True
        for N in HORIZONS:
            fe = fut_end[N]
            if fe is None or fe not in closes or T not in closes:
                rets[f"ret{N}"] = None
            else:
                rets[f"ret{N}"] = closes[fe] / closes[T] - 1 - COST
            if rets[f"ret{N}"] is None and N == 20:
                ok = False
        rec = {"ret20": rets["ret20"], "ret60": rets.get("ret60")}
        if is_evt and ok:
            events[code] = rec
        elif ok:
            pool[code] = rec
    return T, events, pool


def pd_read(con, d0, d1):
    import pandas as pd
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? AND change_pct IS NOT NULL", con, params=(d0, d1))
    df["date"] = df["date"].astype(int)      # sqlite TEXT列 → int口径统一
    return df


def main():
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(20260902)     # 种子锁死
    dates, snaps = month_end_dates()
    print(f"快照数: {len(snaps)} ({snaps[0]}~{snaps[-1]})", flush=True)
    all_events, all_pool = [], []
    t0 = time.time()
    for i, T in enumerate(snaps):
        T_, events, pool = worker((T, dates))
        if events is None:
            print(f"[{i+1}/{len(snaps)}] {T} 窗口不足, 跳过", flush=True)
            continue
        for c, r in events.items():
            all_events.append({"T": T, "code": c, **r})
        for c, r in pool.items():
            all_pool.append({"T": T, "code": c, **r})
        print(f"[{i+1}/{len(snaps)}] {T} 事件{len(events)} 基准池{len(pool)} 累计{len(all_events)} ({time.time()-t0:.0f}s)", flush=True)

    ev = pd.DataFrame(all_events)
    pl = pd.DataFrame(all_pool)
    # 增量落盘明细(防汇总阶段崩溃丢结果)
    ev_out = r"D:\zcodesj\data\chanlun_b23_events_all.csv"
    ev.to_csv(ev_out, index=False)
    print(f"[明细落盘] {ev_out} ({len(ev)}行)", flush=True)
    out = {"snapshots": len(snaps), "events": len(ev), "pool": len(pl), "by_horizon": {}}
    for N in HORIZONS:
        col = f"ret{N}"
        e = ev.dropna(subset=[col])
        p = pl.dropna(subset=[col])
        # bootstrap 基准: 逐月等量抽样 (p["T"] 显式列索引, 勿用 .T 转置属性)
        evm = e.groupby("T")[col].agg(["count", "median", "mean"])
        pool_by_T = {t_: g[col].values for t_, g in p.groupby("T")}
        boots = []
        for _ in range(NBOOT):
            samp = []
            for T, row in evm.iterrows():
                cand = pool_by_T.get(T)
                if cand is None or len(cand) == 0 or row["count"] == 0:
                    continue
                k = min(int(row["count"]), len(cand))
                samp.append(float(rng.choice(cand, size=k, replace=False).mean()))
            boots.append(float(np.mean(samp)) if samp else 0.0)
        bm, bs = float(np.mean(boots)), float(np.std(boots))
        evt_m = float(e[col].mean()); evt_med = float(e[col].median())
        excess = evt_m - bm
        z = excess / bs if bs > 0 else 0
        from scipy import stats as st
        tstat, pval = st.ttest_1samp(e[col].values, bm)
        out["by_horizon"][N] = {
            "n_events": int(len(e)), "event_mean": evt_m, "event_median": evt_med,
            "boot_benchmark_mean": bm, "excess_mean": excess, "z": z,
            "t_vs_benchmark": float(tstat), "p_one_sided": float(st.t.sf(tstat, len(e) - 1)),
            "win_rate": float((e[col] > 0).mean()),
        }
        print(f"N={N}: 事件{len(e)} 均值{evt_m:+.4f} 基准{bm:+.4f} 超额{excess:+.4f} z={z:+.2f} 胜率{(e[col]>0).mean():.1%}", flush=True)
        # 子区间
        for a, b, lab in ((2019, 2022, "2019-2022"), (2023, 2026, "2023-2026H1")):
            sub = e[(e["T"] >= a * 10000) & (e["T"] < (b + 1) * 10000)]
            out["by_horizon"][N][f"sub_{lab}"] = {"n": int(len(sub)), "mean": float(sub[col].mean()) if len(sub) else None}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    ev.to_csv(r"D:\zcodesj\data\chanlun_b23_events_all.csv", index=False)
    print(f"[完成] {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
