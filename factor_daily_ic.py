#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, sqlite3, time
import numpy as np, pandas as pd
from scipy.stats import spearmanr
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="An input array is constant")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
FUND_FACTORS = ["ep", "bp", "bp_ep", "neg_turnover"]  # volume_ratio removed: all zeros in daily_basic
PIT_FACTORS = ["droe", "roa"]
ALL_FACTORS = FUND_FACTORS + PIT_FACTORS
FORWARD_HORIZONS = [1, 5, 20]
MIN_CROSS_SECTION = 50

def log(msg):
    print(f"[{time.strftime(chr(37)+chr(72)+chr(58)+chr(37)+chr(77)+chr(58)+chr(37)+chr(83))}] {msg}", flush=True)

def load_qfq_prices(start_date="20200101"):
    log("Loading qfq prices...")
    conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
    df = pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= ? AND close > 0",
        conn, params=[start_date])
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = df["date"].astype(str)
    prices = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    log(f"  qfq: {prices.shape[0]} days x {prices.shape[1]} stocks")
    return prices

def load_fund_factors(start_date="20200101"):
    log("Loading daily fundamental factors...")
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=120)
    df = pd.read_sql_query(
        "SELECT ts_code, trade_date, close, pe_ttm, pb, dv_ratio, turnover_rate, volume_ratio "
        "FROM daily_basic WHERE trade_date >= ? AND close IS NOT NULL",
        conn, params=[start_date])
    conn.close()
    for col in ["close", "pe_ttm", "pb", "dv_ratio", "turnover_rate", "volume_ratio"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["code"] = df["ts_code"].astype(str).str[:6]
    df["date"] = df["trade_date"].astype(str)
    df["ep"] = 1.0 / df["pe_ttm"].where(df["pe_ttm"] > 0)
    df["bp"] = 1.0 / df["pb"].where(df["pb"] > 0)
    df["bp_ep"] = (df["ep"] + df["bp"]) / 2.0
    df["neg_turnover"] = -df["turnover_rate"]
    log(f"  fund: {df["date"].nunique()} days")
    return df

def load_pit_factors():
    log("Loading PIT factors...")
    conn = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=120)
    # (2026-08-25 主人裁决3A) v1(98.7%前视)已改名deprecated隔离, 生产IC链切v2+排tail前视
    df = pd.read_sql_query(
        "SELECT code, pit_avail_date, droe, roa "
        "FROM stock_factors_fundamental_pit_v2 "
        "WHERE pit_avail_date >= 20180101 "
        "AND NOT (snap_rule='tail' AND ref_date < pit_avail_date)", conn)
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["pit_avail_date"] = df["pit_avail_date"].astype(str)
    return df

def forward_fill_pit_to_daily(pit_df, trading_dates):
    log("Forward-filling PIT to daily...")
    result = {}
    pit_dates = sorted(pit_df["pit_avail_date"].unique())
    for factor in PIT_FACTORS:
        pivot = pit_df.pivot_table(index="pit_avail_date", columns="code", values=factor, aggfunc="last").sort_index()
        daily = pd.DataFrame(index=trading_dates, columns=pivot.columns, dtype=float)
        for td in trading_dates:
            valid = [d for d in pit_dates if d <= td]
            if valid:
                daily.loc[td] = pivot.loc[max(valid)]
        result[factor] = daily
    return result

def compute_forward_returns(prices):
    log("Computing forward returns...")
    return {h: prices.shift(-h) / prices - 1.0 for h in FORWARD_HORIZONS}

def compute_daily_ic(factor_values, fwd_ret):
    common = factor_values.dropna().index.intersection(fwd_ret.dropna().index)
    if len(common) < MIN_CROSS_SECTION:
        return None, 0.0, 0.0, 0.0, len(common)
    fv, fr = factor_values.loc[common], fwd_ret.loc[common]
    ic, _ = spearmanr(fv, fr)
    if np.isnan(ic):
        return None, 0.0, 0.0, 0.0, len(common)
    n_top = max(int(len(common) * 0.2), 5)
    sidx = fv.argsort()
    lr = float(fr.iloc[sidx[-n_top:]].mean())
    sr = float(fr.iloc[sidx[:n_top]].mean())
    return round(float(ic), 4), round(lr, 4), round(sr, 4), round(lr - sr, 4), len(common)

