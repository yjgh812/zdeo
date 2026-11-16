# -*- coding: utf-8 -*-
"""forward_up_prob_gauge.py — 持仓/标的 前向上涨概率仪表 (观测层, 零交易权)

目的: 回应「找出的股票上涨概率太低」——把"上涨归因"从事后描述变成事前概率输出。
方法: T日收盘口径画像分组(与 reports/up_prob_recon_20260924.md 同一口径) x 滚动历史窗口
      内各组的 T+1 上涨概率/超额(控制当日全市场上涨占比)/T+5 表现, 对持仓逐一打分。
性质: 观测仪表, 只读计算, 不产生任何交易信号或买卖建议; 「零期望占用候选」仅为
      呈报标签, 处置权在主人。

用法:
    python forward_up_prob_gauge.py                    # 默认=data/swing_phase_holdings_latest.json 持仓
    python forward_up_prob_gauge.py --codes 600519,000001 [--names 贵州茅台,平安银行]

输出: 控制台表 + data/forward_up_prob_gauge_<生成日>.json (幂等覆盖同名)
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

DB_PATH = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
HOLDINGS_FILE = "data/swing_phase_holdings_latest.json"
OUT_DIR = "data"
LOAD_FROM = "20240101"          # pos250 需 250 日回看, 取 2 年余量
STAT_WINDOW_DAYS = 250          # 条件概率统计窗口(交易日)
MIN_CELL_N = 300                # 组内样本下限, 不足则「样本不足」(反臆想)


def load_holdings(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    recs = d.get("records", [])
    codes = [(r["code"], r.get("name", "")) for r in recs if r.get("code")]
    if not codes:
        raise ValueError(f"{path} 无 records.code")
    return codes


def classify(r):
    """口径与 up_prob_recon_20260924 完全一致"""
    if r.pos250 < 30 and abs(r.chg5) < 3:
        return "A低位横盘"
    if 60 <= r.pos250 < 95 and 5 <= r.chg5 < 10:
        return "B启动确认"
    if r.chg5 >= 15:
        return "C追高禁区"
    if r.pos250 >= 95 and r.chg5 < 5:
        return "D高位滞涨"
    return "R其余"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--names", default="")
    args = ap.parse_args()

    if args.codes:
        ns = args.names.split(",") if args.names else []
        cs = [c.strip()[-6:] for c in args.codes.split(",") if c.strip()]
        targets = list(zip(cs, ns + [""] * (len(cs) - len(ns))))
    else:
        targets = load_holdings(HOLDINGS_FILE)

    db = sqlite3.connect(DB_PATH, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE date>='{LOAD_FROM}' ORDER BY code,date",
        db)
    db.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    g = df.groupby("code")
    df["chg5"] = g["close"].transform(lambda s: s.pct_change(5) * 100)
    df["pos250"] = np.where(
        g["close"].transform(lambda s: s.rolling(250, min_periods=120).max())
        > g["close"].transform(lambda s: s.rolling(250, min_periods=120).min()),
        (df["close"] - g["close"].transform(lambda s: s.rolling(250, min_periods=120).min()))
        / (g["close"].transform(lambda s: s.rolling(250, min_periods=120).max())
           - g["close"].transform(lambda s: s.rolling(250, min_periods=120).min())) * 100,
        np.nan)
    df["volr"] = g["volume"].transform(lambda s: s.rolling(5).mean() / s.rolling(60).mean())
    df["fwd1_up"] = (g["close"].shift(-1) > df["close"]).astype(float)
    df["fwd1"] = g["close"].shift(-1) / df["close"] - 1
    df["fwd5"] = g["close"].shift(-5) / df["close"] - 1
    # T+20 可实现口径: 收盘入场, 持有至 T+20; 期间任一日(T+1起)收盘价跌破入场价×0.92
    # 则以该日收盘止损(S-5 口径为盘中触发即卖, 此处用收盘价模拟=保守偏差, 已在局限标注)
    entry = df["close"]
    stop_line = entry * 0.92
    stop_px = pd.Series(np.nan, index=df.index)
    for k in range(1, 21):
        px = g["close"].shift(-k)
        stop_px = stop_px.where(stop_px.notna() | ~(px <= stop_line), px)
    df["t20_exit"] = g["close"].shift(-20)
    df["t20e"] = np.where(stop_px.notna(), stop_px / entry - 1,
                          df["t20_exit"] / entry - 1)
    df["t20_stopped"] = stop_px.notna() & df["t20_exit"].notna()
    # 停牌删失处理: 剩余交易日不足20根(T+20完整视野不存在)的样本剔除, 不允许仅止损分支单边计入
    df["t20e"] = df["t20e"].where(df["t20_exit"].notna())
    df["grp"] = df.apply(classify, axis=1)

    stat = df[df["pos250"].notna() & df["chg5"].notna()].copy()
    stat_dates = sorted(stat["date"].unique())[-STAT_WINDOW_DAYS:]
    stat = stat[stat["date"].isin(stat_dates)].copy()
    mkt_up = stat.groupby("date")["fwd1_up"].mean().rename("mkt_up")
    stat = stat.join(mkt_up, on="date")
    stat["excess"] = stat["fwd1_up"] - stat["mkt_up"]

    asof_feat = df["date"].max()          # 特征日=库内最新交易日
    stat_end = stat_dates[-1]             # 统计窗口末日
    t20_end = stat[stat["t20e"].notna()]["date"].max()  # T+20样本实际覆盖末 Feature日
    cells = {}
    for k, s in stat.groupby("grp"):
        ok = len(s) >= MIN_CELL_N
        t20 = s[s["t20e"].notna()]
        ok20 = len(t20) >= MIN_CELL_N
        cells[k] = {
            "n": int(len(s)),
            "t1_win_pct": round(s["fwd1_up"].mean() * 100, 1) if ok else None,
            "excess_pp": round(s["excess"].mean() * 100, 2) if ok else None,
            "t1_med_pct": round(s["fwd1"].median() * 100, 2) if ok else None,
            "t5_med_pct": round(s["fwd5"].median() * 100, 2) if ok else None,
            "t5_win_pct": round((s["fwd5"] > 0).mean() * 100, 1) if ok else None,
            "t20e_n": int(len(t20)),
            "t20e_mean_pct": round(t20["t20e"].mean() * 100, 2) if ok20 else None,
            "t20e_med_pct": round(t20["t20e"].median() * 100, 2) if ok20 else None,
            "t20e_win_pct": round((t20["t20e"] > 0).mean() * 100, 1) if ok20 else None,
            "t20e_stop_pct": round(t20["t20_stopped"].mean() * 100, 1) if ok20 else None,
            "sample_ok": ok, "sample_ok20": ok20,
        }

    snap = df[df["date"] == asof_feat].set_index("code")
    rows = []
    for code, name in targets:
        if code not in snap.index:
            rows.append({"code": code, "name": name, "status": "N/A(停牌或库缺)"})
            continue
        r = snap.loc[code]
        c = cells.get(r["grp"], {})
        if not c.get("sample_ok20"):
            verdict = "样本不足"
        elif c["t20e_med_pct"] is not None and c["t20e_med_pct"] > 0:
            verdict = "正期望倾向"
        else:
            verdict = "零/负期望占用候选"
        rows.append({
            "code": code, "name": name, "close": float(r["close"]),
            "chg5_pct": round(float(r["chg5"]), 2), "pos250": round(float(r["pos250"]), 1),
            "volr": round(float(r["volr"]), 2) if pd.notna(r["volr"]) else None,
            "grp": r["grp"], "cell": c, "verdict": verdict,
        })

    rows_ok = [r for r in rows if r.get("verdict") not in (None,) and "N/A" not in str(r.get("status", ""))]
    rows_ok.sort(key=lambda x: (x.get("cell", {}).get("excess_pp") is not None,
                                x.get("cell", {}).get("excess_pp") or 0))
    n_zero = sum(1 for r in rows_ok if r.get("verdict") == "零/负期望占用候选")

    def fmt(v, w=9, suf=""):
        return (f"{v}{suf}".rjust(w)) if v is not None else "-".rjust(w)

    print(f"前向上涨概率仪表 v2 | 特征日={asof_feat} 统计窗口={stat_dates[0]}~{stat_end}({len(stat_dates)}交易日) "
          f"T+20样本覆盖至={t20_end} 目标数={len(targets)}")
    print(f"{'代码':<8}{'名称':<9}{'画像组':<9}{'T20En':>8}{'T20E均%':>8}{'T20E中%':>8}{'T20E胜%':>8}{'止损%':>7}{'T+1超额pp':>10}  判定")
    for r in rows:
        c = r.get("cell") or {}
        print(f"{r['code']:<8}{(r['name'] or '-')[:8]:<9}{r.get('grp', '-')[:8]:<9}"
              f"{fmt(c.get('t20e_n'), 8)}{fmt(c.get('t20e_mean_pct'), 8)}{fmt(c.get('t20e_med_pct'), 8)}"
              f"{fmt(c.get('t20e_win_pct'), 8)}{fmt(c.get('t20e_stop_pct'), 7)}"
              f"{fmt(c.get('excess_pp'), 10)}  {r.get('verdict') or r.get('status')}")
    print(f"\n汇总: 正期望倾向 {sum(1 for r in rows_ok if r['verdict']=='正期望倾向')} / "
          f"零/负期望占用候选 {n_zero} / 样本不足 {sum(1 for r in rows_ok if r['verdict']=='样本不足')} / "
          f"N/A {len(rows)-len(rows_ok)}")
    print("主判据=T20E(收盘入场持有20日,期间收盘跌破入场价×0.92则当日收盘止损的可实现历史期望);")
    print("S-5实际为盘中触发即卖,本模拟用收盘价=口径偏保守;判定为观测标签零交易权,处置权在主人。")

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "v2_t20e",
        "asof_feature_date": asof_feat, "stat_window": [stat_dates[0], stat_end],
        "stat_days": len(stat_dates),
        "t20e_coverage_end": t20_end, "min_cell_n": MIN_CELL_N,
        "stop_rule": "收盘入场×0.92, T+1起任一日收盘跌破则该日收盘止损, 否则持有至T+20收盘(模拟口径, S-5实盘为盘中触发)",
        "cells": cells, "targets": rows,
        "summary": {"n_target": len(targets), "n_pos": sum(1 for r in rows_ok if r["verdict"] == "正期望倾向"),
                    "n_zero": n_zero,
                    "n_insufficient": sum(1 for r in rows_ok if r["verdict"] == "样本不足"),
                    "n_na": len(rows) - len(rows_ok)},
        "disclaimer": "观测仪表零交易权; 条件概率=历史窗口统计非预测; 处置权在主人",
    }
    outp = f"{OUT_DIR}/forward_up_prob_gauge_{datetime.now().strftime('%Y%m%d')}.json"
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("落盘:", outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
