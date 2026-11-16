# -*- coding: utf-8 -*-
"""adjustment_consistency_20261019.py — 呈请③a: 复权口径对照 不复权(raw) vs 前复权(qfq)（2026-10-19「继续」令）

问题(外部材料第二轮缺口1判定): 若复权口径改变信号收益结构(胜率差>3pp) → 数据问题先修, 不归因模型。
口径: 对 dose_n15 双臂全部入场事件(2016-2026), 以入场日为基点取 20 个交易日窗:
  fwd20_qfq = qfq close(t+20)/close(t) − 1 (前复权收益链)
  fwd20_raw = raw close(t+20)/close(t) − 1 (不复权, 含分红/送转缺口)
两口径差异大的事件=分红除权密集票; 若胜率差>3pp → 判数据风险。
真源: qfq=D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db (唯一真源);
      raw=D:/workbuddyspace/factor_engine/data/dragon_quant.db stock_daily_raw (不复权主库表)。
只读研究件·零交易权·零生产改动。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

TRADES_DIR = r"D:\zcodesj\reports\backtest_full_20261018_dose_n15"
QFQ_URI = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
RAW_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT_JSON = r"D:\zcodesj\analysis\adjustment_consistency_20261019_out.json"
OUT_MD = r"D:\zcodesj\reports\adjustment_consistency_20261019.md"


def load_events():
    frames = []
    for arm, fname in [("bp_ep", "bp_ep_core_trades.csv"), ("ml", "ml_signal_trades.csv")]:
        df = pd.read_csv(f"{TRADES_DIR}\\{fname}", encoding="utf-8-sig")
        df["arm"] = arm
        frames.append(df)
    t = pd.concat(frames, ignore_index=True)
    t["code"] = t["symbol"].astype(str).str.zfill(6)
    t["d0"] = pd.to_numeric(t["entry_date"].str.replace("-", ""), errors="coerce").astype("int64")
    return t.dropna(subset=["d0"])


def build_series(conn, table, codes):
    """code -> (dates int64 array 升序, closes array); 两库各自加载."""
    out = {}
    cur = conn.cursor()
    for code in codes:
        rows = cur.execute(
            f"select date, close from {table} where code=? order by date", (code,)
        ).fetchall()
        if rows:
            d = np.array([int(r[0]) for r in rows])
            v = np.array([float(r[1]) for r in rows if r[1] is not None])
            if len(d) == len(v):
                out[code] = (d, v)
    return out


def fwd_n(series, d0, n=20):
    if series is None:
        return np.nan
    d, v = series
    i = int(np.searchsorted(d, d0))
    if i >= len(d) or d[i] != d0 or i + n >= len(d):
        return np.nan
    base = v[i]
    if base <= 0:
        return np.nan
    return float(v[i + n] / base - 1.0)


def main():
    ev = load_events()
    codes = sorted(ev["code"].unique())

    cq = sqlite3.connect(QFQ_URI, uri=True)
    qfq_map = build_series(cq, "stock_daily_meta_qfq", codes)
    cq.close()
    cr = sqlite3.connect(RAW_DB)
    cur_r = cr.cursor()
    cols = [r[1] for r in cur_r.execute("pragma table_info(stock_daily_raw)")]
    date_col = "date" if "date" in cols else cols[1]
    raw_map = build_series(cr, "stock_daily_raw", codes)
    cr.close()

    rows = []
    n_no_raw = 0
    for r in ev.itertuples(index=False):
        a = fwd_n(qfq_map.get(r.code), int(r.d0))
        b = fwd_n(raw_map.get(r.code), int(r.d0))
        if np.isnan(b):
            n_no_raw += 1
        rows.append({"arm": r.arm, "year": int(r.d0) // 10000, "code": r.code, "d0": int(r.d0),
                     "f_qfq": a, "f_raw": b})
    X = pd.DataFrame(rows).dropna(subset=["f_qfq"], how="all")
    Xc = X.dropna(subset=["f_qfq", "f_raw"])
    Xc["d_abs"] = (Xc["f_qfq"] - Xc["f_raw"]).abs()

    def blk(col):
        s = Xc.dropna(subset=[col])
        if len(s) == 0:
            return None
        return {"n": int(len(s)), "win_pct": round(float((s[col] > 0).mean()) * 100, 2),
                "med_pct": round(float(s[col].median()) * 100, 3)}

    res = {
        "meta": {"events": int(len(ev)), "paired": int(len(Xc)), "raw_missing": int(n_no_raw),
                 "raw_cols": cols[:10], "criterion": "胜率差>3pp → 数据风险先修"},
        "overall": {"qfq": blk("f_qfq"), "raw": blk("f_raw")},
        "by_period": {},
        "big_diff_events": int((Xc["d_abs"] > 0.05).sum()),
        "median_abs_diff_pct": round(float(Xc["d_abs"].median()) * 100, 3),
    }
    for lo, hi, name in [(20160101, 20231231, "2016-2023"), (20240101, 20261016, "2024-2026")]:
        s = Xc[(Xc["d0"] >= lo) & (Xc["d0"] <= hi)]
        res["by_period"][name] = {"qfq": blk_col(s, "f_qfq"), "raw": blk_col(s, "f_raw")}

    win_q = res["overall"]["qfq"]["win_pct"] if res["overall"]["qfq"] else None
    win_r = res["overall"]["raw"]["win_pct"] if res["overall"]["raw"] else None
    diff_pp = round(win_q - win_r, 2) if (win_q is not None and win_r is not None) else None
    res["verdict"] = {"win_gap_pp": diff_pp,
                      "pass": bool(diff_pp is not None and abs(diff_pp) <= 3.0)}

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    L = ["# 复权口径对照：不复权(raw) vs 前复权(qfq) 信号20日收益（呈请③a · 2026-10-19）\n",
         f"> 只读研究件。队列=dose_n15 双臂入场事件 {res['meta']['events']} 件；配对成功 {res['meta']['paired']}（raw 缺失 {res['meta']['raw_missing']}，含退市段/停牌日历差）。窗口=入场日起 20 个交易日收盘对收盘。判定=两口径胜率差≤3pp PASS。\n",
         f"## 总判定: {'PASS' if res['verdict']['pass'] else 'FAIL'}（胜率差 {diff_pp}pp）\n",
         f"- qfq 口径: n={res['overall']['qfq']['n']}, 胜率 {res['overall']['qfq']['win_pct']}%, 中位 {res['overall']['qfq']['med_pct']}%",
         f"- raw 口径: n={res['overall']['raw']['n']}, 胜率 {res['overall']['raw']['win_pct']}%, 中位 {res['overall']['raw']['med_pct']}%",
         f"- 配对事件 |qfq−raw|>5pp: {res['big_diff_events']} 笔；|差|中位 {res['median_abs_diff_pct']}pp（分红/送转除权事件密度）\n",
         "## 分段\n",
         "| 段 | 口径 | n | 胜率% | 中位% |", "|---|---|---|---|---|"]
    for name, d in res["by_period"].items():
        for k, cn in [("qfq", "qfq"), ("raw", "raw")]:
            b = d.get(k)
            if b:
                L.append(f"| {name} | {cn} | {b['n']} | {b['win_pct']} | {b['med_pct']} |")
    L.append("\n- 注: 复权方式(qfq/hfq)只影响价格水平不影响相邻收益率, 与「不复权」的差才是分红缺口暴露——本件测的是后者(材料原文三口径中实质可区分的两口径)。")
    L.append("\n*脚本直出零转抄: analysis/adjustment_consistency_20261019.py。*")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("VERDICT:", res["verdict"], "| paired:", res["meta"]["paired"], "| big_diff>5pp:", res["big_diff_events"])


def blk_col(s, col):
    ss = s.dropna(subset=[col])
    if len(ss) == 0:
        return None
    return {"n": int(len(ss)), "win_pct": round(float((ss[col] > 0).mean()) * 100, 2),
            "med_pct": round(float(ss[col].median()) * 100, 3)}


if __name__ == "__main__":
    main()
