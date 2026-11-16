# -*- coding: utf-8 -*-
"""T1执行时点研究: 爆量末段信号(强势股持仓的退出场景)后, 三个卖出时点的差异
事件: pos60>=80 & volr>=2.0 & ret20>=15% (爆量末段代理, 与波段相位END特征同族)
口径: 当日收盘卖=0基线; 次日开盘卖=open[t+1]/close[t]-1; 次日收盘卖=close[t+1]/close[t]-1
另测: 高开占比, 开盘后走弱占比(次日收盘<次日开盘), 5日内最大次日差
"""
import sqlite3

import numpy as np
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20220401' AND date<='20260930' "
    "AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')",
    conn)
conn.close()
df["date"] = df["date"].astype(int)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)
df["low60"] = g["low"].transform(lambda s: s.rolling(60, min_periods=40).min())
df["high60"] = g["high"].transform(lambda s: s.rolling(60, min_periods=40).max())
df["pos60"] = (df["close"] - df["low60"]) / (df["high60"] - df["low60"]) * 100
df["ret20"] = g["close"].transform(lambda s: s.pct_change(20)) * 100
df["vol5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean().shift(1))
df["volr"] = df["volume"] / df["vol5"]
df["bar_n"] = g.cumcount()
df = df[df["bar_n"] >= 60]
df["o1"] = g["open"].transform(lambda s: s.shift(-1))
df["c1"] = g["close"].transform(lambda s: s.shift(-1))
df["c5"] = g["close"].transform(lambda s: s.shift(-5))

sig = (df["pos60"] >= 80) & (df["volr"] >= 2.0) & (df["ret20"] >= 15)
# 新进入: 前一日不满足(避免连续信号重复)
sig_prev = sig.shift(1).fillna(False)
ev = df[sig & (~sig_prev) & df["o1"].notna() & df["c5"].notna()].copy()
print("events:", len(ev))

ev["ret_open1"] = ev["o1"] / ev["close"] - 1
ev["ret_close1"] = ev["c1"] / ev["close"] - 1
ev["fade1"] = ev["c1"] / ev["o1"] - 1
ev["ret_5d"] = ev["c5"] / ev["close"] - 1

def line(s, label):
    print(f"{label:24s} mean={s.mean()*100:+6.2f}%  median={s.median()*100:+6.2f}%  正占比={(s>0).mean()*100:5.1f}%")

print("=== 爆量末段信号后(n=%d) ===" % len(ev))
line(ev["ret_open1"], "次日开盘卖 vs 当日收盘")
line(ev["ret_close1"], "次日收盘卖 vs 当日收盘")
line(ev["fade1"], "次日开盘->次日收盘(开盘后)")
line(ev["ret_5d"], "持有多5日 vs 当日收盘")
print(f"高开占比(open1>close0): {(ev['ret_open1']>0).mean()*100:.1f}%")
print(f"高开且收盘仍高于昨收: {((ev['ret_open1']>0)&(ev['ret_close1']>0)).mean()*100:.1f}%")
print(f"低开占比: {(ev['ret_open1']<=0).mean()*100:.1f}%  低开后再走弱(收<开<昨收): {((ev['ret_open1']<=0)&(ev['fade1']<0)).mean()*100:.1f}%")
print()
print("=== 分年(次日开盘卖差值中位) ===")
ev["year"] = (ev["date"] // 10000).astype(int)
for y, sub in ev.groupby("year"):
    print(f"{y}: n={len(sub):6d} 次开差中位={sub['ret_open1'].median()*100:+.2f}%  次收差中位={sub['ret_close1'].median()*100:+.2f}%  高开占比={(sub['ret_open1']>0).mean()*100:.0f}%")
ev[["code", "date", "pos60", "volr", "ret20", "ret_open1", "ret_close1", "fade1", "ret_5d"]].to_csv(
    r"D:\zcodesj\reports\t1_exit_timing_events_20261003.csv", index=False, encoding="utf-8-sig")
print("saved csv")
