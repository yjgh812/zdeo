# -*- coding: utf-8 -*-
"""
组合优化 v2 — 真实求解器替换启发式 Phase 2.4
================================================
升级点 vs attribution/l3_portfolio_optimizer.py (启发式):
  1. scipy SLSQP 凸优化: 最小方差 / 最大分散度 / 均值方差
  2. 风险平价 ERC: 循环迭代法(Spinu 2013凸优化公式)
  3. 约束集: 行业≤30% + 个股[1%,20%] + 满仓
  4. 协方差: Ledoit-Wolf收缩(scikit-learn)
验证: bp+ep PIT Top20 月度调仓 2019-2026,
      等权(基线) vs minvar vs ERC vs maxdiv 三口径对比
产出: reports/PORTFOLIO_OPT_V2_20260816.md
"""
from __future__ import annotations

import pickle
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

PIT_DB = r"D:\zcodesj\factor_pit.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
PRICE_CAP = 30.0
COST = 0.003


# ═══════════════════════════════════════════════════════
#  数据: PIT bp+ep 月度Top20 (as-of无前视)
# ═══════════════════════════════════════════════════════

def load_pit_bp_ep() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=120)
    df = pd.read_sql_query(
        "SELECT code, pit_avail_date, bp, ep FROM stock_factors_fundamental_pit_v2 "
        "WHERE pit_avail_date >= 20180601", conn)
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["pit_avail_date"] = df["pit_avail_date"].astype(str)
    return df


def month_top20(pit: pd.DataFrame, month_end: pd.Timestamp,
                raw_close: pd.DataFrame) -> list:
    """月末as-of PIT bp+ep 等权排名 Top20（≤30元、bp/ep>0）。"""
    ymd = month_end.strftime("%Y%m%d")
    avail = pit[pit["pit_avail_date"] <= ymd]
    latest = (avail.sort_values("pit_avail_date")
              .groupby("code")[["bp", "ep", "pit_avail_date"]].last())
    latest = latest[(latest["bp"] > 0) & (latest["ep"] > 0)]
    latest["r"] = latest["bp"].rank(pct=True) + latest["ep"].rank(pct=True)
    # 价格过滤
    if ymd in raw_close.index:
        px = raw_close.loc[ymd]
        latest = latest.loc[[c for c in latest.index
                             if c in px.index and 0 < px[c] <= PRICE_CAP]]
    return list(latest.sort_values("r", ascending=False).head(20).index)


def load_returns(start="20190101", end="20260814") -> pd.DataFrame:
    """qfq日收益宽表（白名单+污染日剔除）。"""
    cache = CACHE / "opt_v2_ret.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE date BETWEEN ? AND ? AND close > 0", conn, params=[start, end])
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(("60", "00", "30", "68", "92"))]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    close = df.pivot(index="date", columns="code", values="close").sort_index()
    ret = (close / close.shift(1) - 1).clip(-0.21, 0.21)
    # 污染日剔除
    bad = ((ret.abs() > 0.21).sum(axis=1) /
           ret.notna().sum(axis=1).replace(0, np.nan)).fillna(0)
    ret = ret.drop(index=bad[bad > 0.05].index)
    ret.to_pickle(cache)
    return ret


def load_raw_close_monthly() -> pd.DataFrame:
    """原始close(月末行) — 30元过滤用。"""
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_meta "
        "WHERE date BETWEEN 20181201 AND 20260814 AND close > 0", conn)
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = df["date"].astype(str)
    piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last")
    return piv


# ═══════════════════════════════════════════════════════
#  优化器 (scipy真实求解)
# ═══════════════════════════════════════════════════════

def _industry_masks(codes, industry_map):
    """行业约束: 每行业权重和≤0.3。返回 list of bool arrays。"""
    inds = [industry_map.get(c, "其他") for c in codes]
    uniq = sorted(set(inds))
    return [(np.array(inds) == u) for u in uniq if (np.array(inds) == u).sum() >= 2]


