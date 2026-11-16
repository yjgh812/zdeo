#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""课题#2b：个股级机构净买强度选股研究（2026-09-08，席位明细 238 万行的正确用法）

预注册：data/wfa_prereg_lhb_inst_stock_20260908.json（跑前钉死）
oos_lock：统计 year<=2025。
设计：仅在"机构专用席位净买上榜的股票-日"内部，按 机构净买/accum_amount 五分位 →
     次 10 日收盘收益（next-bar，qfq），条件排序消除"上榜即异动"的共同选择偏差。
负先验：9/6 画像"龙虎榜净买=暴跌前兆"（lift 6.22），方向不预设。

运行：python lhb_inst_stock_study.py
输出：reports/LHB_INST_STOCK_20260908.{json,md} + wfa_trials.jsonl 追加
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
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_JSON = Path(BASE) / "reports" / "LHB_INST_STOCK_20260908.json"
OUT_MD = Path(BASE) / "reports" / "LHB_INST_STOCK_20260908.md"
TRIALS = Path(BASE) / "data" / "wfa_trials.jsonl"
PREREG = Path(BASE) / "data" / "wfa_prereg_lhb_inst_stock_20260908.json"

VERIFY_END_YEAR = 2025
HOLD = 10
MIN_GROUP = 200


def welch_t(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    return (a.mean() - b.mean()) / math.sqrt(va + vb) if (va + vb) > 0 else 0.0


def main() -> int:
    t0 = time.time()
    # ---- 1. 机构席位净买（股票-日聚合，去重）
    con = sqlite3.connect(f"file:{SEAT_DB}?mode=ro", uri=True, timeout=120)
    inst = pd.read_sql_query(
        "SELECT trade_date, security_code, net, accum_amount FROM lhb_seat_detail "
        "WHERE operate_dept_name LIKE '%机构专用%'", con)
    con.close()
    inst = inst.drop_duplicates(subset=["trade_date", "security_code", "net"])
    g = inst.groupby(["trade_date", "security_code"]).agg(
        inst_net=("net", "sum"), accum=("accum_amount", "max")).reset_index()
    g = g[g["accum"] > 0]
    g["strength"] = g["inst_net"] / g["accum"]
    g = g.sort_values(["trade_date", "security_code"]).reset_index(drop=True)
    print(f"[1/4] 机构净买股票-日={len(g)}", flush=True)

    # ---- 2. qfq 收益映射（次日买入持有 HOLD 日：c[t+1+HOLD]/c[t+1]-1, next-bar）
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
    px = pd.read_sql_query(
        "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20100101' ORDER BY code,date",
        con)
    con.close()
    px["code"] = px["code"].map(lambda c: __import__("re").sub(r"\D", "", str(c)).zfill(6)[-6:])
    fwd = {}
    for code, grp in px.groupby("code", sort=False):
        cc = grp["close"].to_numpy(dtype=float)
        dd = grp["date"].to_numpy().astype(str)
        for i in range(1, len(cc) - HOLD):
            fwd[(code, dd[i])] = cc[i + HOLD] / cc[i] - 1.0     # 信号日 t=dd[i], 次日买持有10
    print(f"[2/4] fwd map={len(fwd)} elapsed={time.time()-t0:.0f}s", flush=True)

    # ---- 3. 对齐
    g["key"] = list(zip(g["security_code"], g["trade_date"].str.replace("-", "", regex=False)))
    g["ret"] = g["key"].map(fwd)
    g["year"] = g["trade_date"].str[:4].astype(int)
    D = g.dropna(subset=["ret"])
    D = D[D["year"] <= VERIFY_END_YEAR]                     # oos_lock
    print(f"[3/4] 对齐样本={len(D)} elapsed={time.time()-t0:.0f}s", flush=True)

    # ---- 4. 年内五分位（按日截面更稳：先按(日)分位再汇总，避免跨年幅度漂移）
    def _q5(s: pd.Series) -> pd.Series:
        if len(s) < 5:
            return pd.Series(np.nan, index=s.index)
        return pd.qcut(s.rank(method="first"), 5, labels=False) + 1

    D["q"] = D.groupby("trade_date")["strength"].transform(_q5) if len(D) else np.nan
    D = D.dropna(subset=["q"])
    D["q"] = D["q"].astype(int)
    q_stats_df = D.groupby("q")["ret"].agg(n="count", mu="mean",
                                           win=lambda s: float((s > 0).mean()))
    q_stats = {int(k): {"n": int(r["n"]), "mu": round(float(r["mu"]), 5),
                        "win": round(float(r["win"]), 3)}
               for k, r in q_stats_df.iterrows()}
    q5 = D[D["q"] == 5]["ret"].to_numpy()
    q1 = D[D["q"] == 1]["ret"].to_numpy()
    t51 = welch_t(q5, q1)
    main_sign = "positive(聪明钱)" if t51 > 0 else "negative(出货陷阱)"
    # 方向一致年
    yr = {}
    for y, gg in D.groupby("year"):
        hi, lo = gg[gg["q"] == 5]["ret"], gg[gg["q"] == 1]["ret"]
        if len(hi) >= 20 and len(lo) >= 20:
            yr[int(y)] = welch_t(hi.to_numpy(), lo.to_numpy())
    main_pos = t51 > 0
    cons = sum(1 for v in yr.values() if (v > 0) == main_pos)
    h1 = bool(abs(t51) >= 2.0 and len(yr) >= 8 and cons >= 6)

    if h1 and main_pos:
        verdict = "H1_pass_positive"
        note = "聪明钱语义 → 登记观察（名单制通道候选，禁直接生产）"
    elif h1:
        verdict = "H1_pass_negative"
        note = "出货陷阱语义，与 9/6 画像互证 → 登记排雷信号（观察族）"
    else:
        verdict = "ARCHIVE"
        note = "上榜股内机构净买强度无区分度 → 归档"

    # 敏感性：净买为正样本
    Dp = D[D["inst_net"] > 0]
    p_q5 = Dp[Dp["q"] == 5]["ret"].to_numpy()
    p_q1 = Dp[Dp["q"] == 1]["ret"].to_numpy()
    sens_t = welch_t(p_q5, p_q1)

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": PREREG.name,
        "n_obs": len(D), "n_years": len(yr),
        "q_stats": q_stats,
        "H1": {"q5_q1_t": round(t51, 2), "q5_mu": round(float(q5.mean()), 5),
               "q1_mu": round(float(q1.mean()), 5), "main_sign": main_sign,
               "consistent_years": f"{cons}/{len(yr)}", "pass": h1},
        "sens_netbuy_positive_t": round(sens_t, 2),
        "verdict": verdict, "verdict_note": note,
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 个股级机构净买强度研究（上榜股内条件排序）2026-09-08", "",
          f"- 预注册 `{PREREG.name}` | 样本 {len(D)}（{len(yr)} 年）| oos_lock year≤{VERIFY_END_YEAR}",
          f"- 五分位次10日：{result['q_stats']}",
          f"- Q5-Q1 Welch t=**{t51:.2f}**，主方向={main_sign}，一致年 {cons}/{len(yr)} → H1={h1}",
          f"- 敏感性（净买为正样本内）t={sens_t:.2f}",
          f"- **判定：{verdict}** — {note}", ""]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    with open(TRIALS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": result["generated_at"], "trial": "lhb_inst_stock",
                             "verdict": verdict, "t51": round(t51, 2),
                             "main_sign": main_sign}, ensure_ascii=False) + "\n")

    print(f"[4/4] verdict={verdict} t51={t51:.2f} md={OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
