# -*- coding: utf-8 -*-
"""gauge A格子基线复现自检件 (可复用, 供任何低位横存款族 runner 调用/独立运行)
m7债务转化: v3链Part0未实现→本独立件统一承载; 阈值=声明43.2%±1.5pp, 超差exit 2
用法: python analysis/gauge_baseline_check.py [--quiet]
"""
import sys, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DECLARED_WIN, TOL = 43.2, 1.5


def check(quiet=False):
    db = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20230101' ORDER BY code,date", db)
    db.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    g = df.groupby("code")["close"]
    df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
    mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
    df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)
    out = []
    for code, sub in df.groupby("code", sort=False):
        close = sub["close"].to_numpy(); n = len(close)
        fut = np.full((n, 20), np.nan)
        for k in range(1, min(21, n)):
            fut[: n - k, k - 1] = close[k:]
        entry = close; stop_line = entry * 0.92
        stop_px = np.full(n, np.nan)
        for k in range(20):
            m = np.isnan(stop_px) & (fut[:, k] <= stop_line)
            stop_px[m] = fut[m, k]
        has20 = ~np.isnan(fut[:, -1])
        t20e = np.where(~np.isnan(stop_px), stop_px / entry - 1,
                        np.where(has20, fut[:, -1] / entry - 1, np.nan))
        t20e[~has20] = np.nan
        out.append(pd.DataFrame({"is_A": sub["is_A"].to_numpy(), "t20e": t20e}, index=sub.index))
    alld = pd.concat(out)
    dates = np.sort(df["date"].unique())
    b = alld[df["date"].isin(set(dates[-250:])) & alld["is_A"] & alld["t20e"].notna()]
    win = float((b["t20e"] > 0).mean() * 100)
    ok = abs(win - DECLARED_WIN) <= TOL
    if not quiet:
        print(f"[Part0] A格子复现: n={len(b)}, win={win:.2f}% vs 声明{DECLARED_WIN}% "
              f"→ {'PASS' if ok else 'FAIL(偏差>±1.5pp, 口径实现有误)'}")
    return ok, win, len(b)


if __name__ == "__main__":
    ok, win, n = check(quiet="--quiet" in sys.argv)
    sys.exit(0 if ok else 2)
