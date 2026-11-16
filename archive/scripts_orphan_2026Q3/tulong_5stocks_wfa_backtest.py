#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5只热点突破候选 Walk-Forward 验证回测（2016-01-01 ~ 2026-08-24）。

目的：验证"底部盘整+放量突破"信号在 5 只候选上的历史有效性（上涨概率），
采用与系统 tulong_wfa_gate 一致的 walk-forward 口径：
  train=500 交易日 → test=60 交易日 滚动，OOS 仅取 test 窗内信号事件。

信号判据（与 tulong_hotspot_breakout_picker.is_breakout_box 完全一致）：
  横盘(前20日振幅≤25% 且 涨幅≤15%) + 放量(量比≥1.5) + 突破(收盘≥前20日高点×0.98 且 当日涨幅≥3%)
  + 上升趋势(收盘>MA20) + 未涨停 + 当日上涨。
收益口径：信号次日(T+1)开盘买入，持有 H=20 交易日收盘卖出（next-bar，无未来函数）。
价格源：dragon_quant_qfq.db（系统唯一价格正源，前复权）。

注意：301487 盟固利 2023-08 上市、603320 迪贝电气 2017-05 上市，各自区间内有效；
次新股上市初期无前20日基准，信号自然从有足够历史后开始。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

CODES = {
    "002125": "湘潭电化",
    "301487": "盟固利",
    "600740": "山西焦化",
    "002274": "华昌化工",
    "603320": "迪贝电气",
}
H = 20          # 持有交易日
TRAIN = 500     # WFA 训练窗
TEST = 60       # WFA 测试窗


def load_k(code: str) -> pd.DataFrame:
    con = sqlite3.connect("file:" + QFQ_DB + "?mode=ro", uri=True, timeout=60)
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "WHERE code=? ORDER BY date", con, params=(code,))
    con.close()
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = df["date"].astype(str)
    return df.reset_index(drop=True)


