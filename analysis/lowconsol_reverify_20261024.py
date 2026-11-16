# -*- coding: utf-8 -*-
"""低位横盘效应复验 + 个股胜率排名(20261024) — 依据 prereg_lowconsol_reverify_20261024.md。

复刻 PREREG-LOW-CONSOLIDATION-ALPHA-20261021 主口径(cd5_t20e): 事件=A格子状态进入(冷却5日),
收盘入场×0.92止损T20E模拟, G_RANDOM随机对照12000股日/窗(seed42)。
三窗: W1=2022-2023(档案+5.91pp) / W2=2024+(档案+4.55pp) / W3=近250交易日(持续性)。
变体F因子口径(无价格帽)+T交易口径(≤30元); E2=main_hit载体子集; 38只池内股条件胜率排名。
只读, 零交易权。输出: analysis/lowconsol_reverify_20261024_result.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
INDDB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
SIG = r"D:\zcodesj\data\lowconsol_signal_latest.json"
OUT = r"D:\zcodesj\analysis\lowconsol_reverify_20261024_result.json"

LOAD_START = "20190101"
W1 = ("20220101", "20231231")
W2 = ("20240101", "20261023")
COOLDOWN = 5
HOLD = 20
STOP_RATIO = 0.92
N_RANDOM = 12000
SEED = 42
PASS_BANDS = {"W1_2022_2023": (3.0, 9.0), "W2_2024_plus": (2.0, 7.0)}


def t20e_sim(close: np.ndarray, ev_idx: np.ndarray) -> np.ndarray:
    """收盘入场; ×0.92止损(T+1起任一日收盘跌破→当日收盘出); 否则T+20收盘出。要求完整前向20日。"""
    n = len(close)
    ev = ev_idx[(ev_idx + HOLD < n) & (ev_idx >= 0)]
    if len(ev) == 0:
        return np.array([], dtype=bool)
    win20 = sliding_window_view(close, HOLD)[ev + 1]
    stop = close[ev, None] * STOP_RATIO
    broke = win20 < stop
    has_break = broke.any(axis=1)
    first = np.argmax(broke, axis=1)
    exit_px = np.where(has_break, win20[np.arange(len(ev)), first], close[ev + HOLD])
    return exit_px > close[ev]


def cooldown_filter(idxs: np.ndarray, cd: int) -> np.ndarray:
    keep, last = [], -10**9
    for i in idxs:
        if i - last > cd:
            keep.append(i)
            last = i
    return np.array(keep, dtype=int)


def main():
    t0 = time.time()
    st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    st_rows = pd.read_sql_query("SELECT code, date FROM st_daily WHERE is_st=1", st_con)
    st_con.close()
    st_by_code = {}
    for c, d in zip(st_rows["code"].str[-6:], st_rows["date"].astype("int64")):
        st_by_code.setdefault(c, set()).add(int(d))
    l1c = sqlite3.connect(INDDB)
    l1map = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", l1c)
    l1c.close()
    l1map["code"] = l1map["code"].astype(str).str[-6:]
    l1_of = dict(zip(l1map["code"], l1map["l1"]))

    db = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE date>='{LOAD_START}' "
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
    df["l1"] = df["code"].map(l1_of)
    df["_tail"] = df.groupby("code").cumcount(ascending=False) < HOLD
    valid_base = ~df["_tail"].to_numpy()
    df = df.drop(columns=["_tail"])
    print(f"[1/5] 状态矩阵 行={len(df)} 股={df['code'].nunique()}  ({time.time()-t0:.1f}s)")

    vol_l1 = df[df["l1"].notna()].groupby(["l1", "d"])["volume"].sum().rename("vol_l1").reset_index()
    icm = df[df["l1"].notna()].groupby(["l1", "d"])["chg5"].median().rename("icm").reset_index()
    ind = vol_l1.merge(icm, on=["l1", "d"]).sort_values(["l1", "d"])
    ind["v5"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.rolling(5).sum())
    ind["v5p"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
    ind["volr_l1"] = ind["v5"] / ind["v5p"]
    ind["c5"] = ind.groupby("l1")["icm"].transform(lambda s: s.rolling(5).mean())
    hot_by_date = {}
    for dt, sub in ind.groupby("d"):
        s = sub.dropna(subset=["volr_l1", "c5"])
        hot_by_date[int(dt)] = set(s[s["c5"] > 0].nlargest(3, "volr_l1")["l1"])
    print(f"[2/5] hot L1 覆盖{len(hot_by_date)}日  ({time.time()-t0:.1f}s)")

    d_g = df["d"].to_numpy(dtype="int64")
    close_g = df["close"].to_numpy(dtype=float)
    code_g = df["code"].to_numpy()
    is_A_g = df["is_A"].to_numpy()

    records = []
    for code, gi in df.groupby("code", sort=False).indices.items():
        gi = np.sort(gi)
        c = close_g[gi]
        d_loc = d_g[gi]
        st_d = st_by_code.get(code, ())
        st_flag = np.isin(d_loc, np.fromiter(st_d, dtype="int64", count=len(st_d))) if st_d else np.zeros(len(gi), bool)
        isA = is_A_g[gi]
        prevA = np.append([False], isA[:-1])
        ev = cooldown_filter(np.nonzero(isA & ~prevA)[0], COOLDOWN)
        if len(ev) == 0:
            continue
        wins = t20e_sim(c, ev)
        keep = ev + HOLD < len(c)          # 与t20e_sim内部过滤对齐
        ev_k, wins_k = ev[keep], wins
        price_ok = c[ev_k] <= 30.0
        st_ok = ~st_flag[ev_k]
        l1v = l1_of.get(code)
        hotflags = np.array([l1v in hot_by_date.get(int(d), ()) for d in d_loc[ev_k]], dtype=bool)
        records.append((code, d_loc[ev_k], wins_k, price_ok, st_ok, hotflags))
    print(f"[3/5] 事件模拟完成 有事件股票={len(records)}  ({time.time()-t0:.1f}s)")

    evD = np.concatenate([r[1] for r in records])
    evW = np.concatenate([r[2] for r in records])
    evP = np.concatenate([r[3] for r in records])
    evS = np.concatenate([r[4] for r in records])
    evH = np.concatenate([r[5] for r in records])
    ordr = np.argsort(evD, kind="stable")
    evD, evW, evP, evS, evH = evD[ordr], evW[ordr], evP[ordr], evS[ordr], evH[ordr]
    ud, ustart = np.unique(evD, return_index=True)
    uend = np.append(ustart[1:], len(evD))

    def cap20_sample(mask):
        """原runner估计器: 每日随机≤20只(seed42), 返回选中的win数组。"""
        rngc = np.random.default_rng(SEED)
        out = []
        for s, e in zip(ustart, uend):
            idx = np.arange(s, e)
            m = mask[idx] if mask is not None else np.ones(e - s, dtype=bool)
            idx = idx[m]
            if len(idx) > 20:
                idx = rngc.choice(idx, 20, replace=False)
            if len(idx):
                out.append(evW[idx])
        return np.concatenate(out) if out else np.array([])

    # 非A格随机股日基线(逐日≤20, 原runner口径)
    nonA_idx = np.nonzero(valid_base & ~is_A_g)[0]
    ordn = np.argsort(d_g[nonA_idx], kind="stable")
    nonA_sorted = nonA_idx[ordn]
    dsorted = d_g[nonA_sorted]
    ud_n, st_n = np.unique(dsorted, return_index=True)
    en_n = np.append(st_n[1:], len(dsorted))
    date_slice = {int(d): (int(s), int(e)) for d, s, e in zip(ud_n, st_n, en_n)}

    def cap20_baseline(w):
        lo, hi = int(w[0]), int(w[1])
        rngc = np.random.default_rng(SEED)
        sampled = []
        for d in ud_n:
            if not (lo <= d <= hi):
                continue
            s, e = date_slice[int(d)]
            idx = np.arange(s, e)
            if len(idx) > 20:
                idx = rngc.choice(idx, 20, replace=False)
            sampled.append(nonA_sorted[idx])
        if not sampled:
            return float("nan")
        blk = np.sort(np.concatenate(sampled))
        lc = code_g[blk]
        change = np.nonzero(lc[1:] != lc[:-1])[0] + 1
        starts_b = np.concatenate([[0], change])
        ends_b = np.concatenate([change, [len(blk)]])
        code_starts = np.append([0], np.nonzero(code_g[1:] != code_g[:-1])[0] + 1)
        code_ends = np.append(code_starts[1:], len(code_g))
        wins_all = []
        for sb, eb in zip(starts_b, ends_b):
            block = blk[sb:eb]
            g0 = int(block[0])
            bi = int(np.searchsorted(code_starts, g0, side="right") - 1)
            b0, b1 = int(code_starts[bi]), int(code_ends[bi])
            wins_all.append(t20e_sim(close_g[b0:b1], block - b0))
        pooled = [x for x in wins_all if len(x)]
        return float(np.concatenate(pooled).mean()) if pooled else float("nan")



    rng = np.random.default_rng(SEED)
    results = {"windows": {}, "e2": {}, "per_stock": {}}
    windows = {"W1_2022_2023": W1, "W2_2024_plus": W2}
    all_valid_dates = np.unique(d_g[valid_base])
    cutoff = int(all_valid_dates[-250])
    windows["W3_last250d"] = (str(cutoff), str(int(all_valid_dates[-1])))
    for tag, w in windows.items():
        br = cap20_baseline(w)
        lo, hi = int(w[0]), int(w[1])
        winmask = (evD >= lo) & (evD <= hi)
        aF = cap20_sample(winmask)                                  # 忠实复刻: 每日≤20, 无过滤
        aT = cap20_sample(winmask & evP & evS)                      # 交易口径(≤30, 非ST日)
        hT = cap20_sample(winmask & evP & evS & evH)                # main_hit载体子集
        pF = evW[winmask]; pT = evW[winmask & evP & evS]
        results["windows"][tag] = {
            "replica_win_A": float(aF.mean()) if len(aF) else None, "replica_n": int(len(aF)),
            "replica_win_R": br,
            "replica_effect_pp": round((float(aF.mean()) - br) * 100, 2) if len(aF) else None,
            "T_trading_win": float(aT.mean()) if len(aT) else None, "T_n": int(len(aT)),
            "T_effect_pp": round((float(aT.mean()) - br) * 100, 2) if len(aT) else None,
            "pooled_F_win": float(pF.mean()), "pooled_F_n": int(len(pF)),
            "pooled_F_effect_pp": round((float(pF.mean()) - br) * 100, 2),
            "pooled_T_win": float(pT.mean()), "pooled_T_n": int(len(pT)),
            "pooled_T_effect_pp": round((float(pT.mean()) - br) * 100, 2),
            "main_hit_win": float(hT.mean()) if len(hT) else None, "main_hit_n": int(len(hT)),
            "main_hit_effect_pp": round((float(hT.mean()) - br) * 100, 2) if len(hT) else None}
        band = PASS_BANDS.get(tag)
        if band:
            results["windows"][tag]["reverify"] = (
                "PASS" if band[0] <= results["windows"][tag]["replica_effect_pp"] <= band[1] else "FAIL")

    sig = json.load(open(SIG, encoding="utf-8"))
    pool38 = {p["code"]: p["name"] for p in sig["main_hit_list"]}
    rec_by_code = {r[0]: r for r in records}
    for code, name in pool38.items():
        r = rec_by_code.get(code)
        if r is None:
            results["per_stock"][code] = {"name": name, "n": 0, "win20e": None}
            continue
        _, dts, wins, price_ok, st_ok, _ = r
        sel = price_ok & st_ok
        wins_T = wins[sel]
        results["per_stock"][code] = {"name": name, "n": int(len(wins_T)),
                                      "win20e": round(float(wins_T.mean()) * 100, 1) if len(wins_T) else None}

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[4/5] 完成  ({time.time()-t0:.1f}s)  → {OUT}")
    for tag, d in results["windows"].items():
        print(f"\n== {tag} ==  基线(每日≤20非A格随机)={d['replica_win_R']:.2%}")
        print(f"  忠实复刻(每日≤20,因子口径): win_A={d['replica_win_A']:.2%} n={d['replica_n']} "
              f"effect={d['replica_effect_pp']:+.2f}pp"
              + (f"  复验={d['reverify']}" if "reverify" in d else "  (持续性检查,无档案锚)"))
        print(f"  交易口径(≤30元,非ST,每日≤20): win={d['T_trading_win']:.2%} n={d['T_n']} effect={d['T_effect_pp']:+.2f}pp")
        print(f"  稳健对照(全事件池F): {d['pooled_F_effect_pp']:+.2f}pp (n={d['pooled_F_n']}) | "
              f"(全事件池T): {d['pooled_T_effect_pp']:+.2f}pp (n={d['pooled_T_n']})")
        print(f"  main_hit载体子集(每日≤20): win={d['main_hit_win']:.2%} n={d['main_hit_n']} effect={d['main_hit_effect_pp']:+.2f}pp")
    print(f"\n== 38只个股条件胜率(win20e降序, 交易口径) ==")
    ps = sorted(results["per_stock"].items(), key=lambda kv: -(kv[1]["win20e"] if kv[1]["win20e"] is not None else -1))
    for code, v in ps:
        flag = "" if (v["n"] >= 5 and v["win20e"] is not None) else "  ←样本不足"
        print(f"  {code} {v['name']:<6} n={v['n']:<4} win20e={v['win20e']}%{flag}")


if __name__ == "__main__":
    sys.exit(main())
