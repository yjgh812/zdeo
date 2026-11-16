#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_meta_rebuild.py — stock_daily_raw / stock_daily_meta 污染股修复（一次性数据链修复工具）
================================================================
背景（2026-08-26 诊断，见 reports/META_QFQ_POLLUTION_ESCALATION_20260826.md）：
  stock_daily_raw 基表 8/20-8/25 被写入 ≥65 只老股/退市股几千元假价（000982=9225、
  000016 8/24=2870.8 等，与 qfq 同源 tickflow），meta 同步污染。当前 tulong_daily_update
  版本已安全（只从干净 vipdoc 读 + 全量重建自愈），但已污染数据需清理。

修复策略（精细区分三类）：
  [重刷]  vipdoc 有该日且偏离>5%  → 用 vipdoc 真实价覆盖（INSERT OR REPLACE）
  [删除]  vipdoc 无该日且停牌/退市（vipdoc 最后日距目标 >10 交易日）
          → raw+meta 删除该股污染窗口假记录（真实无交易，不应有停牌后数据）
  [待确认] vipdoc 无该日但滞后（距目标 ≤10 交易日）→ 不动，需真实价源确认（如 000016）

用法:
  python fix_meta_rebuild.py --scan --dry-run   # 扫描并预览分类清单（不写库）
  python fix_meta_rebuild.py --scan --repair    # 执行修复（先备份）
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from build_qfq_meta_tdx import read_tdx_day  # noqa: E402

MAIN_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
META_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db"
WIN_S, WIN_E = 20260820, 20260825     # 污染窗口
DEVIATION = 0.05
STALE_DAYS = 10                        # vipdoc 滞后容忍（交易日）


def scan():
    """扫描 raw/meta 污染窗口内与 vipdoc 不一致的股票，分类。"""
    conn = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    rows = conn.execute(
        f"SELECT DISTINCT code FROM stock_daily_raw WHERE date BETWEEN ? AND ?",
        (WIN_S, WIN_E)).fetchall()
    conn.close()
    codes = [r[0] for r in rows]

    targets = [str(d) for d in pd.bdate_range(
        str(WIN_S), str(WIN_E)).strftime("%Y%m%d")]
    repair, delete, confirm, skip = [], [], [], []
    for code in sorted(codes):
        if code.startswith("92"):        # 北交所 vipdoc 不覆盖，跳过
            continue
        vd = read_tdx_day(code)
        # 该股 raw 窗口数据
        conn = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
        rw = conn.execute(
            "SELECT date, close FROM stock_daily_raw WHERE code=? AND date BETWEEN ? AND ?",
            (code, WIN_S, WIN_E)).fetchall()
        conn.close()
        raw_by_date = {str(d): c for d, c in rw}
        if vd is None:
            confirm.append((code, raw_by_date, "vipdoc 全无"))
            continue
        vmp = {d.strftime("%Y%m%d"): r["close"] for d, r in vd.iterrows()}
        vd_last = vd.index[-1]
        days_since_last = len(pd.bdate_range(vd_last.strftime("%Y%m%d"), str(WIN_E)))
        for d, rc in raw_by_date.items():
            if d in vmp:   # vipdoc 有该日 → 偏离则重刷
                if abs(rc / vmp[d] - 1) > DEVIATION:
                    repair.append((code, d, rc, vmp[d], rc / vmp[d]))
            else:          # vipdoc 无该日
                if days_since_last > STALE_DAYS:
                    delete.append((code, d, rc, f"vipdoc最后{vd_last.date()}"))
                else:
                    confirm.append((code, {d: rc}, f"vipdoc滞后({vd_last.date()})需真实价源"))
    return repair, delete, confirm, skip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--repair", action="store_true")
    a = ap.parse_args()

    repair, delete, confirm, _ = scan()
    print(f"\n=== 污染扫描（窗口 {WIN_S}~{WIN_E}）===")
    print(f"[重刷] {len(repair)} 条（vipdoc 有该日且偏离>5%）：")
    for c, d, rc, vc, r in repair[:20]:
        print(f"    {c} {d}: raw={rc:.2f} → vipdoc={vc:.2f} ({r:.2f}x)")
    if len(repair) > 20:
        print(f"    ... 共 {len(repair)} 条")
    print(f"[删除] {len(delete)} 条（停牌/退市，vipdoc 无该日）：")
    for c, d, rc, note in delete[:20]:
        print(f"    {c} {d}: raw={rc:.2f}（{note}）→ 删除假记录")
    if len(delete) > 20:
        print(f"    ... 共 {len(delete)} 条")
    print(f"[待确认] {len(confirm)} 只（vipdoc 滞后，需真实价源，如 000016）：")
    seen = {}
    for c, d, note in confirm[:15]:
        seen.setdefault(c, note)
    for c, note in list(seen.items())[:15]:
        print(f"    {c}（{note}）")
    if len(seen) > 15:
        print(f"    ... 共 {len(seen)} 只待确认")

    if not a.repair:
        print("\n（dry-run，未写库。--repair 执行前请人工确认分类）")
        return 0

    # ── 执行修复 ──
    if not repair and not delete:
        print("\n无重刷/删除项，结束")
        return 0
    # 备份
    bak = {"repair": repair, "delete": delete}
    bak_path = ROOT / "data" / f"meta_raw_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
    bak_path.write_text(json.dumps(bak, ensure_ascii=False), encoding="utf-8")
    print(f"\n📦 备份 → {bak_path}")

    conn = sqlite3.connect(str(MAIN_DB), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    mconn = sqlite3.connect(str(META_DB), timeout=60)
    mconn.execute("PRAGMA journal_mode=WAL")
    n_rep = n_del = 0
    for c, d, rc, vc, r in repair:
        # raw 覆盖为 vipdoc 真实价（保持其他字段近似：从 vipdoc 取完整行）
        vd = read_tdx_day(c)
        row = vd.loc[pd.Timestamp(str(d))]
        conn.execute(
            "INSERT OR REPLACE INTO stock_daily_raw (code,date,open,high,low,close,vol,amt) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (c, d, row["open"], row["high"], row["low"], vc, row["volume"], row["amount"]))
        # meta 同步（change_pct/amplitude 由当前数据重算近似：简单覆盖 close 系）
        mconn.execute(
            "UPDATE stock_daily_meta SET close=?, open=?, high=?, low=?, volume=?, amount=?, "
            "updated_at=? WHERE code=? AND date=?",
            (vc, row["open"], row["high"], row["low"], row["volume"], row["amount"],
             time.strftime("%Y-%m-%d %H:%M:%S"), c, d))
        n_rep += 1
    for c, d, rc, note in delete:
        conn.execute("DELETE FROM stock_daily_raw WHERE code=? AND date=?", (c, d))
        mconn.execute("DELETE FROM stock_daily_meta WHERE code=? AND date=?", (c, d))
        n_del += 1
    conn.commit(); mconn.commit()
    conn.close(); mconn.close()
    print(f"完成: 重刷 {n_rep} 条 / 删除 {n_del} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
