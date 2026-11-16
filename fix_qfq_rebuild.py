#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_qfq_rebuild.py — qfq 库污染股票全量重算修复（一次性数据链修复工具）
================================================================
背景（2026-08-25 诊断，见 reports/QFQ_CORRUPTION_DIAGNOSIS_20260825.md）：
  run_qfq 增量更新对部分股票在 8/11、8/24 应用了错误因子/日K（tickflow 源批量错误），
  导致 qfq 价与真实价（vipdoc/meta）固定比例偏离（0.29~8.05 倍），当前仍污染。
  本工具用【vipdoc 原始价 + 新浪 qfq.js 因子】对污染股【全量重算】qfq 序列并覆盖写回
  （INSERT OR REPLACE，主键 code+date），消除增量基准漂移。

用法:
  python fix_qfq_rebuild.py --codes 600381,000509   # 指定重刷
  python fix_qfq_rebuild.py --scan                   # 自动扫描 qfq[8/25] vs vipdoc 偏离>5% 的污染股
  python fix_qfq_rebuild.py --scan --dry-run         # 只打印清单不写库
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from build_qfq_meta_tdx import (  # noqa: E402  生产 qfq 逻辑复用
    QFQ_DB, QFQ_TABLE, compute_qfq, ensure_table, fetch_sina_qfq, read_tdx_day,
    _write_rows,
)

DEVIATION = 0.05   # qfq[最新日] vs vipdoc[最新日] 偏离阈值


def scan_polluted(threshold: float = DEVIATION):
    """扫描污染股：qfq 最新日收盘 vs **vipdoc 同日** 收盘 偏离 > threshold。
    同日对比避免停牌误判（vipdoc 同日无该股 → 跳过，不纳入修复）。"""
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    last_date = con.execute(f"SELECT MAX(date) FROM {QFQ_TABLE}").fetchone()[0]
    rows = con.execute(
        f"SELECT code, date, close FROM {QFQ_TABLE} WHERE date=?", (last_date,)).fetchall()
    con.close()
    polluted = []
    for code, date, qfq_close in rows:
        raw_df = read_tdx_day(code)
        if raw_df is None:
            continue
        mp = {d.strftime("%Y%m%d"): r["close"] for d, r in raw_df.iterrows()}
        if date not in mp:
            continue   # vipdoc 同日无该股（停牌/未同步）→ 不误判
        vr = mp[date]
        if abs(qfq_close / vr - 1) > threshold:
            polluted.append((code, qfq_close, vr, qfq_close / vr))
    return last_date, sorted(polluted, key=lambda x: -abs(x[3] - 1))


def rebuild_one(qfq_conn, code: str, verbose: bool = True):
    """用 vipdoc + 新浪因子全量重算单只 qfq 并覆盖写回。返回 (status, info)。"""
    raw_df = read_tdx_day(code)
    if raw_df is None:
        return ("skip", "vipdoc 无日线")
    factor_df = fetch_sina_qfq(code)
    if factor_df is None:
        return ("failed", "新浪 qfq.js 因子拉取失败（不写库，保留现状）")
    qfq_df = compute_qfq(code, raw_df, factor_df)
    if qfq_df is None or qfq_df.empty:
        return ("skip", "compute_qfq 空")
    rows = []
    for d, row in qfq_df.iterrows():
        rows.append((d.strftime("%Y%m%d"), row["open"], row["high"], row["low"],
                     row["close"], row["volume"], row["amount"], row["change_pct"]))
    _write_rows(qfq_conn, code, rows)   # INSERT OR REPLACE 覆盖
    if verbose:
        print(f"  ✅ {code}: 重算 {len(rows)} 行并覆盖写回")
    return ("ok", len(rows))


def main() -> int:
    ap = argparse.ArgumentParser(description="qfq 污染股全量重算修复")
    ap.add_argument("--codes", help="指定重刷的股票代码（逗号分隔）")
    ap.add_argument("--scan", action="store_true", help="自动扫描污染股")
    ap.add_argument("--dry-run", action="store_true", help="只打印清单/预览，不写库")
    a = ap.parse_args()

    if a.scan:
        last_date, polluted = scan_polluted()
        print(f"qfq 最新日 {last_date} | 污染股（偏离>{DEVIATION:.0%}）{len(polluted)} 只：")
        for code, qf, vr, ratio in polluted:
            print(f"  {code}: qfq={qf:.3f} vipdoc={vr:.3f} 偏离={ratio:.2f}x")
        if a.dry_run or not polluted:
            print("（--dry-run 或无需修复，结束）" if a.dry_run else "（无污染股）")
            return 0
        codes = [c for c, _, _, _ in polluted]
    elif a.codes:
        codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    else:
        ap.error("须提供 --codes 或 --scan")
        return 2

    if a.dry_run:
        print(f"[dry-run] 将重刷 {len(codes)} 只：{codes}")
        return 0

    # 写前备份（轻量：备份受影响股票的 qfq 行到 data/backup_*.json）
    import json
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    bak = {}
    for code in codes:
        r = con.execute(f"SELECT * FROM {QFQ_TABLE} WHERE code=?", (code,)).fetchall()
        bak[code] = r
    con.close()
    bak_path = ROOT / "data" / f"qfq_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
    bak_path.write_text(json.dumps(bak, ensure_ascii=False), encoding="utf-8")
    print(f"📦 备份 {len(codes)} 只 qfq 行 → {bak_path}")

    qfq_conn = sqlite3.connect(str(QFQ_DB), timeout=60)
    qfq_conn.execute("PRAGMA journal_mode=WAL")
    qfq_conn.execute("PRAGMA busy_timeout=60000")
    ensure_table(qfq_conn)
    ok = failed = skipped = 0
    for code in codes:
        status, info = rebuild_one(qfq_conn, code)
        if status == "ok":
            ok += 1
        elif status == "failed":
            failed += 1
            print(f"  ❌ {code}: {info}")
        else:
            skipped += 1
            print(f"  ⏭️  {code}: {info}")
    qfq_conn.close()
    print(f"完成: 重刷 {ok} / 失败 {failed} / 跳过 {skipped}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
