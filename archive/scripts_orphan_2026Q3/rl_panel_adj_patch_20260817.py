# -*- coding: utf-8 -*-
"""RL panel复权补丁 — 给ashare_panel加adj_factor列 (2026-08-17晚)
adj_factor = qfq复权close / panel原始close (同期同股); loader见此列自动用复权收益+保留原始涨停检测
产出: ashare_panel_adj_20260817.parquet
"""
import sqlite3

import numpy as np
import pandas as pd
import polars as pl

SRC = r"D:\zcodesj\strategy_libs\aurumq-rl\data\ashare_panel_20260816.parquet"
DST = r"D:\zcodesj\strategy_libs\aurumq-rl\data\ashare_panel_adj_20260817.parquet"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def main():
    df = pl.read_parquet(SRC)
    print(f"[panel] {df.shape}; trade_date类型 {df.schema['trade_date']}")

    pdf = df.select(["trade_date", "ts_code", "close"]).to_pandas()
    # ts_code: 000001.SZ -> 000001 ; trade_date -> YYYYMMDD str
    pdf["code"] = pdf["ts_code"].str.split(".").str[0]
    td = pdf["trade_date"].astype(str)
    pdf["ymd"] = td.str.replace("-", "").str.slice(0, 8)
    print(f"[panel] 日期样例 {pdf['ymd'].iloc[0]}..{pdf['ymd'].iloc[-1]}, 股票 {pdf['code'].nunique()}")

    con = sqlite3.connect(QFQ_DB)
    codes = pdf["code"].unique().tolist()
    for i in range(0, len(codes), 1000):
        chunk = codes[i:i + 1000]
        q = ",".join(f"'{c}'" for c in chunk)
        part = pd.read_sql_query(
            f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({q})", con)
        if i == 0:
            qfq = part
        else:
            qfq = pd.concat([qfq, part], ignore_index=True)
    con.close()
    print(f"[qfq] {len(qfq)} 行 / {qfq['code'].nunique()} 股")

    m = pdf.merge(qfq, left_on=["code", "ymd"], right_on=["code", "date"], how="left")
    m["adj_factor"] = np.where(
        m["close_y"].notna() & (m["close_x"] > 0) & (m["close_y"] > 0),
        m["close_y"] / m["close_x"], np.nan)
    cov = m["adj_factor"].notna().mean()
    med = m["adj_factor"].median()
    print(f"[adj] 覆盖率 {cov:.1%}, 中位 {med:.4f}, "
          f"P1 {m['adj_factor'].quantile(.01):.3f} P99 {m['adj_factor'].quantile(.99):.3f}")
    # 极端值保护: 复权因子异常(数据错位)置NaN
    bad = (m["adj_factor"] < 0.05) | (m["adj_factor"] > 20)
    print(f"[adj] 极端因子 {bad.sum()} 行 -> NaN")
    m.loc[bad, "adj_factor"] = np.nan

    # 挂回polars (对齐原行序)
    adj = pl.Series("adj_factor", m["adj_factor"].to_numpy(dtype=np.float64))
    out = df.with_columns(adj.cast(pl.Float64))
    out.write_parquet(DST)
    print(f"[out] {DST} {out.shape}")


if __name__ == "__main__":
    main()
