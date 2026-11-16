# -*- coding: utf-8 -*-
"""强档新高龙头(观察层)影子记账: 强档(涨停家数>=61) ∧ 当日涨停 ∧ 收盘创60日新高
对应 PD-033 / PREREG_STRONG_NEWHIGH_LEADER_20261006。判据预钉死(60交易日 f5med>=+1% win>=50% n>=30)。
用法:
  python analysis/strong_newhigh_leader_daily.py                # 扫描库内最新交易日
  python analysis/strong_newhigh_leader_daily.py --date=20260930
  python analysis/strong_newhigh_leader_daily.py --backfill 20190101 20260930
幂等: (code,date)主键; 纯观察零交易权, 不接闸门不接cron。与deepfirst_shadow_daily.py同构。
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = Path(r"D:\zcodesj\data\strong_newhigh_leader_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\strong_newhigh_leader_latest.json")
PREREG_DATE = 20261006
HIST_START = "20180901"          # 2019Q1证据窗前留60bar预热
ZT_PCT = 9.5
STRONG_ZT_CNT = 61               # PD-025强档阈值族
GATES = {"f5_med_min": 1.0, "f5_win_min": 50.0, "n_min": 30}


def load_frame() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    df = pd.read_sql(
        "SELECT code,date,close,volume,change_pct FROM stock_daily_meta_qfq "
        f"WHERE date>='{HIST_START}' "
        "AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')",
        conn)
    conn.close()
    df["date"] = df["date"].astype(int)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("code", sort=False)
    df["prev_max60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=60).max().shift(1))
    df["bar_n"] = g.cumcount()
    df["is_zt"] = df["change_pct"] >= ZT_PCT
    for k in (5, 10, 20):
        df[f"f{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k)) / df["close"] - 1
    zt_daily = df.groupby("date")["change_pct"].apply(lambda s: (s >= ZT_PCT).sum())
    df["zt_cnt"] = df["date"].map(zt_daily)
    ev = df[(df["bar_n"] >= 60)
            & df["is_zt"]
            & (df["close"] > df["prev_max60"])
            & (df["zt_cnt"] >= STRONG_ZT_CNT)].copy()
    return ev


def load_ledger() -> dict:
    rows = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[(r["code"], r["date"])] = r
    return rows


def gate_status(rows: dict) -> dict:
    win_rows = [r for r in rows.values() if not r["in_sample"] and r["f5"] is not None]
    out = {"n": len(win_rows), "gates": GATES}
    if win_rows:
        f5 = np.array([r["f5"] for r in win_rows])
        out["f5_med"] = round(float(np.median(f5)), 2)
        out["f5_win"] = round(float((f5 > 0).mean() * 100), 1)
        out["verdict"] = ("PASS" if (out["f5_med"] >= GATES["f5_med_min"]
                                     and out["f5_win"] >= GATES["f5_win_min"]
                                     and len(win_rows) >= GATES["n_min"]) else "PENDING")
    else:
        out["verdict"] = "NO_DATA"
    return out


def main():
    t0 = time.time()
    scan_from = scan_to = None
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            scan_from = scan_to = int(a.split("=", 1)[1])
        elif a == "--backfill":
            args = [x for x in sys.argv[1:] if not x.startswith("--")]
            scan_from, scan_to = int(args[0]), int(args[1])
    df = load_frame()
    latest = int(df["date"].max())
    if scan_from is None:
        scan_from = scan_to = latest
    ev = compute_signals(df)
    ev = ev[(ev["date"] >= scan_from) & (ev["date"] <= scan_to)]
    if len(ev) > 100000:
        raise SystemExit(f"GUARD: 事件量异常({len(ev)}), 信号过滤疑似失效, 拒绝落盘")
    rows = load_ledger()
    added = updated = 0
    for _, e in ev.iterrows():
        key = (e["code"], int(e["date"]))
        rec = {
            "code": e["code"],
            "date": int(e["date"]),
            "close": round(float(e["close"]), 2),
            "zt_cnt": None if pd.isna(e["zt_cnt"]) else int(e["zt_cnt"]),
            "f5": None if pd.isna(e["f5"]) else round(float(e["f5"]) * 100, 2),
            "f10": None if pd.isna(e["f10"]) else round(float(e["f10"]) * 100, 2),
            "f20": None if pd.isna(e["f20"]) else round(float(e["f20"]) * 100, 2),
            "in_sample": int(e["date"]) < PREREG_DATE,
            "ts_added": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if key in rows:
            old = rows[key]
            if old["f5"] is None and rec["f5"] is not None:
                for k in ("f5", "f10", "f20"):
                    old[k] = rec[k]
                old["fwd_filled"] = time.strftime("%Y-%m-%d")
                updated += 1
        else:
            rows[key] = rec
            added += 1
    ordered = sorted(rows.values(), key=lambda r: (r["date"], r["code"]))
    LEDGER.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ordered) + "\n", encoding="utf-8")
    snap = {
        "scanned": {"from": scan_from, "to": scan_to, "latest_data_date": latest},
        "added": added, "fwd_updated": updated, "total_rows": len(rows),
        "shadow_window": gate_status(rows),
        "runtime_sec": round(time.time() - t0, 1),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(snap, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
