# -*- coding: utf-8 -*-
"""只读普查(20261021): qfq 库隔夜越限断层全库清点。

定义: 同股相邻交易日 |close[t]/close[t-1]-1| > 25% 视为越限点(A股涨跌停极限±20%,
      25%阈值排除正常涨跌停与除息小跳; 首发上市前5日剔除, 新股无涨跌幅限制日不计)。
输出: 总越限点数/涉及股票数/按年分布/训练窗(2017-2024)与测试窗(2026)计数/Top例证。
用途: 给 qlib_bin/qfq 修复裁决提供完整爆炸半径。
"""
import json
import sys

import numpy as np
import pandas as pd
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\qfq_break_census_20261021_result.json"
TH = 0.25


def main():
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20150101' ORDER BY code,date", con)
    con.close()
    df = df[df["close"] > 0].copy()
    g = df.groupby("code")
    df["days"] = g.cumcount() + 1
    df["chg"] = g["close"].pct_change()
    df["prev_days"] = g["days"].shift(1)
    mask = (df["chg"].abs() > TH) & (df["prev_days"] > 5)  # 上市前5日(含首发)不计
    hits = df[mask].copy()
    hits["year"] = hits["date"].astype(str).str[:4]
    hits["month"] = hits["date"].astype(str).str[4:6]

    by_year = hits.groupby("year").size().to_dict()
    train_win = hits[(hits["date"] >= "20170101") & (hits["date"] <= "20241231")]
    test_win = hits[hits["date"] >= "20260101"]
    top = hits.reindex(hits["chg"].abs().sort_values(ascending=False).index).head(15)
    by_month_test = test_win.groupby(test_win["date"].astype(str).str[4:6]).size().to_dict()

    result = {
        "probe": "qfq_break_census", "date": "2026-10-21",
        "threshold": TH, "scan_from": "20150101",
        "rows_scanned": int(len(df)), "codes_scanned": int(df["code"].nunique()),
        "break_points": int(len(hits)), "stocks_affected": int(hits["code"].nunique()),
        "by_year": by_year,
        "train_window_2017_2024": {"points": int(len(train_win)), "stocks": int(train_win["code"].nunique())},
        "test_window_2026": {"points": int(len(test_win)), "stocks": int(test_win["code"].nunique())},
        "test_window_by_month": by_month_test,
        "top15": [{"code": r.code, "date": r.date, "chg": round(float(r.chg), 3), "close": float(r.close)}
                  for r in top.itertuples()],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT}\n")
    print(f"扫描 {len(df)} 行 / {df['code'].nunique()} 只 → 越限点 {len(hits)} 个, 涉及 {hits['code'].nunique()} 只")
    print(f"训练窗(2017-2024): {len(train_win)} 点 / {train_win['code'].nunique()} 只")
    print(f"测试窗(2026): {len(test_win)} 点 / {test_win['code'].nunique()} 只 | 按月: {by_month_test}")
    print("\n按年分布:", dict(sorted(by_year.items())))
    print("\nTop15 极端例证:")
    for r in top.itertuples():
        print(f"  {r.code} @ {r.date}  chg={float(r.chg):+.1%}  close={r.close}")


if __name__ == "__main__":
    sys.exit(main())
