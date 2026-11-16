# -*- coding: utf-8 -*-
"""Kronos v2 sweep三关评估 — Fwd10口径对齐官方H=10 (2026-08-18)
用法: python kronos_eval_v2_20260818.py <sweep_csv> [<fwd=10>]
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

CSV = sys.argv[1] if len(sys.argv) > 1 else r"D:\zcodesj\Kronos\finetune_csv\ckpt_ashare_v2_sweep_v2.csv"
FWD = int(sys.argv[2]) if len(sys.argv) > 2 else 10
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def fwd_returns(px):
    out = {}
    px = px.sort_values("date").reset_index(drop=True)
    for code, g in px.groupby("code"):
        c, d = g["close"].values, g["date"].values
        for i in range(len(g) - FWD):
            out[(d[i], code)] = c[i + FWD] / c[i] - 1
    return out


def main():
    sig = pd.read_csv(CSV)
    sig["date"] = sig["date"].astype(str)
    sig["code"] = sig["code"].astype(str).str.zfill(6)
    con = sqlite3.connect(QFQ_DB)
    q = ",".join(f"'{c}'" for c in sig["code"].unique())
    px = pd.read_sql_query(
        f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({q})", con)
    con.close()
    fr = fwd_returns(px)
    sig["fwd"] = [fr.get((d, c), np.nan) for d, c in zip(sig["date"], sig["code"])]
    sig = sig.dropna(subset=["fwd"])
    print(f"[data] 有效样本 {len(sig)} / {sig['date'].nunique()} 期 (Fwd{FWD})")

    cols = [c for c in ("sig_horizon_mean", "sig_last", "mean_ret", "p_up")
            if c in sig.columns]
    lines = [f"# Kronos v2官方口径评估 ({os.path.basename(CSV)}, Fwd{FWD})", ""]
    best = None
    for col in cols:
        ics, sp = [], []
        for d, g in sig.groupby("date"):
            if len(g) < 30:
                continue
            ic, _ = spearmanr(g[col], g["fwd"])
            ics.append((d, ic))
            if len(g) >= 50:
                qq = pd.qcut(g[col].rank(method="first"), 5, labels=False)
                mm = g.groupby(qq)["fwd"].mean()
                sp.append(mm.iloc[-1] - mm.iloc[0])
        ic_df = pd.DataFrame(ics, columns=["date", "ic"])
        m, sd = ic_df["ic"].mean(), ic_df["ic"].std()
        t = m / (sd / np.sqrt(len(ic_df))) if sd > 0 else 0
        sp = np.array(sp)
        gate = m >= 0.03 and sp.mean() > 0
        tag = "PASS" if gate else "FAIL"
        print(f"[{col:18s}] IC均值{m:+.4f} t={t:+.2f} 正占比{(ic_df['ic']>0).mean():.0%} "
              f"| q5-q1月均{sp.mean():+.4f} 胜率{(sp>0).mean():.0%} -> {tag}")
        lines.append(f"- **{col}**: IC{m:+.4f}(t={t:+.2f}) 价差{sp.mean():+.4f} → {tag}")
        if best is None or m > best[1]:
            best = (col, m)
    lines += ["", f"最优信号: {best[0]} IC={best[1]:+.4f}",
              f"样本 {len(sig)} | 生成 {pd.Timestamp.now():%Y-%m-%d %H:%M}"]
    out = r"D:\zcodesj\reports\KRONOS_V2_EVAL_20260818.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[report] -> {out}")


if __name__ == "__main__":
    main()
