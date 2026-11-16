# -*- coding: utf-8 -*-
"""
st_universe_materiality_20261023.py — 发现B材料度定量（审计报告AUDIT_EXTERNAL_TDX_PIPELINE_20261023 §七）
=========================================================================================================
只读诊断：复刻 bt_bp_ep_realistic_backtest.py build_signals 的历史月度 Top20 选股代码
（START/END/TOPN/PRICE_MAX 逐常量对齐；industry cap 不复刻=只影响行业内替换不改变主导成分），
逐月统计选股中 科创(688/689)/北交(8/4/92前缀)/当前快照ST 的占比。
零 P&L、零改判——不重算任何收益数字，仅为「标注口径 vs 重跑」裁决提供材料度证据。
注意：当前快照ST 只是历史真实ST的下界（曾ST已摘帽/已退市的票不在集内），实际混入≥此数。

用法: python analysis/st_universe_materiality_20261023.py
输出: reports/st_universe_materiality_20261023.json + stdout 摘要
"""
from __future__ import annotations

import json
import sqlite3
import time

import pandas as pd

FACTOR_URI = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro"
START, END = 20180101, 20260814          # bt_bp_ep_realistic_backtest.py:36
TOPN, PRICE_MAX = 20, 30.0               # :37-38
OUT_JSON = r"D:\zcodesj\reports\st_universe_materiality_20261023.json"


def classify(code: str, st: set[str]) -> tuple[bool, bool, bool]:
    star = code.startswith(("688", "689"))
    bse = (not star) and (code.startswith("8") or code.startswith("4") or code.startswith("92"))
    return star, bse, code in st


def main() -> None:
    t0 = time.time()
    con = sqlite3.connect(FACTOR_URI, uri=True)
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_factors_fundamental "
        "WHERE date BETWEEN ? AND ? ORDER BY date", (START, END))]
    me = [int(x) for x in pd.DataFrame({"d": dates}).assign(ym=lambda t: t["d"].astype(str).str[:6])
          .groupby("ym")["d"].max().tolist()]
    st = {str(r[0]).zfill(6) for r in con.execute(
        "SELECT code FROM stock_info WHERE is_st=1 OR name LIKE '%ST%'")}
    ph = ",".join("?" * len(me))
    snap = pd.read_sql_query(
        f"SELECT date, code, close, ep, bp FROM stock_factors_fundamental WHERE date IN ({ph})",
        con, params=me)
    con.close()
    snap["code"] = snap["code"].astype(str).str.zfill(6)

    buckets = ("picks", "star", "bse", "snapshot_st", "star_or_bse_or_st")
    overall = dict.fromkeys(buckets, 0)
    overall["months"] = 0
    distinct: set[str] = set()
    per_year: dict[str, dict[str, int]] = {}

    for d in me:
        g = snap[(snap["date"] == d) & (snap["close"] > 0) & (snap["close"] <= PRICE_MAX)]
        g = g.dropna(subset=["ep", "bp"])
        if len(g) < TOPN * 3:
            continue
        g = g.assign(combo=(g["ep"].rank(pct=True) + g["bp"].rank(pct=True)) / 2)
        picks = g.sort_values("combo", ascending=False)["code"].head(TOPN).tolist()

        y = str(d)[:4]
        overall["months"] += 1
        rec = per_year.setdefault(y, dict.fromkeys(buckets, 0))
        for c in picks:
            star, bse, cst = classify(c, st)
            hits = {"picks": True, "star": star, "bse": bse, "snapshot_st": cst,
                    "star_or_bse_or_st": star or bse or cst}
            distinct.add(c)
            for b in buckets:
                if hits[b]:
                    overall[b] += 1
                    rec[b] += 1

    out = {
        "caliber": ("复刻bt_bp_ep_realistic_backtest.py:73-93月度Top20选股(20180101~20260814, "
                    "close>0且<=30, ep/bp非空, (ep.rank(pct)+bp.rank(pct))/2 Top20, rank=average法; "
                    "industry cap不复刻), 当前快照ST=历史真实ST下界, 只统计成分构成零P&L"),
        "snapshot_st_size": len(st),
        "overall": {**overall, "distinct_codes": len(distinct)},
        "per_year": per_year,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    p = overall["picks"]
    print(f"月份={overall['months']} 选股总笔数={p} distinct={len(distinct)}")
    for b, label in (("star", "科创"), ("bse", "北交"), ("snapshot_st", "快照ST"),
                     ("star_or_bse_or_st", "合计命中")):
        print(f"{label}={overall[b]}({overall[b]/p*100:.2f}%)", end="  ")
    print()
    for y in sorted(per_year):
        r = per_year[y]
        print(f"  {y}: picks={r['picks']} 科创={r['star']} 北交={r['bse']} "
              f"快照ST={r['snapshot_st']} 命中={r['star_or_bse_or_st']}"
              f"({r['star_or_bse_or_st']/max(r['picks'],1)*100:.1f}%)")


if __name__ == "__main__":
    main()
