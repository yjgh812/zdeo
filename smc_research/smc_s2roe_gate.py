# -*- coding: utf-8 -*-
"""
S2×ROE>10% 正式过闸：
1. 参数网格（swing×ROE阈值×range%）→ PBO 输入（各轨道月度收益）
2. 主策略（swing16/range1%/ROE>10%）月度序列 → DSR/PSR 自算
3. 与 bp_ep/momvol 相关性（用 ROE 因子是否冗余检查）
"""
import sqlite3
import time
import warnings
import math
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
print(f"qfq 读入 {len(df)} 行 {time.time()-t0:.0f}s")

# PIT ROE
con2 = sqlite3.connect("file:D:/zcodesj/factor_pit.db?mode=ro", uri=True)
pit = pd.read_sql_query("SELECT code,pit_avail_date,roe FROM stock_factors_fundamental_pit_v2 WHERE pit_avail_date>0", con2)
con2.close()
pit["pit_int"] = pit["pit_avail_date"].astype(int)
pit = pit.sort_values(["code", "pit_int"]).reset_index(drop=True)
pit_grp = {c: g.reset_index(drop=True) for c, g in pit.groupby("code")}
print(f"PIT ROE {len(pit)} 行")


def get_roe(code, buy_date):
    g = pit_grp.get(str(code))
    if g is None or len(g) == 0:
        return np.nan
    bd = int(buy_date)
    arr = g["pit_int"].values
    idx = np.searchsorted(arr, bd, side="right") - 1
    if idx < 0:
        return np.nan
    return g["roe"].iloc[idx]


def limit_up_pct(code):
    c = str(code)
    if c.startswith(("300", "301", "688")):
        return 19.8
    if c.startswith(("8", "4", "92")):
        return 29.8
    return 9.8


def run_grid(swing, rp, roe_thr):
    COST = 0.003
    trades = []
    for code, o in df.groupby("code"):
        o = o.sort_values("date").reset_index(drop=True)
        if len(o) < 80:
            continue
        ohlc = o[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
        try:
            shl = smc.swing_highs_lows(ohlc, swing_length=swing)
            liq = smc.liquidity(ohlc, shl, range_percent=rp)
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
            conf = None
            for j in range(swept + 1, min(swept + 4, len(o))):
                if o["close"].iloc[j] > lvl and o["volume"].iloc[j] > vol5.iloc[j] * 1.2:
                    conf = j
                    break
            if conf is None:
                continue
            buy_idx = conf + 1
            if o["change_pct"].iloc[buy_idx] >= lu or o["change_pct"].iloc[buy_idx] <= -9.8:
                continue
            if buy_idx + 20 >= len(o):
                continue
            sell_idx = buy_idx + 20
            while sell_idx < len(o) and o["change_pct"].iloc[sell_idx] <= -9.8:
                sell_idx += 1
            if sell_idx >= len(o):
                continue
            bp = o["open"].iloc[buy_idx]
            sp = o["open"].iloc[sell_idx]
            if bp <= 0 or sp <= 0:
                continue
            buy_date = o["date"].iloc[buy_idx]
            roe = get_roe(code, buy_date)
            if np.isnan(roe) or roe <= roe_thr:
                continue
            ret = sp / bp - 1 - COST
            trades.append((buy_date, ret))
    if not trades:
        return pd.Series(dtype=float)
    T = pd.DataFrame(trades, columns=["buy_date", "ret"])
    T["ym"] = T["buy_date"].str[:6]
    return T.groupby("ym")["ret"].mean()


# 主策略 swing16/rp0.01/ROE>10%
print("\n== 主策略 swing16/rp1%/ROE>10% ==")
main = run_grid(16, 0.01, 0.10)
print(f"  月份 {len(main)} 月均 {main.mean()*100:.2f}% 中位 {main.median()*100:.2f}%")
sharpe = main.mean() / main.std() * np.sqrt(12) if main.std() > 0 else 0
print(f"  年化 {main.mean()*12*100:.1f}% Sharpe {sharpe:.2f} 最大月亏 {main.min()*100:.1f}% 月胜率 {(main>0).mean()*100:.0f}%")
main.to_csv("smc_s2roe_monthly_main.csv")

# DSR 自算
r = main.values
T = len(r)
skew = pd.Series(r).skew()
kurt = pd.Series(r).kurt()


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


sr_mean = r.mean()
sr_std = r.std()
z = (sr_mean - 0) / sr_std * np.sqrt(T)
psr = ncdf(z)
print(f"\n  DSR/PSR: T={T} 偏度{skew:.2f} 峰度{kurt:.2f}")
print(f"  PSR(0)={psr:.3f}")
for N in [1, 3, 5, 10]:
    e0 = math.sqrt(2 * max(0, math.log(N)))
    d = (z - e0) / math.sqrt(1 + kurt - 1)
    print(f"  DSR(N={N})≈{ncdf(d):.3f}")

# 参数网格 PBO
print("\n== 参数网格 PBO ==")
grid = [(8, 0.005, 0.10), (8, 0.01, 0.10), (16, 0.005, 0.10), (16, 0.01, 0.10), (16, 0.02, 0.10),
        (16, 0.01, 0.08), (16, 0.01, 0.12), (24, 0.01, 0.10), (24, 0.02, 0.10)]
out = pd.DataFrame()
t1 = time.time()
for swing, rp, roe_thr in grid:
    s = run_grid(swing, rp, roe_thr)
    col = f"s{swing}_r{int(rp*1000)}_roe{int(roe_thr*100)}"
    out[col] = s
    print(f"  {col}: {len(s)}月 月均{s.mean()*100:.2f}% Sharpe{s.mean()/s.std()*np.sqrt(12) if s.std()>0 else 0:.2f}", flush=True)
out.to_csv("smc_s2roe_pbo_input.csv")
print(f"网格完成 {out.shape} 耗时{time.time()-t1:.0f}s")
print(f"\n总耗时 {time.time()-t0:.0f}s")
