# -*- coding: utf-8 -*-
"""Kronos v2补考训练数据生成 — 2026-08-17晚
官方协议对齐: CSI300 x 2016-2023(8年) 训练, 评估期2024+完全隔离(v1的2020-2026覆盖评估期=前视)
amount置0(qfq库amount列不可用教训20260815); 格式=finetune_csv单流(code,date排序)
"""
import sqlite3

import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\Kronos\finetune_csv\data\ashare_csi300_2016_2023.csv"


def main():
    import akshare as ak
    cons = ak.index_stock_cons_csindex(symbol="000300")
    codes = list(cons["成分券代码"])
    print(f"[universe] CSI300 {len(codes)} 只")

    con = sqlite3.connect(QFQ_DB)
    q = ",".join(f"'{c}'" for c in codes)
    df = pd.read_sql_query(
        f"SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        f"WHERE code IN ({q}) AND date BETWEEN '20160104' AND '20231229' "
        f"ORDER BY code, date", con)
    con.close()
    # 清洗: 每只股票至少需 lookback90+predict10+1=101 行
    cnt = df.groupby("code").size()
    ok = cnt[cnt >= 150].index
    df = df[df["code"].isin(ok)]
    print(f"[data] {len(df)} 行 / {df['code'].nunique()} 只 (剔除短历史后)")

    out = pd.DataFrame({
        "timestamps": pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y/%m/%d"),
        "open": df["open"], "high": df["high"], "low": df["low"],
        "close": df["close"], "volume": df["volume"], "amount": 0.0})
    out.to_csv(OUT, index=False)
    print(f"[out] {OUT} {len(out)} 行; "
          f"日期 {out['timestamps'].min()} ~ {out['timestamps'].max()}")


if __name__ == "__main__":
    main()
