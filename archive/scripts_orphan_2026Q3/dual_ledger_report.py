#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双账本胜率报表（2026-08-13 主人指令：名单内/名单外分流统计）。

按 list_membership（bp_ep_top20 / outside_list / unknown）× subsystems（信号源）
双维度分组，输出买入笔数、平仓胜率、平均盈亏、总盈亏、平均持有期。
每月跑一次，验证"信号保真工程"是否达标（目标：名单内组 3 个月滚动期望 ≥ 回测月均 +1.9%）。

用法：
  python dual_ledger_report.py              # 全量
  python dual_ledger_report.py --since 20260813   # 只看某日起
"""
from __future__ import annotations

import argparse
import json
import sqlite3

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    a = ap.parse_args()

    con = sqlite3.connect(ATTR_DB)
    con.row_factory = sqlite3.Row

    # 1) 买入侧：按名单归属 × 信号源分组
    where = "WHERE action='BUY'"
    if a.since:
        where += f" AND trade_date >= '{a.since}'"
    print("=== 买入账本：名单归属 × 信号源 ===")
    rows = con.execute(
        f"SELECT list_membership, subsystems, COUNT(*) n, ROUND(COALESCE(SUM(amount),0),0) amt "
        f"FROM trade_records {where} GROUP BY list_membership, subsystems ORDER BY amt DESC"
    ).fetchall()
    for r in rows:
        print(f"  {r['list_membership'] or 'NULL':14s} | {r['subsystems'] or 'NULL':20s} | {r['n']:3d}笔 | {r['amt']:>12,.0f}元")

    # 2) 平仓侧：position_history join trade_records 按名单归属
    # 2026-08-29: position_history=冻结历史表(写入链路死亡, kb/rules/data-field-semantics.md §五)
    # 本段统计仅覆盖冻结历史段(≈2026-08-27 前部分行); 增量平仓请走 trade_records FIFO 视图
    print("\n=== 平仓账本：名单归属分组表现 ===")
    rows = con.execute(
        """
        SELECT COALESCE(t.list_membership,'unknown') grp,
               COUNT(*) n,
               SUM(CASE WHEN p.pnl_amount>0 THEN 1 ELSE 0 END) wins,
               ROUND(COALESCE(AVG(p.pnl_pct),0),2) avg_pct,
               ROUND(COALESCE(SUM(p.pnl_amount),0),0) pnl,
               ROUND(COALESCE(AVG(julianday(p.exit_date)-julianday(p.entry_date)),0),1) hold_days
        FROM position_history p
        LEFT JOIN trade_records t
          ON t.code = printf('%06d', CAST(p.code AS INTEGER)) AND t.action='BUY'
         AND t.trade_date = replace(p.entry_date,'-','')
        WHERE p.exit_date IS NOT NULL
        GROUP BY grp
        """
    ).fetchall()
    for r in rows:
        wr = (r["wins"] / r["n"] * 100) if r["n"] else 0
        print(f"  {r['grp']:14s} | {r['n']:3d}笔 | 胜率{wr:5.1f}% | 均{r['avg_pct']:+.2f}% | "
              f"盈亏{r['pnl']:>+9,.0f}元 | 均持{r['hold_days']}天")

    # 3) 信号源维度平仓表现（验证哪个信号源真有效）
    print("\n=== 平仓账本：信号源分组表现 ===")
    rows = con.execute(
        """
        SELECT COALESCE(t.subsystems,'unknown') grp,
               COUNT(*) n,
               SUM(CASE WHEN p.pnl_amount>0 THEN 1 ELSE 0 END) wins,
               ROUND(COALESCE(AVG(p.pnl_pct),0),2) avg_pct,
               ROUND(COALESCE(SUM(p.pnl_amount),0),0) pnl
        FROM position_history p
        LEFT JOIN trade_records t
          ON t.code = printf('%06d', CAST(p.code AS INTEGER)) AND t.action='BUY'
         AND t.trade_date = replace(p.entry_date,'-','')
        WHERE p.exit_date IS NOT NULL
        GROUP BY grp ORDER BY pnl DESC
        """
    ).fetchall()
    for r in rows:
        wr = (r["wins"] / r["n"] * 100) if r["n"] else 0
        print(f"  {r['grp']:20s} | {r['n']:3d}笔 | 胜率{wr:5.1f}% | 均{r['avg_pct']:+.2f}% | 盈亏{r['pnl']:>+9,.0f}元")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
