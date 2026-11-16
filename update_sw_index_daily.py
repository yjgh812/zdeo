# -*- coding: utf-8 -*-
"""申万官方行业指数日线增量更新 → sw_industry.db.industry_kline_sw

数据源: akshare index_hist_sw (申万宏源官网, 无起止参数, 必须全量拉取后过滤)
- L1 一级(31只): 每日全量拉取 → 与表内已有日期求差增量写入 (约 1.5~2 分钟)
- L2 二级(131只): 数据源仅提供至 2024-06(申万官网限制), 视为历史档案, 仅 --backfill 时拉取
用途: 申万官方口径行业走势研究; 生产链路 aux 环节每日调用(L1 增量)

用法:
  python update_sw_index_daily.py             # L1 增量
  python update_sw_index_daily.py --backfill  # 首次回补 L1+L2 全量
"""
import argparse
import sqlite3
import sys
import time

import akshare as ak

SWDB = r"D:\workbuddyspace\databases\sw_industry.db"

DDL = """
CREATE TABLE IF NOT EXISTS industry_kline_sw (
    ts_code     TEXT NOT NULL,
    name        TEXT,
    level       TEXT NOT NULL,
    parent_name TEXT,
    trade_date  TEXT NOT NULL,
    open        REAL, high REAL, low REAL, close REAL,
    volume      REAL,
    amount      REAL,
    PRIMARY KEY (ts_code, trade_date)
)"""
IDX = "CREATE INDEX IF NOT EXISTS idx_kline_sw_date ON industry_kline_sw(trade_date)"


def get_codes(level: str):
    """返回 [(code, name, parent), ...]"""
    fn = "sw_index_first_info" if level == "L1" else "sw_index_second_info"
    df = getattr(ak, fn)()
    out = []
    for _, r in df.iterrows():
        code = str(r["行业代码"]).replace(".SI", "")
        out.append((code, str(r["行业名称"]), str(r.get("上级行业", "")) or None))
    return out


def fetch_all(symbol: str):
    df = ak.index_hist_sw(symbol=symbol, period="day")
    if df is None or len(df) == 0:
        return []
    return [
        (str(r["日期"]), r["开盘"], r["最高"], r["最低"], r["收盘"], r["成交量"], r["成交额"])
        for _, r in df.iterrows()
    ]


def sync_level(con, level: str, include_l2_hist: bool):
    if level == "L2" and not include_l2_hist:
        return 0, 0, None
    codes = get_codes(level)
    have = {r[0]: r[1] for r in con.execute(
        "SELECT ts_code, MAX(trade_date) FROM industry_kline_sw WHERE level=? GROUP BY ts_code",
        (level,)).fetchall()}
    total_new = 0
    t0 = time.time()
    for i, (code, name, parent) in enumerate(codes, 1):
        try:
            rows = fetch_all(code)
        except Exception as e:
            print(f"  [{level}] {code} {name} 拉取失败: {str(e)[:80]}", flush=True)
            continue
        if not rows:
            continue
        mx = have.get(code)
        new = [r for r in rows if mx is None or r[0] > mx]
        if new:
            con.executemany(
                "INSERT OR REPLACE INTO industry_kline_sw"
                "(ts_code, name, level, parent_name, trade_date, open, high, low, close, volume, amount)"
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [(code, name, level, parent, *r) for r in new])
            total_new += len(new)
            have[code] = new[-1][0]
        if i % 10 == 0:
            con.commit()
            print(f"  [{level}] {i}/{len(codes)} 累计新增 {total_new}  ({time.time()-t0:.0f}s)", flush=True)
    con.commit()
    latest = max(have.values()) if have else None
    return len(codes), total_new, latest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="回补 L1+L2 全量历史")
    args = ap.parse_args()

    con = sqlite3.connect(SWDB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(DDL)
    con.execute(IDX)
    con.commit()

    print(f"=== 申万行业指数同步 {'回补(L1+L2)' if args.backfill else '增量(L1)'} ===", flush=True)
    for lv in ("L1", "L2"):
        n, added, latest = sync_level(con, lv, args.backfill)
        if n:
            print(f"[{lv}] {n} 只, 新增 {added} 行, 最新 {latest}", flush=True)
    con.close()
    print("完成 ✅", flush=True)


if __name__ == "__main__":
    main()