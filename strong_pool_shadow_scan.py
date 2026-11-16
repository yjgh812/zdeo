# -*- coding: utf-8 -*-
"""strong_pool_shadow_scan.py — 强势池每日影子快照 (观测层, 零交易权, PD-021/选股端影子验证)

背景(9/28裁决「按推荐继续」): 事件研究证明三因动量池大涨事件率=基线3.7倍, 但等权净期望≈零
(+0.24%, tmp/pool_exit_backtest_20260928.py)——池=方向非买入清单; 本组件每日快照池成员,
积累前向样本供二次选择研究与池质量持续验证。准入唯一依据=admit闸门+R闸, 本清单非准入通道。

双口径打标(前向A/B):
- pool3 : 事件研究三因=近20日有涨停 ∧ 创60日新高 ∧ pos250>=80
- legacy: 2026-09-15 strong_pool_flag=MA5>MA10>MA20 ∧ close>MA20 ∧ ret20>0

用法: python strong_pool_shadow_scan.py [--date YYYYMMDD](默认=库最新交易日)
输出: data/strong_pool_shadow/pool_<date>.json (幂等覆盖) + track.jsonl (按date幂等追加)
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
OUT_DIR = "data/strong_pool_shadow"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    args = ap.parse_args()

    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    db = sqlite3.connect(DB, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,open,high,low,close,volume,change_pct,amount FROM stock_daily_meta_qfq "
        f"WHERE date>='20250101' ORDER BY code,date", db)
    db.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    g = df.groupby("code")
    rmin = g["close"].transform(lambda s: s.rolling(250, min_periods=120).min())
    rmax = g["close"].transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(rmax > rmin, (df["close"] - rmin) / (rmax - rmin) * 100, np.nan)
    df["limit_today"] = df["change_pct"] >= np.where(df["code"].str[0] == "3", 19.9, 9.9)
    df["near_limit20"] = g["limit_today"].transform(lambda s: s.rolling(20).max()) >= 1
    hh60 = g["close"].transform(lambda s: s.rolling(60).max())
    df["new_high_60"] = df["close"] >= hh60
    ma5 = g["close"].transform(lambda s: s.rolling(5).mean())
    ma10 = g["close"].transform(lambda s: s.rolling(10).mean())
    ma20 = g["close"].transform(lambda s: s.rolling(20).mean())
    ret20 = g["close"].transform(lambda s: s.pct_change(20) * 100)
    df["legacy_strong"] = (ma5 > ma10) & (ma10 > ma20) & (df["close"] > ma20) & (ret20 > 0)
    df["in_pool3"] = df["near_limit20"] & df["new_high_60"] & (df["pos250"] >= 80)
    # PD-021 prereg 影子池(9/28回测: V6 -12%+T20 净均值+0.65%/中位+0.14% 转正, 止损率13.4%)
    df["vwap"] = np.where(df["volume"] > 0, df["amount"] / df["volume"], np.nan)
    df["chg5"] = g["close"].transform(lambda s: s.pct_change(5) * 100)
    df["in_pool_a8"] = (df["vwap"] <= 8) & (df["pos250"] < 30) & (df["chg5"].abs() < 3)

    tgt = args.date or str(df["date"].max())
    snap = df[df["date"] == tgt]
    if len(snap) == 0:
        print(f"目标日 {tgt} 无数据")
        return 2
    vwap = np.where(snap["volume"] > 0, snap["amount"] / snap["volume"], np.nan)
    m3 = snap[snap["in_pool3"]].copy()
    vwap_s = pd.Series(vwap, index=snap.index)
    m3["vwap_calc"] = vwap_s.loc[m3.index]
    rows3 = [{"code": c, "pos250": round(float(p), 1),
              "vwap": round(float(v), 2) if pd.notna(v) else None,
              "chg_pct": round(float(ch), 2)}
             for c, p, v, ch in zip(m3["code"], m3["pos250"], m3["vwap_calc"], m3["change_pct"])]

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": tgt,
        "n_pool3": len(rows3), "n_legacy": int(snap["legacy_strong"].sum()),
        "n_both": int((snap["in_pool3"] & snap["legacy_strong"]).sum()),
        "n_pool_a8": int(snap["in_pool_a8"].sum()),
        "pool_a8_members": snap.loc[snap["in_pool_a8"], "code"].tolist(),
        "pool3_members": rows3,
        "legacy_members": snap.loc[snap["legacy_strong"], "code"].tolist(),
        "disclaimer": "观测快照零交易权; 池=方向非买入清单(等权净期望≈零见tmp/pool_exit_backtest_20260928.py); pool_a8=PD-021宽止损prereg影子池(仅研究口径,止损铁律改动须主人裁决); 准入=admit闸门+R闸",
    }
    with open(f"{OUT_DIR}/pool_{tgt}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    track = f"{OUT_DIR}/track.jsonl"
    have = set()
    try:
        for line in open(track, encoding="utf-8"):
            if line.strip():
                have.add(json.loads(line).get("date"))
    except FileNotFoundError:
        pass
    if tgt not in have:
        with open(track, "a", encoding="utf-8") as f:
            f.write(json.dumps({"date": tgt, "n_pool3": len(rows3),
                                "n_legacy": out["n_legacy"], "n_both": out["n_both"],
                                "n_pool_a8": out["n_pool_a8"],
                                "generated": out["generated"]}, ensure_ascii=False) + "\n")
        print(f"快照落盘 {OUT_DIR}/pool_{tgt}.json (池{len(rows3)}/legacy{out['n_legacy']}/双标{out['n_both']}/A8池{out['n_pool_a8']}) + track追加")
    else:
        print(f"快照落盘 {OUT_DIR}/pool_{tgt}.json (track已有{tgt}, 未重复追加)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
