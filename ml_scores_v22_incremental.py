# -*- coding: utf-8 -*-
"""stock_ml_scores_v22 incremental scorer (2026-09-05, P0 fix for M-3 revival gap)

Background: v22 scoring pipeline was archived on 2026-08-18 audit cleanup; table stale since
2026-08-14 while production_combo_monthly reads MAX(date) silently (10/1 rebalance, ml weight 40).
This script restores scoring as an INCREMENTAL, idempotent job - same model, same features,
same SQL as the archived original (precompute_ml_scores_v22.py @ snapshot 2026-07-03-21-47-27)
to keep scoring caliber identical to all historical v22 numbers.

Caliber note (honest): features join stock_factors_fundamental (non-PIT) - this is the round11
"leaky-feature" caliber, kept intentionally for production consistency with M-3 revival.
PIT-izing features = caliber change, requires owner ruling + wfa_gate (NOT done here).

Usage:
  python ml_scores_v22_incremental.py                       # fill (table MAX, feature MAX]
  python ml_scores_v22_incremental.py --start 20260815 --end 20260904
Exit: 0 ok / 2 fail-closed (model or features missing)
"""
import argparse
import gc
import pickle
import sqlite3
import sys
import time

MODEL_PATH = r"D:\zcodesj\models\exp_tulong_v22_model.pkl"
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=0, help="YYYYMMDD inclusive; default = table MAX+1")
    ap.add_argument("--end", type=int, default=0, help="YYYYMMDD inclusive; default = feature MAX")
    args = ap.parse_args()

    t0 = time.time()
    import pandas as pd
    import numpy as np
    try:
        with open(MODEL_PATH, "rb") as f:
            m = pickle.load(f)
    except FileNotFoundError:
        print(f"[FAIL] model not found: {MODEL_PATH} (fail-closed)")
        return 2
    model, fc = m["model"], m["feature_cols"]
    print(f"model loaded: {len(fc)} features")

    db = sqlite3.connect(DB)
    d_max_table = db.execute("SELECT MAX(date) FROM stock_ml_scores_v22").fetchone()[0]
    d_max_feat = db.execute("SELECT MAX(date) FROM feature_daily_v2").fetchone()[0]
    start = args.start or (int(str(d_max_table)[:4]) * 10000 + int(str(d_max_table)[4:6]) * 100
                           + int(str(d_max_table)[6:]) + 1)  # calendar +1; holes covered by range scan below
    end = args.end or d_max_feat
    if start > end:
        print(f"[OK] already fresh: table MAX={d_max_table}, feature MAX={d_max_feat}, nothing to score")
        db.close()
        return 0
    print(f"scoring window: {start} ~ {end} (table MAX={d_max_table}, feature MAX={d_max_feat})")

    fv2_cols = [c for c in fc if c not in (
        "ep", "bp", "sp", "roe", "lev", "ato", "pm",
        "smart_money", "momentum", "ma_bull", "boll_upper", "boll_mid", "boll_lower",
        "gross_margin", "net_margin", "revenue_growth", "profit_growth",
        "debt_ratio", "current_ratio", "basic_eps", "bps")]
    fv2_sel = ", ".join([f"f.{c}" for c in fv2_cols])
    sql = f"""SELECT f.code, f.date, {fv2_sel},
        fu.ep, fu.bp, fu.sp, fu.roe, fu.lev, fu.ato, fu.pm,
        sf.smart_money, sf.momentum, sf.ma_bull, sf.boll_upper, sf.boll_mid, sf.boll_lower,
        fi.gross_margin, fi.net_margin, fi.revenue_growth, fi.profit_growth,
        fi.debt_ratio, fi.current_ratio, fi.basic_eps, fi.bps
        FROM feature_daily_v2 f
        LEFT JOIN stock_factors_fundamental fu ON f.code=fu.code AND f.date=fu.date
        LEFT JOIN stock_factors sf ON f.code=sf.code AND f.date=sf.date
        LEFT JOIN stock_financial fi ON f.code=fi.code AND fu.report_date=fi.report_date
        WHERE f.date>={start} AND f.date<={end}"""

    try:
        df = pd_read(db, sql)
    except Exception as e:
        print(f"[FAIL] feature query error: {type(e).__name__}: {e} (fail-closed)")
        db.close()
        return 2
    if df is None or len(df) == 0:
        print(f"[WARN] no feature rows in window {start}~{end} - check daily chain freshness")
        db.close()
        return 2

    for c in fc:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)
        else:
            df[c] = 0
    X = df[fc].values.astype(np.float64)
    probs = model.predict(X, num_iteration=model.best_iteration)

    # idempotent upsert: clear window then insert (PK code+date dedupes too)
    db.execute("DELETE FROM stock_ml_scores_v22 WHERE date>=? AND date<=?", (start, end))
    rows = [(str(df.iloc[j].code), int(df.iloc[j].date), float(probs[j])) for j in range(len(df))]
    db.executemany("INSERT OR REPLACE INTO stock_ml_scores_v22 VALUES(?,?,?)", rows)
    db.commit()

    new_max = db.execute("SELECT MAX(date) FROM stock_ml_scores_v22").fetchone()[0]
    n_days = db.execute("SELECT COUNT(DISTINCT date) FROM stock_ml_scores_v22 WHERE date>=? AND date<=?",
                        (start, end)).fetchone()[0]
    db.close()
    print(f"[OK] upserted {len(rows):,} rows / {n_days} days in {time.time()-t0:.0f}s; table MAX now={new_max}")
    del df, X, probs, rows
    gc.collect()
    return 0


def pd_read(db, sql):
    import pandas as pd
    return pd.read_sql(sql, db)


if __name__ == "__main__":
    sys.exit(main())
