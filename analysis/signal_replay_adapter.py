#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signal_replay_adapter.py — 信号回放适配器 v0（回放适配器第二步，呈现层零交易权）。

输入：attribution.db signal_snapshots（生产信号快照 379 事件 / 26 交易日，20260812~1106）。
对每个信号：
  执行日 = 该股次一交易日；执行日封死涨停 → 幽灵信号（实盘不可执行）
  否则 minute_fill_engine.walk_fill 市价买(qty=100, 量帽25%) → 首根5分钟K开盘价成交
  分钟库无该日 → 回退日开盘价（如实计数来源）
输出指标：
  可执行率 / 幽灵率(R1类) / 成交溢价中位(执行日开盘 vs 信号日收盘) /
  前瞻5日收益(可执行子集 vs 全体, 中位)
诚实边界：快照无信号强度列(signal_count全空)；窗口仅26交易日；qty=100量帽几乎不触发
(更真实仓位=v1)；20261106 信号执行日=11-09尚未发生→pending不计入。
退出码：0正常/3数据源不可用。"""
from __future__ import annotations

import bisect
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")
import market_rules_config as mrc  # noqa: E402
from minute_fill_engine import walk_fill  # noqa: E402

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
PRICE_DB = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
            + "?mode=ro")
REPORT_DIR = r"D:\zcodesj\reports"


def _load_signals():
    conn = sqlite3.connect(ATTR_DB)
    try:
        rows = conn.execute(
            "SELECT code, date, close_price FROM signal_snapshots "
            "ORDER BY date, code").fetchall()
    finally:
        conn.close()
    return [(_code6(c), int(str(d)[:8]), float(cp or 0)) for c, d, cp in rows
            if c6_ok(c)]


def c6_ok(c):
    s = _code6(c)
    return len(s) == 6 and s.isdigit() and not s.startswith(("4", "8", "92"))


def run():
    try:
        sigs = _load_signals()
        conn = sqlite3.connect(PRICE_DB, uri=True, check_same_thread=False)
    except Exception as e:  # noqa: BLE001
        print(f"[SIG-REPLAY] [FAIL-CLOSED] 数据源不可用({type(e).__name__}: {e})")
        return 3
    day_cache = {}
    stats = {"total": len(sigs), "pending": 0, "no_day": 0, "ghost": 0,
             "minute_fill": 0, "daily_fallback": 0, "executed": 0}
    f_prem, f_fwd5, a_fwd5 = [], [], []
    f_b5, a_b5 = [], []          # 沪深300同期5日收益（超额分母）
    brows = conn.execute(
        "SELECT date, close FROM index_daily WHERE index_code='000300' "
        "ORDER BY date").fetchall()
    bd = [int(r[0]) for r in brows]
    bc = {int(r[0]): float(r[1]) for r in brows}

    def _bench_fwd5(exec_d):
        i = bisect.bisect_left(bd, exec_d)
        if i >= len(bd) or bd[i] != exec_d or i + 5 >= len(bd):
            return None
        return (bc[bd[i + 5]] / bc[exec_d] - 1.0) * 100

    for code, d, cp in sigs:
        exec_d, exec_c, fwd5 = _next_day_and_fwd5(conn, code, d)
        if not exec_d or not exec_c:
            stats["no_day"] += 1
            continue
        if exec_d >= 20261107:
            stats["pending"] += 1  # 执行日未到, 不计入(11-06信号执行日=11-09周一)
            continue
        sealed, _pc = _sealed_up_daily(conn, code, exec_d)
        if sealed:
            stats["ghost"] += 1
            if fwd5 is not None:
                a_fwd5.append(fwd5)
                a_b5.append(_bench_fwd5(exec_d))
            continue
        w = walk_fill(code, exec_d, "BUY", 1e9, 100, day_cache)
        if w is None or not w["fills"]:
            stats["daily_fallback"] += 1
            rows2 = conn.execute(
                "SELECT open FROM stock_daily_raw WHERE code=? AND date=?",
                (code, exec_d)).fetchone()
            fill = float(rows2[0]) if rows2 else None
        else:
            stats["minute_fill"] += 1
            fill = w["fills"][0][1]
        if fill is None:
            stats["no_day"] += 1
            continue
        stats["executed"] += 1
        if cp > 0:
            f_prem.append((fill / cp - 1.0) * 100)
        if fwd5 is not None:
            f_fwd5.append(fwd5)
            f_b5.append(_bench_fwd5(exec_d))
            a_fwd5.append(fwd5)
            a_b5.append(_bench_fwd5(exec_d))
    _med = lambda v: sorted(v)[len(v) // 2] if v else None  # noqa: E731
    m_prem = _med(f_prem)
    m_f5 = _med(f_fwd5)
    m_a5 = _med(a_fwd5)
    e_ex = [f - b for f, b in zip(f_fwd5, f_b5) if b is not None]
    a_ex = [f - b for f, b in zip(a_fwd5, a_b5) if b is not None]
    m_b5 = _med([b for b in f_b5 if b is not None])
    m_eex = _med(e_ex)
    m_aex = _med(a_ex)
    L = [f"# 信号回放适配器v0 {datetime.now():%Y-%m-%d %H:%M}", "",
         f"- 信号快照 {stats['total']} 事件（26交易日, 20260812~1106）；"
         f"快照无强度列(signal_count全空)——如实边界",
         f"- 可执行 {stats['executed']}｜幽灵(执行日封死涨停) {stats['ghost']}｜"
         f"pending {stats['pending']}｜无日线 {stats['no_day']}",
         f"- 成交来源: 分钟撮合 {stats['minute_fill']}｜日级回退 {stats['daily_fallback']}", "",
         "## 指标", ""]
    ex = stats['executed'] + stats['ghost']
    if ex:
        L.append(f"- 可执行率={stats['executed'] / ex * 100:.1f}% "
                 f"幽灵率={stats['ghost'] / ex * 100:.1f}% (n={ex})")
    if m_prem is not None:
        L.append(f"- 成交溢价中位={m_prem:+.2f}% (执行日开盘/首bar开盘 vs 信号日收盘)")
    if m_f5 is not None:
        L.append(f"- 前瞻5日收益中位: 可执行子集={m_f5:+.2f}% vs 全体={m_a5:+.2f}% "
                 f"(可执行n={len(f_fwd5)})")
    if m_eex is not None:
        L.append(f"- 基准(沪深300)同期5日中位={m_b5:+.2f}%｜**信号层超额中位: "
                 f"可执行={m_eex:+.2f}pp vs 全体={m_aex:+.2f}pp** (n={len(e_ex)})")
    L += ["", "## 诚实边界", "",
          "- qty=100量帽几乎不触发(更真实仓位=v1)；窗口仅26交易日；",
          "- 幽灵判定=日线封死口径(与撮合引擎同公式)；分钟回退按日开盘计溢价。",
          "- 本报告=信号层绩效基线，不含闸门(R闸)与人工干预成分。"]
    path = os.path.join(REPORT_DIR,
                        f"signal_replay_adapter_{datetime.now():%Y%m%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    ex_r = f"{stats['executed'] / ex * 100:.1f}%" if ex else "N/A"
    g_r = f"{stats['ghost'] / ex * 100:.1f}%" if ex else "N/A"
    print(f"[SIG-REPLAY] 信号{stats['total']} 可执行率={ex_r} 幽灵率={g_r} "
          f"溢价中位={m_prem if m_prem is None else round(m_prem, 2)}% "
          f"fwd5中位: 可执行={m_f5 if m_f5 is None else round(m_f5, 2)}% "
          f"超额中位: 可执行={m_eex if m_eex is None else round(m_eex, 2)}pp -> {path}")
    return 0


def _code6(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


def _sealed_up_daily(conn, code, d):
    """执行日封死涨停判定（日线口径，与撮合引擎同公式）。"""
    prev, lo, pct = None, None, None
    rows = conn.execute(
        "SELECT date, low, close FROM stock_daily_raw WHERE code=? AND date<=? "
        "ORDER BY date DESC LIMIT 2", (code, d)).fetchall()
    if len(rows) < 2:
        return False, None
    prev = float(rows[1][2])
    lo = float(rows[0][1])
    pct = mrc.limit_pct(code, d)
    lim = prev * (1.0 + pct)
    tol = max(lim * 1e-3, 1e-6)
    return lo >= lim - tol, prev


def _next_day_and_fwd5(conn, code, d):
    """返回 (执行日, 执行日收盘, fwd5收益%或None)。"""
    rows = conn.execute(
        "SELECT date, close FROM stock_daily_raw WHERE code=? AND date>? "
        "ORDER BY date LIMIT 6", (code, d)).fetchall()
    if not rows:
        return None, None, None
    exec_d, exec_c = int(rows[0][0]), float(rows[0][1])
    fwd5 = None
    if len(rows) >= 6:
        fwd5 = (float(rows[5][1]) / exec_c - 1.0) * 100
    return exec_d, exec_c, fwd5


if __name__ == "__main__":
    raise SystemExit(run())
