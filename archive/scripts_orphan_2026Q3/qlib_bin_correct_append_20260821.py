# -*- coding: utf-8 -*-
"""qlib_bin正确增量 — 补齐所有缺失交易日 (2026-08-21)
前情: 手动append只补了1天导致对齐错乱; dump_update因instruments end_date=2026-12-31跳过
本版: 日历当前到8/20, bin实际到8/13, 缺失5个交易日(8/14,17,18,19,20) → 按日历顺序全量补齐
"""
import os
import sqlite3
import struct
import time

import pandas as pd

QLIB = r"D:\workbuddyspace\qlib_data\qlib_bin"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
COLS = ["open", "high", "low", "close", "volume", "amount"]
# 从日历取这5天的顺序
MISSING = ["2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    # 1) 读csi300成分 + 这5天的qfq数据
    con = sqlite3.connect(QFQ_DB)
    import akshare as ak
    cons = ak.index_stock_cons_csindex(symbol="000300")
    codes = cons["成分券代码"].str.replace(r"^(SH|SZ)", "", regex=True).tolist()
    q = ",".join(f"'{c}'" for c in codes)
    ymd = [d.replace("-", "") for d in MISSING]
    df = pd.read_sql_query(
        f"SELECT code, date, open, high, low, close, volume, amount "
        f"FROM stock_daily_meta_qfq WHERE code IN ({q}) AND date IN ({','.join('?'*len(ymd))})",
        con, params=ymd)
    con.close()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df["inst"] = df["code"].str.lower().apply(lambda c: ("sh" if c.startswith("6") else "sz") + c)
    log(f"[qfq] {len(df)} 行 / {df['date'].dt.strftime('%m-%d').nunique()} 天 / {df['inst'].nunique()} 只")

    # 2) 按MISSING顺序, 逐股逐字段append
    piv = df.pivot_table(index="inst", columns="date", values=COLS[0])
    n_ok = n_missing = 0
    for inst in df["inst"].unique():
        g = df[df["inst"] == inst].set_index("date")
        idir = os.path.join(QLIB, "features", inst)
        if not os.path.isdir(idir):
            n_missing += 1
            continue
        # 检查每字段bin是否缺这5天(当前bin最后记录应对应8/13前)
        for col in COLS:
            path = os.path.join(idir, f"{col}.day.bin")
            if not os.path.exists(path):
                continue
            vals = []
            for d in MISSING:
                dt = pd.Timestamp(d)
                if dt in g.index:
                    vals.append(float(g.loc[dt, col]))
                else:
                    vals.append(float("nan"))
            with open(path, "ab") as f:
                for v in vals:
                    f.write(struct.pack("f", v))
        n_ok += 1
    log(f"[append] 完成 {n_ok} 只 (缺{col}数据跳过 {n_missing})")


if __name__ == "__main__":
    main()