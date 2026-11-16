# -*- coding: utf-8 -*-
"""38只池内股·条件胜率复验与稳健排名(20261025)。

复验三层: ①与昨日口径(lowconsol_reverify_20261024 per_stock)可复现性对账;
②bootstrap95%CI + 时间半分稳定性(前/后一半事件); ③相位切分(等权指数MA60牛/熊)。
口径: 事件=A格状态进入(冷却5, 个股行序), close≤30, 剔ST日, t20e(收盘入场×0.92/T+20)。
排名=win20e降序 + 稳健性标注。只读, 零交易权。
输出: analysis/perstock_winrate_reverify_20261025_result.json
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
PREV = r"D:\zcodesj\analysis\lowconsol_reverify_20261024_result.json"
OUT = r"D:\zcodesj\analysis\perstock_winrate_reverify_20261025_result.json"

LOAD_START = "20190101"
COOLDOWN = 5
HOLD = 20
STOP = 0.92
SEED = 42
NBOOT = 1000


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
    prev = json.load(open(PREV, encoding="utf-8")).get("per_stock", {})

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
    print(f"[1/3] 状态矩阵 行={len(df)}  ({time.time()-t0:.1f}s)")

    # 相位: 等权指数收盘>MA60
    mkt = df.groupby("d").agg(chg=("close", lambda s: s.pct_change().mean())).reset_index().sort_values("d")
    idx_lvl = (1.0 + mkt["chg"].fillna(0.0)).cumprod()
    ma60 = idx_lvl.rolling(60, min_periods=60).mean()
    bull_arr = (idx_lvl > ma60).to_numpy()
    bull_by_d = dict(zip(mkt["d"].astype("int64"), bull_arr))

    # 相位基线(全池事件, 非目标38股): 供"相对自身相位基线超额"参考
    code_g = df["code"].to_numpy()
    is_A_g = df["is_A"].fillna(False).to_numpy()
    d_g = df["d"].to_numpy(dtype="int64")
    close_g = df["close"].to_numpy(dtype=float)
    prevA_g = df.groupby("code")["is_A"].shift(1).fillna(False).to_numpy()
    st_ok_g = np.array([(d in st_by_code.get(c, ())) is False for c, d in zip(code_g, d_g)])
    entry_mask = is_A_g & ~prevA_g & st_ok_g & (close_g <= 30.0)

    # 全池事件(排除38股自身)按相位胜率 = 相位基线
    pool_mask = entry_mask & ~np.isin(code_g, list(pool38.keys()))
    idx_pool = np.nonzero(pool_mask)[0]
    wins_pool, phases_pool = [], []
    lc = code_g[idx_pool]
    ch = np.nonzero(lc[1:] != lc[:-1])[0] + 1
    s_b = np.concatenate([[0], ch])
    e_b = np.concatenate([ch, [len(idx_pool)]])
    for sb, eb in zip(s_b, e_b):
        block = idx_pool[sb:eb]
        g0, g1 = int(block[0]), int(block[-1])
        block = block[block + HOLD <= g1]
        if len(block) == 0:
            continue
        w = t20e_sim(close_g[g0:g1 + 1], block - g0)
        wins_pool.append(w)
        phases_pool.append(np.array([bull_by_d[int(d)] for d in d_g[block]], dtype=bool))
    pw = np.concatenate(wins_pool)
    pp = np.concatenate(phases_pool)
    base_bull = float(pw[pp].mean()) * 100
    base_bear = float(pw[~pp].mean()) * 100
    print(f"[2/3] 相位基线(池外全市场A格事件): 牛市={base_bull:.1f}% 弱势={base_bear:.1f}%  ({time.time()-t0:.1f}s)")

    rows = []
    repro_ok, repro_bad = 0, 0
    for code, name in pool38.items():
        gi = np.sort(df.groupby("code", sort=False).indices.get(code, np.array([], dtype=int)))
        if len(gi) == 0:
            continue
        c = close_g[gi]
        d_loc = d_g[gi]
        st_d = st_by_code.get(code, ())
        ok = ~np.isin(d_loc, np.fromiter(st_d, dtype="int64", count=len(st_d))) if st_d else np.ones(len(gi), bool)
        isA = is_A_g[gi]
        prevA = np.append([False], isA[:-1])
        ev = cooldown_filter(np.nonzero(isA & ~prevA & ok & (c <= 30.0))[0], COOLDOWN)
        wins = t20e_sim(c, ev)
        keep = ev + HOLD < len(c)
        ev = ev[keep]
        wins = wins[: len(ev)] if len(wins) != len(ev) else wins
        n = len(wins)
        if n == 0:
            rows.append({"code": code, "name": name, "n": 0})
            continue
        win = float(wins.mean()) * 100
        rg = np.random.default_rng(SEED)
        boots = [float(wins[rg.choice(n, n, replace=True)].mean()) * 100 for _ in range(NBOOT)]
        ci_lo, ci_hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
        half = n // 2
        w1 = float(wins[:half].mean()) * 100 if half else None
        w2 = float(wins[half:].mean()) * 100 if n - half else None
        phases = np.array([bull_by_d[int(d)] for d in d_loc[ev]], dtype=bool)
        wb = float(wins[phases].mean()) * 100 if phases.any() else None
        nb = int(phases.sum())
        wr = float(wins[~phases].mean()) * 100 if (~phases).any() else None
        nr = int((~phases).sum())
        p = prev.get(code, {})
        match = (p.get("win20e") is not None and abs(p["win20e"] - win) < 0.05 and p.get("n") == n)
        repro_ok += int(bool(match))
        repro_bad += int(not match)
        if half and w1 is not None and w2 is not None:
            stability = "稳定" if (w1 >= 50 and w2 >= 50) else ("反转" if (w1 - 50) * (w2 - 50) < 0 else "不稳")
        else:
            stability = "样本不足"
        rows.append({"code": code, "name": name, "n": n, "win": round(win, 1),
                     "ci95": [round(ci_lo, 1), round(ci_hi, 1)],
                     "half1": round(w1, 1) if w1 is not None else None,
                     "half2": round(w2, 1) if w2 is not None else None,
                     "stability": stability,
                     "phase_bull": round(wb, 1) if wb is not None else None, "n_bull": nb,
                     "phase_bear": round(wr, 1) if wr is not None else None, "n_bear": nr,
                     "prev_win": p.get("win20e"), "repro": bool(match)})

    repro_ok = sum(1 for r in rows if r.get("repro"))
    repro_bad = sum(1 for r in rows if r.get("repro") is False)
    rows.sort(key=lambda r: -(r.get("win") or -1))
    results = {"reproducibility": {"match": repro_ok, "mismatch": repro_bad},
               "phase_baseline": {"bull": round(base_bull, 1), "bear": round(base_bear, 1)},
               "ranking": rows}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[3/3] 完成 ({time.time()-t0:.1f}s) → {OUT}")
    print(f"可复现性: 与昨日对账 一致{repro_ok} / 不一致{repro_bad}")
    print(f"\n== 38只稳健排名(win降序; 相位基线: 牛{base_bull:.1f}%/弱{base_bear:.1f}%) ==")
    hdr = f"{'code':<8}{'name':<7}{'n':>4}{'win':>7}{'ci95':>16}{'h1':>7}{'h2':>7}{'稳定':>5}{'牛相':>7}{'弱相':>7}{'复验':>5}"
    print(hdr)
    for r in rows:
        if r.get("n", 0) == 0:
            print(f"{r['code']:<8}{r['name']:<7}{0:>4}  无事件"); continue
        ci = f"[{r['ci95'][0]},{r['ci95'][1]}]"
        print(f"{r['code']:<8}{r['name']:<7}{r['n']:>4}{r['win']:>6.1f}%{ci:>16}"
              f"{r['half1']:>6.1f}%{r['half2']:>6.1f}%{r['stability']:>5}"
              f"{(r['phase_bull'] if r['phase_bull'] is not None else 0):>6.1f}%"
              f"{(r['phase_bear'] if r['phase_bear'] is not None else 0):>6.1f}%"
              f"{'✓' if r['repro'] else '✗':>4}")


if __name__ == "__main__":
    sys.exit(main())
