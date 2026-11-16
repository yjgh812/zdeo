# -*- coding: utf-8 -*-
"""只读探针(20261021): master_feat.parquet LABEL0 真实视野K探测。

背景: B10按源码判定标签=N=1(次日), 但极端行证据矛盾(301308@20260708 LABEL0=-36.1% 精确匹配
7/9→7/17跌幅, 次日真实=+4.6%; 5只高价股同日LABEL0均-31~-36%)。本探针直接回归定谳:
  对 test期样本, 计算 corr(LABEL0, fwdK) 与斜率, K∈1..30, 两种对齐口径:
    A: close[d+K+1]/close[d+1]-1   (源码公式 Ref(-K-1)/Ref(-1) 的推广)
    B: close[d+K]/close[d]-1       (朴素前视)
  corr≈1 且斜率≈1 的 K 即真实视野。
"""
import sqlite3

import numpy as np
import pandas as pd

FEAT = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\master_feat.parquet"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def main():
    lab = pd.read_parquet(FEAT, columns=["LABEL0"])["LABEL0"]
    lab = lab[lab.index.get_level_values("datetime") <= "2026-07-01"].dropna()
    lab = lab.sample(n=min(4000, len(lab)), random_state=20261021)
    rows = [(str(ins)[-6:], dt.strftime("%Y%m%d"), v) for (dt, ins), v in lab.items()]

    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    codes = sorted({r[0] for r in rows})
    ph = ",".join("?" * len(codes))
    q = pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20250601' ORDER BY code,date",
        con, params=codes)
    con.close()
    cal = sorted(q["date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    closes = q.pivot(index="date", columns="code", values="close")
    closes_i = closes.set_axis([pos[d] for d in closes.index], axis=0).sort_index()

    recs = []
    for code, d, v in rows:
        if d not in pos:
            continue
        i = pos[d]
        recs.append((code, i, v))
    df = pd.DataFrame(recs, columns=["code", "i", "lab"])

    print(f"样本 n={len(df)} (采样自 test期 ≤2026-07-01)")
    print(f"{'K':>3} {'corr_A':>8} {'slope_A':>8} {'corr_B':>8} {'slope_B':>8}")
    best = None
    for K in range(1, 31):
        # A: close[i+K+1]/close[i+1]
        a_end = closes_i.shift(-K - 1)
        a_beg = closes_i.shift(-1)
        # B: close[i+K]/close[i]
        b_end = closes_i.shift(-K)
        vals_a, vals_b, labs = [], [], []
        for code, i, v in df.itertuples(index=False):
            if code not in closes_i.columns:
                continue
            try:
                if i + K + 1 < len(closes_i):
                    va = a_end.at[i + K + 1, code] if (i + K + 1) in a_end.index else np.nan
                    vb = b_end.at[i + K, code] if (i + K) in b_end.index else np.nan
                    va = va / closes_i.at[i + 1, code] - 1 if (i + 1) in closes_i.index else np.nan
                    vb = vb / closes_i.at[i, code] - 1
                    vals_a.append(va); vals_b.append(vb); labs.append(v)
            except KeyError:
                continue
        la, aa = np.array(labs), np.array(vals_a)
        ab = np.array(vals_b)
        m = ~np.isnan(aa)
        ca = np.corrcoef(la[m], aa[m])[0, 1] if m.sum() > 100 else np.nan
        sa = (np.polyfit(la[m], aa[m], 1)[0] if m.sum() > 100 else np.nan)
        m2 = ~np.isnan(ab)
        cb = np.corrcoef(la[m2], ab[m2])[0, 1] if m2.sum() > 100 else np.nan
        sb = (np.polyfit(la[m2], ab[m2], 1)[0] if m2.sum() > 100 else np.nan)
        print(f"{K:>3} {ca:>8.4f} {sa:>8.3f} {cb:>8.4f} {sb:>8.3f}")
        if best is None or (not np.isnan(ca) and ca > best[1]):
            best = (K, ca, sa)
    print(f"\n定谳: 真实视野 K={best[0]} (corr_A={best[1]:.4f}, slope_A={best[2]:.3f})")


if __name__ == "__main__":
    main()
