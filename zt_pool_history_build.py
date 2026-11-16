# -*- coding: utf-8 -*-
"""zt_pool_history_build.py — 东财涨停池历史全量落库 (数据积累工程, 2026-10-06)
源: akshare stock_zt_pool_em(date) 历史日期可查
库: data/zt_pool_history.db 表 zt_pool (date UPSERT 幂等)
用途: 五角色/四期/龙头溢价的历史统计验证底座
🔴 2026-10-08口径闸: 本库已被qfq派生口径接管(build_log含'派生'note, 生成器=zt_pool_derived_build.py, 迁自tmp/build_zt_pool_derived_20261007.py);
   akshare东财口径写入(本脚本)对已接管库一律拒绝——两口径涨停判定规则不同源(9/30实测71vs52只,交集50),
   防同表口径混库; 恢复akshare口径须主人裁决+全史重刷。
"""
import sqlite3
import sys
import time
from datetime import datetime

import akshare as ak

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = r"D:\zcodesj\data\zt_pool_history.db"
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"

def trading_dates():
    con = sqlite3.connect(QFQ, uri=True)
    ds = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>='20250101' ORDER BY date")]
    con.close()
    return ds

def init_db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS zt_pool (
        date TEXT, code TEXT, name TEXT, pct REAL, close REAL, amount REAL,
        circ_mv REAL, total_mv REAL, turnover REAL, seal_amount REAL,
        first_time TEXT, last_time TEXT, break_cnt INTEGER, zt_stat TEXT,
        boards INTEGER, industry TEXT, PRIMARY KEY(date, code))""")
    con.execute("""CREATE TABLE IF NOT EXISTS build_log (
        date TEXT PRIMARY KEY, rows INTEGER, ts TEXT, note TEXT)""")
    con.commit()
    con.close()

def done_dates():
    con = sqlite3.connect(DB)
    ds = {r[0] for r in con.execute("SELECT date FROM build_log WHERE rows IS NOT NULL AND rows>=0")}
    con.close()
    return ds

def derived_owned():
    """库是否已被qfq派生口径接管(10/7派生工程的note标记)"""
    con = sqlite3.connect(DB)
    n = con.execute("SELECT COUNT(*) FROM build_log WHERE note LIKE '%派生%'").fetchone()[0]
    con.close()
    return n > 0

def fetch_one(d):
    if derived_owned():
        raise SystemExit(
            "[GUARD] zt_pool_history.db 已由qfq派生口径接管(生成器 zt_pool_derived_build.py),"
            " akshare东财口径写入被拒——防同表口径混库; 恢复须主人裁决+全史重刷")
    df = ak.stock_zt_pool_em(date=d)
    if df is None or df.empty:
        return 0
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM zt_pool WHERE date=?", (d,))
    for _, r in df.iterrows():
        con.execute(
            "INSERT OR REPLACE INTO zt_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d, str(r.get("代码", "")), str(r.get("名称", "")),
             float(r.get("涨跌幅", 0) or 0), float(r.get("最新价", 0) or 0),
             float(r.get("成交额", 0) or 0), float(r.get("流通市值", 0) or 0),
             float(r.get("总市值", 0) or 0), float(r.get("换手率", 0) or 0),
             float(r.get("封板资金", 0) or 0), str(r.get("首次封板时间", "")),
             str(r.get("最后封板时间", "")), int(r.get("炸板次数", 0) or 0),
             str(r.get("涨停统计", "")), int(r.get("连板数", 1) or 1),
             str(r.get("所属行业", ""))))
    con.execute("INSERT OR REPLACE INTO build_log(date,rows,ts,note) VALUES (?,?,?,?)",
                (d, len(df), datetime.now().isoformat(timespec="seconds"),
                 f"akshare东财口径 增量{d}"))
    con.commit()
    con.close()
    return len(df)

def main():
    init_db()
    if derived_owned():
        raise SystemExit(
            "[GUARD] 本库已由qfq派生口径接管, akshare批量重刷会以不同口径覆盖全史——已拒绝。"
            " 增量更新请运行 zt_pool_derived_build.py(全窗幂等重建, 依赖qfq库先更新到目标日)")
    tds = trading_dates()
    done = done_dates()
    todo = [d for d in tds if d not in done]
    print(f"[START] 交易日 {len(tds)} 已完成 {len(done)} 待抓 {len(todo)}")
    fail = []
    for i, d in enumerate(todo):
        try:
            n = fetch_one(d)
            if i % 20 == 0 or i == len(todo) - 1:
                print(f"[{i+1}/{len(todo)}] {d} rows={n}", flush=True)
        except Exception as e:
            fail.append((d, str(e)[:60]))
            print(f"[FAIL] {d}: {str(e)[:60]}", flush=True)
            time.sleep(3)
        time.sleep(0.6)
    print(f"[DONE] 成功 {len(todo)-len(fail)} 失败 {len(fail)}")
    if fail:
        print("失败清单:", fail[:20])

if __name__ == "__main__":
    main()
