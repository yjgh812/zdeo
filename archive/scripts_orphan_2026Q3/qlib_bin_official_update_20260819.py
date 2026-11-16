# -*- coding: utf-8 -*-
"""qlib_bin官方增量更新 — 用dump_bin.py dump_update (2026-08-19)
从qfq库导出csi300最新交易日CSV → dump_update正确对齐追加
"""
import os
import sqlite3
import subprocess
import time

import pandas as pd

QLIB_DIR = r"D:\workbuddyspace\qlib_data\qlib_bin"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CSV_DIR = r"D:\zcodesj\qlib_exp\update_csv"
DUMP = r"D:\zcodesj\qlib\scripts\dump_bin.py"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    os.makedirs(CSV_DIR, exist_ok=True)
    # 1) 找出qlib_bin instrument当前end_date(从instruments/*.txt)
    #    简化: 读csi300.txt, 看每个股票最后有数据的日期 = bin文件长度对齐的日历日
    #    更稳: 直接导出qfq库所有 > 8/13 的交易日, 让dump_update自己判断
    con = sqlite3.connect(QFQ_DB)
    # 全市场8/13之后的交易日(取csi300成分)
    import akshare as ak
    cons = ak.index_stock_cons_csindex(symbol="000300")
    codes = cons["成分券代码"].str.replace(r"^(SH|SZ)", "", regex=True).tolist()
    q = ",".join(f"'{c}'" for c in codes)
    df = pd.read_sql_query(
        f"SELECT code, date, open, high, low, close, volume, amount "
        f"FROM stock_daily_meta_qfq WHERE code IN ({q}) AND date > '20260813' "
        f"ORDER BY code, date", con)
    con.close()
    log(f"[qfq] 8/13后数据 {len(df)} 行 / {df['date'].nunique()} 天 / {df['code'].nunique()} 只")

    # 转qlib CSV格式: symbol(小写带前缀), date(Y-M-D), 价格列
    df["code"] = df["code"].str.lower()
    df["symbol"] = df["code"].apply(lambda c: ("sh" if c.startswith("6") else "sz") + c)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    out = df[["symbol", "date", "open", "high", "low", "close", "volume", "amount"]]
    csv_path = rf"{CSV_DIR}\csi300_update.csv"
    out.to_csv(csv_path, index=False)
    log(f"[csv] {csv_path} {len(out)} 行")

    # 2) dump_update
    cmd = ["python", DUMP, "dump_update",
           f"--data_path={CSV_DIR}", f"--qlib_dir={QLIB_DIR}",
           "--max_workers=8"]
    log(f"[dump] 运行: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    tail = (r.stdout or "")[-2000:] + (r.stderr or "")[-2000:]
    log(tail)


if __name__ == "__main__":
    main()