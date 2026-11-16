# -*- coding: utf-8 -*-
"""低位横盘池效应·生产口径按年衰减定位(20261025)。

生产口径 = 每日扫描脚本实际语义: 全历史加载, pos250=rolling(250,min120)真250日窗口,
事件=is_A(pos250<30且|chg5|<3)全日+冷却5日(dpos日历序), t20e=收盘入场×0.92止损/T+20收盘。
与v3复刻的差异 = 不截断加载/不含退市并库(生产扫描器同样不含) — 此为"验证口径≠生产口径"缺口的
生产侧真值测量。双估计器(全池加权 + 每日≤20等权)并行, 按年分解: 2024/2025/2026YTD/近250日。
只读, 零交易权。输出: analysis/lowconsol_production_decay_20261025_result.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = r"D:\zcodesj\analysis\lowconsol_production_decay_20261025_result.json"

LOAD_START = "20190101"
COOLDOWN = 5
HOLD = 20
STOP = 0.92
SEED = 42

WINDOWS = {
    "Y2024": (20240101, 20241231),
    "Y2025": (20250101, 20251231),
    "Y2026ytd": (20260101, 20261023),
    "last250d": (None, None),   # 运行时定位
}


def t20e_sim(close: np.ndarray, ev_idx: np.ndarray):
    n = len(close)
    ev = ev_idx[(ev_idx + HOLD < n) & (ev_idx >= 0)]
    if len(ev) == 0:
        return np.array([], dtype=bool), np.array([], dtype=float)
    win20 = np.lib.stride_tricks.sliding_window_view(close, HOLD)[ev + 1]
    stop = close[ev, None] * STOP
    broke = win20 <= stop
    has_break = broke.any(axis=1)
    first = np.argmax(broke, axis=1)
    exit_px = np.where(has_break, win20[np.arange(len(ev)), first], close[ev + HOLD])
    ret = exit_px / close[ev] - 1.0
    return ret > 0, ret


def main():
    t0 = time.time()
    db = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,open,close,volume FROM stock_daily_meta_qfq WHERE date>='{LOAD_START}' "
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
    df["_tail"] = df.groupby("code").cumcount(ascending=False) < HOLD
    valid_base = (~df["_tail"]).to_numpy() & (df["volume"] > 0).to_numpy() & (df["open"] > 0).to_numpy()
    print(f"[1/3] 状态矩阵 行={len(df)} 股={df['code'].nunique()}  ({time.time()-t0:.1f}s)")

    d_g = df["d"].to_numpy(dtype="int64")
    close_g = df["close"].to_numpy(dtype=float)
    code_g = df["code"].to_numpy()
    is_A_g = df["is_A"].fillna(False).to_numpy()

    # 事件: is_A全日 + dpos冷却5
    uniq_d = np.unique(d_g)
    dpos_map = {int(d): i for i, d in enumerate(uniq_d)}
    dpos_g = np.array([dpos_map[int(d)] for d in d_g], dtype="int64")
    ev_pos_all = []
    for code, gi in df.groupby("code", sort=False).indices.items():
        gi = np.sort(gi)
        local = np.nonzero(is_A_g[gi])[0]
        keep, last = [], -10**9
        for i in local:
            if dpos_g[gi[i]] - last >= COOLDOWN:
                keep.append(i)
                last = dpos_g[gi[i]]
        if keep:
            ev_pos_all.append(gi[np.array(keep, dtype=int)])
    ev_pos = np.concatenate(ev_pos_all)
    ev_d = d_g[ev_pos]
    # 按股票块模拟(全局行号连续段)
    wins_all, dates_all = [], []
    code_starts = np.append([0], np.nonzero(code_g[1:] != code_g[:-1])[0] + 1)
    code_ends = np.append(code_starts[1:], len(code_g))
    order = np.argsort(ev_pos, kind="stable")
    ep = ev_pos[order]
    lc = code_g[ep]
    change = np.nonzero(lc[1:] != lc[:-1])[0] + 1
    starts_b = np.concatenate([[0], change])
    ends_b = np.concatenate([change, [len(ep)]])
    for sb, eb in zip(starts_b, ends_b):
        block = ep[sb:eb]
        g0, g1 = int(block[0]), int(block[-1])
        block = block[block + HOLD <= g1]   # 与t20e_sim内部过滤对齐(完整前向20日)
        if len(block) == 0:
            continue
        w, _ = t20e_sim(close_g[g0:g1 + 1], block - g0)
        wins_all.append(w)
        dates_all.append(d_g[block])
    ev_win = np.concatenate(wins_all)
    ev_date = np.concatenate(dates_all)
    print(f"[2/3] 事件模拟完成 事件数={len(ev_win)}  ({time.time()-t0:.1f}s)")

    # 非A格基线池(逐日≤20用)
    nonA_idx = np.nonzero(valid_base & ~is_A_g)[0]
    ordn = np.argsort(d_g[nonA_idx], kind="stable")
    nonA_sorted = nonA_idx[ordn]
    dsorted = d_g[nonA_sorted]
    ud_n, st_n = np.unique(dsorted, return_index=True)
    en_n = np.append(st_n[1:], len(dsorted))
    date_slice = {int(d): (int(s), int(e)) for d, s, e in zip(ud_n, st_n, en_n)}
    all_valid_dates = np.unique(d_g[valid_base])

    def pooled_stats(w):
        lo, hi = w
        m = (ev_date >= lo) & (ev_date <= hi)
        if not m.any():
            return None
        return {"win_A": round(float(ev_win[m].mean()) * 100, 2), "n": int(m.sum())}

    def pooled_baseline(w):
        lo, hi = w
        mask = valid_base & (d_g >= lo) & (d_g <= hi)
        idx = np.nonzero(mask)[0]
        rg = np.random.default_rng(SEED)
        if len(idx) > 20000:
            idx = rg.choice(idx, 20000, replace=False)
        idx = np.sort(idx)
        lc = code_g[idx]
        change = np.nonzero(lc[1:] != lc[:-1])[0] + 1
        s_b = np.concatenate([[0], change])
        e_b = np.concatenate([change, [len(idx)]])
        wins = []
        for sb, eb in zip(s_b, e_b):
            block = idx[sb:eb]
            g0, g1 = int(block[0]), int(block[-1])
            w, _ = t20e_sim(close_g[g0:g1 + 1], block - g0)
            wins.append(w)
        pooled = np.concatenate([x for x in wins if len(x)])
        return round(float(pooled.mean()) * 100, 2) if len(pooled) else None

    def cap20_stats(w):
        lo, hi = w
        rngc = np.random.default_rng(SEED)
        sel = []
        ud = np.unique(ev_date[(ev_date >= lo) & (ev_date <= hi)])
        dmap = {}
        for i, d in enumerate(ev_date):
            if lo <= d <= hi:
                dmap.setdefault(int(d), []).append(i)
        for d in ud:
            idx = np.array(dmap.get(int(d), []), dtype=int)
            if len(idx) > 20:
                idx = rngc.choice(idx, 20, replace=False)
            sel.append(idx)
        if not sel:
            return None
        sel = np.concatenate(sel)
        return {"win_A": round(float(ev_win[sel].mean()) * 100, 2), "n": int(len(sel))}

    def cap20_baseline(w):
        lo, hi = w
        rngc = np.random.default_rng(SEED)
        sampled = []
        for d in ud_n:
            di = int(d)
            if not (lo <= di <= hi):
                continue
            s, e = date_slice[di]
            idx = np.arange(s, e)
            if len(idx) > 20:
                idx = rngc.choice(idx, 20, replace=False)
            sampled.append(nonA_sorted[idx])
        if not sampled:
            return None
        blk = np.sort(np.concatenate(sampled))
        lc = code_g[blk]
        change = np.nonzero(lc[1:] != lc[:-1])[0] + 1
        s_b = np.concatenate([[0], change])
        e_b = np.concatenate([change, [len(blk)]])
        wins = []
        for sb, eb in zip(s_b, e_b):
            block = blk[sb:eb]
            g0, g1 = int(block[0]), int(block[-1])
            w, _ = t20e_sim(close_g[g0:g1 + 1], block - g0)
            wins.append(w)
        pooled = np.concatenate([x for x in wins if len(x)])
        return round(float(pooled.mean()) * 100, 2) if len(pooled) else None

    results = {}
    for tag, w in WINDOWS.items():
        if tag == "last250d":
            w = (int(all_valid_dates[-250]), int(all_valid_dates[-1]))
        pa = pooled_stats(w)
        pb = pooled_baseline(w)
        ca = cap20_stats(w)
        cb = cap20_baseline(w)
        results[tag] = {
            "window": [str(w[0]), str(w[1])],
            "pooled": {**pa, "win_R": pb, "effect_pp": round(pa["win_A"] - pb, 2)} if pa and pb else None,
            "cap20": {**ca, "win_R": cb, "effect_pp": round(ca["win_A"] - cb, 2)} if ca and cb else None,
        }
        print(f"[{tag}] 全池: {results[tag]['pooled']} | 每日≤20: {results[tag]['cap20']}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[3/3] 完成 ({time.time()-t0:.1f}s) → {OUT}")


if __name__ == "__main__":
    sys.exit(main())
