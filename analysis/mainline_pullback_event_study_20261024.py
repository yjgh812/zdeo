# -*- coding: utf-8 -*-
"""主线叠加×回调企稳 条件事件研究(20261024) — 依据 prereg_mainline_pullback_screen_20261024.md。

四cell一次性运行(冻结矩阵): A=回调企稳全市场(2022+,附2020+参考) / B=追高∩主线Top3(2022+) /
B_ref=追高全市场(2022+) / C=回调∩主线Top3(2022+)。
主线=申万L1(非PIT快照, 差值设计)等权20日动量Top3(成分>=10只)。
只读三库(qfq/st_history/industry_map_snapshot), 零写入, 零交易权。
输出: analysis/mainline_pullback_event_study_20261024_result.json + stdout摘要。
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STDB = r"D:\zcodesj\data\st_history.db"
INDDB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
OUT_JSON = r"D:\zcodesj\analysis\mainline_pullback_event_study_20261024_result.json"

LOOKBACK_START = "20190101"
EVAL_FULL = "20200104"       # cell A 参考全窗
EVAL_MAIN = "20220104"       # 主窗(对齐映射漂移先例)
EVAL_END = "20261023"
COOLDOWN = 10

PASS_MIN_EVENTS = 2000
PASS_MIN_WINRATE_20D = 0.52
PASS_MIN_EXCESS_20D = 0.005
GAIN_MIN_PP = 0.010          # 主线增益线 +1.0pp

TOPK_MAINLINE = 3
MIN_MEMBERS = 10


def norm_code(raw: str) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def in_universe(code6: str) -> bool:
    return len(code6) == 6 and code6.isdigit() and not code6.startswith(("688", "689", "4", "8", "92"))


def load_st_dates_by_code() -> dict:
    con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", con)
    con.close()
    out = {}
    for c, d in zip(df["code"].map(norm_code), df["date"].astype("int64")):
        out.setdefault(c, set()).add(int(d))
    return out


def load_l1_map() -> dict:
    con = sqlite3.connect(f"file:{INDDB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT code,l1,ambiguous_flag FROM l1_member", con)
    con.close()
    m = {}
    for c, l1, amb in zip(df["code"].map(norm_code), df["l1"], df["ambiguous_flag"]):
        if l1 and not amb:
            m[c] = l1
    return m


def cooldown_mask(sig: np.ndarray, cooldown: int) -> np.ndarray:
    out = np.zeros(len(sig), dtype=bool)
    last = -10**9
    for i in range(len(sig)):
        if sig[i] and (i - last) > cooldown:
            out[i] = True
            last = i
    return out


def main():
    t0 = time.time()
    st_by_code = load_st_dates_by_code()
    l1_map = load_l1_map()
    print(f"[1/5] ST股日={sum(len(v) for v in st_by_code.values())}  L1映射股={len(l1_map)}  ({time.time()-t0:.1f}s)")

    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,close,volume,change_pct FROM stock_daily_meta_qfq "
        f"WHERE date>='{LOOKBACK_START}' ORDER BY code,date", con)
    con.close()
    df["code6"] = df["code"].map(norm_code)
    df = df[df["code6"].map(in_universe)].copy()
    df["d"] = df["date"].astype("int64")
    df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
    df["l1"] = df["code6"].map(l1_map)
    print(f"[2/5] qfq 行数={len(df)} 股票数={df['code6'].nunique()} 有L1映射行占比={df['l1'].notna().mean():.1%}  ({time.time()-t0:.1f}s)")

    # ---- 行业等权指数与主线Top3集合 ----
    ind = df[df["l1"].notna()].groupby(["l1", "d"]).agg(r=("change_pct", "mean"), n=("change_pct", "size")).reset_index()
    piv_r = ind.pivot(index="d", columns="l1", values="r").sort_index() / 100.0
    piv_n = ind.pivot(index="d", columns="l1", values="n").sort_index()
    idxlvl = (1.0 + piv_r.fillna(0.0)).cumprod()
    mom20 = idxlvl / idxlvl.shift(20) - 1.0
    mom20[piv_n < MIN_MEMBERS] = np.nan
    dates_sorted = mom20.index.to_numpy()
    top3_by_date = {}
    for d, row in zip(dates_sorted, mom20.itertuples(index=False)):
        pairs = [(v, mom20.columns[i]) for i, v in enumerate(row) if not np.isnan(v)]
        pairs.sort(key=lambda x: (-x[0], x[1]))
        top3_by_date[int(d)] = {name for _, name in pairs[:TOPK_MAINLINE]}
    last_d = int(dates_sorted[-1])
    print(f"[3/5] 行业指数构建 行业数={mom20.shape[1]} 最新日({EVAL_END})Top3={sorted(top3_by_date[last_d])}  ({time.time()-t0:.1f}s)")

    base_rows, cell_events = [], {c: [] for c in ("A", "A_full", "B", "B_ref", "C")}
    grouped = list(df.groupby("code6", sort=False))
    for idx, (code, g) in enumerate(grouped):
        g = g.sort_values("d")
        close = g["close"].to_numpy(dtype=float)
        open_ = g["open"].to_numpy(dtype=float)
        high = g["high"].to_numpy(dtype=float)
        vol = g["volume"].to_numpy(dtype=float)
        chg = g["change_pct"].to_numpy(dtype=float)
        dates = g["d"].to_numpy(dtype="int64")
        l1arr = g["l1"].to_numpy()
        st_d = st_by_code.get(code)
        st_ok = ~np.isin(dates, np.fromiter(st_d, dtype="int64", count=len(st_d))) if st_d else np.ones(len(dates), bool)

        s = pd.Series(close)
        dif = (s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean())
        dea = dif.ewm(span=9, adjust=False).mean()
        dif = dif.to_numpy(); dea = dea.to_numpy()
        trend = (dif > dea) & (dif > 0)

        hh_prior = pd.Series(close).rolling(20, min_periods=20).max().shift(1).to_numpy()
        pullback = (close <= hh_prior * 0.97) & (close >= hh_prior * 0.88)
        vol5 = pd.Series(vol).rolling(5, min_periods=5).mean().shift(1).to_numpy()
        sig_pull = trend & pullback & (chg > 0) & (vol >= vol5)
        brk = close >= pd.Series(close).rolling(20, min_periods=20).max().to_numpy()
        sig_chase = trend & brk & (vol >= 1.5 * vol5) & (chg > 0)

        n = len(close)
        nxt_open = np.full(n, np.nan); nxt_open[:-1] = open_[1:]
        fwd = {}
        for k in (5, 10, 20):
            ex = np.full(n, np.nan)
            if n > k:
                ex[:n - k] = close[k:]
            fwd[k] = np.where(nxt_open > 0, ex / nxt_open - 1.0, np.nan)
        hh = pd.Series(high).rolling(20, min_periods=20).min().shift(-19).to_numpy()
        dd20 = np.where(nxt_open > 0, hh / nxt_open - 1.0, np.nan)
        inmain = np.array([l1 in top3_by_date.get(int(d), ()) for l1, d in zip(l1arr, dates)], dtype=bool)
        ev_ok = ~np.isnan(nxt_open) & (nxt_open > 0) & st_ok & ~np.isnan(fwd[20])
        w_full = ev_ok & (dates >= int(EVAL_FULL)) & (dates <= int(EVAL_END))
        w_main = ev_ok & (dates >= int(EVAL_MAIN)) & (dates <= int(EVAL_END))

        vmask = w_full
        if vmask.any():
            base_rows.append(pd.DataFrame({"date": dates[vmask], "one": 1.0,
                                           "r5": fwd[5][vmask], "r10": fwd[10][vmask], "r20": fwd[20][vmask],
                                           "w20": (fwd[20][vmask] > 0).astype(float)}))

        cells = {
            "A_full": (sig_pull, w_full), "A": (sig_pull, w_main),
            "B": (sig_chase & inmain, w_main), "B_ref": (sig_chase, w_main),
            "C": (sig_pull & inmain, w_main),
        }
        for cname, (sig, win) in cells.items():
            ev = cooldown_mask(sig, COOLDOWN) & win & ~np.isnan(fwd[20])
            for i in np.nonzero(ev)[0]:
                cell_events[cname].append({"code": code, "date": int(dates[i]),
                                           "r5": float(fwd[5][i]), "r10": float(fwd[10][i]),
                                           "r20": float(fwd[20][i]),
                                           "dd20": float(dd20[i]) if not np.isnan(dd20[i]) else np.nan})
        if (idx + 1) % 1500 == 0:
            print(f"    ... {idx+1}/{len(grouped)}  ({time.time()-t0:.1f}s)")

    print(f"[4/5] 信号计算完成  ({time.time()-t0:.1f}s)")
    base = pd.concat(base_rows, ignore_index=True)
    base_g = base.groupby("date").agg(m5=("r5", "mean"), m20=("r20", "mean"), w20=("w20", "mean"))

    def stat_cell(name, ev_list):
        ev = pd.DataFrame(ev_list)
        if ev.empty:
            return {"events": 0, "verdict": "NO_EVENTS"}
        b20 = ev["date"].map(base_g["m20"])
        ex = ev["r20"] - b20
        st = {"events": len(ev),
              "win20": float((ev["r20"] > 0).mean()),
              "win10": float((ev["r10"] > 0).mean()),
              "mean20": float(ev["r20"].mean()),
              "median20": float(ev["r20"].median()),
              "excess20": float(ex.mean()),
              "dd20_p50": float(ev["dd20"].median()),
              "dd20_p95": float(ev["dd20"].quantile(0.05)),
              "excess20_median": float(ex.median())}
        st["verdict"] = ("PASS_CANDIDATE" if (st["events"] >= PASS_MIN_EVENTS and st["win20"] >= PASS_MIN_WINRATE_20D
                                              and st["excess20"] >= PASS_MIN_EXCESS_20D and st["median20"] > 0)
                         else "FALSIFIED")
        return st

    result = {"generated_at": "2026-10-24", "main_window": [EVAL_MAIN, EVAL_END],
              "top3_last_day": sorted(top3_by_date[last_d]), "cells": {}}
    for cname in ("A_full", "A", "B", "B_ref", "C"):
        result["cells"][cname] = stat_cell(cname, cell_events[cname])

    gB = result["cells"]["B"]["excess20"] - result["cells"]["B_ref"]["excess20"]
    gC = result["cells"]["C"]["excess20"] - result["cells"]["A"]["excess20"]
    result["gain"] = {"mainline_on_chase_pp": gB, "mainline_on_pullback_pp": gC,
                      "gain_verdict_chase": "GAIN" if gB >= GAIN_MIN_PP else "NO_GAIN",
                      "gain_verdict_pullback": "GAIN" if gC >= GAIN_MIN_PP else "NO_GAIN"}

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[5/5] 完成  ({time.time()-t0:.1f}s)  → {OUT_JSON}")

    names = {"A_full": "回调全市场(2020+参考)", "A": "回调全市场(主窗2022+)", "B": "追高∩主线(2022+)",
             "B_ref": "追高全市场(2022+)", "C": "回调∩主线(2022+)"}
    for cname in ("A_full", "A", "B_ref", "B", "C"):
        s = result["cells"][cname]
        if s.get("events", 0) == 0:
            print(f"\n== {cname} {names[cname]}: NO_EVENTS")
            continue
        print(f"\n== {cname} {names[cname]} == 事件={s['events']} 胜率20日={s['win20']:.2%} "
              f"均值20日={s['mean20']:+.2%} 中位数={s['median20']:+.2%} 超额={s['excess20']:+.2%} "
              f"dd20p95={s['dd20_p95']:.1%} → {s['verdict']}")
    print(f"\n主线增益: 追高(B−B_ref)={gB:+.2%} {result['gain']['gain_verdict_chase']} | "
          f"回调(C−A)={gC:+.2%} {result['gain']['gain_verdict_pullback']}  (增益线=+1.0pp)")


if __name__ == "__main__":
    sys.exit(main())
