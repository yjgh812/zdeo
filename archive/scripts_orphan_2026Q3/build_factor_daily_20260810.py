# -*- coding: utf-8 -*-
"""
build_factor_daily_20260810.py — 为 factor_daily 表增量补 20260810 行

前置：daily_basic 已补到 20260810（依赖 backfill_daily_basic_20260810.py）。
逻辑（与既有行一致，实测验证）：
  - 行情/估值/换手列 ← daily_basic(trade_date=20260810)
  - 财务列 ← factor_daily 既有 20260807 行（report_date=2024-12-31，fina_indicator 2024年报期，沿用保持表内一致）
  - code ← 'sz'+6位 / 'sh'+6位；ts_code ← 6位+'.SZ'/'.SH'
  - report_date ← '2024-12-31 00:00:00'（与 8/7 行一致）
说明：factor_daily 既有行财务列用的是构建时点(2026-06)可用的最新报告期(2024年报)，
      且 fina_indicator 当时未含 2025Q1；为保持相邻交易日财务列连续，增量行沿用同一 report_date。
"""
import sqlite3
from pathlib import Path

DRAGON = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
TARGET_DATE = "20260810"
TEMPLATE_DATE = "20260807"   # 财务列复制源（相邻交易日，report_date=2024-12-31）

# daily_basic 列 → factor_daily 列 的映射（行情/估值/换手）
DB_TO_FD = {
    "turnover_rate": "turnover_rate", "turnover_rate_f": "turnover_rate_f",
    "volume_ratio": "volume_ratio", "pe": "pe", "pe_ttm": "pe_ttm",
    "pb": "pb", "ps": "ps", "ps_ttm": "ps_ttm",
    "dv_ratio": "dv_ratio", "dv_ttm": "dv_ttm",
    "total_share": "total_share", "float_share": "float_share", "free_share": "free_share",
    "total_mv": "total_mv", "circ_mv": "circ_mv",
}


def main():
    fund = sqlite3.connect(f"file:{FUND}?mode=ro", uri=True)
    # 检查 daily_basic 8/10 是否就绪
    n_db = fund.execute(
        "SELECT COUNT(*) FROM daily_basic WHERE trade_date=?", (TARGET_DATE,)).fetchone()[0]
    if n_db < 1000:
        print(f"[FAIL] daily_basic {TARGET_DATE} 仅 {n_db} 行，回补未完成，终止")
        fund.close()
        return
    print(f"[OK] daily_basic {TARGET_DATE}: {n_db} 行（部分回补，factor_daily 将按现有数据增量）")

    # 财务列：从 factor_daily 8/7 行复制（取 code→财务列）
    dragon = sqlite3.connect(str(DRAGON), timeout=60)
    dragon.execute("PRAGMA busy_timeout=60000")
    tmpl_rows = dragon.execute(
        f"SELECT code, ts_code FROM factor_daily WHERE trade_date=?", (TEMPLATE_DATE,)).fetchall()
    tmpl_map = {r[0]: r[1] for r in tmpl_rows}   # code(sz000001) -> ts_code(000001.SZ)
    print(f"[OK] 模板 {TEMPLATE_DATE}: {len(tmpl_map)} 只")

    # 拉财务列（8/7 行的全部非行情列）
    fin_cols_info = dragon.execute("PRAGMA table_info(factor_daily)").fetchall()
    all_cols = [c[1] for c in fin_cols_info]
    # 排除：id, ts_code, code, trade_date, report_date + 行情估值列(DB_TO_FD.values) + close?（factor_daily 无 close）
    exclude = {"id", "ts_code", "code", "trade_date", "report_date"} | set(DB_TO_FD.values())
    fin_cols = [c for c in all_cols if c not in exclude]
    print(f"[OK] 财务列 {len(fin_cols)} 个（从 {TEMPLATE_DATE} 复制）")

    # 取 8/7 财务列值
    fin_placeholder = ",".join(fin_cols)
    fin_data = {}
    for code, *vals in dragon.execute(
        f"SELECT code,{fin_placeholder} FROM factor_daily WHERE trade_date=?", (TEMPLATE_DATE,)):
        fin_data[code] = vals

    # 取 daily_basic 8/10 行情估值列
    db_cols = list(DB_TO_FD.keys())
    db_placeholder = ",".join(["ts_code"] + db_cols)
    market_rows = fund.execute(
        f"SELECT {db_placeholder} FROM daily_basic WHERE trade_date=?", (TARGET_DATE,)).fetchall()
    fund.close()
    print(f"[OK] daily_basic {TARGET_DATE} 行情行: {len(market_rows)}")

    # 组装并写入
    fd_market_cols = [DB_TO_FD[c] for c in db_cols]
    insert_cols = ["ts_code", "code", "trade_date", "report_date"] + fin_cols + fd_market_cols
    placeholders = ",".join(["?"] * len(insert_cols))
    sql = f"INSERT OR REPLACE INTO factor_daily ({','.join(insert_cols)}) VALUES ({placeholders})"

    inserted = 0
    skipped = 0
    batch = []
    for row in market_rows:
        ts_code = row[0]
        if not ts_code or len(ts_code) < 6:
            skipped += 1
            continue
        pure = ts_code[:6]
        code_prefixed = ("sh" if pure.startswith("6") else "sz") + pure
        # 财务列从模板复制
        fin_vals = fin_data.get(code_prefixed)
        if fin_vals is None:
            fin_vals = tuple([None] * len(fin_cols))   # 模板无该股则财务列留 NULL
        market_vals = row[1:]
        report_date = "2024-12-31 00:00:00"
        batch.append((ts_code, code_prefixed, TARGET_DATE, report_date) + tuple(fin_vals) + tuple(market_vals))
        if len(batch) >= 500:
            dragon.executemany(sql, batch)
            inserted += len(batch)
            batch = []
    if batch:
        dragon.executemany(sql, batch)
        inserted += len(batch)
    dragon.commit()

    # 验证
    chk = dragon.execute(
        "SELECT trade_date, COUNT(*) FROM factor_daily WHERE trade_date=? GROUP BY trade_date",
        (TARGET_DATE,)).fetchone()
    maxd = dragon.execute("SELECT MAX(trade_date) FROM factor_daily").fetchone()[0]
    dragon.close()
    print(f"[完成] 插入 {inserted} 行（跳过 {skipped}）")
    print(f"[验证] factor_daily {TARGET_DATE}: {chk}")
    print(f"[验证] factor_daily MAX(trade_date) = {maxd}")


if __name__ == "__main__":
    main()
