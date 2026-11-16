# -*- coding: utf-8 -*-
"""只读探针(20261021): forward_up_prob_gauge 组分类判别力 + 月度稳定性检验。

问题: gauge 最新缓存"B启动确认"组 T+1 超额 -1.47pp 是结构性、相位依赖还是噪声?
口径: 逐字段镜像 forward_up_prob_gauge.py L69-111(词法同源, classify 直接 import 防漂移):
  宇宙=code[0]∈{6,0,3} 上市>60日; pos250=(c-min250)/(max250-min250)*100(rolling250,min120);
  chg5=pct_change(5)*100; fwd1_up=次日收盘>今收(收盘口径非next-bar, 与gauge一致);
  t20e=收盘入场, T+1起任一日收盘<=入场*0.92则当日收盘止损, 否则持有T+20收盘; 不足20根删失。
  统计窗=最后250个交易日; excess=当日组上涨率-当日全市场上涨率。
预注册判定规则(跑前写死):
  B组月度负超额占比>=0.6 且 上下半窗同负 → 结构性(窗口内稳健);
  B组月度负超额占比>=0.6 但半窗分歧     → 相位依赖倾向;
  B组月度负超额占比<0.6                 → 集中于少数月份(相位噪声为主);
  全窗超额 Bootstrap95%CI 跨0           → 幅度不显著(叠加标注)。
"""
import json
import sys

import numpy as np
import pandas as pd
import sqlite3

sys.path.insert(0, r"D:\zcodesj")
from forward_up_prob_gauge import classify  # noqa: E402  组分类唯一真源

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\gauge_discrim_probe_20261021_result.json"
STAT_WINDOW_DAYS = 250
BOOT = 500


