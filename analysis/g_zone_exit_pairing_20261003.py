# -*- coding: utf-8 -*-
"""G×强档(唯一放行买入格) × 6种退出规则 路径模拟配对回测
事件: 黄金低吸区新进入 × 当日涨停家数>=61 (2021-09~2026-09, n≈16.5k)
退出规则(全部收盘执行, 最长持有20bar):
  T5/T10/T20固定 | S5止损(收<=入*0.92)否则T20 | MA10 Trail(收<MA10出,缓冲2bar)
  爆量盈出(vol>=2x均量且盈利)合S5 | 强中卖(单日涨>=7%且盈利)合S5 | 组合=S5+爆量盈+T20
"""
import sqlite3

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, r"D:\zcodesj\analysis")
from buy_sell_zone_engine_20261003 import load, build_features, classify  # noqa: E402

df = build_features(load())
df = df[df["bar_n"] >= 60].reset_index(drop=True)
df = classify(df)
lim = df[df["change_pct"] >= 9.5].groupby("date").size().rename("limit_cnt")
df = df.merge(lim, left_on="date", right_index=True, how="left")
ev = df[(df["zone_new"]) & (df["zone"] == "G") & (df["limit_cnt"] >= 61) & df["f20"].notna()]
print("G×强档 events:", len(ev))

# 按code建数组索引
groups = {c: g.reset_index(drop=True) for c, g in df.groupby("code", sort=False)}


def simulate(e, rule):
    g = groups[e["code"]]
    # 找事件行位置(g有序, 用date匹配)
    i_arr = np.where(g["date"].values == e["date"])[0]
    if len(i_arr) == 0:
        return None
    i = int(i_arr[0])
    entry = g["close"].iat[i]
    n = len(g)
    vol_ma = g["vol20"].values  # 已shift(1)的20日均量
    for k in range(1, 21):
        j = i + k
        if j >= n:
            return g["close"].iat[n - 1] / entry - 1, 20
        c = g["close"].iat[j]
        ret_now = c / entry - 1
        if rule == "T5" and k == 5:
            return ret_now, k
        if rule == "T10" and k == 10:
            return ret_now, k
        if rule == "T20" and k == 20:
            return ret_now, k
        stop = c <= entry * 0.92
        if rule == "S5":
            if stop:
                return ret_now, k
        elif rule == "MA10":
            if k >= 2 and c < g["ma10"].iat[j]:
                return ret_now, k
        elif rule == "VOL":
            if stop:
                return ret_now, k
            if g["volume"].iat[j] >= 2 * (vol_ma[j] or np.inf) and ret_now > 0:
                return ret_now, k
        elif rule == "STRONG":
            if stop:
                return ret_now, k
            if (g["change_pct"].iat[j] or 0) >= 7 and ret_now > 0:
                return ret_now, k
        elif rule == "COMBO":
            if stop:
                return ret_now, k
            if g["volume"].iat[j] >= 2 * (vol_ma[j] or np.inf) and ret_now > 0:
                return ret_now, k
    c = g["close"].iat[min(i + 20, n - 1)]
    return c / entry - 1, 20


rows = []
for rule in ("T5", "T10", "T20", "S5", "MA10", "VOL", "STRONG", "COMBO"):
    rets, bars = [], []
    for _, e in ev.iterrows():
        r = simulate(e, rule)
        if r is None:
            continue
        rets.append(r[0] * 100)
        bars.append(r[1])
    s = pd.Series(rets)
    gp = s[s > 0].mean()
    gl = abs(s[s < 0].mean())
    rows.append((rule, len(s), (s > 0).mean() * 100, s.median(), s.mean(), gp / gl if gl else np.nan,
                 pd.Series(bars).median(), (s <= -8).mean() * 100))
    print(f"{rule:7s} n={len(s):6d} win={(s>0).mean()*100:5.1f}% med={s.median():+6.2f}% "
          f"mean={s.mean():+6.2f}% RR={gp/gl:4.2f} bars_med={pd.Series(bars).median():.0f} "
          f"深亏<=8%占比={(s<=-8).mean()*100:.1f}%")

out = pd.DataFrame(rows, columns=["rule", "n", "win%", "med%", "mean%", "RR", "bars_med", "deep_loss%"])
out.to_csv(r"D:\zcodesj\reports\g_zone_exit_pairing_20261003.csv", index=False, encoding="utf-8-sig")
print("saved csv")