def optimize_minvar(cov: np.ndarray, codes=None, industry_map=None,
                    lo=0.01, hi=0.20, ind_cap=0.30) -> np.ndarray:
    """最小方差 SLSQP。"""
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    if industry_map and codes is not None:
        for m in _industry_masks(codes, industry_map):
            cons.append({"type": "ineq", "fun": lambda w, m=m: ind_cap - w[m].sum()})
    bounds = [(lo, hi)] * n
    res = minimize(lambda w: w @ cov @ w, w0, method="SLSQP",
                   bounds=bounds, constraints=cons,
                   options={"maxiter": 300, "ftol": 1e-10})
    return res.x if res.success else w0


def optimize_erc(cov: np.ndarray, max_iter=200, tol=1e-8) -> np.ndarray:
    """风险平价(Spinu凸优化): min ½wᵀΣw − Σ b_i·ln(w_i), b=1/n。"""
    n = cov.shape[0]
    b = np.full(n, 1 / n)
    w = np.full(n, 1 / n) / np.sqrt(np.diag(cov))
    w /= w.sum()
    for _ in range(max_iter):
        grad = cov @ w - b / w
        hess_diag = np.diag(cov) + b / w ** 2
        step = grad / hess_diag
        w_new = np.maximum(w - step * 0.5, 1e-9)
        w_new /= w_new.sum()
        if np.abs(w_new - w).max() < tol:
            w = w_new
            break
        w = w_new
    # 截到[1%,20%]再归一（近似投影）
    w = np.clip(w, 0.01, 0.20)
    return w / w.sum()


def optimize_maxdiv(cov: np.ndarray, codes=None, industry_map=None,
                    lo=0.01, hi=0.20, ind_cap=0.30) -> np.ndarray:
    """最大分散度: max wᵀσ / sqrt(wᵀΣw)。"""
    n = cov.shape[0]
    sigma = np.sqrt(np.diag(cov))
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1}]
    if industry_map and codes is not None:
        for m in _industry_masks(codes, industry_map):
            cons.append({"type": "ineq", "fun": lambda w, m=m: ind_cap - w[m].sum()})
    def neg_dr(w):
        vol = np.sqrt(w @ cov @ w)
        return -(w @ sigma) / (vol + 1e-12)
    res = minimize(neg_dr, w0, method="SLSQP", bounds=[(lo, hi)] * n,
                   constraints=cons, options={"maxiter": 300})
    return res.x if res.success else w0


# ═══════════════════════════════════════════════════════
#  回测: 月度重优化 vs 等权
# ═══════════════════════════════════════════════════════