def run_ic_computation(prices, fund_df, pit_daily, target_dates):
    fwd = compute_forward_returns(prices)
    results = []
    ic_history = {f: [] for f in ALL_FACTORS}
    for dt in prices.index:
        day_fund = fund_df[fund_df["date"] == dt]
        day_factors = {}
        for fac in FUND_FACTORS:
            if fac in day_fund.columns:
                day_factors[fac] = day_fund.set_index("code")[fac]
        for fac in PIT_FACTORS:
            if fac in pit_daily and dt in pit_daily[fac].index:
                day_factors[fac] = pit_daily[fac].loc[dt]
        for fac in ALL_FACTORS:
            if fac not in day_factors:
                continue
            fv = day_factors[fac]
            ic_1d, lr, sr, ls1, n = compute_daily_ic(fv, fwd[1].loc[dt])
            if ic_1d is None:
                continue
            ic_5d, _, _, _, _ = compute_daily_ic(fv, fwd[5].loc[dt])
            ic_20d, _, _, _, _ = compute_daily_ic(fv, fwd[20].loc[dt])
            ic_history[fac].append((dt, ic_1d))
            hist = [v for _, v in ic_history[fac]]
            ma5 = round(np.mean(hist[-5:]), 4) if len(hist) >= 5 else None
            ma20 = round(np.mean(hist[-20:]), 4) if len(hist) >= 20 else None
            ma60 = round(np.mean(hist[-60:]), 4) if len(hist) >= 60 else None
            icir = None
            if len(hist) >= 20:
                r20 = hist[-20:]
                s20 = np.std(r20)
                icir = round(np.mean(r20) / s20, 4) if s20 > 0 else None
            decay = "ok"
            if ma60 is not None:
                hm = np.mean(hist)
                if ma60 < 0 or (abs(hm) > 0.001 and abs(ma60) < 0.3 * abs(hm)):
                    decay = "critical"
            if decay == "ok" and icir is not None and len(hist) >= 60:
                hv = [v for _, v in ic_history[fac][-60:]]
                hs = np.std(hv)
                if hs > 0 and (np.mean(hv) / hs) > 0 and icir < 0.5 * (np.mean(hv) / hs):
                    decay = "warn"
            if dt in target_dates:
                results.append(dict(
                    date=dt, factor_name=fac,
                    ic_1d=ic_1d, ic_5d=ic_5d, ic_20d=ic_20d,
                    ic_1d_ma5=ma5, ic_1d_ma20=ma20, ic_1d_ma60=ma60,
                    icir_20d=icir, n_stocks=n,
                    long_ret_1d=lr, short_ret_1d=sr, long_short_1d=ls1,
                    decay_alert=decay))
    return results

def write_results(results):
    conn = sqlite3.connect(ATTR_DB, timeout=60)
    n = 0
    for r in results:
        conn.execute(
            "INSERT OR REPLACE INTO factor_daily_ic VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["date"], r["factor_name"], r["ic_1d"], r["ic_5d"], r["ic_20d"],
             r["ic_1d_ma5"], r["ic_1d_ma20"], r["ic_1d_ma60"],
             r["icir_20d"], r["n_stocks"], r["long_ret_1d"], r["short_ret_1d"],
             r["long_short_1d"], r["decay_alert"]))
        n += 1
    conn.commit()
    conn.close()
    log(f"Wrote {n} rows to factor_daily_ic")

