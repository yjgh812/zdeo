# -*- coding: utf-8 -*-
"""
行业轮动定量化 Phase 2.3
================================
数据: 自建申万一级行业等权日收益指数
  (stock_sector_sw1映射 + qfq个股日收益缓存 → 每行业成分等权日收益 → 31个行业指数)
  ⚠️ sw行业K线库只到2026-03且仅5个月, 不可用于长期回测
信号: 行业20日动量排名 + 60日动量 + 动量加速度(20日动量的1月变化)
策略: Top4行业等权, 月度调仓, next-bar, 单边成本0.3%
基准: 全行业等权
变体: A=纯20日动量Top4 / B=20日+60日双确认 / C=动量+加速度过滤
产出: reports/INDUSTRY_ROTATION_20260816.md
"""
from __future__ import annotations

import pickle
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
QFQ_CHG_CACHE = CACHE / "qfq_chg_pivot_2019.pkl"
WHITELIST = ("60", "00", "30", "68", "92")
COST = 0.003


def build_industry_returns() -> pd.DataFrame:
    """行业等权日收益: date × industry。"""
    cache = CACHE / "industry_eq_returns.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    with open(QFQ_CHG_CACHE, "rb") as f:
        chg = pickle.load(f)  # 行=code, 列=date(百分数)
    chg = chg.loc[[c for c in chg.index if c[:2] in WHITELIST]]
    ret = chg / 100.0
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    m = pd.read_sql_query("SELECT code, sector_name FROM stock_sector_sw1", conn)
    conn.close()
    m["code"] = m["code"].astype(str).str[-6:]  # sz000796 → 000796
    imap = dict(zip(m["code"], m["sector_name"]))
    common = [c for c in ret.index if c in imap]
    ret = ret.loc[common]
    ind = pd.Series([imap[c] for c in ret.index], index=ret.index)
    # 行业等权日收益 (ret: 行=code 列=date → 行=行业)
    ind_ret = ret.groupby(ind).mean().T  # date × industry
    ind_ret.index = pd.to_datetime(ind_ret.index.astype(str), format="%Y%m%d")
    ind_ret = ind_ret.sort_index()
    # 剔除行业成立前(全NaN段)
    ind_ret.to_pickle(cache)
    return ind_ret


def rotation_backtest(ind_ret: pd.DataFrame) -> dict:
    """三变体月度轮动回测。"""
    cal = ind_ret.index
    me = pd.Series(cal, index=cal).groupby(cal.to_period("M")).last().sort_values()
    me = list(me)
    mom20 = (1 + ind_ret).rolling(20).apply(lambda x: np.prod(x), raw=True) - 1
    mom60 = (1 + ind_ret).rolling(60).apply(lambda x: np.prod(x), raw=True) - 1
    accel = mom20 - mom20.shift(20)   # 动量加速度

    navs = {"A_top4_mom20": [], "B_dual_confirm": [], "C_accel_filter": [],
            "D_bottom4_reversal": [], "bench_equal": []}
    dates = []
    prev_sets = {k: set() for k in navs}
    for i, d in enumerate(me[:-1]):
        next_d = me[i + 1]
        if d not in mom20.index or mom20.loc[d].notna().sum() < 15:
            continue
        seg = ind_ret.loc[(ind_ret.index > d) & (ind_ret.index <= next_d)]
        if seg.empty:
            continue
        m20 = mom20.loc[d].dropna()
        m60 = mom60.loc[d].reindex(m20.index).dropna()
        ac = accel.loc[d].reindex(m20.index)

        # A: 纯20日动量Top4
        pickA = list(m20.sort_values(ascending=False).head(4).index)
        # B: 20日与60日均在Top8交集, 取交集内20日最高4个
        top20_8 = set(m20.sort_values(ascending=False).head(8).index)
        top60_8 = set(m60.sort_values(ascending=False).head(8).index)
        inter = m20[list(top20_8 & top60_8)].sort_values(ascending=False)
        pickB = list(inter.head(4).index) if len(inter) >= 4 else pickA
        # C: 20日Top8中加速度>0的取前4（不足补齐）
        cand = m20.sort_values(ascending=False).head(8)
        ac_pos = cand[(ac.reindex(cand.index) > 0).fillna(True)]
        pickC = list(ac_pos.head(4).index) if len(ac_pos) >= 3 else pickA
        # D: 行业反转 — 20日动量Bottom4（回避60日强趋势延续的拥挤行业）
        pickD = list(m20.sort_values(ascending=True).head(4).index)

        picks = {"A_top4_mom20": pickA, "B_dual_confirm": pickB,
                 "C_accel_filter": pickC, "D_bottom4_reversal": pickD,
                 "bench_equal": list(m20.index)}
        dates.append(d)
        for k, p in picks.items():
            mr = seg[p].mean(axis=1).fillna(0.0)
            to = 1 - len(prev_sets[k] & set(p)) / max(len(p), 1)
            mr.iloc[0] -= COST * to
            prev_sets[k] = set(p)
            navs[k].append(mr.mean())

    out = {}
    for k, mr in navs.items():
        s = pd.Series(mr).dropna()
        nav = (1 + s).cumprod()
        out[k] = {"cagr": float(nav.iloc[-1] ** (12 / len(s)) - 1),
                  "sharpe": float(s.mean() / (s.std() + 1e-12) * np.sqrt(12)),
                  "mdd": float((nav / nav.cummax() - 1).min()),
                  "n": len(s)}
    return out


def main():
    t0 = time.time()
    print("=== 行业轮动定量化 ===")
    ind_ret = build_industry_returns()
    print(f"  行业等权日收益: {ind_ret.shape[0]}天 × {ind_ret.shape[1]}行业 ({time.time()-t0:.0f}s)")
    perf = rotation_backtest(ind_ret)
    print(f"  回测完成 ({time.time()-t0:.0f}s)")
    lines = [
        "# 行业轮动定量化 — 实证报告",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 数据: 自建申万一级行业等权日收益指数(qfq+白名单, 2019~2026)",
        f"- 信号: 20日行业动量(主) / 60日动量(确认) / 加速度(过滤)",
        f"- 口径: 月度调仓·Top4行业等权·next-bar·单边成本{COST:.1%}",
        "",
        "| 变体 | CAGR | Sharpe | MDD | 月数 |",
        "|------|------|--------|-----|------|",
    ]
    for k, v in perf.items():
        lines.append(f"| {k} | {v['cagr']:.2%} | {v['sharpe']:.3f} | {v['mdd']:.2%} | {v['n']} |")
    b = perf.get("bench_equal", {})
    best_k = max(perf, key=lambda k: perf[k]["sharpe"])
    ex = perf[best_k]["cagr"] - b.get("cagr", np.nan)
    lines += ["",
              f"**结论**: 最优变体={best_k}; 相对全行业等权超额CAGR {ex:+.2%}",
              "",
              "## 对接主线雷达",
              "- 每月末计算31个申万一级行业20日动量排名, Top4行业作为主线雷达的定量锚",
              "- Top4行业外的主线候选 → 降级为观察级(需范式库强证据才升级)",
              f"- 耗时 {time.time()-t0:.0f}s", ""]
    p = REPORT / f"INDUSTRY_ROTATION_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")


if __name__ == "__main__":
    main()
