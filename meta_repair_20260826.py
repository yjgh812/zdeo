#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meta_repair_20260826.py — 20260824/25 假价污染三表定点重刷（主人已授权·取证升级版）

事实链修订（相比 META_QFQ_POLLUTION_ESCALATION_20260826）：
  * 本地 vipdoc 日线只到 2026-08-21 → 不可能作两日的干净基准;
  * 三表 stock_daily_raw / stock_daily_meta / stock_daily_meta_qfq 的 8/24-25 同批假价
    （000016 两日均 2870.8, baostock 真值 2.33, 偏移 1232×; 而 600519 与 baostock 完全一致
    → 污染为选择性补数失败, 非全市场性);
  * 干净唯一外部真值源 = baostock (adjustflag="3" 不复权, 与 raw/meta 口径一致)。

修复口径（对 20260824/20260825 的每一行, raw 与 meta 同法; qfq 仅出差异清单不改写——
复权口径边界交由 fix_qfq_rebuild / O2 QfqGuard 体系处置, 避免 double-fix）:
    a) 库内行存在 & baostock 无该日（停牌/退市）  → DELETE 幽灵行;
    b) 库内行存在 & baostock OHLC 不符            → 按 baostock 重写(|diff|>max(0.01,0.5%));
    c) 库内无行 & baostock 有                     → 记缺口计数, 不自动补(归生成器职责)。

安全: 改动行先备份至 backups/repair_bak_<ts>.db(独立小库); 分批提交; 幂等可重跑;
      绝不触碰 tulong_daily_update.py 本体(根因处置归 git init 后审计)。

用法: python meta_repair_20260826.py [--apply] [--tables meta,raw]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

TARGET_DATES = ("20260824", "20260825")
BS_DATES = {"20260824": ("2026-08-24",), "20260825": ("2026-08-25",)}
REL_TOL = 0.005
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
BAK_DIR = r"D:\zcodesj\backups"


