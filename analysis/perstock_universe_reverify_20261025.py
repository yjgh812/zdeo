# -*- coding: utf-8 -*-
"""全宇宙A股·个股条件胜率复验与分布检验(20261025)。

目的: 检验"按个股条件胜率排名"是否有多重比较噪声嫌疑 —
  ①全宇宙(6/0/3,>60日)逐股条件胜率分布(生产口径: A格状态进入+冷却5+close≤30+非ST日, t20e);
  ②38只军工池股在全宇宙分位;
  ③池内 vs 池外同n档均值差(主线命中是否含个股信息);
  ④前后半样本秩相关(个股排名有无前瞻内容);
  ⑤全宇宙Top20(n≥40) — 38只之外是否有同口径更优股。
只读, 零交易权。输出: analysis/perstock_universe_reverify_20261025_result.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
SIG = r"D:\zcodesj\data\lowconsol_signal_latest.json"
OUT = r"D:\zcodesj\analysis\perstock_universe_reverify_20261025_result.json"

LOAD_START = "20190101"
COOLDOWN = 5
HOLD = 20
STOP = 0.92
PRICE_MAX = 30.0


def t20e_sim(close: np.ndarray, ev_idx: np.ndarray):
    n = len(close)
    ev = ev_idx[(ev_idx + HOLD < n) & (ev_idx >= 0)]
    if len(ev) == 0:
        return np.array([], dtype=bool)
    win20 = np.lib.stride_tricks.sliding_window_view(close, HOLD)[ev + 1]
    stop = close[ev, None] * STOP
    broke = win20 <= stop
    has_break = broke.any(axis=1)
    first = np.argmax(broke, axis=1)
    exit_px = np.where(has_break, win20[np.arange(len(ev)), first], close[ev + HOLD])
    return exit_px > close[ev]


def cooldown_filter(idxs: np.ndarray, cd: int):
    keep, last = [], -10**9
    for i in idxs:
        if i - last > cd:
            keep.append(i)
            last = i
    return np.array(keep, dtype=int)


def main():
    t0 = time.time()
    sig = json.load(open(SIG, encoding="utf-8"))
    pool38 = {p["code"]: p["name"] for p in sig["main_hit_list"]}

    st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
    st_con.close()
    st_by_code = {}
    for c, d in zip(st_rows["code"].str[-6:], st_rows["date"].astype("int64")):
        st_by_code.setdefault(c, set()).add(int(d))

    db = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='{LOAD_START}' "
        "ORDER BY code,date", db)
    db.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    df["d"] = df["date"].astype("int64")
    g = df.groupby("code")["close"]
    df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
    mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
    df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)
    print(f"[1/3] 状态矩阵 行={len(df)} 股={df['code'].nunique()}  ({time.time()-t0:.1f}s)")

    d_g = df["d"].to_numpy(dtype="int64")
    close_g = df["close"].to_numpy(dtype=float)
    code_g = df["code"].to_numpy()
    is_A_g = df["is_A"].fillna(False).to_numpy()
    prevA_g = df.groupby("code")["is_A"].shift(1).fillna(False).to_numpy()

    per = {}
    total_events = 0
    for code, gi in df.groupby("code", sort=False).indices.items():
        gi = np.sort(gi)
        c = close_g[gi]
        d_loc = d_g[gi]
        st_d = st_by_code.get(code, ())
        ok = ~np.isin(d_loc, np.fromiter(st_d, dtype="int64", count=len(st_d))) if st_d else np.ones(len(gi), bool)
        isA = is_A_g[gi]
        prevA = np.append([False], isA[:-1])
        ev = cooldown_filter(np.nonzero(isA & ~prevA & ok & (c <= PRICE_MAX))[0], COOLDOWN)
        if len(ev) == 0:
            continue
        ev2 = ev[ev + HOLD < len(c)]
        wins = t20e_sim(c, ev2)
        if len(wins) == 0:
            continue
        total_events += len(wins)
        dts = d_loc[ev2]
        half = len(wins) // 2
        order = np.argsort(dts, kind="stable")
        w_sorted = wins[order]
        h1 = float(w_sorted[:half].mean()) * 100 if half else None
        h2 = float(w_sorted[half:].mean()) * 100 if len(wins) - half else None
        per[code] = {"n": int(len(wins)), "win": float(wins.mean()) * 100,
                     "h1": h1, "h2": h2}
    print(f"[2/3] 全宇宙逐股完成 股数={len(per)} 总事件={total_events}  ({time.time()-t0:.1f}s)")

    rows = [{"code": c, **v} for c, v in per.items()]
    dist = pd.DataFrame(rows)
    results = {}
    for cohort, nmin in (("n>=20", 20), ("n>=40", 40)):
        sub = dist[dist["n"] >= nmin]
        results_c = {
            "n_stocks": int(len(sub)),
            "mean_win": round(float(sub["win"].mean()), 2),
            "median_win": round(float(sub["win"].median()), 2),
            "p90": round(float(sub["win"].quantile(0.90)), 2),
            "p95": round(float(sub["win"].quantile(0.95)), 2),
            "p99": round(float(sub["win"].quantile(0.99)), 2),
            "count_ge55": int((sub["win"] >= 55).sum()),
            "count_ge60": int((sub["win"] >= 60).sum()),
            "count_ge_64_5": int((sub["win"] >= 64.5).sum()),
            "count_ge50": int((sub["win"] >= 50).sum()),
        }
        if cohort == "n>=40":
            results["dist_n40"] = results_c
        else:
            results["dist_n20"] = results_c
    # 前后半秩相关(两边都有≥10事件的股票)
    both = dist[(dist["h1"].notna()) & (dist["h2"].notna())
                & (dist["n"] >= 20)]
    r1 = both["h1"].rank()
    r2 = both["h2"].rank()
    corr = float(np.corrcoef(r1, r2)[0, 1]) if len(both) > 10 else None
    results["split_half_spearman"] = {"n_stocks": int(len(both)), "rho": round(corr, 4) if corr is not None else None}

    # 38只在全宇宙的分位与池内/池外对比(n>=20)
    sub20 = dist[dist["n"] >= 20].copy()
    sub20["pctile"] = sub20["win"].rank(pct=True) * 100
    pool_rows = []
    for code, name in pool38.items():
        r = sub20[sub20["code"] == code]
        if len(r):
            rr = r.iloc[0]
            pool_rows.append({"code": code, "name": name, "win": round(rr["win"], 1), "n": int(rr["n"]),
                              "universe_pctile": round(float(rr["pctile"]), 1)})
    results["pool38_in_universe"] = sorted(pool_rows, key=lambda x: -x["win"])
    nsub = dist[dist["n"] >= 20]
    pool_codes = set(pool38)
    in_pool = nsub[nsub["code"].isin(pool_codes)]["win"]
    out_pool = nsub[~nsub["code"].isin(pool_codes)]["win"]
    results["pool_vs_out"] = {
        "pool_mean": round(float(in_pool.mean()), 2), "pool_n": int(len(in_pool)),
        "out_mean": round(float(out_pool.mean()), 2), "out_n": int(len(out_pool))}

    top = dist[dist["n"] >= 40].sort_values("win", ascending=False).head(20)
    results["universe_top20_n40"] = [
        {"code": r.code, "n": int(r.n), "win": round(r.win, 1)} for r in top.itertuples()]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[3/3] 完成 ({time.time()-t0:.1f}s) → {OUT}")
    for k in ("dist_n20", "dist_n40"):
        print(f"\n== 分布 {k}: {results[k]}")
    print(f"前后半Spearman: {results['split_half_spearman']}")
    print(f"池内均值={results['pool_vs_out']['pool_mean']} vs 池外={results['pool_vs_out']['out_mean']}")
    print("\n38只全宇宙分位:")
    for r in results["pool38_in_universe"]:
        print(f"  {r['code']} {r['name']:<6} win={r['win']}% n={r['n']} 分位={r['universe_pctile']}")
    print("\n全宇宙Top20(n>=40):")
    for r in results["universe_top20_n40"]:
        mark = " ←池内" if r["code"] in pool38 else ""
        print(f"  {r['code']} n={r['n']} win={r['win']}%{mark}")


if __name__ == "__main__":
    sys.exit(main())
