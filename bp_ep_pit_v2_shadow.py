#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bp_ep_pit_v2_shadow.py -- V2 cross-section list generator for W2 shadow track
================================================================================
Generates 20260814 cross-section list from v2 PIT table into pit_shadow_track.db.

Strategy: bp_ep_pit_v2 -- production-identical bp+ep selection, v2 snapshot caliber.

Usage: python bp_ep_pit_v2_shadow.py
"""
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from build_factor_pit_20260815 import get_pit_factor_v2, TBL_PIT_V2

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
SHADOW_DB = r"D:\zcodesj\pit_shadow_track.db"

MAX_PRICE = 30.0
TOPN = 15  # S-3落地2026-10-18(主人裁决A): TopN 20→15
STALE_DAYS = 400
BOARD_ALLOWED = ("60", "00")


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def main():
    t0 = time.time()
    log("=" * 60)
    log("V2 Cross-Section List Generator (20260814)")
    log("=" * 60)

    main_conn = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True, timeout=60)
    pit_conn = sqlite3.connect("file:%s?mode=ro" % PIT_DB, uri=True, timeout=60)

    date = 20260814

    # Load stock info
    info = pd.read_sql_query("SELECT code, name, industry FROM stock_info", main_conn)
    info["code"] = info["code"].astype(str).str.zfill(6)
    name_map = dict(zip(info["code"], info["name"]))
    ind_map = dict(zip(info["code"], info["industry"]))
    log("stock_info: %d stocks" % len(info))

    # Load v2 snapshot
    log("Loading v2 pit snapshot @ %d..." % date)
    snap = get_pit_factor_v2(pit_conn, date, max_stale_days=STALE_DAYS)
    snap["code"] = snap["code"].astype(str).str.zfill(6)
    log("  v2 pit snap: %d stocks" % len(snap))

    # Load closes
    closes = pd.read_sql_query(
        "SELECT code, close FROM stock_daily_hsj WHERE date=?",
        main_conn, params=[date])
    closes["code"] = closes["code"].astype(str).str.zfill(6)

    # Merge
    df = snap.merge(closes, on="code", how="inner")
    log("  merged: %d" % len(df))

    # Board filter
    df = df[df["code"].isin(set(
        info["code"][info["code"].str[:2].isin(BOARD_ALLOWED)
                     & ~info["code"].str.startswith(("300","301","688","689","8","920","43"))]
    ))]
    log("  after board filter: %d" % len(df))

    # Compute bp = bps / close_T  [2026-08-22修复: 每股净资产/价 = 账面市值比, 原 close/bps 反向]
    with np.errstate(divide="ignore", invalid="ignore"):
        bp = np.where((df["bps"] > 0) & (df["close"] > 0),
                     df["bps"] / df["close"], np.nan)

    # Rank ep and bp independently
    ep_valid = df.dropna(subset=["eps"]).copy()
    bp_valid = df.dropna(subset=["bp"])
    bp_valid = bp_valid[bp_valid["close"] > 0]
    # Recompute bp on the valid subset
    bp_valid["bp_v2"] = np.where(
        (bp_valid["bps"] > 0) & (bp_valid["close"] > 0),
        bp_valid["bps"] / bp_valid["close"], np.nan)
    bp_valid = bp_valid.dropna(subset=["bp_v2"])

    ep_ranked = ep_valid[["code", "eps", "report_date", "pit_avail_date", "snap_rule"]].copy()
    ep_ranked["rank_ep"] = ep_valid["eps"].rank(pct=True)

    bp_ranked = bp_valid[["code", "bp_v2", "close"]].copy()
    bp_ranked["rank_bp"] = bp_valid["bp_v2"].rank(pct=True)

    merged = ep_ranked.merge(bp_ranked, on="code", how="inner")
    merged["composite"] = (merged["rank_ep"] + merged["rank_bp"]) / 2

    # Price <= 30
    merged = merged[merged["close"] <= MAX_PRICE]

    # Sort and apply industry cap
    merged = merged.sort_values("composite", ascending=False)

    cand = merged.head(TOPN * 3)
    picks, used_inds, used_rows = [], {}, []
    for _, row in cand.iterrows():
        if len(picks) >= TOPN:
            break
        code = row["code"]
        ind = ind_map.get(code, "UNKNOWN")
        if used_inds.get(ind, 0) >= 4:
            continue
        used_inds[ind] = used_inds.get(ind, 0) + 1
        picks.append(code)
        used_rows.append(row)

    log("Selected %d stocks" % len(picks))

    # Register in shadow_track
    conn = sqlite3.connect(SHADOW_DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS shadow_lists (
        date         INTEGER NOT NULL,
        strategy     TEXT    NOT NULL,
        code         TEXT    NOT NULL,
        name         TEXT    NOT NULL,
        rank         INTEGER NOT NULL,
        factor_value REAL,
        source       TEXT    NOT NULL,
        PRIMARY KEY (date, strategy, code)
    );
    """)

    conn.execute("DELETE FROM shadow_lists WHERE date=? AND strategy=?",
                 (date, "bp_ep_pit_v2"))

    n = 0
    for i, row in enumerate(used_rows):
        code = row["code"]
        name = name_map.get(code, "")
        if not name:
            log("  WARNING: missing name for %s" % code)
            continue
        conn.execute(
            "INSERT INTO shadow_lists "
            "(date, strategy, code, name, rank, factor_value, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (date, "bp_ep_pit_v2", code, name, i + 1,
             round(float(row["composite"]), 6), "factor_pit_v2"))
        n += 1

    conn.commit()

    # Also register production and v1 for comparison
    log("Registering production comparison track...")
    # Load production as-of
    prod = pd.read_sql_query(
        "SELECT code, ep, bp, close FROM stock_factors_fundamental WHERE date=?",
        main_conn, params=[date])
    prod["code"] = prod["code"].astype(str).str.zfill(6)
    prod = prod[prod["code"].isin(set(
        info["code"][info["code"].str[:2].isin(BOARD_ALLOWED)
                     & ~info["code"].str.startswith(("300","301","688","689","8","920","43"))]
    ))]
    prod = prod.dropna(subset=["ep", "bp"])
    prod = prod[(prod["ep"] > 0) & (prod["bp"] > 0) & (prod["close"] <= MAX_PRICE)]
    prod["rank_ep"] = prod["ep"].rank(pct=True)
    prod["rank_bp"] = prod["bp"].rank(pct=True)
    prod["composite"] = (prod["rank_ep"] + prod["rank_bp"]) / 2
    prod = prod.sort_values("composite", ascending=False)

    conn.execute("DELETE FROM shadow_lists WHERE date=? AND strategy=?",
                 (date, "bp_ep_prod_asof"))

    n_prod = 0
    cand_prod = prod.head(TOPN * 3)
    picks_prod, used_inds_p = [], {}
    for _, row in cand_prod.iterrows():
        if len(picks_prod) >= TOPN:
            break
        code = row["code"]
        ind = ind_map.get(code, "UNKNOWN")
        if used_inds_p.get(ind, 0) >= 4:
            continue
        used_inds_p[ind] = used_inds_p.get(ind, 0) + 1
        picks_prod.append(code)
        name = name_map.get(code, "")
        conn.execute(
            "INSERT INTO shadow_lists "
            "(date, strategy, code, name, rank, factor_value, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (date, "bp_ep_prod_asof", code, name, len(picks_prod),
             round(float(row["composite"]), 6), "stock_factors_fundamental_asof"))
        n_prod += 1

    conn.commit()
    log("Registered %d production as-of stocks" % n_prod)

    total = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT strategy) FROM shadow_lists "
        "WHERE date=?", (date,)).fetchone()
    conn.close()
    log("Total records for date %d: %d rows / %d strategies" % (date, total[0], total[1]))

    main_conn.close()
    pit_conn.close()
    log("Done, %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    sys.exit(main())