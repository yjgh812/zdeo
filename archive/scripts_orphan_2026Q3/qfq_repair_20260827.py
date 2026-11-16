#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qfq_repair_20260827.py — dragon_quant_qfq 20260824/25 假价定点修复（meta_repair 同源事件）

事实（2026-08-27 凌晨取证）：tulong_daily_update.py 8/25 21:06 改版后从垃圾源补数,
假价贯穿三库。raw/meta 已由 meta_repair_20260826.py 以 baostock 修复;本脚本处理 qfq 库:
  受影响判定: qfq 20260824/25 行 vs **raw 表(已修真值)** 同码同日偏离>0.5%；
             或 qfq 有行而 raw 无该行(停牌幽灵,qfq==raw 最近期无除权时成立,
             幽灵一律 DELETE——复权库同样不该有停牌日行情)。
  修复方式(两条路径):
    A) 无除权窗口( sina 因子在 8/21..最新 无变化 ): qfq_corrected = raw_true(逐OHLC) 直替;
    B) 有除权: 该股整条调用 build_qfq_meta_tdx.compute_qfq 全量重算
       (raw=vipdoc全史∪baostock 8/22..最新, 因子=sina), 不手推单点因子。
  8/22~8/26 中 qfq 缺行而 raw/baostock 有的(补数断档)一并按 B/A 补齐。

安全: 写前备份改动行为独立 bak db; --dry-run 默认; 不碰 tulong_daily_update 本体;
     写入仅限 code 子集（非全表 VACUUM/重构）。

用法: python qfq_repair_20260827.py [--apply] [--max-codes N]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj")

from build_qfq_meta_tdx import (  # noqa: E402
    QFQ_DB, QFQ_TABLE, _write_rows, compute_qfq, fetch_sina_qfq, read_tdx_day,
)

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
BAK_DIR = r"D:\zcodesj\backups"
TARGET_DATES = ("20260824", "20260825", "20260826")  # 8/27 复检实证: 假价残留集中于纯 8/26 行
                                                     # (raw/meta 已净且为基准; 000007 等 6 样本矩阵核对)
BS_EXTEND = ("20260822", "20260823", "20260824", "20260825", "20260826")
REL_TOL = 0.005


