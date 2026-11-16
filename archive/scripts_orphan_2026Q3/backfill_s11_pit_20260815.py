#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_s11_pit_20260815.py — s11通道PIT治理(20260815)一次性回填
==================================================================
背景:
  stock_signals.s11_financial(财务排雷) 生成端用 bisect_right(report_dates, d)-1
  期末as-of选财报 —— 交易日一过报告期末即用该期数据, 法定披露前最长前视约4个月
  (与已修复的 Phase4 stock_factors_fundamental 同模式)。

本脚本(加列不加乱, 与Phase4口径一致, 仅写新列/新表):
  1) stock_financial 加列 pit_avail_date = 法定披露截止日并回填:
       Q1(0331)→当年0430 | 中报(0630)→当年0831 | Q3(0930)→当年1031 | 年报(1231)→次年0430
  2) stock_signals 加列 s11_pit(REAL) 并全量回填历史:
     打分规则与旧 s11 逐条相同, 仅 as-of 键从 report_date 换成 pit_avail_date。
     披露截止前无可用财报 → 0(与旧逻辑"无数据=0"语义一致)。
  3) 输出统计: 总行数/覆盖数/旧s11≠s11_pit行数(残余前视规模)及分年分布。

铁律: 不改 s11_financial / composite_score 等任何旧列(下游组合器还在消费)。
用法: python backfill_s11_pit_20260815.py [--dry-run]
"""
import sqlite3
import sys
import time

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"

FIN_PIT_SQL = """
UPDATE stock_financial
SET pit_avail_date = CASE report_date % 10000
    WHEN 331  THEN (report_date / 10000) * 10000 + 430
    WHEN 630  THEN (report_date / 10000) * 10000 + 831
    WHEN 930  THEN (report_date / 10000) * 10000 + 1031
    WHEN 1231 THEN (report_date / 10000 + 1) * 10000 + 430 END