def norm_baostock_code(code: str) -> str:
    """库存裸码(000016)/前缀码(sz000016) → sz.000016"""
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    c = c.split(".")[0].zfill(6)
    mkt = "sh" if c.startswith(("6",)) else ("bj" if c.startswith(("4", "8", "9")) else "sz")
    return f"{mkt}.{c}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--tables", default="meta,raw",
                   help="参与修复的表(meta/raw 组合); qfq 恒只报告")
    p.add_argument("--limit-codes", type=int, default=0, help="调试: 只扫前N只")
    a = p.parse_args()
    tables = [t.strip() for t in a.tables.split(",") if t.strip() in ("meta", "raw")]

    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print(f"[FAIL-CLOSED] baostock 登录失败: {lg.error_msg}")
        return 3

    cons = {"meta": sqlite3.connect(META_DB), "raw": sqlite3.connect(MAIN_DB)}
    for k, con in cons.items():
        con.execute("PRAGMA journal_mode=WAL")

    # 收集待查代码全集(两表并集)
    union_codes: set[str] = set()
    for key, tbl in (("meta", "stock_daily_meta"), ("raw", "stock_daily_raw")):
        ph = ",".join("?" * len(TARGET_DATES))
        cs = cons[key].execute(
            f"SELECT DISTINCT code FROM {tbl} WHERE date IN ({ph})", TARGET_DATES).fetchall()
        s = {str(r[0]).split(".")[-1].zfill(6) for r in cs}
        print(f"[SCAN] {tbl}: {len(s)} codes")
        union_codes |= s
    codes = sorted(union_codes)
    if a.limit_codes:
        codes = codes[: a.limit_codes]
    print(f"[SCAN] 并集 {len(codes)} 只; tables={tables}; apply={a.apply}")

    bak_rows: list[tuple] = []
    stats = {t: {"fix": 0, "ghost": 0, "clean": 0} for t in tables}
    gap = {"meta": 0, "raw": 0}
    bs_bad = 0
    samples = []
    t0 = time.time()

    buf_fix = {t: [] for t in tables}   # (table, code, date, o,h,l,c)
    buf_del = {t: [] for t in tables}   # (table, code, date)
    buf_gap = {t: [] for t in tables}

    for i, c6 in enumerate(codes):
        bsc = norm_baostock_code(c6)
        bsmap: dict[str, tuple] = {}
        try:
            # 区间单次拉取两日(RPC 减半; 停牌日该行自然缺失)
            rs = bs.query_history_k_data_plus(
                bsc, "date,open,high,low,close",
                start_date="2026-08-24", end_date="2026-08-25",
                frequency="d", adjustflag="3")
            while rs.next():
                row = rs.get_row_data()
                d_norm = row[0].replace("-", "")
                o, h, l_, cl = row[1], row[2], row[3], row[4]
                if cl and float(cl) > 0:
                    bsmap[d_norm] = (float(o), float(h), float(l_), float(cl))
        except Exception as e:
            bs_bad += 1
            if bs_bad < 5:
                print(f"[WARN] baostock {c6}: {e}")
            continue
        for t in tables:
            tbl = "stock_daily_meta" if t == "meta" else "stock_daily_raw"
            # 精确匹配, 防 LIKE '%000016' 误吞 600016(后缀撞码)
            rows = cons[t].execute(
                f"SELECT code,date,open,high,low,close FROM {tbl} "
                f"WHERE date IN ({','.join('?'*len(TARGET_DATES))}) "
                f"AND code IN (?, ?, ?, ?)",
                TARGET_DATES + (c6, "sh" + c6, "sz" + c6, "bj" + c6)).fetchall()
            by_date = {str(r[1]): r for r in rows}
            for d in TARGET_DATES:
                v = bsmap.get(d)
                rec = by_date.get(d)
                if rec is not None and v is None:
                    stats[t]["ghost"] += 1
                    bak_rows.append((t,) + tuple(rec))
                    buf_del[t].append((rec[0], rec[1]))
                elif rec is not None and v is not None:
                    vo, vh, vl, vc = v
                    bad = any(abs(float(rec[k]) - ref) > max(0.01, abs(ref) * REL_TOL)
                              for k, ref in ((2, vo), (3, vh), (4, vl), (5, vc)))
                    if bad:
                        stats[t]["fix"] += 1
                        bak_rows.append((t,) + tuple(rec))
                        buf_fix[t].append((vo, vh, vl, vc, rec[0], rec[1]))
                        if len(samples) < 15:
                            samples.append((t, c6, d, float(rec[5]), round(vc, 3)))
                elif rec is None and v is not None:
                    gap[t] += 1
                    buf_gap[t].append((rec, c6))
        if (i + 1) % 500 == 0:
            el = time.time() - t0
            eta = el / (i + 1) * (len(codes) - i - 1)
            print(f"  [{i+1}/{len(codes)}] elapsed={el:.0f}s eta={eta:.0f}s stats={stats}",
                  flush=True)

    bs.logout()

    if a.apply and bak_rows:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(BAK_DIR, exist_ok=True)
        bpath = os.path.join(BAK_DIR, f"repair_bak_{ts}.db")
        bcon = sqlite3.connect(bpath)
        bcon.execute("CREATE TABLE origin (tbl TEXT, code TEXT, date TEXT,"
                     "open REAL, high REAL, low REAL, close REAL)")
        bcon.executemany("INSERT INTO origin VALUES (?,?,?,?,?,?,?)", bak_rows)
        bcon.commit()
        bcon.close()
        print(f"[BACKUP] 原始行 {len(bak_rows)} 条 → {bpath}")

    # 写入(分批提交)
    if a.apply:
        for t in tables:
            tbl = "stock_daily_meta" if t == "meta" else "stock_daily_raw"
            n = 0
            for args in buf_del[t]:
                cons[t].execute(f"DELETE FROM {tbl} WHERE code=? AND date=?", args)
                n += 1
                if n % 500 == 0:
                    cons[t].commit()
            for args in buf_fix[t]:
                cons[t].execute(
                    f"UPDATE {tbl} SET open=?,high=?,low=?,close=? WHERE code=? AND date=?",
                    args)
                n += 1
                if n % 500 == 0:
                    cons[t].commit()
            cons[t].commit()
            print(f"[APPLIED] {tbl}: delete={len(buf_del[t])} update={len(buf_fix[t])}")

    # qfq 差异清单(只报告)
    try:
        cq = sqlite3.connect(QFQ_DB)
        qf = {"ghost": 0, "diff": 0}
        qlines = []
        ph = ",".join("?" * len(TARGET_DATES))
        qc_all = {str(r[0]).split(".")[-1].zfill(6) for r in cq.execute(
            f"SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date IN ({ph})",
            TARGET_DATES).fetchall()}
        for i2, c6 in enumerate(sorted(qc_all)):
            if a.limit_codes and i2 >= a.limit_codes:
                break
            # 复用 bsmap 太重—qfq 单独轻量核对 close 只对抽样? 为完整性仍全量循环baostock会翻倍耗时。
            # 方案: 只对 qfq 中 |close| 异常量级(>300 或 <0.05)报告 + 幽灵检测复用上面vipdoc不再可行,
            # 故此处只做量级告警清单(真值核修走 fix_qfq_rebuild)。
            rows = cq.execute(
                f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE date IN ({ph}) "
                f"AND code IN (?, ?, ?, ?)",
                TARGET_DATES + (c6, "sh" + c6, "sz" + c6, "bj" + c6)).fetchall()
            for r_ in rows:
                cl = float(r_[2] or 0)
                if cl > 300 or (0 < cl < 0.05):
                    qf["diff"] += 1
                    if len(qlines) < 10:
                        qlines.append((r_[0], r_[1], cl))
        cq.close()
        print(f"[QFQ-REPORT ONLY] 量级异常(close>300 或 <0.05) {qf['diff']} 笔")
        for l_ in qlines:
            print("   ", l_)
    except Exception as e:
        print(f"[WARN] qfq 报告跳过: {e}")

    print(f"\n[STATS] per-table={stats} bs_fail={bs_bad} 缺口(库无行baostock有)={gap}")
    for s in samples:
        print("  DIFF", s)
    if not a.apply:
        print("\n[DRY-RUN] 未写入任何变更; 加 --apply 执行")
    for con in cons.values():
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
