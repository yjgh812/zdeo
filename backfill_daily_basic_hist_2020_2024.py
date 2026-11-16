#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
backfill_daily_basic_hist_2020_2024.py — daily_basic 历史缺口回填器（一次性）

背景：daily_basic 2020-2023 各年仅 ~3% 覆盖（各约 1 个月）、2024 缺 22%
（2026-09-08 实测年度行数），卡融合总图课题#3 筹码分布因子数据闸，v22 打分/
画像等换手类消费方同受影响。2025/2026 已全覆盖不动。

口径：与 daily_chain_update.run_daily_basic 完全一致（18 列同构，peTTM→pe_ttm/
psTTM→ps_ttm 位置，最新股本 failover 腾讯，历史 mv 含最新股本口径失真=既有
口径非本脚本新增）；自愈=3 连败重连（9/7 语义）+30s socket 超时。
CKPT 语义适配区间场景：接口 success 即记 CKPT（区间零行=未上市/长期停牌的
永久态，重试无意义）；接口 failure 不记（走重试，SKIP 留痕可重跑补）。
幂等：INSERT OR REPLACE，可安全重跑。

用法：python backfill_daily_basic_hist_2020_2024.py [--dry-run]
"""
import socket
import sqlite3
import sys
import time
from pathlib import Path

import baostock as bs

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
LOG_DIR = Path(r"D:\workbuddyspace\logs")
CKPT = LOG_DIR / "backfill_ckpt_daily_basic_hist_2020_2024.txt"
SKIP = LOG_DIR / "backfill_skip_daily_basic_hist_2020_2024.txt"
START, END = "2020-01-01", "2024-12-31"


def log(msg):
    print(msg, flush=True)


def load_universe():
    with sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True) as m:
        codes = [r[0] for r in m.execute(
            "SELECT DISTINCT code FROM stock_daily_meta WHERE date BETWEEN ? AND ?",
            (START.replace("-", ""), END.replace("-", ""))).fetchall()]
    codes = sorted(c for c in codes if not c.startswith(("688", "8", "9", "4")))
    # 最新股本（现链同源口径）
    with sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True) as f:
        shares = {}
        for ts, tot, flt in f.execute(
                "SELECT ts_code, total_share, float_share FROM daily_basic "
                "WHERE (ts_code, trade_date) IN (SELECT ts_code, MAX(trade_date) FROM daily_basic GROUP BY ts_code)"):
            if tot and tot > 0:
                shares[ts] = (tot, flt or 0.0)
    uni = [(c, f"{c}.SH" if c.startswith("6") else f"{c}.SZ",
            *shares.get(f"{c}.SH" if c.startswith("6") else f"{c}.SZ", (0.0, 0.0)))
           for c in codes]
    return uni


def main():
    dry = "--dry-run" in sys.argv
    uni = load_universe()
    done = set(CKPT.read_text(encoding="utf-8").split()) if CKPT.exists() else set()
    todo = [x for x in uni if x[1] not in done]
    log(f"[hist_daily_basic] 池 {len(uni)} 只 / 已完成 {len(done)} / 待回补 {len(todo)} 区间 {START}~{END}")
    if dry or not todo:
        return 0

    socket.setdefaulttimeout(30)
    lg = bs.login()
    if lg.error_code != "0":
        sys.exit(f"baostock 登录失败: {lg.error_msg}")
    con = sqlite3.connect(FUND_DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    n = 0
    consec_fail = 0
    t0 = time.time()
    try:
        for i, (code, ts, tot_sh, flt_sh) in enumerate(todo, 1):
            bscode = ("sh." if code.startswith("6") else "sz.") + code
            rs = None
            for attempt in range(3):
                try:
                    rs = bs.query_history_k_data_plus(
                        bscode, "date,close,peTTM,pbMRQ,psTTM,turn",
                        start_date=START, end_date=END, frequency="d", adjustflag="3")
                except Exception:
                    rs = None
                if rs is not None and rs.error_code == "0":
                    break
                time.sleep(3 * (attempt + 1))
            if rs is None or rs.error_code != "0":
                consec_fail += 1
                with SKIP.open("a", encoding="utf-8") as f:
                    f.write(ts + "\n")
                if consec_fail >= 3:
                    log(f"[hist_daily_basic] 连续 {consec_fail} 只失败，重开 baostock 会话...")
                    try:
                        bs.logout()
                    except Exception:
                        pass
                    lg = bs.login()
                    if lg.error_code != "0":
                        log(f"[hist_daily_basic] 重连失败: {lg.error_msg}，继续尝试")
                    consec_fail = 0
                continue
            consec_fail = 0
            rows = []
            while rs.next():
                d, close, pe, pb, ps, turn = rs.get_row_data()
                if not close:
                    continue
                close = float(close)
                rows.append((ts, d.replace("-", ""), close,
                             float(turn) if turn else 0.0, 0.0, 0.0, 0.0,
                             float(pe) if pe else 0.0, float(pb) if pb else 0.0,
                             0.0, float(ps) if ps else 0.0, 0.0, 0.0,
                             tot_sh, flt_sh, 0.0, close * tot_sh, close * flt_sh))
            if rows:
                con.executemany(
                    "INSERT OR REPLACE INTO daily_basic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
                n += len(rows)
            # 区间场景: 接口 success 即记 CKPT(零行=未上市/长期停牌永久态, 重试无意义;
             # 与单日链内场景"空行不记"的差异=临时态 vs 永久态, 见 docstring)
            with CKPT.open("a", encoding="utf-8") as f:
                f.write(ts + "\n")
            if i % 100 == 0:
                con.commit()
                log(f"[hist_daily_basic] 进度 {i}/{len(todo)} 已写 {n:,} 行 {time.time()-t0:.0f}s")
        con.commit()
    finally:
        try:
            bs.logout()
        except Exception:
            pass
        con.close()
        socket.setdefaulttimeout(None)
    log(f"[hist_daily_basic] 完成：写入 {n:,} 行 耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
