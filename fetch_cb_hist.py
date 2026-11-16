# -*- coding: utf-8 -*-
"""L3 可转债数据抓取: 东财 bond_zh_cov_value_analysis 单券全史(价格+溢价率) → data/cb_hist.db
幂等+断点续传: 已入库 code 跳过; 失败重试1次; sleep 0.4s 限频。用法: python fetch_cb_hist.py
"""
import sys, os, time, sqlite3
import pandas as pd
import akshare as ak

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = r"D:\zcodesj\data\cb_hist.db"
LIST_LOG = r"D:\zcodesj\tmp\cb_fetch.log"

def log(msg):
    with open(LIST_LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%F %T')} {msg}\n")

con = sqlite3.connect(DB)
con.execute("""CREATE TABLE IF NOT EXISTS cb_value(
    code TEXT, date TEXT, close REAL, conv_value REAL, conv_premium REAL,
    pure_bond_value REAL, pure_bond_premium REAL, PRIMARY KEY(code, date))""")
con.execute("""CREATE TABLE IF NOT EXISTS cb_meta(
    code TEXT PRIMARY KEY, name TEXT, stock_code TEXT, stock_name TEXT, issue_size REAL,
    rating TEXT, apply_date TEXT, list_date TEXT, conv_price REAL)""")

done = {r[0] for r in con.execute("SELECT DISTINCT code FROM cb_value")}
print(f"已入库 {len(done)} 券, 续传剩余", flush=True)

cov = ak.bond_zh_cov()
log(f"列表 {len(cov)} 券")
n_ok = n_fail = n_skip = 0
for _, row in cov.iterrows():
    code = str(row["债券代码"]).zfill(6)
    if code in done:
        n_skip += 1
        continue
    try:
        try:
            df = ak.bond_zh_cov_value_analysis(symbol=code)
        except Exception:
            time.sleep(1.0)
            df = ak.bond_zh_cov_value_analysis(symbol=code)
        if df is None or len(df) == 0:
            n_fail += 1
            log(f"EMPTY {code} {row['债券简称']}")
            continue
        df = df.rename(columns={"日期": "date", "收盘价": "close", "转股价值": "conv_value",
                                "转股溢价率": "conv_premium", "纯债价值": "pure_bond_value",
                                "纯债溢价率": "pure_bond_premium"})
        df["date"] = df["date"].astype(str)
        df["code"] = code
        cols = ["code", "date", "close", "conv_value", "conv_premium", "pure_bond_value", "pure_bond_premium"]
        con.execute("DELETE FROM cb_value WHERE code=?", (code,))
        con.executemany(f"INSERT OR REPLACE INTO cb_value({','.join(cols)}) VALUES ({','.join('?'*7)})",
                        df[cols].itertuples(index=False, name=None))
        con.execute("INSERT OR REPLACE INTO cb_meta VALUES (?,?,?,?,?,?,?,?,?)",
                    (code, str(row["债券简称"]), str(row["正股代码"]).zfill(6), str(row["正股简称"]),
                     float(row["发行规模"]) if pd.notna(row["发行规模"]) else None,
                     str(row["信用评级"]), str(row["申购日期"]), str(row["上市时间"]),
                     float(row["转股价"]) if pd.notna(row["转股价"]) else None))
        con.commit()
        n_ok += 1
        if n_ok % 50 == 0:
            log(f"进度 {n_ok} 券 (skip {n_skip} fail {n_fail})")
    except Exception as e:
        n_fail += 1
        log(f"FAIL {code} {row['债券简称']}: {str(e)[:80]}")
    time.sleep(0.4)
con.close()
log(f"完成: ok={n_ok} fail={n_fail} skip={n_skip}")
print(f"OK={n_ok} FAIL={n_fail} SKIP={n_skip}", flush=True)
