# -*- coding: utf-8 -*-
"""申万L1 行业20日动量(RS)缓存·P2-B(PREREG_P2B_SECTOR_MOMENTUM_20260829)数据基础

RS(行业) = 行业成分等权 20 日累计收益 - 全市场(sw1 覆盖)等权 20 日累计收益。
消费点: bp_ep_signal 调仓日(T-1 数据, 无前视); tail_k 尾部行业成员剔除后顺延补足 TopN。
预计算全历史存 data/sector_momentum.db, 幂等重建; 用当前 sw1 快照=已知幸存者偏差局限(记录)。

用法: python sector_momentum.py --build   # 全历史重建(2015-12 起, 覆盖2016+ 回测20日窗)
      python sector_momentum.py --date YYYYMMDD  # 查询某日各行业RS
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys

BASE = r"D:\zcodesj"
SW1_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_DB = os.path.join(BASE, "data", "sector_momentum.db")
SINCE = "20151201"   # 覆盖 2016+ 回测的 20 日 RS 窗
TO = "20260828"


def build() -> int:
    import numpy as np
    conn = sqlite3.connect(SW1_DB)
    sw1 = {str(r[0])[-6:]: r[1] for r in conn.execute("SELECT code, sector_name FROM stock_sector_sw1")}
    conn.close()

    qconn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    rows = qconn.execute(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= ? AND date <= ?",
        (SINCE, TO)).fetchall()
    qconn.close()

    # code -> {date: close}
    px: dict[str, dict[str, float]] = {}
    for code, d, c in rows:
        if code in sw1 and c is not None:
            px.setdefault(code, {})[d] = c
    dates = sorted({d for p in px.values() for d in p})
    di = {d: i for i, d in enumerate(dates)}
    nd = len(dates)

    sectors = sorted(set(sw1.values()))
    # 行业指数日收益: idx[sector] = np.array 长度 nd(用 close 前向填充缺失再差分)
    raw = {s: np.full(nd, np.nan) for s in sectors}
    for code, sec in sw1.items():
        p = px.get(code)
        if not p:
            continue
        arr = raw[sec]
        for d, c in p.items():
            arr[di[d]] = c
    idx_c = {s: np.array([a if not np.isnan(a) else 0 for a in arr]) for s, arr in raw.items()}

    # 市场等权 = 各行业等权指数再平均(sw1 覆盖)
    mkt = np.mean(np.array([idx_c[s] for s in sectors]), axis=0)
    ret = {s: np.diff(idx_c[s]) / np.where(idx_c[s][:-1] == 0, 1, idx_c[s][:-1]) for s in sectors}
    mkt_ret = np.diff(mkt) / np.where(mkt[:-1] == 0, 1, mkt[:-1])

    def cum20(r):
        out = np.full(len(r) + 1, np.nan)
        for i in range(20, len(r) + 1):
            out[i] = np.prod(1 + r[i - 20:i]) - 1
        return out

    mkt_c = cum20(mkt_ret)
    out_rows = []
    for s in sectors:
        sc = cum20(ret[s])
        for i in range(20, nd):
            out_rows.append((dates[i], s, float((sc[i] - mkt_c[i]) * 100)))

    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    oconn = sqlite3.connect(OUT_DB)
    oconn.execute("DROP TABLE IF EXISTS sector_mom")
    oconn.execute("CREATE TABLE sector_mom (date TEXT, sector TEXT, rs_20 REAL, "
                  "PRIMARY KEY(date, sector))")
    oconn.executemany("INSERT OR REPLACE INTO sector_mom VALUES (?,?,?)", out_rows)
    oconn.commit()
    n = oconn.execute("SELECT COUNT(*) FROM sector_mom").fetchone()[0]
    oconn.close()
    print(f"[sector_momentum] 全历史重建完成: {n} 行, {len(dates)} 交易日, {len(sectors)} 行业, "
          f"窗口 {dates[0]}~{dates[-1]}")
    return 0


def query(date: str) -> dict:
    conn = sqlite3.connect(f"file:{OUT_DB}?mode=ro", uri=True)
    rows = conn.execute("SELECT sector, rs_20 FROM sector_mom WHERE date<=? "
                        "ORDER BY date DESC LIMIT 31", (date,)).fetchall()
    conn.close()
    return {s: r for s, r in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    if a.build:
        return build()
    if a.date:
        rs = query(a.date)
        for s, r in sorted(rs.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {s:<6} {r:+.2f}pp")
        return 0
    print("用 --build 重建 / --date 查询")
    return 0


if __name__ == "__main__":
    sys.exit(main())
