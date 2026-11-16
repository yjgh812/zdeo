# -*- coding: utf-8 -*-
"""
AG因子库二关筛选 — 遗留#3 (65候选 → 五分层+Top20组合回测)
================================================
输入: _cache/ag_ic_results.pkl 中 |IC|>0.03 & ICIR>0.4 & n>=60 的65个因子
评估(第二关):
  A. 五分层单调性(Spearman p<0.05) — 复用面板月末截面
  B. Top20组合回测(next-bar·≤30元·0.3%·2019-2026)
三关全过 → 注册FACTOR_REGISTRY候选

产出: reports/AG_SECOND_GATE_20260816.md
"""
from __future__ import annotations

import pickle
import sys
import time
import types
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from scipy.stats import spearmanr

_pkg = types.ModuleType("aurumq_rl")
_pkg.__path__ = [r"D:\zcodesj\strategy_libs\aurumq-rl\src\aurumq_rl"]
sys.modules.setdefault("aurumq_rl", _pkg)
sys.path.insert(0, r"D:\zcodesj\strategy_libs\aurumq-rl\src")
import aurumq_rl.factors.alpha101  # noqa
import aurumq_rl.factors.gtja191   # noqa
from aurumq_rl.factors.registry import ALPHA101_REGISTRY, GTJA191_REGISTRY  # noqa

sys.path.insert(0, r"D:\zcodesj")
CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
COST = 0.003
PRICE_CAP = 30.0


def load_context():
    """面板v2(含adv列) + 月末 + fwd20 + 原始close(30元过滤) + 白名单。"""
    panel = pl.read_parquet(CACHE / "ag_panel_v2.parquet")
    import importlib
    ag = importlib.import_module('alpha_gtja_local_20260816')
    month_ends, fwd20, close_qfq = ag.build_eval_base(panel)
    # 原始close
    import sqlite3
    conn = sqlite3.connect(
        r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro",
        uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_meta WHERE date>=20190101 AND close>0",
        conn)
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    raw_close = df.pivot_table(index="date", columns="code", values="close",
                               aggfunc="last").sort_index()
    # qfq收益(回测)
    ret = (close_qfq / close_qfq.shift(1) - 1).clip(-0.21, 0.21)
    return panel, month_ends, fwd20, raw_close, ret


def factor_wide(panel, entry):
    """因子 → 月末宽表。"""
    s = entry.impl(panel)
    df = panel.select(["stock_code", "trade_date"]).with_columns(
        s.alias("v")).to_pandas()
    df = df.dropna(subset=["v"])
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    return df.pivot_table(index="date", columns="stock_code", values="v",
                          aggfunc="last").sort_index()


def second_gate(wide, month_ends, fwd20, raw_close, ret):
    """五分层单调 + Top20组合回测。"""
    # 月末对齐
    me_avail = [d for d in month_ends if d in wide.index]
    # A: 五分层
    layer_rets = []
    for i, d in enumerate(me_avail[:-1]):
        nxt = me_avail[i + 1]
        row = wide.loc[d].dropna()
        if d in raw_close.index:
            px = raw_close.loc[d]
            row = row[[c for c in row.index if c in px.index and 0 < px[c] <= PRICE_CAP]]
        if len(row) < 100:
            continue
        tgt = fwd20.loc[d] if d in fwd20.index else None
        if tgt is None:
            continue
        try:
            q = pd.qcut(row.rank(method="first"), 5, labels=False)
        except ValueError:
            continue
        for L in range(5):
            codes = q[q == L].index
            layer_rets.append((d, L, tgt.reindex(codes).dropna().mean()))
    lr = pd.DataFrame(layer_rets, columns=["date", "layer", "fwd"])
    means = lr.groupby("layer")["fwd"].mean().values
    ok_layers = ~np.isnan(means)
    if ok_layers.sum() < 4:
        mono_p = np.nan
    else:
        try:
            _, mono_p = spearmanr(np.arange(1, 6)[ok_layers], means[ok_layers])
        except Exception:
            mono_p = np.nan
    # B: Top20组合回测
    cal = ret.index
    picks_by_month = {}
    for i, d in enumerate(me_avail[:-1]):
        row = wide.loc[d].dropna()
        if d in raw_close.index:
            px = raw_close.loc[d]
            row = row[[c for c in row.index if c in px.index and 0 < px[c] <= PRICE_CAP]]
        if len(row) < 50:
            continue
        picks_by_month[d] = list(row.sort_values(ascending=False).head(20).index)
    monthly, prev = [], set()
    months = sorted(picks_by_month)
    for i, d in enumerate(months[:-1]):
        nxt = months[i + 1]
        codes = picks_by_month[d]
        try:
            loc0, loc1 = cal.get_loc(d), cal.get_loc(nxt)
        except KeyError:
            continue
        seg = ret.iloc[loc0 + 1:loc1 + 1]
        sub = seg[seg.columns.intersection(codes)]
        if sub.empty:
            continue
        mr = (1 + sub.mean(axis=1).fillna(0)).prod() - 1
        to = 1 - len(prev & set(codes)) / max(len(codes), 1)
        mr -= COST * to
        prev = set(codes)
        monthly.append(mr)
    s = pd.Series(monthly)
    if len(s) < 24:
        return {"mono_p": mono_p, "cagr": np.nan, "sharpe": np.nan,
                "mdd": np.nan, "n": len(s)}
    nav = (1 + s).cumprod()
    return {"mono_p": float(mono_p) if not np.isnan(mono_p) else np.nan,
            "cagr": float(nav.iloc[-1] ** (12 / len(s)) - 1),
            "sharpe": float(s.mean() / (s.std() + 1e-12) * np.sqrt(12)),
            "mdd": float((nav / nav.cummax() - 1).min()), "n": len(s)}


