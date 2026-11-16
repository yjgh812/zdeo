# -*- coding: utf-8 -*-
"""旗舰volr排序篮子·生产语义走前锋(20261025) — 依据 prereg_lowconsol_volr_basket_20261025.md。

每月(20交易日间隔)调仓: m1池(低位横盘∩主线L1Top3)过闸(≤30/R32/流动性/非ST)按volr降序Top10,
次日开盘等权买入, T+20收盘卖出。基线B1=m1池全体等权; B2=全宇宙过闸逐日≤20抽样。
窗: W1=2022+, W2=2024+。只读, 零交易权。输出: analysis/lowconsol_volr_basket_wf_20261025_result.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
INDDB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
OUT = r"D:\zcodesj\analysis\lowconsol_volr_basket_wf_20261025_result.json"

LOAD_START = "20190101"
HOLD = 20
SEED = 42
TOPN = 10
WINDOWS = {"W1_2022_plus": 20220101, "W2_2024_plus": 20240101}


def main():
    t0 = time.time()
    st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
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
    gv = df.groupby("code")["volume"]
    df["volr"] = gv.transform(lambda s: s.rolling(5).mean() / s.rolling(60).mean())
    df["chg20"] = g.transform(lambda s: s.pct_change(20))
    df["amt20"] = df.groupby("code")["amount"].transform(lambda s: s.rolling(20).mean())
    df["l1"] = df["code"].map(l1_of)
    df["_tail"] = df.groupby("code").cumcount(ascending=False) < HOLD
    valid_base = (~df["_tail"]).to_numpy() & (df["volume"] > 0).to_numpy() & (df["open"] > 0).to_numpy()
    print(f"[1/3] 指标矩阵 行={len(df)}  ({time.time()-t0:.1f}s)")

    # 主线L1 Top3(扫描器公式)
    w = df[df["l1"].notna()]
    vol_l1 = w.groupby(["l1", "d"])["volume"].sum().rename("vol_l1").reset_index()
    icm = w.groupby(["l1", "d"])["chg5"].median().rename("icm").reset_index()
    ind = vol_l1.merge(icm, on=["l1", "d"]).sort_values(["l1", "d"])
    ind["v5"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.rolling(5).sum())
    ind["v5p"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
    ind["volr_l1"] = ind["v5"] / ind["v5p"]
    ind["c5"] = ind.groupby("l1")["icm"].transform(lambda s: s.rolling(5).mean())
    hot_by_date = {}
    for dt, sub in ind.groupby("d"):
        s = sub.dropna(subset=["volr_l1", "c5"])
        hot_by_date[int(dt)] = set(s[s["c5"] > 0].nlargest(3, "volr_l1")["l1"])

    d_g = df["d"].to_numpy(dtype="int64")
    o_g = df["open"].to_numpy(dtype=float)
    c_g = df["close"].to_numpy(dtype=float)
    code_g = df["code"].to_numpy()
    l1_g = df["l1"].to_numpy()
    volr_g = df["volr"].to_numpy(dtype=float)
    is_A_g = df["is_A"].fillna(False).to_numpy()
    ok_gate = ((c_g <= 30.0) & (df["chg20"].fillna(1) < 0.30).to_numpy()
               & (df["amt20"].fillna(0) >= 30e6) & (df["open"] > 0).to_numpy() & (df["volume"] > 0).to_numpy())
    st_ok_g = np.array([(d in st_by_code.get(c, ())) is False for c, d in zip(code_g, d_g)])
    gate_ok = ok_gate & st_ok_g & valid_base
    m1_mask = is_A_g & np.array([(l1 in hot_by_date.get(int(d), ())) for l1, d in zip(l1_g, d_g)], dtype=bool)
    basket_mask = m1_mask & gate_ok
    print(f"[2/3] m1&闸门事件日行数={int(basket_mask.sum())}  ({time.time()-t0:.1f}s)")

    # 每候选日候选表(date -> list of (row, volr))
    cand_rows = np.nonzero(basket_mask)[0]
    cand_by_date = {}
    for i in cand_rows:
        cand_by_date.setdefault(int(d_g[i]), []).append(i)
    all_rows = np.nonzero(valid_base)[0]
    all_by_date = {}
    for i in all_rows:
        all_by_date.setdefault(int(d_g[i]), []).append(i)
    ud = np.sort(uniq_dates := np.unique(d_g))

    code_starts = np.append([0], np.nonzero(code_g[1:] != code_g[:-1])[0] + 1)
    code_ends = np.append(code_starts[1:], len(code_g))

    def fwd(code_rows, j):
        """给定全局行号集合, T+1开盘→T+20收盘等权收益(缺行剔除)。"""
        rets = []
        for r in np.sort(code_rows):
            bi = int(np.searchsorted(code_starts, int(r), side="right") - 1)
            b0, b1 = int(code_starts[bi]), int(code_ends[bi])
            dd = d_g[b0:b1]
            loc = int(r) - b0
            ri = loc + 1
            if ri >= len(dd) or dd[ri] != ud[j + 1]:
                continue
            ri20 = ri + HOLD - 1
            if ri20 < len(dd) and dd[ri20] == ud[j + HOLD]:
                rets.append(c_g[b0 + ri20] / o_g[b0 + ri] - 1.0)
        return np.array(rets, dtype=float)

    def run_window(w_start):
        j0 = int(np.searchsorted(ud, w_start))
        rebal, rets_top, rets_pool, rets_uni = [], [], [], []
        rg = np.random.default_rng(SEED)
        j = j0
        while j + HOLD < len(ud):
            d = int(ud[j])
            rows = np.array(cand_by_date.get(d, []), dtype=int)
            if len(rows) >= TOPN:
                vr = volr_g[rows]
                vr = np.where(np.isnan(vr), -1.0, vr)
                top = rows[np.argsort(-vr)[:TOPN]]
                r_top = fwd(top, j)
                r_pool = fwd(rows, j)
                # B2: 全宇宙逐日≤20抽样
                arows = all_by_date.get(d, [])
                if len(arows) > 20:
                    arows = rg.choice(np.array(arows, dtype=int), 20, replace=False)
                r_uni = fwd(arows, j) if len(arows) else np.array([])
                if len(r_top) == TOPN and len(r_pool):
                    rebal.append(d)
                    rets_top.append(r_top.mean())
                    rets_pool.append(r_pool.mean())
                    if len(r_uni):
                        rets_uni.append(r_uni.mean())
            j += HOLD
        return {
            "rebalances": len(rebal),
            "top10_win": round(float(np.mean([r > 0 for r in rets_top])) * 100, 2),
            "top10_mean": round(float(np.mean(rets_top)) * 100, 2),
            "pool_mean": round(float(np.mean(rets_pool)) * 100, 2),
            "uni_mean": round(float(np.mean(rets_uni)) * 100, 2) if rets_uni else None,
            "excess_vs_pool_pp": round((float(np.mean(rets_top)) - float(np.mean(rets_pool))) * 100, 2),
            "excess_vs_uni_pp": round((float(np.mean(rets_top)) - float(np.mean(rets_uni))) * 100, 2) if rets_uni else None,
        }

    results = {}
    for tag, ws in WINDOWS.items():
        results[tag] = run_window(ws)
        print(f"[{tag}] {results[tag]}")

    gate = (results["W1_2022_plus"]["excess_vs_pool_pp"] >= 1.0 and results["W1_2022_plus"]["excess_vs_uni_pp"] > 0
            and results["W2_2024_plus"]["excess_vs_pool_pp"] >= 1.0 and results["W2_2024_plus"]["excess_vs_uni_pp"] > 0)
    results["verdict"] = "PASS" if gate else "FALSIFIED"
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[3/3] 完成 ({time.time()-t0:.1f}s) → {OUT}")
    print(f"\n判定(两窗均: 对池≥+1pp且对宇宙>0): {results['verdict']}")


if __name__ == "__main__":
    sys.exit(main())