WHERE pit_avail_date IS NULL
"""


def score11(roe, pe, pb, dr, rg, pg):
    """与 tulong_daily_update.py Phase3 逐条相同(浮点运算顺序一致保证逐位一致)。"""
    score = 0
    if roe is not None and roe > 15: score += 0.3
    elif roe is not None and roe > 8: score += 0.15
    elif roe is not None and roe < 0: score -= 0.3
    if pe is not None and 5 <= pe <= 30: score += 0.25
    elif pe is not None and pe > 100: score -= 0.2
    if pb is not None and 0 < pb < 3: score += 0.2
    elif pb is not None and pb > 10: score -= 0.15
    if rg is not None and rg > 20: score += 0.15
    if pg is not None and pg > 20: score += 0.15
    if dr is not None and 0 < dr < 60: score += 0.1
    return max(-1, min(1, score))


def main():
    dry_run = "--dry-run" in sys.argv
    t0 = time.time()
    conn = sqlite3.connect(MAIN_DB, timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")
    c = conn.cursor()

    # ── 1) stock_financial.pit_avail_date ──
    fin_cols = [r[1] for r in c.execute("PRAGMA table_info(stock_financial)")]
    if "pit_avail_date" not in fin_cols:
        if dry_run:
            print("[dry-run] 将 ALTER stock_financial ADD COLUMN pit_avail_date INTEGER 并回填")
        else:
            c.execute("ALTER TABLE stock_financial ADD COLUMN pit_avail_date INTEGER")
            conn.commit()
            print("ALTER stock_financial + pit_avail_date 完成")
    n = conn.execute(FIN_PIT_SQL).rowcount if not dry_run else -1
    conn.commit() if not dry_run else None
    if not dry_run:
        print(f"stock_financial pit_avail_date 回填 {n:,} 行")
    bad = c.execute("SELECT COUNT(*) FROM stock_financial WHERE pit_avail_date IS NULL").fetchone()[0] \
        if not dry_run else "N/A(dry-run)"
    if not dry_run:
        print(f"stock_financial pit_avail_date 仍为NULL: {bad:,} (预期0, MMDD全为标准季末)")
    else:
        print("stock_financial pit_avail_date 仍为NULL: N/A(dry-run)")

    # ── 2) stock_signals.s11_pit ──
    sig_cols = [r[1] for r in c.execute("PRAGMA table_info(stock_signals)")]
    if "s11_pit" not in sig_cols:
        if dry_run:
            print("[dry-run] 将 ALTER stock_signals ADD COLUMN s11_pit REAL")
            return
        c.execute("ALTER TABLE stock_signals ADD COLUMN s11_pit REAL")
        conn.commit()
        print("ALTER stock_signals + s11_pit 完成")

    done = c.execute("SELECT COUNT(*) FROM stock_signals WHERE s11_pit IS NOT NULL").fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM stock_signals").fetchone()[0]
    if done >= total and total > 0:
        print(f"s11_pit 已全部回填({done:,}/{total:,}), 幂等退出")
        conn.close()
        return
    if dry_run:
        print(f"[dry-run] 待回填 {total - done:,}/{total:,} 行")
        conn.close()
        return

    # ── 3) 按披露截止分段回填(与Phase3的bisect_right(pit,d)-1语义严格一致) ──
    c.execute("""
        SELECT code, report_date, pit_avail_date, roe, pe, pb, debt_ratio, revenue_growth, profit_growth
        FROM stock_financial
        WHERE roe IS NOT NULL OR pe IS NOT NULL OR pb IS NOT NULL
        ORDER BY code, report_date
    """)
    fin = {}
    for code, rd, pit, roe, pe, pb, dr, rg, pg in c.fetchall():
        if rd is None:
            continue
        pit = int(pit) if pit is not None else None
        fin.setdefault(code, []).append((int(rd), pit, roe, pe, pb, dr, rg, pg))

    print(f"stock_financial 可用代码 {len(fin):,} 只, 开始分段回填 s11_pit ...")
    # 段语义(与Phase3 bisect_right(pit,d)-1严格一致):
    #   date < pit[0] → 0 ; pit[i] <= date < pit[i+1] → score[i] ; date >= pit[-1] → score[-1]
    #   上界 10**12 即"无上界"; pit相等(年报与Q1同截止)时后一期段为空、最新一期胜出
    seg_sql = "UPDATE stock_signals SET s11_pit = ? WHERE code = ? AND date >= ? AND date < ?"
    zero_sql = "UPDATE stock_signals SET s11_pit = 0 WHERE code = ? AND s11_pit IS NULL"
    batch, t_start = [], time.time()

    def flush():
        if batch:
            c.executemany(seg_sql, [b for b in batch if b[0] != "ZERO" and b[3] > b[2]])
            c.executemany(zero_sql, [(b[1],) for b in batch if b[0] == "ZERO"])
            conn.commit()
            batch.clear()

    for i, (code, arr) in enumerate(fin.items(), 1):
        pits = [pit for (_, pit, *_rest) in arr]
        for j, (rd, pit, roe, pe, pb, dr, rg, pg) in enumerate(arr):
            if pit is None:
                continue
            nxt = pits[j + 1] if j + 1 < len(pits) else 10 ** 12
            batch.append((score11(roe, pe, pb, dr, rg, pg), code, pit, nxt))
        if pits and pits[0] is not None:
            batch.append(("ZERO", code, 0, 0))   # 标记: 首个披露截止前的行置0
        if len(batch) >= 5000:
            flush()
        if i % 500 == 0:
            print(f"  进度 {i:,}/{len(fin):,} 只代码 ({time.time()-t_start:.0f}s)")
    flush()

    # 无财务数据的代码 → 0
    nofin = [r[0] for r in c.execute(
        "SELECT DISTINCT code FROM stock_signals WHERE s11_pit IS NULL")]
    c.executemany(zero_sql, [(code,) for code in nofin])
    conn.commit()
    print(f"无财务数据代码置0: {len(nofin):,} 只")

    # ── 4) 统计 ──
    total = c.execute("SELECT COUNT(*) FROM stock_signals").fetchone()[0]
    filled = c.execute("SELECT COUNT(*) FROM stock_signals WHERE s11_pit IS NOT NULL").fetchone()[0]
    diff = c.execute("SELECT COUNT(*) FROM stock_signals "
                     "WHERE s11_financial IS NOT NULL AND s11_financial <> s11_pit").fetchone()[0]
    s11n = c.execute("SELECT COUNT(*) FROM stock_signals WHERE s11_financial IS NOT NULL").fetchone()[0]
    print("=" * 60)
    print(f"回填完成: s11_pit非NULL {filled:,}/{total:,}")
    print(f"残余前视规模: 旧s11≠s11_pit {diff:,}/{s11n:,} ({diff/max(s11n,1)*100:.1f}%)")
    print("分年分布(旧s11≠s11_pit):")
    for y, d, t in c.execute("""
            SELECT date/10000, SUM(s11_financial IS NOT NULL AND s11_financial <> s11_pit), COUNT(*)
            FROM stock_signals GROUP BY date/10000 ORDER BY 1 DESC LIMIT 12"""):
        print(f"  {y}: {d:,}/{t:,} ({d/max(t,1)*100:.1f}%)")
    print(f"耗时 {time.time()-t0:.0f}s")
    conn.close()


if __name__ == "__main__":
    main()
