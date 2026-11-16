#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""课题#2：龙虎榜机构净买强度分布分位择时（华创2022-06，2026-09-08 解锁开工）

预注册：data/wfa_prereg_lhb_inst_timing_20260908.json（跑前钉死）
oos_lock：统计 year<=2025（2010 为 250 日分位暖机）。
特征 f_t = 全市场机构席位净买总额 / 沪深300当日成交额；
分位 = f_t 在过去 250 交易日的分位；
回测：分位五桶 → 沪深300 次日/次5日/次20日收益；主判据 P1=极端桶次20日差 |Welch t|>=2（方向记录），
     P2=方向一致年>=6/10。

运行：python lhb_inst_timing_study.py
输出：reports/LHB_INST_TIMING_20260908.{json,md} + wfa_trials.jsonl 追加
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
SEAT_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
IDX_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT_JSON = Path(BASE) / "reports" / "LHB_INST_TIMING_20260908.json"
OUT_MD = Path(BASE) / "reports" / "LHB_INST_TIMING_20260908.md"
TRIALS = Path(BASE) / "data" / "wfa_trials.jsonl"
PREREG = Path(BASE) / "data" / "wfa_prereg_lhb_inst_timing_20260908.json"

VERIFY_END_YEAR = 2025
ROLL = 250
HORIZONS = (1, 5, 20)


def welch_t(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    return (a.mean() - b.mean()) / math.sqrt(va + vb) if (va + vb) > 0 else 0.0


def main() -> int:
    t0 = time.time()
    # ---- 1. 机构席位净买（去重：多上榜原因重复披露同值合并）
    con = sqlite3.connect(f"file:{SEAT_DB}?mode=ro", uri=True, timeout=120)
    inst = pd.read_sql_query(
        "SELECT trade_date, security_code, net FROM lhb_seat_detail "
        "WHERE operate_dept_name LIKE '%机构专用%'", con)
    con.close()
    inst = inst.drop_duplicates(subset=["trade_date", "security_code", "net"])
    day_net = inst.groupby("trade_date")["net"].sum()
    day_net.index = day_net.index.astype(str).str.replace("-", "", regex=False)  # 席位库带横杠,统一为8位
    print(f"[1/4] 机构席位去重后行={len(inst)} 覆盖日={len(day_net)}", flush=True)

    # ---- 2. 沪深300
    con = sqlite3.connect(f"file:{IDX_DB}?mode=ro", uri=True, timeout=60)
    idx = pd.read_sql_query(
        "SELECT date, close, amount FROM index_daily WHERE index_code='000300' AND date>='20100101' ORDER BY date",
        con)
    con.close()
    idx["date"] = idx["date"].astype(str)
    idx = idx.set_index("date")
    print(f"[2/4] 沪深300 {len(idx)} 日 elapsed={time.time()-t0:.0f}s", flush=True)

    # ---- 3. 特征与滚动分位
    f = pd.DataFrame({"net": day_net}).join(idx[["close", "amount"]], how="inner")
    f["feat"] = f["net"] / f["amount"]
    f["pct"] = f["feat"].rolling(ROLL, min_periods=ROLL).rank(pct=True)
    f = f.dropna(subset=["pct"])
    f["year"] = [int(s[:4]) for s in f.index]
    f = f[f["year"] <= VERIFY_END_YEAR]                    # oos_lock
    # 前向收益
    close = idx["close"]
    for h in HORIZONS:
        f[f"fwd{h}"] = close.shift(-h) / close - 1.0
    f = f.dropna(subset=[f"fwd{h}" for h in HORIZONS])
    print(f"[3/4] 有效样本={len(f)} ({f.index[0]}~{f.index[-1]}) elapsed={time.time()-t0:.0f}s",
          flush=True)

    # ---- 4. 分位五桶回测
    f["bucket"] = pd.qcut(f["pct"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
    bucket_stats = {}
    for h in HORIZONS:
        g = f.groupby("bucket")[f"fwd{h}"]
        bucket_stats[h] = {int(k): {"n": int(len(v)), "mu": float(v.mean()),
                                    "win": float((v > 0).mean())}
                           for k, v in g}
    top = f[f["bucket"] == 5][f"fwd{HORIZONS[2]}"]
    bottom = f[f["bucket"] == 1][f"fwd{HORIZONS[2]}"]
    t20 = welch_t(top.to_numpy(), bottom.to_numpy())
    diff20 = float(top.mean() - bottom.mean())

    # P2 方向一致年（主方向 = sign(diff20)）
    main_sign = 1.0 if diff20 > 0 else -1.0
    f["_yr_ret"] = f[f"fwd{HORIZONS[2]}"]
    yr = {}
    f2 = f.copy()
    f2["grp"] = np.where(f2["bucket"] == 5, "top", np.where(f2["bucket"] == 1, "bot", ""))
    for y, g in f2.groupby("year"):
        t_ = g[g["grp"] == "top"]["_yr_ret"]
        b_ = g[g["grp"] == "bot"]["_yr_ret"]
        if len(t_) and len(b_):
            yr[int(y)] = float(t_.mean() - b_.mean())
    cons_years = sum(1 for v in yr.values() if v * main_sign > 0)
    p1 = bool(abs(t20) >= 2.0)
    p2 = bool(len(yr) >= 8 and cons_years >= 6)
    verdict = "PASS_observe" if (p1 and p2) else "ARCHIVE"

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": PREREG.name,
        "n_days": len(f), "span": f"{f.index[0]}~{f.index[-1]}",
        "bucket_fwd20": bucket_stats[HORIZONS[2]],
        "P1": {"t20": round(t20, 2), "diff20": round(diff20, 5),
               "top_mu": round(float(top.mean()), 5), "bot_mu": round(float(bottom.mean()), 5),
               "pass": p1},
        "P2": {"years": {int(k): round(v, 5) for k, v in yr.items()},
               "consistent_years": cons_years, "n_years": len(yr), "pass": p2},
        "main_sign": "top>bot(机构爆买后涨=聪明钱)" if main_sign > 0 else "top<bot(机构爆买后跌=物极必反)",
        "verdict": verdict,
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 龙虎榜机构净买强度分位择时（华创2022-06 复现）2026-09-08", "",
          f"- 预注册 `{PREREG.name}` | 样本 {len(f)} 日（{result['span']}）| oos_lock year≤{VERIFY_END_YEAR}",
          f"- 主方向：**{result['main_sign']}**",
          f"- 次20日：top(80-100分位) mu={result['P1']['top_mu']:+.5f} vs bot(0-20) mu={result['P1']['bot_mu']:+.5f}，"
          f"差={diff20:+.5f}，Welch t=**{t20:.2f}** → P1={p1}",
          f"- P2 方向一致年：{cons_years}/{len(yr)} → {p2}",
          f"- 分桶次20日：{json.dumps(result['bucket_fwd20'], ensure_ascii=False)}",
          f"- **判定：{verdict}**", ""]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    with open(TRIALS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": result["generated_at"], "trial": "lhb_inst_timing",
                             "verdict": verdict, "t20": round(t20, 2),
                             "main_sign": result["main_sign"]}, ensure_ascii=False) + "\n")

    print(f"[4/4] verdict={verdict} t20={t20:.2f} md={OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
