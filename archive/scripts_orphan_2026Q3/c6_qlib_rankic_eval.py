#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t39 C6 实证 — qlib 混合信号 RankIC 诚实评估
============================================
验证 MEMORY 定论「qlib RankIC≤0（日频无预测力），仅因子研究工具」。

口径:
  - 信号: stock_signals_qlib_hybrid (qlib_score/qlib_rank/hybrid_score), 2025-07~2026-08
  - 收益: qfq 日收益 (next-bar 前向 1/5/20 日累计)
  - IC: 每日截面 Spearman RankIC（信号 vs 前向收益）
  - 统计: 均值/ICIR/正率 + IC t 检验（statlib 风格, 此处内联计算）

铁律: 只读（mode=ro）；不写库。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from strategy_test_harness import (load_qfq, load_raw_close, load_st_codes,
                                   clean_returns, REPORT_DIR)

DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
START, END = "20250701", "20260814"


def load_signals() -> pd.DataFrame:
    import sqlite3
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=120)
    df = pd.read_sql_query(
        "SELECT code, date, qlib_score, qlib_rank, hybrid_score "
        "FROM stock_signals_qlib_hybrid WHERE date BETWEEN ? AND ?",
        conn, params=[START, END])
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    return df


def main() -> int:
    t0 = time.time()
    print("[C6] loading ...")
    q = load_qfq(START, END, fields=("close",))
    close = q["close"]
    ret, polluted = clean_returns(close)
    sig = load_signals()
    st = load_st_codes()
    print(f"[C6] signals {len(sig):,}, ret {ret.shape}, polluted {len(polluted)}")

    # 前向收益: 1/5/20 日
    fwd = {}
    for h in (1, 5, 20):
        mp = 1 if h == 1 else max(2, h // 2)
        fwd[h] = (1 + ret).rolling(h, min_periods=mp).apply(
            lambda x: np.prod(x) - 1, raw=True).shift(-h)

    cols = {"qlib_score": [], "hybrid_score": [], "qlib_rank": []}
    for d, grp in sig.groupby("date"):
        loc = ret.index.get_loc(d) if d in ret.index else None
        if loc is None or loc + 1 >= len(ret.index):
            continue
        for name in cols:
            sub = grp[["code", name]].dropna()
            sub = sub[~sub["code"].isin(st)]
            if len(sub) < 50:
                continue
            sub = sub.set_index("code")[name]
            ic_row = {}
            for h in (1, 5, 20):
                end_pos = min(loc + h, len(ret.index) - 1)
                fr = (1 + ret.iloc[loc + 1:end_pos + 1]).prod() - 1
                common = sub.index.intersection(fr.dropna().index)
                if len(common) < 50:
                    continue
                ic_row[f"ic{h}"] = spearmanr(sub.loc[common], fr.loc[common]).statistic
            if ic_row:
                cols[name].append({"date": d, **ic_row})

    lines = ["# C6 qlib 混合信号 RankIC 诚实评估", "",
             f"- 生成: {time.strftime('%Y-%m-%d %H:%M')} | 区间 {START}~{END}",
             f"- 信号: stock_signals_qlib_hybrid ({len(sig):,} 行), 2025-07~2026-08",
             "- 口径: qfq next-bar 前向收益, 截面 Spearman RankIC, 非ST",
             "",
             "| 信号 | horizon | 均值IC | ICIR | 正率 | t | p | 样本天数 |",
             "|---|---|---|---|---|---|---|---|"]
    for name, rows in cols.items():
        df = pd.DataFrame(rows).set_index("date").sort_index()
        for h in (1, 5, 20):
            s = df[f"ic{h}"].dropna()
            if len(s) < 20:
                continue
            ic_m = s.mean()
            ic_std = s.std(ddof=1)
            icir = ic_m / ic_std if ic_std > 0 else 0
            pos = (s > 0).mean()
            t = ic_m / (ic_std / np.sqrt(len(s))) if ic_std > 0 else 0
            from scipy.stats import t as tdist
            p = 2 * (1 - tdist.cdf(abs(t), len(s) - 1))
            lines.append(f"| {name} | {h}日 | {ic_m:+.4f} | {icir:+.3f} | {pos:.0%} | {t:+.2f} | {p:.3f} | {len(s)} |")
            print(f"[C6] {name} {h}d: IC={ic_m:+.4f} ICIR={icir:+.3f} pos={pos:.0%} t={t:+.2f} p={p:.3f}")

    lines += ["",
              "## 判读",
              "- MEMORY 定论复核: qlib 日频信号是否存在预测力（RankIC 显著性与方向）",
              "- 若 IC≈0 或为负 → 维持「qlib 仅因子研究工具、不并入生产」结论",
              "",
              f"- 耗时 {time.time()-t0:.0f}s", ""]
    p = REPORT_DIR / f"C6_qlib_rankic_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"[C6] report: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