def compute_signals(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """逐日计算突破信号。返回 df 增加列 sig(0/1) 与 pct_today。"""
    n = len(df)
    sig = np.zeros(n, dtype=int)
    prev_close = df["close"].shift(1)
    pct_today = (df["close"] / prev_close - 1.0) * 100.0
    # 涨停价：300/301 → 20%，其余 10%（与选股判据一致）
    limit_rate = 20.0 if code[:3] in ("300", "301") else 10.0
    limit_price = (prev_close * (1 + limit_rate / 100.0)).round(2)
    for i in range(22, n):
        if i - 22 < 0:
            continue
        prev = df.iloc[i - 21:i]          # 前21根（不含当日）
        if len(prev) < 21:
            continue
        lo20 = prev["low"].min()
        if lo20 <= 0:
            continue
        hi20 = prev["high"].max()
        rng20 = (hi20 - lo20) / lo20 * 100.0
        chg20 = (prev["close"].iloc[-1] / prev["close"].iloc[0] - 1.0) * 100.0
        avg_vol = prev["volume"].mean()
        vr = df["volume"].iloc[i] / avg_vol if avg_vol > 0 else 0.0
        ma20 = df["close"].iloc[i - 20:i + 1].mean()
        c = df["close"].iloc[i]
        p = pct_today.iloc[i]
        if pd.isna(p):
            continue
        if rng20 <= 25.0 and chg20 <= 15.0 and vr >= 1.5 \
                and c >= hi20 * 0.98 and p >= 3.0 and c > ma20 \
                and c < limit_price.iloc[i] and p > 0:
            sig[i] = 1
    df = df.copy()
    df["sig"] = sig
    df["pct_today"] = pct_today
    return df


def event_returns(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """信号事件 → 持有H日收益（T+1 open 买入，T+H close 卖出）。"""
    sig_idx = df.index[df["sig"] == 1].tolist()
    rows = []
    n = len(df)
    for i in sig_idx:
        if i + 1 >= n or i + 1 + H > n:
            continue
        buy = df["open"].iloc[i + 1]          # 次日开盘买入
        sell = df["close"].iloc[i + 1 + H - 1]  # 持有H日后收盘卖出
        if buy <= 0 or pd.isna(buy) or pd.isna(sell):
            continue
        ret = sell / buy - 1.0
        rows.append({"date": df["date"].iloc[i], "ret": ret, "code": code})
    return pd.DataFrame(rows)


def walk_forward_oos(ev: pd.DataFrame, all_dates: pd.DatetimeIndex,
                     train: int, test: int) -> dict:
    """按 train/test 滚动，取每个 test 窗内的事件收益作为 OOS。"""
    if ev.empty:
        return {"n": 0, "mean": np.nan, "win": np.nan, "median": np.nan,
                "min": np.nan, "max": np.nan, "folds": 0, "dates": []}
    ev = ev.copy()
    ev["dt"] = pd.to_datetime(ev["date"], format="%Y%m%d")
    oos_ret = []
    oos_dates = []
    n = len(all_dates)
    start_i = 0
    folds = 0
    while start_i + train + test <= n:
        folds += 1
        te_start = all_dates[start_i + train]
        te_end = all_dates[start_i + train + test - 1]
        m = (ev["dt"] >= te_start) & (ev["dt"] <= te_end)
        sub = ev.loc[m, "ret"]
        if not sub.empty:
            oos_ret.extend(sub.tolist())
            oos_dates.extend(ev.loc[m, "date"].tolist())
        start_i += test
    oos_ret = np.array(oos_ret)
    return {
        "n": len(oos_ret),
        "mean": float(oos_ret.mean()) if len(oos_ret) else np.nan,
        "win": float((oos_ret > 0).mean()) if len(oos_ret) else np.nan,
        "median": float(np.median(oos_ret)) if len(oos_ret) else np.nan,
        "min": float(oos_ret.min()) if len(oos_ret) else np.nan,
        "max": float(oos_ret.max()) if len(oos_ret) else np.nan,
        "folds": folds,
        "dates": oos_dates,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="5只热点突破候选 WFA 回测")
    ap.add_argument("--start", default="20160101")
    ap.add_argument("--end", default="20260824")
    ap.add_argument("--train", type=int, default=TRAIN)
    ap.add_argument("--test", type=int, default=TEST)
    a = ap.parse_args()


    print("=" * 78)
    print("5只热点突破候选 · Walk-Forward 回测")
    print(f"  区间 {a.start} ~ {a.end} | 信号=底部盘整+放量突破(与选股判据一致) | "
          f"持有 {H} 交易日(T+1开盘买) | WFA train={a.train}d test={a.test}d")
    print("=" * 78)

    all_events = []
    per_stock = []
    for code, name in CODES.items():
        df = load_k(code)
        df = df[(df["date"] >= a.start) & (df["date"] <= a.end)].reset_index(drop=True)
        df["code"] = code
        if len(df) < 60:
            print(f"\n{code} {name}: 区间内数据不足({len(df)}行)，跳过")
            continue
        df = compute_signals(df, code)
        ev = event_returns(df, code)
        all_dates = pd.to_datetime(df["date"], format="%Y%m%d").reset_index(drop=True)
        oos = walk_forward_oos(ev, all_dates, a.train, a.test)
        # 全样本（非OOS）统计作对照
        full = ev["ret"] if not ev.empty else pd.Series([], dtype=float)
        per_stock.append({
            "code": code, "name": name,
            "data_start": df["date"].iloc[0], "data_end": df["date"].iloc[-1],
            "bars": len(df), "sig_n": int(df["sig"].sum()),
            "full_n": int(len(full)),
            "full_mean": float(full.mean()) if len(full) else np.nan,
            "full_win": float((full > 0).mean()) if len(full) else np.nan,
            "oos": oos,
        })
        if not ev.empty:
            ev["name"] = name
            all_events.append(ev)
        print(f"\n【{code} {name}】数据 {df['date'].iloc[0]}~{df['date'].iloc[-1]} "
              f"({len(df)}根)")
        print(f"  信号触发 {int(df['sig'].sum())} 次（可回测事件 {int(len(ev))} 次）")
        if len(full):
            print(f"  全样本{len(full)}事件: 均收益 {full.mean():+.2%}  胜率 "
                  f"{(full > 0).mean():.1%}  中位 {full.median():+.2%}")
        o = oos
        if o["n"]:
            print(f"  [WFA-OOS] {o['n']}事件 / {o['folds']}折: 均收益 {o['mean']:+.2%}  "
                  f"胜率 {o['win']:.1%}  中位 {o['median']:+.2%}  "
                  f"min {o['min']:+.2%} max {o['max']:+.2%}")
        else:
            print("  [WFA-OOS] 测试窗内无信号事件")

    # 组合 OOS（5只合并事件）
    print("\n" + "=" * 78)
    if all_events:
        pool = pd.concat(all_events, ignore_index=True)
        pool["dt"] = pd.to_datetime(pool["date"], format="%Y%m%d")
        pool = pool.sort_values("dt").reset_index(drop=True)
        rets = pool["ret"]
        print("【5只合并（事件池，全样本）】")
        print(f"  事件 {len(pool)} 次：均收益 {rets.mean():+.2%}  胜率 {(rets > 0).mean():.1%}  "
              f"中位 {rets.median():+.2%}  min {rets.min():+.2%} max {rets.max():+.2%}")
        # 按年度胜率
        pool["year"] = pool["dt"].dt.year
        print("  年度分布：")
        for y, g in pool.groupby("year"):
            print(f"    {y}: {len(g)}事件  均收益 {g['ret'].mean():+.2%}  胜率 {(g['ret'] > 0).mean():.1%}")
        # 事件级滚动 WFA：train=120 事件 / test=30 事件（事件时间序列滚动）
        n_ev = len(pool)
        tr, te = 120, 30
        oos_list, fold_cnt = [], 0
        i = 0
        while i + tr + te <= n_ev:
            fold_cnt += 1
            te_ret = pool["ret"].iloc[i + tr:i + tr + te]
            oos_list.extend(te_ret.tolist())
            i += te
        oos_arr = np.array(oos_list)
        print(f"\n【5只合并 · 事件级 WFA（train={tr}事件/test={te}事件 滚动）】")
        if len(oos_arr):
            print(f"  OOS {len(oos_arr)} 事件 / {fold_cnt} 折：均收益 {oos_arr.mean():+.2%}  "
                  f"胜率 {(oos_arr > 0).mean():.1%}  中位 {np.median(oos_arr):+.2%}")
            annual = (1 + oos_arr.mean()) ** (252 / H) - 1
            print(f"  年化(粗略, 252/持有日复利) {annual:+.1%}")
        # 排序：按 WFA-OOS 均收益（逐只 OOS，非组合切窗）
        print("\n【按上涨概率排序（逐只 WFA-OOS 平均收益，从高到低）】")
        for rank, s in enumerate(sorted(per_stock,
                                        key=lambda x: (x["oos"]["mean"] if x["oos"]["n"] else -1),
                                        reverse=True), 1):
            o = s["oos"]
            if o["n"]:
                print(f"  {rank}. {s['code']} {s['name']}: OOS均收益 {o['mean']:+.2%}  "
                      f"胜率 {o['win']:.1%}  中位 {o['median']:+.2%}  ({o['n']}事件/{o['folds']}折)")
            else:
                print(f"  {rank}. {s['code']} {s['name']}: 无OOS事件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
