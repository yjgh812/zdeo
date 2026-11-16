# -*- coding: utf-8 -*-
"""
S2 选股改进测试（克制版，防数据窥探）：
对 SSL 扫荡信号按有逻辑的分组因子做组合模拟，找稳定转正的规则。
分组因子：扫荡深度 / 收回质量 / 市场环境 / 价格 / ret20(中深 vs 最深)
组合模拟：每天选组内前 K 只等权(20日)，月度 Sharpe
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
# 市场代理(全样本等权MA60)用于买入日环境
mkt = df.groupby("date")["change_pct"].mean().div(100).sort_index()
mkt_ma60 = mkt.rolling(60).mean()
mkt_bull = (mkt > mkt_ma60).astype(int)
df["mkt"] = df["date"].map(mkt_bull)
print(f"读入 {len(df)} 行")


def limit_up_pct(code):
    c = str(code)
    if c.startswith(("300", "301", "688")):
        return 19.8
    if c.startswith(("8", "4", "92")):
        return 29.8
    return 9.8


COST = 0.003
trades = []
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
        lvl = liq["Level"].iloc[i]
        if not lvl or np.isnan(lvl):
            continue
        buy_idx = swept + 1
        if o["change_pct"].iloc[buy_idx] >= lu or o["change_pct"].iloc[buy_idx] <= -9.8:
            continue
        buy_px = o["open"].iloc[buy_idx]
        if buy_px <= 0 or buy_idx + 20 >= len(o):
            continue
        sell_idx = buy_idx + 20
        while sell_idx < len(o) and o["change_pct"].iloc[sell_idx] <= -9.8:
            sell_idx += 1
        if sell_idx >= len(o):
            continue
        sell_px = o["open"].iloc[sell_idx]
        if sell_px <= 0:
            continue
        ret = sell_px / buy_px - 1 - COST
        depth = (ohlc["low"].iloc[swept] / lvl - 1) * 100
        back_ratio = ohlc["close"].iloc[swept] / lvl  # >1 收回在水平上方
        r20 = o["close"].iloc[buy_idx] / o["close"].iloc[buy_idx - 20] - 1 if buy_idx >= 20 else np.nan
        trades.append((code, o["date"].iloc[buy_idx], ret, r20, depth, back_ratio,
                       o["mkt"].iloc[buy_idx], buy_px))

T = pd.DataFrame(trades, columns=["code", "buy_date", "ret", "ret20", "depth", "back_ratio", "mkt", "price"])
T = T.dropna(subset=["ret20"]).reset_index(drop=True)
T["year"] = T["buy_date"].str[:4]
print(f"交易 {len(T)} 笔, 全信号: 均值{T['ret'].mean()*100:.2f}% 胜率{(T['ret']>0).mean()*100:.1f}%")


def combo(mask, name, K=5):
    s = T[mask].copy()
    if len(s) < 200:
        print(f"  {name}: 样本不足({len(s)})"); return
    sel = s.sort_values(["buy_date", "ret20"]).groupby("buy_date").head(K)
    m = sel.groupby(sel["buy_date"].str[:6])["ret"].mean()
    sharpe = m.mean() / m.std() * np.sqrt(12) if m.std() > 0 else 0
    wins = (sel["ret"] > 0).groupby(sel["buy_date"].str[:4]).mean() * 100
    pos = sum(1 for y, v in wins.items() if v >= 50)
    print(f"  {name}: n={len(sel)} 月均{m.mean()*100:.2f}% Sharpe{sharpe:.2f} 年化{m.mean()*12*100:.1f}% "
          f"胜率{(sel['ret']>0).mean()*100:.1f}% 正胜率年数{pos}/{len(wins)}")


print("\n== 分组组合(K=5, 每天按ret20中深优先) ==")
combo(pd.Series(True, index=T.index), "全部信号(对照)")
combo(T["depth"] > -1.5, "扫荡深度浅(>-1.5%)")
combo((T["depth"] >= -3) & (T["depth"] <= -1.5), "扫荡深度中(-3~-1.5%)")
combo(T["depth"] < -3, "扫荡深度深(<-3%)")
combo(T["back_ratio"] >= 1, "收回在水平上方")
combo(T["back_ratio"] < 1, "收回在水平下方")
combo(T["mkt"] == 0, "偏空市场")
combo(T["mkt"] == 1, "偏多市场")
combo(T["price"] < 15, "低价(<15元)")
combo(T["price"] >= 15, "高价(>=15元)")
combo((T["ret20"] >= -0.4) & (T["ret20"] <= -0.1), "中深跌(ret20 -40%~-10%)")
combo(T["ret20"] < -0.4, "最深跌(ret20<-40%)")

print("\n== 组合分层交叉：偏空市场 × 收回上方 × 中深跌 ==")
m = (T["mkt"] == 0) & (T["back_ratio"] >= 1) & (T["ret20"] >= -0.4) & (T["ret20"] <= -0.1)
combo(m, "偏空+收回上+中深跌(交集)")
T.to_csv("smc_s2_trades_enriched.csv", index=False)
print(f"\n增强交易表已存 smc_s2_trades_enriched.csv, 耗时{time.time()-t0:.0f}s")
