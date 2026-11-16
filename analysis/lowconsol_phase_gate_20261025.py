# -*- coding: utf-8 -*-
"""低位横盘池·相位门假设检验(20261025) — 依据 prereg_lowconsol_phase_gate_20261025.md。

生产口径(全史250日窗口) + 相位切分: 主相位=等权指数收盘>MA60; 辅相位=全市场20日额分位≥0.5。
事件=is_A全日+dpos冷却5; t20e收盘入场×0.92止损/T+20收盘; 双估计器(全池加权/每日≤20)。
只读, 零交易权。输出: analysis/lowconsol_phase_gate_20261025_result.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = r"D:\zcodesj\analysis\lowconsol_phase_gate_20261025_result.json"

LOAD_START = "20190101"
EVAL_START = 20200101
COOLDOWN = 5
HOLD = 20
STOP = 0.92
SEED = 42

WINDOWS = {
    "agg_2020_plus": (20200101, 20261231),
    "Y2024": (20240101, 20241231),
    "Y2025": (20250101, 20251231),
    "Y2026ytd": (20260101, 20261023),
}


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


def main():
    t0 = time.time()
    db = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,open,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
        f"WHERE date>='{LOAD_START}' ORDER BY code,date", db)
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
    print(f"[1/4] 状态矩阵 行={len(df)} 股={df['code'].nunique()}  ({time.time()-t0:.1f}s)")

    # 相位1(主): 等权指数收盘>MA60
    mkt = df.groupby("d").agg(chg=("change_pct", "mean"), amt=("amount", "sum")).reset_index().sort_values("d")
    idx_lvl = (1.0 + mkt["chg"].fillna(0.0) / 100.0).cumprod()
    ma60 = idx_lvl.rolling(60, min_periods=60).mean()
    bull_trend = (idx_lvl > ma60).to_numpy()
    # 相位2(辅): 全市场20日额分位≥0.5
    amt20 = mkt["amt"].rolling(20).mean()
    amt_q = amt20.rank(pct=True).to_numpy()
    bull_active = amt_q >= 0.5
    dates_m = mkt["d"].to_numpy(dtype="int64")
    bullT_by_d = dict(zip(dates_m, bull_trend))
    bullA_by_d = dict(zip(dates_m, bull_active))
    last_d = int(dates_m[-1])
    print(f"[2/4] 相位序列完成 最新日({last_d}) 主相位={'牛市' if bull_trend[-1] else '弱势'} "
          f"辅相位={'活跃' if bull_active[-1] else '低迷'} amt_q={amt_q[-1]:.3f}  ({time.time()-t0:.1f}s)")

    d_g = df["d"].to_numpy(dtype="int64")
    close_g = df["close"].to_numpy(dtype=float)
    code_g = df["code"].to_numpy()
    is_A_g = df["is_A"].fillna(False).to_numpy()

    # 事件: is_A全日+dpos冷却5
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
    code_starts = np.append([0], np.nonzero(code_g[1:] != code_g[:-1])[0] + 1)
    code_ends = np.append(code_starts[1:], len(code_g))
    order = np.argsort(ev_pos, kind="stable")
    ep = ev_pos[order]
    lc = code_g[ep]
    change = np.nonzero(lc[1:] != lc[:-1])[0] + 1
    s_b = np.concatenate([[0], change])
    e_b = np.concatenate([change, [len(ep)]])
    ev_win, ev_date, ev_bt, ev_ba = [], [], [], []
    for sb, eb in zip(s_b, e_b):
        block = ep[sb:eb]
        g0, g1 = int(block[0]), int(block[-1])
        block = block[block + HOLD <= g1]
        if len(block) == 0:
            continue
        w = t20e_sim(close_g[g0:g1 + 1], block - g0)
        ev_win.append(w)
        ev_date.append(d_g[block])
        ev_bt.append(np.array([bullT_by_d[int(d)] for d in d_g[block]], dtype=bool))
        ev_ba.append(np.array([bullA_by_d[int(d)] for d in d_g[block]], dtype=bool))
    ev_win = np.concatenate(ev_win)
    ev_date = np.concatenate(ev_date)
    ev_bt = np.concatenate(ev_bt)
    ev_ba = np.concatenate(ev_ba)
    print(f"[3/4] 事件模拟完成 事件数={len(ev_win)}  ({time.time()-t0:.1f}s)")

    # 基线: 非A格, 每日≤20(seed42), 分相位
    nonA_idx = np.nonzero(valid_base & ~is_A_g)[0]
    ordn = np.argsort(d_g[nonA_idx], kind="stable")
    nonA_sorted = nonA_idx[ordn]
    dsorted = d_g[nonA_sorted]
    ud_n, st_n = np.unique(dsorted, return_index=True)
    en_n = np.append(st_n[1:], len(dsorted))
    date_slice = {int(d): (int(s), int(e)) for d, s, e in zip(ud_n, st_n, en_n)}

    def phase_baseline(w, use_trend):
        lo, hi = w
        rngc = np.random.default_rng(SEED)
        sampled = []
        for d in ud_n:
            di = int(d)
            if not (lo <= di <= hi):
                continue
            b = bullT_by_d[di] if use_trend else bullA_by_d[di]
            if not b:
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
        ch = np.nonzero(lc[1:] != lc[:-1])[0] + 1
        s_b2 = np.concatenate([[0], ch])
        e_b2 = np.concatenate([ch, [len(blk)]])
        code_starts2 = np.append([0], np.nonzero(code_g[1:] != code_g[:-1])[0] + 1)
        code_ends2 = np.append(code_starts2[1:], len(code_g))
        wins = []
        for sb, eb in zip(s_b2, e_b2):
            block = blk[sb:eb]
            g0 = int(block[0])
            bi = int(np.searchsorted(code_starts2, g0, side="right") - 1)
            b0, b1 = int(code_starts2[bi]), int(code_ends2[bi])
            wins.append(t20e_sim(close_g[b0:b1], block - b0))
        pooled = [x for x in wins if len(x)]
        return float(np.concatenate(pooled).mean()) if pooled else None

    def phase_stats(w):
        lo, hi = w
        m = (ev_date >= lo) & (ev_date <= hi)
        out = {}
        for pname, pflag in (("trend_bull", ev_bt), ("amt_active", ev_ba)):
            sub = {}
            for lbl, ph in (("bull", pflag), ("bear", ~pflag)):
                mm = m & ph
                sub[lbl] = {"win": round(float(ev_win[mm].mean()) * 100, 2) if mm.any() else None,
                            "n": int(mm.sum())}
            bl = phase_baseline(w, pname == "trend_bull")
            sub["bull_effect_pp"] = round(sub["bull"]["win"] - bl * 100, 2) if (sub["bull"]["win"] is not None and bl) else None
            sub["baseline_bull"] = round(bl * 100, 2) if bl else None
            sub["interaction_pp"] = (round(sub["bull"]["win"] - sub["bear"]["win"], 2)
                                     if sub["bull"]["win"] is not None and sub["bear"]["win"] is not None else None)
            out[pname] = sub
        return out

    results = {"current_phase": {"date": last_d,
                                 "trend": "bull" if bull_trend[-1] else "bear",
                                 "activity": "active" if bull_active[-1] else "dry",
                                 "amt_q": round(float(amt_q[-1]), 3)}, "windows": {}}
    for tag, w in WINDOWS.items():
        results["windows"][tag] = phase_stats(w)
        print(f"[{tag}] " + json.dumps(results["windows"][tag], ensure_ascii=False))

    agg = results["windows"]["agg_2020_plus"]["trend_bull"]
    gate_pass = (agg["bull_effect_pp"] is not None and agg["bull_effect_pp"] >= 2.0
                 and agg["interaction_pp"] is not None and agg["interaction_pp"] >= 1.0)
    results["verdict"] = {
        "phase_gate": "PASS_PHASE_GATE" if gate_pass else "FALSIFIED",
        "y2026_bull_effect_pp": results["windows"]["Y2026ytd"]["trend_bull"]["bull_effect_pp"],
        "y2026_bull_sign": ("positive" if (results["windows"]["Y2026ytd"]["trend_bull"]["bull_effect_pp"] or 0) > 0
                            else "non_positive")}

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[4/4] 完成 ({time.time()-t0:.1f}s) → {OUT}")
    print(f"\n相位门主判定(聚合2020+, pooled): {results['verdict']['phase_gate']} "
          f"(牛市效应={agg['bull_effect_pp']}pp, 牛熊差={agg['interaction_pp']}pp)")
    print(f"2026YTD牛市子集效应: {results['verdict']['y2026_bull_effect_pp']}pp → {results['verdict']['y2026_bull_sign']}")


if __name__ == "__main__":
    sys.exit(main())
