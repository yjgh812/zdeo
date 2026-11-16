# -*- coding: utf-8 -*-
"""外部AI破译逻辑回测：缩量回调末端低位低吸（主人 2026-09-21 令「这个需要回测，看看胜率」）

信号(全部 T 日收盘可计算, 无未来函数):
  ① 前20日涨幅 ∈ [-18%, -8%]   (回调充分非崩盘)
  ② 量能趋势 < 0.7             (近10日均量/前10日均量, 缩量回调末端)
  ③ pos250 < 35%               (一年低位区)
  ④ MA纠缠                     (非 c>ma20>ma60 多头, 非 c<ma20<ma60 空头)
  ⑤ 当日量比 < 1.2             (静日, 非放量异动)
  ⑥ 当日收跌 (敏感性变体)
执行: T+1 开盘买入(next-bar 可执行), T+N 收盘卖出(N=5/10/20)
对照: A=同条件但多头排列(我方v5.2口径)  B=同期全市场随机基线(同日全体信号均值)
过滤: 沪深主板+创业板, 上市≥120日, 收盘≤30元, 剔ST/科创/北交(code前缀)
区间: 2021-09 ~ 2026-09 (5年, 含牛熊)
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import sqlite3

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"


def load() -> pd.DataFrame:
    con = sqlite3.connect(DB, uri=True)
    df = pd.read_sql(
        "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>=20200901 AND date<=20260918", con)
    con.close()
    df = df[pd.to_numeric(df["close"], errors="coerce").notna()]
    for c in ("open", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # 剔科创(688/689)/北交(4/8开头非4xx保留: 8开头北交/老三板一律剔)/退市标识不做(历史含退市=真实存活偏差偏保守)
    ok_pre = df["code"].str.match(r"^(60|00|30)")
    df = df[ok_pre].sort_values(["code", "date"]).reset_index(drop=True)
    return df


def build_signals(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("code", sort=False)
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20).mean())
    df["ma60"] = g["close"].transform(lambda s: s.rolling(60).mean())
    df["lo250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=120).min())
    df["hi250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=120).max())
    df["ret20"] = g["close"].transform(lambda s: s.pct_change(20))
    df["v_ma10"] = g["volume"].transform(lambda s: s.rolling(10).mean())
    df["v_prev10"] = g["volume"].transform(lambda s: s.shift(10).rolling(10).mean())
    df["v5"] = g["volume"].transform(lambda s: s.rolling(5).mean())
    df["n_hist"] = g.cumcount()
    df["pos250"] = (df["close"] - df["lo250"]) / (df["hi250"] - df["lo250"])
    df["vol_trend"] = df["v_ma10"] / df["v_prev10"]
    df["vr_day"] = df["volume"] / df["v5"]
    df["chg1"] = g["close"].transform(lambda s: s.pct_change())
    # 次日开盘(next-bar)
    df["next_open"] = g["open"].transform(lambda s: s.shift(-1))
    # T+N 收盘
    for n in (5, 10, 20):
        df[f"exit{n}"] = g["close"].transform(lambda s, n=n: s.shift(-n))
    base = (
        (df["n_hist"] >= 120) & df["ret20"].notna() & df["vol_trend"].notna()
        & df["pos250"].notna() & df["ma20"].notna() & df["ma60"].notna()
        & df["next_open"].notna() & (df["close"] <= 30)
    )
    core = base & (
        (df["ret20"] >= -0.18) & (df["ret20"] <= -0.08)
        & (df["vol_trend"] < 0.7) & (df["pos250"] < 0.35)
        & ~((df["close"] > df["ma20"]) & (df["ma20"] > df["ma60"]))
        & ~((df["close"] < df["ma20"]) & (df["ma20"] < df["ma60"]))
        & (df["vr_day"] < 1.2)
    )
    df["sig_core"] = core.fillna(False)
    df["sig_down"] = (core & (df["chg1"] < 0)).fillna(False)      # 敏感性: 静日+收跌
    df["sig_bull"] = (base & (df["ret20"] >= -0.18) & (df["ret20"] <= -0.08)
                      & (df["vol_trend"] < 0.7) & (df["pos250"] < 0.35)
                      & (df["close"] > df["ma20"]) & (df["ma20"] > df["ma60"])
                      & (df["vr_day"] < 1.2)).fillna(False)        # 对照: 同条件多头排列
    return df


def evaluate(df: pd.DataFrame, mask: str, label: str) -> dict:
    s = df[df[mask] & df["exit20"].notna()].copy()
    if s.empty:
        return {"label": label, "n": 0}
    out = {"label": label, "n": len(s)}
    for n in (5, 10, 20):
        r = s[f"exit{n}"] / s["next_open"] - 1
        out[f"win{n}"] = (r > 0).mean() * 100
        out[f"mean{n}"] = r.mean() * 100
        out[f"med{n}"] = r.median() * 100
        gains, losses = r[r > 0].sum(), -r[r < 0].sum()
        out[f"pf{n}"] = gains / losses if losses > 0 else float("inf")
    s["year"] = s["date"].astype(str).str[:4]
    yr = s.groupby("year").apply(
        lambda x: pd.Series({
            "n": len(x),
            "win20": (x["exit20"] / x["next_open"] - 1 > 0).mean() * 100,
            "mean20": (x["exit20"] / x["next_open"] - 1).mean() * 100,
        }), include_groups=False)
    out["years"] = yr
    return out


def market_baseline(df: pd.DataFrame) -> dict:
    """全市场同日基线: 所有可交易日的 next-open→exit20 均值/胜率。"""
    s = df[df["exit20"].notna() & (df["n_hist"] >= 120) & (df["close"] <= 30)].copy()
    r = s["exit20"] / s["next_open"] - 1
    return {"n": len(s), "win20": (r > 0).mean() * 100, "mean20": r.mean() * 100}


def main() -> None:
    print("加载qfq数据...", flush=True)
    df = load()
    print(f"行数={len(df):,} 股票数={df['code'].nunique():,}", flush=True)
    df = build_signals(df)
    base = market_baseline(df)
    print(f"\n全市场基线(T+1开盘→T+20收盘): n={base['n']:,} 胜率{base['win20']:.1f}% 均值{base['mean20']:+.2f}%")
    for mask, label in (("sig_core", "①核心: 缩量回调末端+纠缠+静日"),
                        ("sig_down", "②敏感性: 核心+当日收跌"),
                        ("sig_bull", "③对照: 同条件但多头排列")):
        r = evaluate(df, mask, label)
        if r.get("n", 0) == 0:
            print(f"\n[{label}] 无样本")
            continue
        print(f"\n[{label}] n={r['n']:,}")
        for n in (5, 10, 20):
            print(f"  T+{n}: 胜率{r[f'win{n}']:.1f}% 均值{r[f'mean{n}']:+.2f}% 中位{r[f'med{n}']:+.2f}% PF{r[f'pf{n}']:.2f}")
        print("  分年(T+20):")
        for y, row in r["years"].iterrows():
            print(f"    {y}: n={int(row['n']):>5} 胜率{row['win20']:.1f}% 均值{row['mean20']:+.2f}%")


if __name__ == "__main__":
    main()