def sentinel_check(prices):
    """E74 哨兵：IC_1d 最远可结算日 = qfq 倒数第二个交易日(需次日收盘)。
    库内最新行落后于该日 = 写库窗口竞态/链未跑全，notify WARN 上报。"""
    import subprocess
    if len(prices.index) < 2:
        return
    expected = prices.index[-2]
    conn = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=30)
    try:
        row = conn.execute("SELECT MAX(date) FROM factor_daily_ic").fetchone()
    finally:
        conn.close()
    latest = row[0] if row and row[0] else ""
    if latest < expected:
        msg = f"factor_daily_ic 最新行 {latest or '(空)'} 落后于可结算日 {expected}（E74 写窗口竞态/链未跑全，8/26 缺行即此例）"
        log("[WARN-SENTINEL] " + msg)
        try:
            subprocess.run(["python", r"D:\zcodesj\notify.py", "send",
                            "--title", "因子IC当日行缺失", "--content", msg],
                           capture_output=True, timeout=30)
        except Exception as e:
            log(f"  notify 调用失败(不阻断): {e}")
    else:
        log(f"[SENTINEL-OK] factor_daily_ic 最新行 {latest} = 可结算日 {expected}")

def print_summary(results):
    if not results:
        log("No results")
        return
    latest_date = max(r["date"] for r in results)
    latest = [r for r in results if r["date"] == latest_date]
    print()
    print("=" * 90)
    print(f"  Factor IC Daily Report -- {latest_date}")
    print("=" * 90)
    print(f"{'Factor':<15} {'IC_1d':>8} {'IC_5d':>8} {'IC_20d':>8} {'MA20':>8} {'MA60':>8} {'ICIR20':>8} {'N':>6} {'L-S':>8} {'Alert':>8}")
    print("-" * 90)
    def fmt(v):
        return f"{v:>8}" if v is not None else "     N/A"
    for r in sorted(latest, key=lambda x: abs(x.get("ic_1d") or 0), reverse=True):
        am = {"ok": "[OK]", "warn": "[WARN]", "critical": "[CRIT]"}.get(r["decay_alert"], "[?]")
        print(f"{r['factor_name']:<15} {fmt(r['ic_1d'])} {fmt(r['ic_5d'])} {fmt(r['ic_20d'])} {fmt(r['ic_1d_ma20'])} {fmt(r['ic_1d_ma60'])} {fmt(r['icir_20d'])} {r['n_stocks']:>6} {fmt(r['long_short_1d'])} {am}")
    alerts = [r for r in latest if r["decay_alert"] != "ok"]
    if alerts:
        print()
        print("Decay Alerts:")
        for r in alerts:
            tag = "CRIT" if r["decay_alert"] == "critical" else "WARN"
            print(f"  [{tag}] {r['factor_name']}: MA60={r['ic_1d_ma60']}, ICIR20={r['icir_20d']}")
    else:
        print()
        print("All factors OK")

def main():
    ap = argparse.ArgumentParser(description="Factor Daily IC Auto-Loader")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--date", type=str, default=None)
    ap.add_argument("--start", type=str, default="20250101")
    args = ap.parse_args()
    t0 = time.time()
    log("=== Factor Daily IC Calculator ===")
    prices = load_qfq_prices(args.start)
    fund_df = load_fund_factors(args.start)
    pit_df = load_pit_factors()
    trading_dates = sorted(prices.index)
    pit_daily = forward_fill_pit_to_daily(pit_df, trading_dates)
    if args.date:
        target_dates = [args.date]
    elif args.backfill:
        n = args.days if args.days > 5 else 250
        # 2026-08-24 修复: 不再 [:-20] 截尾——ic_20d 未到期时 compute 端自然 None,
        # 但 1d/5d 可算到最新日(旧逻辑致全表结构性滞后约20个交易日, 衰减监控晚一个月)
        target_dates = trading_dates[-n:]
    else:
        target_dates = trading_dates[-args.days:]  # 2026-08-24 同上: 去除20日截尾
    if not target_dates:
        log("Insufficient trading days")
        return 1
    log(f"Target: {target_dates[0]} ~ {target_dates[-1]} ({len(target_dates)} days)")
    results = run_ic_computation(prices, fund_df, pit_daily, target_dates)
    log(f"IC computed: {len(results)} records")
    if results:
        write_results(results)
        print_summary(results)
    sentinel_check(prices)
    log(f"Total: {time.time()-t0:.1f}s")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
