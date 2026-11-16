# -*- coding: utf-8 -*-
"""低位横盘效应复验v3 — 全保真复刻原runner口径 + 差异消融归因(20261024)。

承接 prereg_lowconsol_reverify_20261024.md §5: 首跑与档案差异(+5.91/+4.55 vs 复刻+1.00/+2.09)的根因排查。
原runner关键语义(逐条复刻):
  1) LOAD_FROM截断: stage1=20240101 / OOS=20210101 — pos250在截断帧上 rolling(250,min_periods=120)
  2) 退市库并入: baostock段×backAdjustFactor(merge_asof backward), 视野不足→最后收盘强制平仓
  3) 冷却=dpos(池内唯一日期序)差<5跳过; 事件=is_A全日(非状态转换)
  4) 每日随机≤20只(单流rng(42), sort(date,rn).head(20)); 对照=非A格每日随机≤20(random_state=42)
  5) t20e: 收盘入场×0.92, fut<=stop_line即当日收盘止损, 否则T+20收盘
消融矩阵: W2 {截断加载 vs 全史加载} × {含退市 vs 不含} → 分离各因子贡献。
只读, 零交易权。输出: analysis/lowconsol_reverify_v3_20261024_result.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DLDB = r"D:\zcodesj\data\delisted_segments_baostock.db"
OUT = r"D:\zcodesj\analysis\lowconsol_reverify_v3_20261024_result.json"

COOLDOWN = 5
HOLD = 20
STOP = 0.92
SEED = 42
ARCHIVE = {"W1": 5.91, "W2": 4.55}


def load_delisted_faithful():
    c = sqlite3.connect(DLDB)
    raw = pd.read_sql_query("SELECT code,date,open,close,volume,amount FROM stock_daily_delisted_raw", c)
    fac = pd.read_sql_query("SELECT code,date,backAdjustFactor FROM adjust_factor", c)
    c.close()
    raw["code6"] = raw["code"].str.split(".").str[1]
    raw["date"] = raw["date"].str.replace("-", "", regex=False).astype(int)
    fac["code6"] = fac["code"].str.split(".").str[1]
    fac["date"] = fac["date"].str.replace("-", "", regex=False).astype(int)
    m = pd.merge_asof(raw.sort_values("date"), fac[["code6", "date", "backAdjustFactor"]].sort_values("date"),
                      on="date", by="code6", direction="backward")
    m["backAdjustFactor"] = m["backAdjustFactor"].fillna(1.0)
    m["close"] = m["close"] * m["backAdjustFactor"]
    m["open"] = m["open"] * m["backAdjustFactor"]
    m = m.drop(columns=["code"]).rename(columns={"code6": "code"})[
        ["code", "date", "open", "close", "volume", "amount"]]
    return m[(m["date"] >= 20220101) & (m["code"].str[0].isin(["6", "0", "3"]))]


def build_indicators(df):
    """与原runner同构(在传入帧上直接计算, 不做额外过滤): days>60(帧内), chg5/pos250/t20e。"""
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    g = df.groupby("code")["close"]
    df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
    mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
    df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)
    out = []
    for code, sub in df.groupby("code", sort=False):
        close = sub["close"].to_numpy()
        n = len(close)
        fut = np.full((n, HOLD), np.nan)
        for k in range(1, min(HOLD + 1, n)):
            fut[: n - k, k - 1] = close[k:]
        entry = close
        stop_line = entry * STOP
        stop_px = np.full(n, np.nan)
        for k in range(HOLD):
            m_ = np.isnan(stop_px) & (fut[:, k] <= stop_line)
            stop_px[m_] = fut[m_, k]
        t20e = np.where(~np.isnan(stop_px), stop_px / entry - 1,
                        np.where(~np.isnan(fut[:, -1]), fut[:, -1] / entry - 1, np.nan))
        out.append(pd.DataFrame({"code": code, "date": sub["date"].to_numpy(), "is_A": sub["is_A"].to_numpy(),
                                 "close": entry, "volume": sub["volume"].to_numpy(),
                                 "open": sub["open"].to_numpy(), "t20e": t20e}))
    return pd.concat(out, ignore_index=True)


def load_main_truncated(load_from):
    db = sqlite3.connect(QFQ, uri=True)
    main = pd.read_sql_query(
        f"SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='{load_from}' "
        "ORDER BY code,date", db)
    db.close()
    main["date"] = main["date"].astype("int64")
    return main


def force_exit_delisted(dl_ind):
    lastpx = dl_ind.sort_values("date").groupby("code")["close"].last()
    nanm = dl_ind["t20e"].isna() & dl_ind["is_A"].notna()
    dl_ind.loc[nanm, "t20e"] = dl_ind.loc[nanm, "code"].map(lastpx) / dl_ind.loc[nanm, "close"] - 1
    return dl_ind


def run_faithful(tag, load_from, ev_lo, ev_hi, dl_on, isdl, cap_on=True):
    t0 = time.time()
    pool = build_indicators(load_main_truncated(load_from))
    n_main = pool["code"].nunique()
    if dl_on:
        dl = build_indicators(load_delisted_faithful())
        if isdl:
            dl = force_exit_delisted(dl)
        pool = pd.concat([pool, dl], ignore_index=True)
    pool = pool[pool["t20e"].notna() & (pool["volume"] > 0) & (pool["open"] > 0)].copy()
    pool = pool.sort_values(["code", "date"])
    date_order = {d: i for i, d in enumerate(np.sort(pool["date"].unique()))}
    pool["dpos"] = pool["date"].map(date_order)
    # 事件: is_A全日 + dpos冷却5 (逐股贪心, 与原runner同构)
    ev_parts = []
    dpos_g = pool["dpos"].to_numpy()
    isA_g = pool["is_A"].fillna(False).to_numpy()
    for code, gi in pool.groupby("code", sort=False).indices.items():
        gi = np.sort(gi)
        local = np.nonzero(isA_g[gi])[0]
        keep, last = [], -10**9
        for i in local:
            if dpos_g[gi[i]] - last >= COOLDOWN:
                keep.append(i)
                last = dpos_g[gi[i]]
        if keep:
            ev_parts.append(gi[np.array(keep, dtype=int)])
    ev = pool.iloc[np.concatenate(ev_parts)].copy()
    ev = ev[(ev["date"] >= ev_lo) & (ev["date"] <= ev_hi)]
    # 每日≤20: 单流rng(42), sort(date,rn).head(20) — 原runner同构
    if cap_on:
        rng = np.random.default_rng(SEED)
        ev["rn"] = rng.random(len(ev))
        ev = ev.sort_values(["date", "rn"]).groupby("date", as_index=False).head(20)
    win_A = float((ev["t20e"] > 0).mean())
    # 对照: 非A格, 每日随机≤20(random_state=42) — 原runner同构
    rnd = pool[~pool["is_A"].fillna(False)]
    rnd = rnd[(rnd["date"] >= ev_lo) & (rnd["date"] <= ev_hi)]
    rnd = rnd.groupby("date", group_keys=False).apply(
        lambda s: s.sample(n=min(20, len(s)), random_state=SEED))
    win_R = float((rnd["t20e"] > 0).mean())
    d = {"win_A": round(win_A * 100, 2), "n_A": int(len(ev)),
         "win_R": round(win_R * 100, 2), "n_R": int(len(rnd)),
         "effect_pp": round((win_A - win_R) * 100, 2),
         "load_from": load_from, "dl_on": dl_on, "forced_exit": isdl and dl_on}
    if tag in ARCHIVE:
        d["archive_pp"] = ARCHIVE[tag]
        d["gap_pp"] = round(d["effect_pp"] - ARCHIVE[tag], 2)
    print(f"[{tag}] load={load_from} dl={dl_on}({isdl}) win_A={d['win_A']}% n_A={d['n_A']} "
          f"win_R={d['win_R']}% effect={d['effect_pp']:+.2f}pp"
          + (f" (档案{ARCHIVE[tag]:+.2f}, gap={d['gap_pp']:+.2f})" if tag in ARCHIVE else "")
          + f"  ({time.time()-t0:.1f}s)")
    return d


def main():
    t0 = time.time()
    results = {}
    # W1: OOS runner 复刻 (load 2021, 事件窗2022-2023, 含退市)
    results["W1_faithful"] = run_faithful("W1_faithful", "20210101", 20220101, 20231231, True, True)
    # W2: stage1 runner 复刻 (load 2024, 事件窗2024+, 含退市)
    results["W2_faithful"] = run_faithful("W2_faithful", "20240101", 20240101, 20261023, True, True)
    # 消融: W2 截断加载但不含退市 → 退市库贡献
    results["W2_trunc_nodl"] = run_faithful("W2_trunc_nodl", "20240101", 20240101, 20261023, False, False)
    # 消融: W2 全史加载(2019起)不含退市 → 截断贡献(应≈首跑稳健口径)
    results["W2_full_nodl"] = run_faithful("W2_full_nodl", "20190101", 20240101, 20261023, False, False)
    # 消融: W1 全史加载不含退市 → 截断贡献
    results["W1_full_nodl"] = run_faithful("W1_full_nodl", "20190101", 20220101, 20231231, False, False)
    # OOS runner实为无帽估计器(n_A=156379): W1 无帽+2021截断+含退市 → 终局对齐
    results["W1_oos_exact"] = run_faithful("W1_oos_exact", "20210101", 20220101, 20231231, True, True, cap_on=False)
    # 生产口径参照: 全史加载无帽不含退市
    results["W1_full_uncapped"] = run_faithful("W1_full_uncapped", "20190101", 20220101, 20231231, False, False, cap_on=False)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[done] ({time.time()-t0:.1f}s) → {OUT}")
    print("归因摘要: W2_full_nodl→W2_trunc_nodl = 截断贡献; W2_trunc_nodl→W2_faithful = 退市库贡献")


if __name__ == "__main__":
    sys.exit(main())