def norm6(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.split(".")[0].zfill(6)


def bs_code(c6: str) -> str:
    mkt = "sh" if c6.startswith("6") else ("bj" if c6.startswith(("4", "8", "9")) else "sz")
    return f"{mkt}.{c6}"


def fetch_bs_range(bs, c6: str) -> dict[str, tuple]:
    """{yyyymmdd: (o,h,l,c,amount,volume)} 2026-08-22..26"""
    out: dict[str, tuple] = {}
    try:
        rs = bs.query_history_k_data_plus(
            bs_code(c6), "date,open,high,low,close,amount,volume",
            start_date="2026-08-22", end_date="2026-08-26",
            frequency="d", adjustflag="3")
        while rs.next():
            r = rs.get_row_data()
            d = r[0].replace("-", "")
            o, h, l_, cl = float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0)
            if cl <= 0:
                continue
            out[d] = (o, h, l_, cl, float(r[5] or 0), int(float(r[6] or 0)))
    except Exception:
        pass
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--max-codes", type=int, default=0)
    a = p.parse_args()

    cq = sqlite3.connect(QFQ_DB)
    cm = sqlite3.connect(MAIN_DB)
    cq.execute("PRAGMA journal_mode=WAL")
    cm.execute("PRAGMA journal_mode=WAL")

    # ---- 第一步: 以 raw(已修真值) 全集为基准, 反查 qfq 的「错价行」与「缺口」----
    # 注意: qfq.date TEXT / raw.date INTEGER —— dkey 统一规范化;
    # 从 raw 出发而不仅看 qfq 存在行, 否则会漏掉已被其他会话清空的缺口股(8/27 凌晨教训)。
    def dkey(d) -> str:
        return str(d).split(".")[0].split()[0].replace("-", "").zfill(8)

    raw_map: dict[tuple[str, str], tuple] = {}
    ph = ",".join("?" * len(TARGET_DATES))
    for code, date, o, h, l_, c in cm.execute(
            f"SELECT code,date,open,high,low,close FROM stock_daily_raw WHERE date IN ({ph})",
            TARGET_DATES).fetchall():
        raw_map[(norm6(code), dkey(date))] = (float(o), float(h), float(l_), float(c))

    suspects: set[str] = set()
    qfq_have: set[tuple[str, str]] = set()
    for code, date, oc in cq.execute(
            f"SELECT code,date,close FROM {QFQ_TABLE} WHERE date IN ({ph})",
            TARGET_DATES).fetchall():
        c6 = norm6(code)
        dk = dkey(date)
        rv = raw_map.get((c6, dk))
        if rv is None:      # qfq 有行而 raw 无 = 停牌幽灵(保守不动, 归口值班处置)
            continue
        qfq_have.add((c6, dk))
        qc = float(oc or 0)
        if qc <= 0 or abs(qc / rv[3] - 1) > REL_TOL:
            suspects.add(c6)          # 错价行
    gaps: set[str] = set()
    for (c6, dk), _v in raw_map.items():       # raw 有真值而 qfq 缺行 → 待补齐
        if (c6, dk) not in qfq_have:
            gaps.add(c6)
    print(f"[SCAN] 错价 {len(suspects)} 只 / 缺口 {len(gaps)} 只")
    codes = sorted(suspects | gaps)
    if a.max_codes:
        codes = codes[: a.max_codes]
    print(f"[SCAN] 待处理股票 {len(codes)} 只; apply={a.apply}")

    if not a.apply:
        for c6 in codes[:10]:
            print("  will-process", c6)
        print("[DRY-RUN] 未写入; 加 --apply 执行")
        cq.close(); cm.close()
        return 0

    # ---- 第二步: 逐只修复 ----
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print(f"[FAIL-CLOSED] baostock 登录失败 {lg.error_msg}")
        return 3

    from pandas import DataFrame  # noqa: PLC0415

    bak_rows: list[tuple] = []
    fixed = failed = skipped = 0
    t0 = time.time()
    for i, c6 in enumerate(codes):
        store_code = c6   # qfq 表 code 为裸码(1200万行 len=6 实测), 严禁加前缀(8/27教训)

        bsmap = fetch_bs_range(bs, c6)
        if not bsmap:
            skipped += 1
            continue
        try:
            factor_df = fetch_sina_qfq(c6)
        except Exception as e:
            print(f"[WARN] {c6} 新浪因子失败 {e}")
            failed += 1
            continue
        raw_df = read_tdx_day(c6)          # vipdoc 全史(至 8/21)
        prev_close = None
        if raw_df is not None and len(raw_df):
            prev_close = float(raw_df["close"].iloc[-1])   # 20260821 收盘, 用于首日 change_pct
        rows_payload = []                  # [(date,o,h,l,c,volume,amount)]
        for d in BS_EXTEND:
            v = bsmap.get(d)
            if v is None:
                continue
            rows_payload.append((d, v[0], v[1], v[2], v[3], v[5], v[4]))
        # 因子事件检测: sina 最新因子 == 8/21 因子 → 期间无除权 → qfq==raw 可直替(direct)
        no_event = False
        try:
            f_series = factor_df.iloc[:, 0]
            last_f = float(f_series.iloc[-1])
            f_at_21 = None
            for idx, fv in f_series.items():
                ds = idx.strftime("%Y%m%d") if hasattr(idx, "strftime") \
                    else str(idx)[:10].replace("-", "")
                if ds == "20260821":
                    f_at_21 = float(fv)
                    break
            no_event = (f_at_21 is not None and abs(last_f - f_at_21) < 1e-9)
        except Exception:
            no_event = False

        if not rows_payload:
            skipped += 1
            continue
        # ⚠️ 生产契约(_write_rows): r 必须 8 元组含 change_pct; code 必须为表内原样形态(裸码)
        def _pct(i: int, c_: float) -> float:
            base = rows_payload[i - 1][4] if i > 0 else prev_close
            return round((c_ / base - 1) * 100, 4) if (base and c_) else 0.0

        write_rows = [(d_, o_, h_, l_, c_, vol_, amt_, _pct(i_, c_))
                      for i_, (d_, o_, h_, l_, c_, vol_, amt_) in enumerate(rows_payload)]

        if no_event:
            mode = "direct_backfill"       # 缺口行为「补齐」语义: 刷除该股该窗口后按真值重灌
            bak_rows.extend(tuple(r) for r in
                            cq.execute(
                                f"SELECT code,date,open,high,low,close FROM {QFQ_TABLE} "
                                f"WHERE code=? AND date BETWEEN ? AND ?",
                                (store_code, min(BS_EXTEND), max(BS_EXTEND))).fetchall())
            cq.execute(f"DELETE FROM {QFQ_TABLE} WHERE code=? AND date BETWEEN ? AND ?",
                       (store_code, min(BS_EXTEND), max(BS_EXTEND)))
            cq.commit()
        else:
            mode = "full_recompute"
            import pandas as pd  # noqa: PLC0415
            tail_idx = pd.DatetimeIndex([datetime.strptime(d_, "%Y%m%d") for d_, *_ in rows_payload])
            tail_df = DataFrame({
                "open": [r[1] for r in rows_payload],
                "high": [r[2] for r in rows_payload],
                "low": [r[3] for r in rows_payload],
                "close": [r[4] for r in rows_payload],
                "volume": [float(r[5]) for r in rows_payload],
                "amount": [float(r[6]) for r in rows_payload],
            }, index=tail_idx)
            cols_use = [c for c in ("open", "high", "low", "close", "volume", "amount")
                        if c in getattr(raw_df, "columns", [])]
            full = raw_df[cols_use]
            full = full.reindex(full.index.union(tail_idx))
            for col in tail_df.columns:
                full.loc[tail_idx, col] = tail_df[col]
            full.index.name = "date"   # compute_qfq.reset_index() 依赖该索引名(union 后易丢)
            qq = compute_qfq(c6, full, factor_df)
            if qq is None or qq.empty:
                failed += 1
                continue
            if hasattr(qq, "columns") and "change_pct" not in qq.columns:
                qq["change_pct"] = (qq["close"].pct_change() * 100).round(4).fillna(0.0)
            write_rows = [(d_.strftime("%Y%m%d"), r["open"], r["high"], r["low"],
                           r["close"], float(r.get("volume", 0) or 0),
                           float(r.get("amount", 0) or 0),
                           float(r.get("change_pct", 0) or 0))
                          for d_, r in qq.iterrows()]

        # direct_backfill 模式已先行备份+删除该股窗口; full_recompute 在此统一备份后覆盖
        if mode != "direct_backfill":
            for r_ in cq.execute(
                    f"SELECT code,date,open,high,low,close FROM {QFQ_TABLE} "
                    f"WHERE code=? AND date BETWEEN ? AND ?",
                    (store_code, min(BS_EXTEND), max(BS_EXTEND))).fetchall():
                bak_rows.append(tuple(r_))

        try:
            _write_rows(cq, store_code, write_rows)   # 契约: 裸码 + 8元组(含change_pct)
        except Exception as e:                        # 单股失败不崩全局(QfqGuard/绑定等)
            failed += 1
            print(f"[FAIL] {store_code}: {type(e).__name__}: {str(e)[:120]}", flush=True)
            continue
        fixed += 1
        if (i + 1) % 20 == 0:
            cq.commit()
            print(f"  [{i+1}/{len(codes)}] fixed={fixed} failed={failed} "
                  f"elapsed={time.time()-t0:.0f}s mode_last={mode}", flush=True)

    cq.commit()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BAK_DIR, exist_ok=True)
    bpath = os.path.join(BAK_DIR, f"qfq_repair_bak_{ts}.db")
    bcon = sqlite3.connect(bpath)
    bcon.execute("CREATE TABLE origin (code TEXT,date TEXT,open REAL,high REAL,"
                 "low REAL,close REAL)")
    bcon.executemany("INSERT INTO origin VALUES (?,?,?,?,?,?)", bak_rows)
    bcon.commit()
    bcon.close()

    # 停牌幽灵行不在本脚本删除: qfq 有而 raw 无的行在扫描阶段已排除出处理集(保守不动),
    # 其真伪须以 baostock 逐日行情为准, 归口 fix_qfq_rebuild/值班会话处置。
    bs.logout()
    print(f"\n[DONE] rebuilt={fixed}(bak {len(bak_rows)}行→{bpath}) "
          f"failed={failed} skip={skipped}")
    cq.close(); cm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
