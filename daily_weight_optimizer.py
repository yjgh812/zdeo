# -*- coding: utf-8 -*-
r"""
每日组合权重优化器 — minvar落地版 (Phase 4.2 生产落地)
====================================================
依据: PORTFOLIO_OPT_V2_20260816 实证 minvar CAGR 6.31% vs 等权5.09%(+1.2pp)
输入: bp+ep最新Top20清单(multifactor/reports/daily_top20/bp_ep_monthly_*.csv)
输出: 权重建议(等权 vs minvar对照) → stdout + JSON(D:\zcodesj\trade_plans\)

约束: 个股[1%,20%] + 行业≤30% + 满仓; 120日Ledoit-Wolf协方差
用法: python daily_weight_optimizer.py [--csv path] [--json out]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

sys.path.insert(0, r"D:\zcodesj")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
TOP20_DIR = Path(r"D:\zcodesj\multifactor\reports\daily_top20")
OUT_DIR = Path(r"D:\zcodesj\trade_plans")
OUT_DIR.mkdir(exist_ok=True)
WHITELIST = ("60", "00", "30", "68", "92")


def latest_top20():
    files = sorted(TOP20_DIR.glob("bp_ep_monthly_*.csv"))
    if not files:
        raise FileNotFoundError("无bp_ep_monthly清单")
    df = pd.read_csv(files[-1], dtype={"code": str})
    return df, files[-1].name


def load_hist_returns(codes):
    conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
    ph = ",".join("?" * len(codes))
    df = pd.read_sql_query(
        f"SELECT code, date, close FROM stock_daily_meta_qfq "
        f"WHERE code IN ({ph}) AND date >= 20260101 AND close > 0", conn,
        params=[c + "%" if len(c) == 6 else c for c in codes])
    conn.close()
    return None  # placeholder


def load_returns(codes):
    """120日qfq收益(Top20票)。"""
    conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
    dfs = []
    for c in codes:
        df = pd.read_sql_query(
            "SELECT date, close FROM stock_daily_meta_qfq "
            "WHERE code LIKE ? AND date >= 20260101 AND close > 0 ORDER BY date",
            conn, params=[c + "%"])
        if len(df) > 30:
            df["code"] = c
            dfs.append(df)
    conn.close()
    if not dfs:
        return pd.DataFrame()
    big = pd.concat(dfs)
    big["date"] = pd.to_datetime(big["date"].astype(str), format="%Y%m%d")
    close = big.pivot(index="date", columns="code", values="close").sort_index()
    return (close / close.shift(1) - 1).clip(-0.21, 0.21).iloc[-120:]


def optimize_minvar(cov, codes, industry_map, lo=0.01, hi=0.20, ind_cap=0.30):
    n = len(codes)
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    inds = [industry_map.get(c, "其他") for c in codes]
    for u in set(inds):
        m = np.array([i == u for i in inds])
        if m.sum() >= 2:
            cons.append({"type": "ineq", "fun": lambda w, m=m: ind_cap - w[m].sum()})
    res = minimize(lambda w: w @ cov @ w, w0, method="SLSQP",
                   bounds=[(lo, hi)] * n, constraints=cons,
                   options={"maxiter": 300, "ftol": 1e-10})
    w = res.x if res.success else w0
    return np.clip(w, lo, hi) / np.clip(w, lo, hi).sum()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    t0 = time.time()
    top, fname = latest_top20()
    codes = [str(c) for c in top["code"].tolist()]
    names = dict(zip(top["code"].astype(str), top.get("name", pd.Series())))
    print(f"清单: {fname} ({len(codes)}只)")

    ret = load_returns(codes)
    valid = [c for c in codes if c in ret.columns and ret[c].notna().sum() >= 60]
    ret = ret[valid]
    print(f"有效收益数据: {len(valid)}/{len(codes)}只 × {ret.shape[0]}日")

    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    m = pd.read_sql_query("SELECT code, sector_name FROM stock_sector_sw1", conn)
    conn.close()
    m["code"] = m["code"].astype(str).str[-6:]
    imap = dict(zip(m["code"], m["sector_name"]))

    lw = LedoitWolf().fit(ret.dropna(axis=0, thresh=len(valid) * 0.5).fillna(0).values)
    cov = lw.covariance_
    w = optimize_minvar(cov, valid, imap)
    w_eq = np.full(len(valid), 1 / len(valid))
    # 风险贡献
    port_var = w @ cov @ w
    mrc = cov @ w
    rc = w * mrc / port_var
    rc_eq = w_eq * (cov @ w_eq) / (w_eq @ cov @ w_eq)

    print("\n=== 权重建议 (minvar vs 等权) ===")
    print(f"{'代码':<8}{'名称':<10}{'行业':<10}{'minvar':>8}{'等权':>8}{'风险贡献差':>10}")
    order = np.argsort(-w)
    for i in order:
        c = valid[i]
        nm = names.get(c, "?")
        if not isinstance(nm, str):
            nm = "?"
        print(f"{c:<8}{nm[:4]:<10}{imap.get(c,'?')[:4]:<10}"
              f"{w[i]:>7.1%}{w_eq[i]:>7.1%}{(rc[i]-rc_eq[i]):>9.1%}")
    print(f"\n组合年化波动: minvar {np.sqrt(port_var*244):.1%} vs "
          f"等权 {np.sqrt(w_eq@cov@w_eq*244):.1%}")

    out = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "source": fname,
        "method": "minvar(LedoitWolf120d, [1%,20%], 行业≤30%)",
        "positions": [{"code": valid[i], "name": str(names.get(valid[i], "?")),
                       "industry": imap.get(valid[i], "?"),
                       "weight": round(float(w[i]), 4),
                       "risk_contrib": round(float(rc[i]), 4)}
                      for i in order],
        "note": "实证依据PORTFOLIO_OPT_V2_20260816: minvar较等权CAGR+1.2pp/Sharpe+0.06",
    }
    jp = args.json or str(OUT_DIR / f"weights_minvar_{time.strftime('%Y%m%d')}.json")
    Path(jp).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {jp} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
