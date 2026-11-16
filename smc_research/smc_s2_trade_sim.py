# -*- coding: utf-8 -*-
"""
S2 SSL扫荡做多 —— 严格交易化重估（全市场 2018-2026）
规则：
  买入 = swept(扫荡日)后第一根K线 open；若买入日涨停/跌停则跳过；T+1天然满足
  持有 = 未来20根K线(跳过停牌, qfq无停牌日即天然跳过)
  卖出 = 第20根K线 open；若卖出日跌停(卖不出)则顺延到下一根
  成本 = 单边0.15%×2 = 0.3%
输出：交易明细 + 期望/胜率/分年/分市 + 持仓重叠检查
"""
import sqlite3
import time
import warnings
import numpy as np
import pandas as pd
from smartmoneyconcepts import smc

warnings.filterwarnings("ignore")

t0 = time.time()
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq WHERE date>='20180101' ORDER BY code,date", con)
con.close()
df["date"] = df["date"].astype(str)
print(f"读入 {len(df)} 行 {time.time()-t0:.0f}s")


def limit_up_pct(code):
    c = str(code)
    if c.startswith(("300", "301", "688")):
        return 19.8
    if c.startswith(("8", "4", "92")):
        return 29.8
    return 9.8


COST = 0.003
trades = []
t1 = time.time()
done = 0
for code, o in df.groupby("code"):
    o = o.sort_values("date").reset_index(drop=True)
    if len(o) < 60:
        continue
    ohlc = o[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
    try:
        shl = smc.swing_highs_lows(ohlc, swing_length=16)
        liq = smc.liquidity(ohlc, shl, range_percent=0.01)
    except Exception:
        continue
    lu = limit_up_pct(code)
    for i in np.where(liq["Liquidity"].values == -1)[0]:
        swept = int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
        if swept <= 0 or swept + 1 >= len(o):
            continue
        buy_idx = swept + 1
        # 买入日涨停(买不进)或跌停(封死)跳过
        if o["change_pct"].iloc[buy_idx] >= lu or o["change_pct"].iloc[buy_idx] <= -9.8:
            continue
        buy_px = o["open"].iloc[buy_idx]
        buy_date = o["date"].iloc[buy_idx]
        if buy_px <= 0:
            continue
        # 持有20根(未来非停牌即o中后续行)
        if buy_idx + 20 >= len(o):
            continue
        sell_idx = buy_idx + 20
        # 卖出日跌停顺延
        while sell_idx < len(o) and o["change_pct"].iloc[sell_idx] <= -9.8:
            sell_idx += 1
        if sell_idx >= len(o):
            continue
        sell_px = o["open"].iloc[sell_idx]
        sell_date = o["date"].iloc[sell_idx]
        if sell_px <= 0:
            continue
        ret = sell_px / buy_px - 1 - COST
        r20 = o["close"].iloc[buy_idx] / o["close"].iloc[buy_idx - 20] - 1 if buy_idx >= 20 else np.nan
        trades.append((code, buy_date, sell_date, ret, r20))
    done += 1
    if done % 1500 == 0:
        print(f"  {done}只 {time.time()-t1:.0f}s", flush=True)

T = pd.DataFrame(trades, columns=["code", "buy_date", "sell_date", "ret", "ret20"])
print(f"\n有效交易 {len(T)} 笔, 耗时{time.time()-t0:.0f}s")
print("== 全市场交易化重估(扣0.3%成本) ==")
print(f"  期望收益均值 {T['ret'].mean()*100:.2f}% 中位 {T['ret'].median()*100:.2f}% 胜率 {(T['ret']>0).mean()*100:.1f}%")
T["year"] = T["buy_date"].str[:4]
print("\n分年(均值/中位):")
print(T.groupby("year")["ret"].agg(["count", "mean", "median"]).round(4).to_string())
wins = (T["ret"] > 0).groupby(T["year"]).mean() * 100
print("分年胜率:", " ".join(f"{y}:{v:.0f}%" for y, v in wins.items()))
print("\n== 持仓重叠粗查(同日多信号买入占比) ==")
print(f"  {T['buy_date'].duplicated().mean()*100:.1f}%")
T.to_csv("smc_s2_trades.csv", index=False)
print("交易明细存 smc_s2_trades.csv")