def main():
    t0 = time.time()
    print("=== AG因子库二关筛选 (65候选) ===")
    results = pickle.load(open(CACHE / "ag_ic_results.pkl", "rb"))
    df = pd.DataFrame(results)
    ok = df[~df["err"].astype(bool)].copy()
    ok["abs_ic"] = ok["ic_mean"].abs()
    cands = ok[(ok["abs_ic"] > 0.03) & (ok["icir"].abs() > 0.4) &
               (ok["n"] >= 60)].sort_values("abs_ic", ascending=False)
    print(f"  候选: {len(cands)}个")
    panel, month_ends, fwd20, raw_close, ret = load_context()
    print(f"  上下文就绪 ({time.time()-t0:.0f}s)")

    regs = {"alpha101": ALPHA101_REGISTRY, "gtja191": GTJA191_REGISTRY}
    out_rows = []
    for k, (_, r) in enumerate(cands.iterrows()):
        t1 = time.time()
        entry = regs[r["lib"]].get(r["id"])
        if entry is None:
            continue
        try:
            wide = factor_wide(panel, entry)
            if wide.empty:
                continue
            g = second_gate(wide, month_ends, fwd20, raw_close, ret)
        except Exception as e:
            continue
        out_rows.append({"lib": r["lib"], "id": r["id"], "ic": r["ic_mean"],
                         "icir": r["icir"], **g})
        if (k + 1) % 10 == 0:
            print(f"  [{k+1}/{len(cands)}] ({time.time()-t0:.0f}s)", flush=True)

    odf = pd.DataFrame(out_rows)
    odf.to_pickle(CACHE / "ag_second_gate.pkl")
    # 三关判定
    odf["pass"] = ((odf["mono_p"] < 0.05) & (odf["sharpe"] > 0.3)).astype(int)
    passed = odf[odf["pass"] == 1].sort_values("sharpe", ascending=False)
    lines = [
        "# AG因子库二关筛选 (65候选)",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        "- 关2A: 五分层单调p<0.05; 关2B: Top20组合Sharpe>0.3 (next-bar·≤30元·0.3%)",
        f"- 评估完成: {len(odf)}/{len(cands)}",
        f"- **三关全过: {len(passed)}个**",
        "",
        "## 三关通过清单",
        "",
        "| 库 | 因子 | IC | ICIR | 单调p | CAGR | Sharpe | MDD |",
        "|----|------|-----|------|-------|------|--------|-----|",
    ]
    for _, r in passed.head(30).iterrows():
        lines.append(f"| {r['lib']} | {r['id']} | {r['ic']:.4f} | {r['icir']:.2f} "
                     f"| {r['mono_p']:.4f} | {r['cagr']:.2%} | {r['sharpe']:.3f} "
                     f"| {r['mdd']:.2%} |")
    # 相关性去重提示(与已知rev20/vol20)
    lines += ["", "## 注册建议",
              "",
              "- 通过者按类别与现有FACTOR_REGISTRY去重(rev20_improved/vol20同族的跳过)",
              "- 优先注册: 不同族且Sharpe>0.5的因子",
              "", f"耗时 {time.time()-t0:.0f}s", ""]
    p = REPORT / f"AG_SECOND_GATE_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  三关全过: {len(passed)}个; 报告: {p}")


if __name__ == "__main__":
    main()
