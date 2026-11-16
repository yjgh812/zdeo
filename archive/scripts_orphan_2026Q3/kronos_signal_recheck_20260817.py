# -*- coding: utf-8 -*-
"""Kronos信号2x2补考 — 主人否决冻结后的协议复盘 (2026-08-17晚)
嫌疑隔离:
  A) 信号聚合: 官方=mean预期收益(mean_ret), 我们评估用的=自创p_up
  B) 欠训: 官方30 epochs, 我们1 epoch
矩阵: {预训练sweep, 微调sweep} x {p_up, mean_ret} -> 三关速查(RankIC/分层/分年)
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CSVS = {
    "预训练": r"D:\zcodesj\kronos_factor_sweep.csv",
    "微调1ep": r"D:\zcodesj\Kronos\finetune_csv\ckpt_ashare_sweep.csv",
}


def fwd_returns(px):
    out = {}
    px = px.sort_values("date").reset_index(drop=True)
    for code, g in px.groupby("code"):
        c, d = g["close"].values, g["date"].values
        for i in range(len(g) - 20):
            out[(d[i], code)] = c[i + 20] / c[i] - 1
    return out


def main():
    con = sqlite3.connect(QFQ_DB)
    all_codes = set()
    frames = {}
    for tag, p in CSVS.items():
        if not os.path.exists(p):
            print(f"[skip] {tag}: {p} 不存在")
            continue
        df = pd.read_csv(p)
        df["date"] = df["date"].astype(str)
        df["code"] = df["code"].astype(str).str.zfill(6)
        frames[tag] = df
        all_codes |= set(df["code"].unique())
    q = ",".join(f"'{c}'" for c in all_codes)
    px = pd.read_sql_query(
        f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({q})", con)
    con.close()
    fr = fwd_returns(px)
    print(f"[data] qfq {len(px)} 行, fwd20字典 {len(fr)}")

    for tag, df in frames.items():
        df = df.copy()
        df["fwd20"] = [fr.get((d, c), np.nan) for d, c in zip(df["date"], df["code"])]
        df = df.dropna(subset=["fwd20"])
        for col in ("p_up", "mean_ret"):
            if col not in df.columns:
                continue
            ics = []
            for d, g in df.groupby("date"):
                if len(g) < 30:
                    continue
                ic, _ = spearmanr(g[col], g["fwd20"])
                ics.append((d, ic))
            ic_df = pd.DataFrame(ics, columns=["date", "ic"])
            m, sd = ic_df["ic"].mean(), ic_df["ic"].std()
            t = m / (sd / np.sqrt(len(ic_df))) if sd > 0 else 0
            # 分层价差
            sp = []
            for d, g in df.groupby("date"):
                if len(g) < 50:
                    continue
                qq = pd.qcut(g[col].rank(method="first"), 5, labels=False)
                mm = g.groupby(qq)["fwd20"].mean()
                sp.append(mm.iloc[-1] - mm.iloc[0])
            sp = np.array(sp)
            ic_df["year"] = ic_df["date"].str[:4]
            yr = ic_df.groupby("year")["ic"].mean().round(3).to_dict()
            print(f"[{tag} x {col:8s}] n={len(ic_df):>3}期 IC均值{m:+.4f} t={t:+.2f} "
                  f"正占比{(ic_df['ic']>0).mean():.0%} | q5-q1月均{sp.mean():+.4f} "
                  f"胜率{(sp>0).mean():.0%} | 分年{yr}", flush=True)


if __name__ == "__main__":
    main()
