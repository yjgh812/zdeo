# -*- coding: utf-8 -*-
"""
Alpha101 + GTJA191 因子库本地移植评估 (298因子) — Phase 1.1
============================================================
数据: qfq库 2019-2026 全市场(白名单)
vwap: (high+low+close)/3 近似 —— ⚠️qfq库amount列不可用铁律(alpha005伪信号前车之鉴)
returns: 日收益(qfq close pct change, clip ±21%)
评估: 月末截面 vs 未来20日收益 → 月度Spearman IC序列 → IC均值/ICIR/正率
产出: reports/ALPHA_GTJA_LOCAL_20260816.md + _cache/ag_ic_results.pkl

方向处理: registry的direction(reverse)只记录, IC符号由数据说话——
         我们报告原始IC与|direction已知时的有效IC|两列。
"""
from __future__ import annotations

import sys
import time
import types
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

# ── 绕过 aurumq_rl.__init__ 的 onnxruntime 依赖 ──
_pkg = types.ModuleType("aurumq_rl")
_pkg.__path__ = [r"D:\zcodesj\strategy_libs\aurumq-rl\src\aurumq_rl"]
sys.modules.setdefault("aurumq_rl", _pkg)
sys.path.insert(0, r"D:\zcodesj\strategy_libs\aurumq-rl\src")

import aurumq_rl.factors.alpha101  # noqa: E402 触发注册
import aurumq_rl.factors.gtja191   # noqa: E402
from aurumq_rl.factors.registry import ALPHA101_REGISTRY, GTJA191_REGISTRY  # noqa: E402

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
START, END = "20190101", "20260814"
WHITELIST = ("60", "00", "30", "68", "92")


def load_panel() -> pl.DataFrame:
    """qfq → polars面板 (stock_code, trade_date, open/high/low/close/volume/vwap/returns/cap代理)。"""
    cache = CACHE / "ag_panel_2019_2026.parquet"
    if cache.exists():
        print("  面板缓存命中")
        return pl.read_parquet(cache)
    import sqlite3
    t0 = time.time()
    conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "WHERE date BETWEEN ? AND ? AND close > 0 AND volume > 0 ORDER BY code, date",
        conn, params=[START, END])
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(WHITELIST)]
    df = df.drop_duplicates(["code", "date"], keep="last")
    df = df.rename(columns={"code": "stock_code", "date": "trade_date"})
    df["trade_date"] = df["trade_date"].astype(str)
    # returns: 组内pct_change clip
    df = df.sort_values(["stock_code", "trade_date"])
    g = df.groupby("stock_code")["close"]
    df["returns"] = (g.pct_change()).clip(-0.21, 0.21)
    # vwap近似 + cap代理(close*volume 20日均值=成交额代理)
    df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3.0
    df["cap"] = df.groupby("stock_code")["close"].transform(lambda s: s.rolling(20, min_periods=1).mean()) \
        * df.groupby("stock_code")["volume"].transform(lambda s: s.rolling(20, min_periods=1).mean())
    panel = pl.from_pandas(df[["stock_code", "trade_date", "open", "high", "low",
                               "close", "volume", "vwap", "returns", "cap"]])
    panel.write_parquet(cache)
    print(f"  面板: {panel.height:,}行 ({time.time()-t0:.0f}s) -> {cache.name}")
    return panel


def build_eval_base(panel: pl.DataFrame):
    """月末截面清单 + 未来20日收益(qfq close)。返回 (month_ends, fwd20_df, close_df)。"""
    close_pd = panel.select(["trade_date", "stock_code", "close"]).to_pandas()
    close_pd["date"] = pd.to_datetime(close_pd["trade_date"], format="%Y%m%d")
    close = close_pd.pivot(index="date", columns="stock_code", values="close").sort_index()
    ret = (close / close.shift(1) - 1.0).clip(-0.21, 0.21)
    # 未来20日收益（含次日起, next-bar）
    fwd = (1 + ret).rolling(20, min_periods=15).apply(lambda x: np.prod(x) - 1, raw=True)
    fwd20 = fwd.shift(-20)
    # 月末交易日
    s = pd.Series(close.index, index=close.index)
    me = [pd.Timestamp(d) for d in s.groupby(s.index.to_period("M")).last().values]
    me = [d for d in me if d in fwd20.index]
    return me, fwd20, close


def eval_factor(panel: pl.DataFrame, factor_series: pl.Series, month_ends,
                fwd20: pd.DataFrame, min_n=100) -> dict:
    """单因子: polars Series(value) + panel键列 → 月度IC统计。"""
    df = panel.select(["stock_code", "trade_date"]).with_columns(
        factor_series.alias("value")).to_pandas()
    df = df.dropna(subset=["value"])
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    wide = df.pivot_table(index="date", columns="stock_code", values="value", aggfunc="last")
    ics = {}
    for d in month_ends:
        if d not in wide.index:
            continue
        row = wide.loc[d].dropna()
        if len(row) < min_n:
            continue
        tgt = fwd20.loc[d] if d in fwd20.index else None
        if tgt is None:
            continue
        common = row.index.intersection(tgt.dropna().index)
        if len(common) < min_n:
            continue
        ic = row[common].astype(float).corr(tgt[common].astype(float), method="spearman")
        if not np.isnan(ic):
            ics[d] = ic
    s = pd.Series(ics)
    if len(s) < 12:
        return {"ic_mean": np.nan, "icir": np.nan, "pos_rate": np.nan, "n": len(s)}
    return {"ic_mean": float(s.mean()), "icir": float(s.mean() / (s.std() + 1e-12)),
            "pos_rate": float((s > 0).mean()), "n": len(s)}


