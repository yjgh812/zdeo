# -*- coding: utf-8 -*-
"""
验证"别人怎么做"：SMC 扫荡后等 displacement（放量反包）确认再入场
对比 A(无确认=次日直接买, S2现状) vs B(displacement确认=放量阳线反包后次日买)
规则(严格交易): 涨停/跌停过滤, T+1, 20根持有, 卖出跌停顺延, 扣0.3%成本
displacement 定义(来自 SMC 实操): 扫荡后 1-3 日内出现 close>流动性水平 且 volume>前5日均量×1.2
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
mkt = df.groupby("date")["change_pct"].mean().div(100).sort_index()
mkt_bull = (mkt > mkt.rolling(60).mean()).astype(int)
df["mkt"] = df["date"].map(mkt_bull)


def limit_up_pct(code):
    c = str(code)
    if c.startswith(("300", "301", "688")):
        return 19.8
    if c.startswith(("8", "4", "92")):
        return 29.8
    return 9.8


COST = 0.003
tradesA = []   # 无确认（次日直接买）
tradesB = []   # displacement 确认（放量反包后次日买）
for code, o in df.groupby("code"):
    o = o.sort_values("date").reset_index(drop=True)
    if len(o) < 80:
        continue
    ohlc = o[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
    try:
        shl = smc.swing_highs_lows(ohlc, swing_length=16)
        liq = smc.liquidity(ohlc, shl, range_percent=0.01)
    except Exception:
        continue
    lu = limit_up_pct(code)
    vol5 = o["volume"].rolling(5).mean()
    for i in np.where(liq["Liquidity"].values == -1)[0]:
        swept = int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
        if swept <= 0 or swept + 3 >= len(o):
            continue
        lvl = liq["Level"].iloc[i]
        if not lvl or np.isnan(lvl):
            continue

        def sim(buy_idx):
            if o["change_pct"].iloc[buy_idx] >= lu or o["change_pct"].iloc[buy_idx] <= -9.8:
                return None
            if buy_idx + 20 >= len(o):
                return None
            sell_idx = buy_idx + 20
            while sell_idx < len(o) and o["change_pct"].iloc[sell_idx] <= -9.8:
                sell_idx += 1
            if sell_idx >= len(o):
                return None
            bp = o["open"].iloc[buy_idx]; sp = o["open"].iloc[sell_idx]
            if bp <= 0 or sp <= 0:
                return None
            return (code, o["date"].iloc[buy_idx], sp / bp - 1 - COST, o["mkt"].iloc[buy_idx])

        # A: 扫荡次日直接买
        ra = sim(swept + 1)
        if ra:
            tradesA.append(ra)
        # B: 找 displacement（扫荡后1-3日内放量阳线反包）
        conf = None
        for j in range(swept + 1, swept + 4):
            if j >= len(o):
                break
            if o["close"].iloc[j] > lvl and o["volume"].iloc[j] > vol5.iloc[j] * 1.2:
                conf = j
                break
        if conf is not None:
            rb = sim(conf + 1)
            if rb:
                tradesB.append(rb)

A = pd.DataFrame(tradesA, columns=["code", "buy_date", "ret", "mkt"])
B = pd.DataFrame(tradesB, columns=["code", "buy_date", "ret", "mkt"])
print(f"\nA 无确认: {len(A)} 笔 均值{A['ret'].mean()*100:.2f}% 胜率{(A['ret']>0).mean()*100:.1f}%")
print(f"B displacement确认: {len(B)} 笔 均值{B['ret'].mean()*100:.2f}% 胜率{(B['ret']>0).mean()*100:.1f}%")


def combo(T, name, K=5):
    if len(T) < 200:
        print(f"  {name}: 样本不足({len(T)})"); return
    sel = T.sort_values(["buy_date"]).groupby("buy_date").head(K)
    m = sel.groupby(sel["buy_date"].str[:6])["ret"].mean()
    sharpe = m.mean() / m.std() * np.sqrt(12) if m.std() > 0 else 0
    print(f"  {name}: n={len(sel)} 月均{m.mean()*100:.2f}% Sharpe{sharpe:.2f} 年化{m.mean()*12*100:.1f}% 胜率{(sel['ret']>0).mean()*100:.1f}%")


print("\n== 组合对比（每天选前K只, 按日期顺序） ==")
combo(A, "A 无确认(现状)")
combo(B, "B displacement确认")
# 分年
for nm, T in [("A", A), ("B", B)]:
    T["year"] = T["buy_date"].str[:4]
    yrs = T.groupby("year")["ret"].mean() * 100
    print(f"  {nm} 分年均值:", " ".join(f"{y}:{v:.1f}%" for y, v in yrs.items()))
print(f"\n耗时 {time.time()-t0:.0f}s")
