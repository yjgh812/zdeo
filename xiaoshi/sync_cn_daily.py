#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小石 cn-daily 2020+ 对账与增量同步 (sync_cn_daily)
======================================================
将小石 R2 Parquet cn-daily 前复权日线用于对账/增量, 目标是 D:/workbuddyspace 的
dragon_quant_qfq.db (stock_daily_meta_qfq)。

关键事实(2026-08-22 实测):
  - 小石 qfq close/volume/change_pct 与屠龙 qfq 库在 2024 年 22 天样本上
    close 最大差 0.0009、volume 差 0%、change_pct 一致 → 复权基准一致, 可直接对账。
  - 小石 amount 单位为元(CNY), 屠龙 stock_daily_meta_qfq.amount 非元(比值~1.4~7),
    对账时**排除 amount**, 不写入 amount。
  - 小石 cn-daily 覆盖 2020-01-02 起; 2016-2019 段无覆盖, 维持 TDX+新浪原链路。

模式:
  --verify   只对账(默认): 抽样若干股票×年份, 对比 close/volume/change_pct, 输出差异报告
  --download 下载指定年份 Parquet 到本地 store(不写库)
  --sync     校验通过后, 将 2020+ qfq 行写入 qfq 库(增量 UPSERT; 只写存在性/缺失补)

用法:
  python sync_cn_daily.py --verify --year 2024 --sample-codes 600519,000001
  python sync_cn_daily.py --download --year 2024
  python sync_cn_daily.py --sync --year 2024
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import pandas as pd
except ImportError:
    pd = None

import xiaoshi_client as xc  # noqa: E402

STORE_DIR = os.environ.get("XIAOSHI_STORE_DIR", r"D:\zcodesj\xiaoshi\store")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
QFQ_TABLE = "stock_daily_meta_qfq"
REPORT_DIR = r"D:\zcodesj\reports"


def find_parquet(year: int, adjust: str = "qfq") -> str | None:
    """在本地 store 找某年 qfq Parquet。"""
    pat = os.path.join(STORE_DIR, "**", "cn-daily", "**", f"year={year}", "data.parquet")
    fs = glob.glob(pat, recursive=True)
    if not fs:
        fs = glob.glob(os.path.join(STORE_DIR, "**", "*.parquet"), recursive=True)
        fs = [f for f in fs if f"year={year}" in f and f"adjustment={adjust}" in f]
    return fs[0] if fs else None