def main():
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20240101' ORDER BY code,date", con)
    con.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    print(f"[load] 行数={len(df)} 股票数={df['code'].nunique()}")

    g = df.groupby("code")
    df["chg5"] = g["close"].transform(lambda s: s.pct_change(5) * 100)
    mn = g["close"].transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g["close"].transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)

    df["fwd1_up"] = (g["close"].shift(-1) > df["close"]).astype(float)
    df["fwd1"] = g["close"].shift(-1) / df["close"] - 1
    df["fwd5"] = g["close"].shift(-5) / df["close"] - 1
    entry = df["close"]
    stop_line = entry * 0.92
    stop_px = pd.Series(np.nan, index=df.index)
    for k in range(1, 21):
        px = g["close"].shift(-k)
        stop_px = stop_px.where(stop_px.notna() | ~(px <= stop_line), px)
    df["t20_exit"] = g["close"].shift(-20)
    df["t20e"] = np.where(stop_px.notna(), stop_px / entry - 1, df["t20_exit"] / entry - 1)
    df["t20_stopped"] = stop_px.notna() & df["t20_exit"].notna()
    df["t20e"] = df["t20e"].where(df["t20_exit"].notna())

    # 向量化 classify(与 forward_up_prob_gauge.classify 逐分支等价; NaN→R 与原实现一致)
    c = np.select(
        [
            (df["pos250"] < 30) & (df["chg5"].abs() < 3),
            (df["pos250"] >= 60) & (df["pos250"] < 95) & (df["chg5"] >= 5) & (df["chg5"] < 10),
            df["chg5"] >= 15,
            (df["pos250"] >= 95) & (df["chg5"] < 5),
        ],
        ["A低位横盘", "B启动确认", "C追高禁区", "D高位滞涨"],
        default="R其余",
    )
    df["grp"] = c

    stat = df[df["pos250"].notna() & df["chg5"].notna()].copy()
    stat_dates = sorted(stat["date"].unique())[-STAT_WINDOW_DAYS:]
    stat = stat[stat["date"].isin(stat_dates)].copy()
    mkt_up = stat.groupby("date")["fwd1_up"].mean().rename("mkt_up")
    stat = stat.join(mkt_up, on="date")
    stat["excess"] = stat["fwd1_up"] - stat["mkt_up"]
    stat["ym"] = stat["date"].astype(str).str[:6]
    base_up = stat["fwd1_up"].mean()
    print(f"[window] 统计窗 {stat_dates[0]}→{stat_dates[-1]} ({len(stat_dates)}日) 样本 n={len(stat)} 基准T+1上涨率={base_up:.1%}")

    rng = np.random.default_rng(20261021)
    cells_out = {}
    dates = np.array(sorted(stat["date"].unique()))
    half = len(dates) // 2
    for k, s in stat.groupby("grp"):
        ok = len(s) >= 300
        t20 = s[s["t20e"].notna()]
        # 月度
        mon = s.groupby("ym").agg(n=("excess", "size"), win=("fwd1_up", "mean"),
                                  exc=("excess", "mean"))
        neg_ratio = float((mon["exc"] < 0).mean()) if len(mon) else np.nan
        # 半窗
        dset = set(dates)
        h1 = s[s["date"].isin(set(dates[:half]))]
        h2 = s[s["date"].isin(set(dates[half:]))]
        # Bootstrap CI(按日重采样)
        dser = s.groupby("date")["excess"].mean()
        dser = dser.reindex(dates).dropna()
        arr = dser.values
        boots = np.empty(BOOT)
        for b in range(BOOT):
            boots[b] = arr[rng.integers(0, len(arr), len(arr))].mean()
        ci = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))
        cells_out[k] = {
            "n": int(len(s)), "n_days": int(len(dser)),
            "t1_win": round(float(s["fwd1_up"].mean()), 4) if ok else None,
            "excess_pp": round(float(s["excess"].mean()) * 100, 2) if ok else None,
            "boot_ci95_pp": [round(ci[0] * 100, 2), round(ci[1] * 100, 2)],
            "ci_exclude_zero": bool(ci[0] > 0 or ci[1] < 0),
            "t5_win": round(float((s["fwd5"] > 0).mean()), 4) if ok else None,
            "t20e_n": int(len(t20)),
            "t20e_med_pp": round(float(t20["t20e"].median()) * 100, 2) if len(t20) >= 300 else None,
            "t20e_stop_pct": round(float(t20["t20_stopped"].mean()) * 100, 1) if len(t20) >= 300 else None,
            "months": int(len(mon)),
            "months_neg_ratio": round(neg_ratio, 3) if not np.isnan(neg_ratio) else None,
            "h1_excess_pp": round(float(h1["excess"].mean()) * 100, 2) if len(h1) else None,
            "h2_excess_pp": round(float(h2["excess"].mean()) * 100, 2) if len(h2) else None,
            "monthly": {ym: {"n": int(r["n"]), "win": round(float(r["win"]), 3), "exc_pp": round(float(r["exc"]) * 100, 2)}
                        for ym, r in mon.iterrows()},
        }

    # 预注册判定(B组)
    b = cells_out.get("B启动确认", {})
    verdict = None
    if b:
        nr = b["months_neg_ratio"]
        halves = (b["h1_excess_pp"] is not None and b["h2_excess_pp"] is not None
                  and b["h1_excess_pp"] < 0 and b["h2_excess_pp"] < 0)
        if nr is not None and nr >= 0.6 and halves:
            verdict = "结构性(窗口内稳健)"
        elif nr is not None and nr >= 0.6:
            verdict = "相位依赖倾向"
        else:
            verdict = "集中于少数月份(相位噪声为主)"
        if not b["ci_exclude_zero"]:
            verdict += "; 幅度不显著(CI跨0)"

    result = {
        "probe": "gauge_discrim", "date": "2026-10-21",
        "window": [stat_dates[0], stat_dates[-1]], "stat_days": len(stat_dates),
        "n_samples": int(len(stat)), "base_t1_up": round(float(base_up), 4),
        "cells": cells_out, "B_verdict": verdict,
        "mirror_check": "与gauge缓存同口径; 全窗数值应接近 forward_up_prob_gauge_20261020.json",
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT}\n")

    print(f"{'组':<8}{'n':>8}{'T+1胜率':>8}{'超额pp':>8}{'Boot95%CI':>18}{'月负占比':>8}{'上半':>7}{'下半':>7}{'T20E中%':>8}{'止损%':>7}")
    for k, v in cells_out.items():
        ci = f"[{v['boot_ci95_pp'][0]:+.2f},{v['boot_ci95_pp'][1]:+.2f}]"
        print(f"{k:<8}{v['n']:>8}{v['t1_win']:>8.1%}{v['excess_pp']:>+8.2f}{ci:>18}"
              f"{v['months_neg_ratio']:>8.2f}{v['h1_excess_pp']:>+7.2f}{v['h2_excess_pp']:>+7.2f}"
              f"{v['t20e_med_pp']:>+8.2f}{v['t20e_stop_pct']:>7.1f}")
    print(f"\n基准T+1上涨率={base_up:.1%} | B组判定: {verdict}")
    bmon = cells_out.get("B启动确认", {}).get("monthly", {})
    if bmon:
        print("B组逐月超额pp: " + "  ".join(f"{ym}:{v['exc_pp']:+.2f}(n={v['n']})" for ym, v in sorted(bmon.items())))


if __name__ == "__main__":
    sys.exit(main())
