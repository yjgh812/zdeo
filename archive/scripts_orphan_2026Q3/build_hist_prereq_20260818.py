# -*- coding: utf-8 -*-
"""HIST前置数据构建 — stock2concept/stock_index (2026-08-18)
格式契约(qlib/contrib/model/pytorch_hist.py实证):
  stock2concept.npy = 2D (n_rows, n_concepts), matrix[stock_row]=概念one-hot
  stock_index.npy   = dict {instrument: row}, 未知股票→NaN→代码里写死733
方案: csi300历史union(qlib instruments) × baostock行业(全市场一次拉取)
      行数=734(row733=未知兜底全零), 概念=行业one-hot
"""
import numpy as np
import pandas as pd

OUT = r"D:\zcodesj\qlib_exp\hist_data"
UNKNOWN_IDX = 733


def main():
    import os
    os.makedirs(OUT, exist_ok=True)

    # 1) csi300 universe(含历史union)
    uni = []
    with open(r"D:\workbuddyspace\qlib_data\qlib_bin\instruments\csi300.txt") as f:
        for line in f:
            parts = line.strip().split("\t")
            if parts and parts[0]:
                uni.append(parts[0])
    uni = sorted(set(uni))
    print(f"[csi300 union] {len(uni)} 只")

    # 2) baostock 行业映射(全市场一次)
    import baostock as bs
    bs.login()
    rs = bs.query_stock_industry()
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    bs.logout()
    ind = pd.DataFrame(rows, columns=rs.fields)
    print(f"[baostock industry] {len(ind)} 行, 字段 {rs.fields}")

    # qlib码 SH600000 -> baostock sh.600000
    ind["inst"] = ("SH" + ind["code"].str.split(".").str[1]).where(
        ind["code"].str.startswith("sh"),
        "SZ" + ind["code"].str.split(".").str[1])
    ind_map = dict(zip(ind["inst"], ind["industry"]))
    industries = sorted(ind["industry"].dropna().unique())
    cid = {g: i for i, g in enumerate(industries)}
    print(f"[行业] {len(industries)} 个: {industries[:8]}...")

    # 3) 组装
    n_concepts = len(industries)
    s2c = np.zeros((734, n_concepts), dtype=np.float32)
    stock_index = {}
    miss = []
    for i, inst in enumerate(uni[:UNKNOWN_IDX]):
        g = ind_map.get(inst)
        if g is None or g not in cid:
            miss.append(inst)
            continue
        s2c[i, cid[g]] = 1.0
        stock_index[inst] = i
    print(f"[映射] 成功 {len(stock_index)} / {len(uni)}; 缺行业 {len(miss)} 只(将落row733兜底)")

    np.save(rf"{OUT}\stock2concept.npy", s2c)
    np.save(rf"{OUT}\stock_index.npy", np.array(stock_index, dtype=object))
    print(f"[out] {OUT}\\stock2concept.npy {s2c.shape} + stock_index.npy {len(stock_index)}映射")


if __name__ == "__main__":
    main()