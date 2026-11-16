# -*- coding: utf-8 -*-
"""
backfill_industry_momentum_20260808.py — 重算 feature_industry_momentum 缺失窗口

背景：原同步脚本依赖已不存在的 change_pct 列而失效（2026-04 起停更），
且表内 2026 年后的存量行为垃圾值（ts_code=指数代码、ret20 数值荒谬）。
本次按清晰口径重算 2026-04-07 → 2026-08-07：

  股票池：stock_info 中有行业归属的 A 股（4586 只）
  ret20：个股 20 交易日累计涨跌幅（%，本地真实日线）
  industry_ret20：行业内个股 ret20 等权均值
  industry_momentum：当日 industry_ret20 的横截面分位（0~1，越大越强）
  ts_code 固定 'index'，trade_date 为 'YYYY-MM-DD'（与表内惯例一致）
"""
import sqlite3
import time
from pathlib import Path

import pandas as pd

MAIN = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
START = "20260407"
END = "20260811"
LOG = Path(r"D:\workbuddyspace\backfill_momentum_20260811.log")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    m = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    ind_map = dict(m.execute(
        "SELECT code, industry FROM stock_info WHERE industry IS NOT NULL AND industry!=''").fetchall())
    m.close()
    log(f"行业映射 {len(ind_map)} 只")

    mt = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
    px = pd.read_sql(
        "SELECT code, date, close FROM stock_daily_meta WHERE date>=? AND date<=?",
        mt, params=("20260101", END))
    mt.close()
    px = px[px["code"].isin(ind_map.keys())]
    panel = px.pivot_table(index="date", columns="code", values="close")
    panel = panel.sort_index()
    log(f"价格面板 {panel.shape[0]} 天 × {panel.shape[1]} 只")

    ret20 = panel.pct_change(20) * 100  # %
    dates = [d for d in panel.index if START <= str(d) <= END]
    log(f"待算日期 {len(dates)} 天")

    f = sqlite3.connect(FUND, timeout=60)
    f.execute("PRAGMA busy_timeout=60000")
    f.execute("PRAGMA journal_mode=WAL")
    total = 0
    for d in dates:
        row = ret20.loc[d].dropna()
        if row.empty:
            continue
        df = row.rename("ret20").reset_index()
        df["industry"] = df["code"].map(ind_map)
        g = df.groupby("industry")["ret20"].mean()
        ranks = g.rank(pct=True)
        dd = f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}"
        recs = [("index", dd, ind, float(g[ind]), float(ranks[ind])) for ind in g.index]
        f.executemany("INSERT OR REPLACE INTO feature_industry_momentum VALUES (?,?,?,?,?)", recs)
        total += len(recs)
    f.commit()
    f.close()
    log(f"完成：{len(dates)} 天 × 行业，共 {total} 行")


if __name__ == "__main__":
    main()