def main():
    t_all = time.time()
    print("=== Alpha101+GTJA191 本地移植评估 ===")
    panel = load_panel()
    month_ends, fwd20, _ = build_eval_base(panel)
    print(f"  月末截面 {len(month_ends)} 期 ({month_ends[0]:%Y%m%d}~{month_ends[-1]:%Y%m%d})")

    results = []
    regs = [("alpha101", ALPHA101_REGISTRY), ("gtja191", GTJA191_REGISTRY)]
    total = sum(len(r) for _, r in regs)
    done = 0
    for lib, reg in regs:
        for fid, entry in reg.items():
            done += 1
            t0 = time.time()
            try:
                series = entry.impl(panel)
                if series is None or series.len() == 0:
                    raise ValueError("空输出")
                st = eval_factor(panel, series, month_ends, fwd20)
                results.append({"lib": lib, "id": fid, "category": entry.category,
                                "direction": entry.direction, **st,
                                "err": ""})
            except Exception as e:
                results.append({"lib": lib, "id": fid, "category": entry.category,
                                "direction": entry.direction, "ic_mean": np.nan,
                                "icir": np.nan, "pos_rate": np.nan, "n": 0,
                                "err": str(e)[:80]})
            if done % 10 == 0 or done == total:
                ok = sum(1 for r in results if not r["err"])
                print(f"  [{done}/{total}] ok={ok} 最新={fid} ({time.time()-t0:.1f}s, 累计{time.time()-t_all:.0f}s)",
                      flush=True)

    with open(CACHE / "ag_ic_results.pkl", "wb") as f:
        pickle.dump(results, f)
    render(results)
    print(f"=== 完成 ({time.time()-t_all:.0f}s) ===")


def render(results):
    df = pd.DataFrame(results)
    ok = df[~df["err"].astype(bool)].copy() if "err" in df else df
    ok["abs_ic"] = ok["ic_mean"].abs()
    ok["eff_ic"] = np.where(ok["direction"] == "reverse", -ok["ic_mean"], ok["ic_mean"])
    ok = ok.sort_values("abs_ic", ascending=False)
    lines = [
        "# Alpha101 + GTJA191 本地移植评估 (298因子)",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 数据: qfq {START}~{END} 全市场白名单, vwap=(H+L+C)/3近似(amount不可用铁律)",
        f"- 评估: 月末截面 Spearman IC vs 未来20日收益(next-bar)",
        f"- 成功计算: {len(ok)}/{len(df)}",
        "",
        "## IC绝对值 Top 40",
        "",
        "| 库 | 因子 | 类别 | 方向 | IC | 有效IC | ICIR | IC正率 | 月数 |",
        "|----|------|------|------|-----|--------|------|--------|------|",
    ]
    for _, r in ok.head(40).iterrows():
        lines.append(f"| {r['lib']} | {r['id']} | {r['category']} | {r['direction']} "
                     f"| {r['ic_mean']:.4f} | {r['eff_ic']:.4f} | {r['icir']:.2f} "
                     f"| {r['pos_rate']:.0%} | {r['n']} |")
    # 分库统计
    lines += ["", "## 分库统计", ""]
    for lib in ("alpha101", "gtja191"):
        sub = ok[ok["lib"] == lib]
        if len(sub):
            strong = sub[sub["abs_ic"] > 0.03]
            lines.append(f"- **{lib}**: {len(sub)}个成功, |IC|>0.03共{len(strong)}个, "
                         f"IC中位数{sub['ic_mean'].abs().median():.4f}")
    # 失败清单
    errs = df[df["err"].astype(bool)] if "err" in df else pd.DataFrame()
    if len(errs):
        lines += ["", f"## 计算失败 ({len(errs)}个)", ""]
        for _, r in errs.head(20).iterrows():
            lines.append(f"- {r['lib']}/{r['id']}: {r['err']}")
    # 下一步: Top因子过完整三关
    lines += ["", "## 后续",
              "",
              f"- |IC|>0.03 且 ICIR>0.4 且 n>=60 的因子进入第二关(五分层+WF回测)",
              f"- 候选数: {len(ok[(ok['abs_ic']>0.03)&(ok['icir'].abs()>0.4)&(ok['n']>=60)])}",
              ""]
    p = REPORT / f"ALPHA_GTJA_LOCAL_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")


if __name__ == "__main__":
    main()
