#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_qfq_meta_tdx.py  —  复权派生表构建 (P0-② 通达信本地版)
==================================================================
腾讯 web.ifzq.gtimg.cn/fqkline 接口已不可用(501), 改用:
  - 日线数据: 通达信本地 vipdoc/{sh,sz}/lday/*.day (不复权)
  - 复权因子: 新浪财经 qfq.js (https://finance.sina.com.cn/realstock/company/{sh/sz}{code}/qfq.js)

口径/约束与串行版保持一致:
  - 写锁解耦: 结果写入 dragon_quant_qfq.db
  - 日期统一 YYYYMMDD, change_pct 百分比口径
  - 脏数据闸门 |chg|>21 置 NULL
  - 腾讯无 qfq 的票回退复制原始 stock_daily_meta 行
  - ADDITIVE 断点续跑 (meta_min 精确判定)
"""
import argparse
import json
import re
import sqlite3
import struct
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"   # 只读源
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"    # 独立写入库
QFQ_TABLE = "stock_daily_meta_qfq"
TDX_DIR = r"D:\anzhuang\tdx"
WINDOW_START = "20160101"

SINA_QFQ_URL = "https://finance.sina.com.cn/realstock/company/{prefix}{code}/qfq.js"
SLEEP_PER_REQ = 0.15          # 新浪 qfq.js 请求间隔(秒)
WORKERS = 8                   # 并发 worker(本地 IO + 新浪请求)
MAX_RETRY = 3
TIMEOUT = 20

_local = threading.local()


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _dash(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


# ----------------------------------------------------------------------------
# 数据库准备
# ----------------------------------------------------------------------------
def ensure_table(qfq_conn: sqlite3.Connection):
    qfq_conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {QFQ_TABLE} (
            code      TEXT NOT NULL,
            date      TEXT NOT NULL,
            open      REAL, high REAL, low REAL, close REAL,
            volume    REAL, amount REAL, change_pct REAL,
            PRIMARY KEY (code, date)
        )
    """)
    qfq_conn.commit()


def get_universe(meta_conn: sqlite3.Connection):
    rows = meta_conn.execute(
        "SELECT DISTINCT code FROM stock_daily_meta "
        "WHERE code GLOB '60*' OR code GLOB '00*' OR code GLOB '30*' "
        "   OR code GLOB '68*' OR code GLOB '92*' "
        "ORDER BY code"
    ).fetchall()
    return [r[0] for r in rows]


def get_coverage(qfq_conn: sqlite3.Connection, meta_conn: sqlite3.Connection, code: str):
    r = qfq_conn.execute(
        f"SELECT MIN(date), MAX(date), COUNT(*) FROM {QFQ_TABLE} WHERE code=?", (code,)
    ).fetchone()
    m = meta_conn.execute(
        "SELECT MIN(date) FROM stock_daily_meta WHERE code=?", (code,)
    ).fetchone()
    def _s(v):
        return str(v).replace("-", "") if v is not None else None
    return (_s(r[0]), _s(r[1]), (r[2] or 0), _s(m[0] if m else None))


def _write_rows(qfq_conn: sqlite3.Connection, code: str, rows):
    sql = f"""INSERT OR REPLACE INTO {QFQ_TABLE}
              (code,date,open,high,low,close,volume,amount,change_pct)
              VALUES (?,?,?,?,?,?,?,?,?)"""
    payload = [(code, *r) for r in rows]
    for attempt in range(1, 6):
        try:
            qfq_conn.executemany(sql, payload)
            qfq_conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 5:
                time.sleep(2 * attempt)
                continue
            raise
    raise sqlite3.OperationalError(f"write failed after 5 retries for {code}")


# ----------------------------------------------------------------------------
# 通达信本地日线读取
# ----------------------------------------------------------------------------
def read_tdx_day(symbol: str):
    """读取通达信本地 .day 日线, 返回 DataFrame[dateindex, open,high,low,close,amount,volume]"""
    market = "sh" if symbol.startswith("6") else "sz"
    fpath = Path(TDX_DIR) / "vipdoc" / market / "lday" / f"{market}{symbol}.day"
    if not fpath.exists():
        return None
    rows = []
    with open(fpath, "rb") as f:
        while True:
            buf = f.read(32)
            if not buf or len(buf) < 32:
                break
            date, o, h, l, c, amount, volume, _ = struct.unpack("<IIIIIfII", buf)
            rows.append({
                "date": date,
                "open": o / 100.0,
                "high": h / 100.0,
                "low": l / 100.0,
                "close": c / 100.0,
                "amount": float(amount),
                "volume": int(volume),
            })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index()
    return df


# ----------------------------------------------------------------------------
# 新浪前复权因子
# ----------------------------------------------------------------------------
def fetch_sina_qfq(symbol: str):
    """从新浪财经 qfq.js 获取前复权因子, 返回 DataFrame[dateindex, factor]"""
    prefix = "sh" if symbol.startswith("6") else "sz"
    url = SINA_QFQ_URL.format(prefix=prefix, code=symbol)
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                text = resp.read().decode("gbk", errors="ignore")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end <= start:
                return None
            obj = json.loads(text[start:end])
            data = obj.get("data", [])
            if not data:
                return None
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["d"])
            df["factor"] = df["f"].astype(float)
            df = df[["date", "factor"]].sort_values("date").set_index("date")
            return df
        except Exception as e:
            last_err = e
            time.sleep(0.5 * attempt)
    log(f"[warn] {symbol} qfq factor failed: {last_err}")
    return None


# ----------------------------------------------------------------------------
# 复权计算
# ----------------------------------------------------------------------------
def compute_qfq(symbol: str, raw_df: pd.DataFrame, factor_df: pd.DataFrame = None):
    """前复权计算; 返回 DataFrame(已过滤 WINDOW_START 之后)"""
    if raw_df is None or raw_df.empty:
        return None
    df = raw_df.reset_index()
    if factor_df is not None and not factor_df.empty:
        df = df.merge(factor_df.reset_index(), on="date", how="left")
        df["factor"] = df["factor"].ffill().fillna(1.0)
    else:
        df["factor"] = 1.0

    for col in ["open", "high", "low", "close"]:
        df[col] = df[col] / df["factor"]

    df = df.set_index("date").sort_index()
    df = df[df.index >= pd.Timestamp(WINDOW_START)]
    if df.empty:
        return None

    df["change_pct"] = df["close"].pct_change() * 100.0
    df.loc[df["change_pct"].abs() > 21, "change_pct"] = None
    return df


# ----------------------------------------------------------------------------
# 回退: 复制原始 stock_daily_meta 行
# ----------------------------------------------------------------------------
def get_meta_conn():
    if not hasattr(_local, "conn"):
        c = sqlite3.connect(META_DB, timeout=30)
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return _local.conn


def _fallback_raw(code: str):
    meta_conn = get_meta_conn()
    rows = meta_conn.execute(
        "SELECT date,open,high,low,close,volume,amount,change_pct "
        "FROM stock_daily_meta WHERE code=?", (code,)
    ).fetchall()
    out = []
    for (d, o, h, l, c, v, amt, chg) in rows:
        if chg is not None and abs(chg) > 21:
            chg = None
        out.append((str(d).replace("-", ""), o, h, l, c, v, amt, chg))
    return out


# ----------------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------------
def worker(task):
    code, sd, ed = task
    try:
        raw_df = read_tdx_day(code)
        if raw_df is None or raw_df.empty:
            rows = _fallback_raw(code)
            return (code, "fallback", rows) if rows else (code, "failed", None)

        factor_df = fetch_sina_qfq(code)
        qfq_df = compute_qfq(code, raw_df, factor_df)
        if qfq_df is None or qfq_df.empty:
            rows = _fallback_raw(code)
            return (code, "fallback", rows) if rows else (code, "failed", None)

        # 过滤到任务区间 [sd, ed]
        qfq_df = qfq_df[(qfq_df.index >= pd.Timestamp(sd)) & (qfq_df.index <= pd.Timestamp(ed))]
        if qfq_df.empty:
            rows = _fallback_raw(code)
            return (code, "fallback", rows) if rows else (code, "failed", None)

        rows = []
        for d, row in qfq_df.iterrows():
            rows.append((
                d.strftime("%Y%m%d"),
                row["open"], row["high"], row["low"], row["close"],
                row["volume"], row["amount"], row["change_pct"]
            ))
        return (code, "ok", rows)
    except Exception as e:
        return (code, "error", f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=int, default=0, help="从 universe 的第 N 只开始处理")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只 (烟测)")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不发请求")
    ap.add_argument("--workers", type=int, default=WORKERS, help="并发 worker 数")
    args = ap.parse_args()
    nw = args.workers

    meta_conn = sqlite3.connect(META_DB, timeout=30)
    meta_conn.execute("PRAGMA busy_timeout=30000")

    qfq_conn = sqlite3.connect(QFQ_DB, timeout=60)
    qfq_conn.execute("PRAGMA journal_mode=WAL")
    qfq_conn.execute("PRAGMA busy_timeout=60000")
    qfq_conn.execute("PRAGMA synchronous=NORMAL")
    ensure_table(qfq_conn)
    log(f"qfq 写入库: {QFQ_DB}")
    log(f"并发 worker 数 = {nw}")

    universe = get_universe(meta_conn)
    log(f"universe size = {len(universe)}")

    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    start_thr = (datetime.strptime(WINDOW_START, "%Y%m%d") + timedelta(days=60)).strftime("%Y%m%d")

    tasks = []
    skipped = 0
    if args.offset:
        universe = universe[args.offset:]
    if args.limit:
        universe = universe[: args.limit]

    for code in universe:
        qmin, qmax, qcnt, mmin = get_coverage(qfq_conn, meta_conn, code)
        reached_start = (qmin is not None and qmin <= start_thr)
        near_ipo = (qmin is not None and mmin is not None and
                    qmin >= (datetime.strptime(mmin, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d"))
        if qmax and qmax >= today and (reached_start or near_ipo):
            skipped += 1
            continue
        if qmax:
            sd = (datetime.strptime(qmax, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        else:
            sd = WINDOW_START
        tasks.append((code, sd, today))

    log(f"待处理任务 = {len(tasks)} (skip={skipped})")
    meta_conn.close()

    if args.dry_run:
        for code, sd, ed in tasks[:20]:
            log(f"[dry] {code}: {sd} -> {ed}")
        log(f"[dry] 共 {len(tasks)} 只待处理")
        qfq_conn.close()
        return

    done = 0
    fallback_cnt = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=nw) as ex:
        futures = {ex.submit(worker, t): t for t in tasks}
        for fut in as_completed(futures):
            code, status, payload = fut.result()
            try:
                if status == "failed":
                    failed += 1
                elif status == "fallback":
                    _write_rows(qfq_conn, code, payload)
                    fallback_cnt += 1
                    done += 1
                elif status == "ok":
                    _write_rows(qfq_conn, code, payload)
                    done += 1
                else:
                    failed += 1
                    log(f"[error] {code}: {payload}")
            except Exception as e:
                failed += 1
                log(f"[write-error] {code}: {e}")
            if (done + failed) % 50 == 0:
                log(f"progress: done={done} skipped={skipped} fallback={fallback_cnt} failed={failed} last={code}")

    qfq_conn.close()
    log(f"FINAL done={done} skipped={skipped} fallback={fallback_cnt} failed={failed}")


if __name__ == "__main__":
    main()