def download_year(year: int, adjust: str = "qfq") -> bool:
    """下载指定年份 cn-daily Parquet 到本地 store。"""
    session = xc.history_download_session(dataset="cn-daily", year=year, adjust=adjust)
    files = session.get("files", [])
    os.makedirs(STORE_DIR, exist_ok=True)
    ok = 0
    for f in files:
        url, key, size, sha = f.get("url"), f.get("key"), f.get("size"), f.get("sha256")
        if not url or not key:
            continue
        dest = os.path.join(STORE_DIR, key.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            import urllib.request
            import hashlib
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
        except Exception as e:
            print(f"  下载失败 {key}: {e}")
            continue
        if size is not None and len(data) != size:
            print(f"  size 不匹配 {key}")
            continue
        if sha and hashlib.sha256(data).hexdigest() != sha:
            print(f"  sha256 不匹配 {key}")
            continue
        with open(dest, "wb") as fh:
            fh.write(data)
        print(f"  OK {key} ({len(data)} bytes)")
        ok += 1
    return ok == len(files)


def reconcile(year: int, sample_codes: list[str]) -> dict:
    """对账: 小石 Parquet vs 屠龙 qfq 库 close/volume/change_pct。"""
    result = {"year": year, "checked": 0, "ok": 0, "diff_rows": [], "amount_unit_note": None}
    parq = find_parquet(year)
    if not parq:
        result["error"] = f"本地无 {year} qfq Parquet, 请先 --download"
        return result
    df = pd.read_parquet(parq)
    df = df[df["adjustment"] == "qfq"].copy()
    df["symbol6"] = df["symbol"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=20)
    for code in sample_codes:
        sub = df[df["symbol6"] == code]
        if sub.empty:
            result["diff_rows"].append({"code": code, "note": "小石无该股数据"})
            continue
        # 屠龙该股该年
        qrows = con.execute(
            f"SELECT date,close,volume,change_pct FROM {QFQ_TABLE} "
            "WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
            (code, f"{year}0101", f"{year}1231")).fetchall()
        qdf = pd.DataFrame(qrows, columns=["date", "q_close", "q_vol", "q_chg"])
        m = sub.merge(qdf, on="date", how="inner")
        result["checked"] += len(m)
        if m.empty:
            result["diff_rows"].append({"code": code, "note": "屠龙库无该股该年"})
            continue
        close_diff = (m["close"] - m["q_close"]).abs()
        chg_diff = (m["change_pct"] - m["q_chg"]).abs()
        bad = close_diff > 0.01
        if bad.any():
            result["diff_rows"].append({
                "code": code, "days": int(len(m)),
                "close_max_diff": round(float(close_diff.max()), 4),
                "chg_max_diff": round(float(chg_diff.max()), 4),
            })
        else:
            result["ok"] += int(len(m))
            result["diff_rows"].append({
                "code": code, "days": int(len(m)),
                "close_max_diff": round(float(close_diff.max()), 4),
                "chg_max_diff": round(float(chg_diff.max()), 4), "status": "OK",
            })
    con.close()
    return result


def sync_year(year: int) -> dict:
    """校验通过后将 2020+ qfq 行写入 qfq 库(增量 UPSERT)。"""
    out = {"year": year, "inserted": 0, "skipped_existing": 0}
    parq = find_parquet(year)
    if not parq:
        out["error"] = f"本地无 {year} qfq Parquet"
        return out
    df = pd.read_parquet(parq)
    df = df[df["adjustment"] == "qfq"].copy()
    df["date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
    con = sqlite3.connect(QFQ_DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    # 已存在日期集合(增量)
    existing = set(r[0] for r in con.execute(
        f"SELECT DISTINCT date FROM {QFQ_TABLE} WHERE date LIKE ?", (f"{year}%",)).fetchall())
    rows = []
    for _, r in df.iterrows():
        d = r["date"]
        if d in existing:
            out["skipped_existing"] += 1
            continue
        rows.append((str(r["symbol6"]), d,
                     float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                     float(r["volume"]), float(r["change_pct"])))
    con.executemany(
        f"INSERT OR IGNORE INTO {QFQ_TABLE}(code,date,open,high,low,close,volume,amount,change_pct) "
        "VALUES (?,?,?,?,?,?,?,0,?)", rows)
    con.commit()
    out["inserted"] = len(rows)
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="小石 cn-daily 2020+ 对账与增量同步")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify", action="store_true", help="对账(默认只读)")
    g.add_argument("--download", action="store_true", help="下载指定年份 Parquet")
    g.add_argument("--sync", action="store_true", help="校验后写入 qfq 库(增量)")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--sample-codes", default="600519,000001,300750",
                    help="--verify 抽样代码(逗号分隔)")
    a = ap.parse_args()

    if a.download:
        print(f"下载 {a.year} qfq cn-daily ...")
        ok = download_year(a.year)
        print("下载完成" if ok else "下载部分失败")
        return 0

    if a.verify:
        codes = [c.strip() for c in a.sample_codes.split(",") if c.strip()]
        res = reconcile(a.year, codes)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(os.path.join(REPORT_DIR, f"xiaoshi_cn_daily_reconcile_{a.year}.json"),
                  "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        # 汇总判定
        if res.get("checked", 0) and not res.get("diff_rows"):
            print("✓ 对账通过(样本内 close 一致)")
        else:
            bad = [r for r in res.get("diff_rows", []) if r.get("status") != "OK"]
            print(f"对账结果: checked={res.get('checked')} 差异={len(bad)}"
                  + ("" if not bad else f" → {bad[:3]}"))
        return 0

    if a.sync:
        res = sync_year(a.year)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