def backtest(ret: pd.DataFrame, pit, raw_close_m, industry_map,
             start="2019-06-30", end="2026-08-14") -> dict:
    cal = ret.index
    me = pd.Series(cal, index=cal).groupby(cal.to_period("M")).last()
    me = [d for d in me if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    variants = {"equal": [], "minvar": [], "erc": [], "maxdiv": []}
    prev_w = {}
    month_picks_log = []
    for i, d in enumerate(me[:-1]):
        next_d = me[i + 1]
        codes = month_top20(pit, d, raw_close_m)
        if len(codes) < 10:
            continue
        month_picks_log.append((d, len(codes)))
        sub = ret.loc[(ret.index > d) & (ret.index <= next_d),
                      ret.columns.intersection(codes)]
        if sub.empty or sub.shape[1] < 8:
            continue
        # 优化窗: 过去120日
        cols_ok = ret.columns.intersection(codes)
        hist = ret.loc[:d].iloc[-120:].loc[:, cols_ok]
        hist = hist.dropna(axis=1, thresh=60)
        if hist.shape[1] < 8:
            variants["equal"].append(sub.mean(axis=1).mean())
            for k in ("minvar", "erc", "maxdiv"):
                variants[k].append(sub.mean(axis=1).mean())
            continue
        use_codes = list(hist.columns)
        sub2 = sub[use_codes]
        X = hist.values.T  # assets × days
        try:
            lw = LedoitWolf().fit(X.T)
            cov = lw.covariance_
        except Exception:
            cov = np.cov(X)
        picks = [c for c in codes if c in use_codes]
        im = {c: industry_map.get(c, "其他") for c in use_codes}
        w_eq = np.full(len(use_codes), 1 / len(use_codes))
        w_mv = optimize_minvar(cov, use_codes, im)
        w_erc = optimize_erc(cov)
        w_md = optimize_maxdiv(cov, use_codes, im)
        daily_eq = sub2.mean(axis=1)
        daily_mv = pd.Series((sub2.values * w_mv).sum(axis=1), index=sub2.index)
        daily_erc = pd.Series((sub2.values * w_erc).sum(axis=1), index=sub2.index)
        daily_md = pd.Series((sub2.values * w_md).sum(axis=1), index=sub2.index)
        # 换手成本: 权重变化绝对和/2 × 成本（简化：与上月等权集对比近似）
        if prev_w:
            to = 1 - len(set(prev_w) & set(use_codes)) / len(use_codes)
            for arr in (daily_eq, daily_mv, daily_erc, daily_md):
                arr.iloc[0] -= COST * to
        prev_w = set(use_codes)
        variants["equal"].append((1 + daily_eq).prod() - 1)
        variants["minvar"].append((1 + daily_mv).prod() - 1)
        variants["erc"].append((1 + daily_erc).prod() - 1)
        variants["maxdiv"].append((1 + daily_md).prod() - 1)

    out = {}
    for k, mr in variants.items():
        s = pd.Series(mr).dropna()
        if len(s) < 12:
            out[k] = None
            continue
        nav = (1 + s).cumprod()
        cagr = nav.iloc[-1] ** (12 / len(s)) - 1
        sharpe = s.mean() / (s.std() + 1e-12) * np.sqrt(12)
        mdd = (nav / nav.cummax() - 1).min()
        out[k] = {"cagr": float(cagr), "sharpe": float(sharpe),
                  "mdd": float(mdd), "n_months": len(s)}
    return {"perf": out, "n_rebal": len(month_picks_log)}


def load_industry_map() -> dict:
    conn = sqlite3.connect(
        f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query("SELECT code, sector_name FROM stock_sector_sw1", conn)
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[-6:]  # sz000796 → 000796
    return dict(zip(df["code"], df["sector_name"]))


def main():
    t0 = time.time()
    print("=== 组合优化v2: 真实求解器对比回测 ===")
    pit = load_pit_bp_ep()
    print(f"  PIT bp+ep: {len(pit):,}行")
    ret = load_returns()
    print(f"  日收益: {ret.shape}")
    raw_close_m = load_raw_close_monthly()
    industry_map = load_industry_map()
    print(f"  行业映射: {len(industry_map)}股 ({time.time()-t0:.0f}s)")

    res = backtest(ret, pit, raw_close_m, industry_map)
    print(f"  回测完成: {res['n_rebal']}次调仓 ({time.time()-t0:.0f}s)")

    lines = [
        "# 组合优化v2 — 真实求解器 vs 等权 (bp+ep PIT Top20 月度)",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        "- 样本: 2019-07~2026-08 月度调仓, bp+ep PIT as-of Top20, ≤30元",
        "- 求解器: scipy SLSQP(最小方差/最大分散度) + Spinu凸优化(风险平价ERC)",
        "- 协方差: Ledoit-Wolf收缩, 120日窗; 约束: 个股[1%,20%]+行业≤30%",
        f"- 成本: 单边{COST:.1%}",
        "",
        "| 变体 | CAGR | Sharpe | MDD | 月数 |",
        "|------|------|--------|-----|------|",
    ]
    for k in ("equal", "minvar", "erc", "maxdiv"):
        p = res["perf"].get(k)
        if p:
            lines.append(f"| {k} | {p['cagr']:.2%} | {p['sharpe']:.3f} "
                         f"| {p['mdd']:.2%} | {p['n_months']} |")
    best = max((k for k in res["perf"] if res["perf"][k]),
               key=lambda k: res["perf"][k]["sharpe"])
    lines += ["", f"**结论**: Sharpe最优={best}; "
              f"minvar-MDD改善 vs 等权: "
              f"{(res['perf']['minvar']['mdd'] - res['perf']['equal']['mdd']):+.2%}"
              f" (MDD负值更大=更差)", "",
              f"- 调仓次数: {res['n_rebal']}",
              f"- 耗时: {time.time()-t0:.0f}s", ""]
    p = REPORT / f"PORTFOLIO_OPT_V2_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")


if __name__ == "__main__":
    main()
