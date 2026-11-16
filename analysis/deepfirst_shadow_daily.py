# -*- coding: utf-8 -*-
"""深位首板信号前向影子记账: pos60[T-1]<=25 + 真首板涨停 + 放量>=1.5x
对应 PD-031 / PREREG_DEEPFIRST_SHADOW_20261004, 判据预钉死(60交易日 f20med>=+2% win>=55% n>=30)。
计算式与回测 analysis/lowpos_firstboard_20261004.py 逐行同构(同库同参数), 禁止改参数。
用法:
  python analysis/deepfirst_shadow_daily.py                # 扫描库内最新交易日
  python analysis/deepfirst_shadow_daily.py --date=20261009
  python analysis/deepfirst_shadow_daily.py --backfill 20260101 20260930
幂等: (code,date) 主键, 重复运行只补 fwd 字段。纯影子零交易权, 不接闸门不接cron。
"""
import json
import sqlite3
import sys
import time
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import get_name

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
LEDGER = Path(r"D:\zcodesj\data\deepfirst_shadow_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\deepfirst_shadow_latest.json")
PREREG_DATE = 20261004          # 此日前=backfill参考层(in_sample=true)
HIST_START = "20220401"         # 与回测同窗, 保证rolling预热一致
POS_MAX = 25.0                  # 深位阈值(回测定谳, 禁改)
VOLR_MIN = 1.5
ZT_PCT = 9.5
GATES = {"f20_med_min": 2.0, "f20_win_min": 55.0, "n_min": 30}


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
    """与回测逐行同构的信号计算, 返回全部候选事件(含fwd)。"""
    g = df.groupby("code", sort=False)
    df["low60p"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).min().shift(1))
    df["high60p"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).max().shift(1))
    df["pos60p"] = (df["close"] - df["low60p"]) / (df["high60p"] - df["low60p"]) * 100
    df["vol5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean().shift(1))
    df["volr"] = df["volume"] / df["vol5"]
    df["bar_n"] = g.cumcount()
    df = df[df["bar_n"] >= 60].reset_index(drop=True)
    g = df.groupby("code", sort=False)
    df["is_zt"] = df["change_pct"] >= ZT_PCT
    df["prev_zt"] = g["is_zt"].transform(lambda s: s.shift(1).fillna(False).astype(bool))
    df["first_board"] = df["is_zt"] & (~df["prev_zt"])
    df["gene10"] = g["change_pct"].transform(lambda s: (s >= ZT_PCT).rolling(10).max().shift(1))
    df["clean_first"] = df["first_board"] & (df["gene10"] != 1)
    for k in (5, 10, 20):
        df[f"f{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k)) / df["close"] - 1
    # 信号日全市场涨停家数(qfq推算,与sentiment_cycle_daily既有source同族口径;
    # PREREG amendment_20261006 预登记的次级观察窗分档字段,不参与信号定义)
    zt_daily = df.groupby("date")["change_pct"].apply(lambda s: (s >= 9.5).sum())
    df["zt_cnt"] = df["date"].map(zt_daily)
    ev = df[df["clean_first"] & (df["volr"] >= VOLR_MIN) & (df["pos60p"] <= POS_MAX)].copy()
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
    win_rows = [r for r in rows.values() if not r["in_sample"] and r["f20"] is not None]
    out = {"n": len(win_rows), "gates": GATES}
    if win_rows:
        f20 = np.array([r["f20"] for r in win_rows])
        out["f20_med"] = round(float(np.median(f20)), 2)
        out["f20_win"] = round(float((f20 > 0).mean() * 100), 1)
        out["verdict"] = ("PASS" if (out["f20_med"] >= GATES["f20_med_min"]
                                     and out["f20_win"] >= GATES["f20_win_min"]
                                     and len(win_rows) >= GATES["n_min"]) else "PENDING")
    else:
        out["verdict"] = "NO_DATA"
    # PREREG amendment_20261006 预登记次级观察窗(仅读数参考,非通过/否决条件)
    for thr in (40, 61):
        sub = [r["f20"] for r in win_rows if (r.get("zt_cnt") or 0) >= thr]
        out[f"secondary_zt{thr}"] = (
            {"n": len(sub), "med": round(float(np.median(sub)), 2), "win": round(float((np.array(sub) > 0).mean() * 100), 1)}
            if len(sub) >= 5 else "n<5")
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
    rows = load_ledger()
    added = updated = 0
    for _, e in ev.iterrows():
        key = (e["code"], int(e["date"]))
        rec = {
            "code": e["code"], "name": get_name(e["code"]) or "N/A",
            "date": int(e["date"]), "close": round(float(e["close"]), 2),
            "pos60p": round(float(e["pos60p"]), 1), "volr": round(float(e["volr"]), 2),
            "zt_cnt": None if pd.isna(e["zt_cnt"]) else int(e["zt_cnt"]),
            "f5": None if pd.isna(e["f5"]) else round(float(e["f5"]) * 100, 2),
            "f10": None if pd.isna(e["f10"]) else round(float(e["f10"]) * 100, 2),
            "f20": None if pd.isna(e["f20"]) else round(float(e["f20"]) * 100, 2),
            "in_sample": int(e["date"]) < PREREG_DATE,
            "ts_added": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if key in rows:
            old = rows[key]
            if old["f20"] is None and rec["f20"] is not None:
                for k in ("f5", "f10", "f20"):
                    old[k] = rec[k]
                old["fwd_filled"] = time.strftime("%Y-%m-%d")
                updated += 1
            if "zt_cnt" not in old:
                old["zt_cnt"] = rec["zt_cnt"]
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
