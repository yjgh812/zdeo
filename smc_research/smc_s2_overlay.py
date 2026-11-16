# -*- coding: utf-8 -*-
"""
S2 叠加验证：SSL 扫荡信号 × PIT bp/ep 因子
逻辑：在 displacement 确认的 S2 信号里，按 buy_date 时点可用的 PIT bp/ep 打分选"好公司"
      → 看"超跌扫荡的好公司"组合是否优于纯 S2（displacement 确认）
PIT 对齐：buy_date 时只能看到 pit_avail_date <= buy_date 的最新因子值
"""
import sqlite3
import time
import warnings
import numpy as np
import pandas as pd
from smartmoneyconcepts import smc

warnings.filterwarnings("ignore")
t0 = time.time()

# 1. 读 S2 交易表（已含 displacement 确认版，smc_s2_displacement 的 B 组）
#    重算一遍 displacement 确认版交易（含 buy_date/code/ret）
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq WHERE date>='20180101' ORDER BY code,date", con)
con.close()
df["date"] = df["date"].astype(str)
print(f"qfq 读入 {len(df)} 行 {time.time()-t0:.0f}s")


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
        # displacement 确认
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
        ret = sp / bp - 1 - COST
        r20 = o["close"].iloc[buy_idx] / o["close"].iloc[buy_idx - 20] - 1 if buy_idx >= 20 else np.nan
        trades.append((code, o["date"].iloc[buy_idx], ret, r20))

T = pd.DataFrame(trades, columns=["code", "buy_date", "ret", "ret20"])
print(f"S2 displacement 确认交易 {len(T)} 笔 {time.time()-t0:.0f}s")

# 2. 读 PIT bp/ep 因子
con2 = sqlite3.connect("file:D:/zcodesj/factor_pit.db?mode=ro", uri=True)
pit = pd.read_sql_query(
    "SELECT code, pit_avail_date, ep, bp, roe FROM stock_factors_fundamental_pit_v2 WHERE pit_avail_date>0", con2)
con2.close()
pit["pit_avail_date"] = pit["pit_avail_date"].astype(str)
pit["code"] = pit["code"].astype(str)
print(f"PIT 因子 {len(pit)} 行")

# 3. PIT 对齐：每个 buy_date 取 pit_avail_date <= buy_date 的最新一期
T["code"] = T["code"].astype(str)
T["buy_date"] = T["buy_date"].astype(str)

# merge_asof 在 Win/pandas 排序判定不稳 → 改用按 code 分组手动 searchsorted 对齐 PIT
pit2 = pit.copy()
pit2["pit_int"] = pit2["pit_avail_date"].astype(int)
pit2 = pit2.sort_values(["code", "pit_int"]).reset_index(drop=True)
# 按 code 建索引：每只股票的 (pit_int 数组, 因子表)
pit_grp = {code: g.reset_index(drop=True) for code, g in pit2.groupby("code")}
print(f"PIT 因子 {len(pit2)} 行, 股票 {len(pit_grp)} 只")


def align_pit(row):
    g = pit_grp.get(row["code"])
    if g is None or len(g) == 0:
        return pd.Series([np.nan] * 3)
    bd = int(row["buy_date"])
    arr = g["pit_int"].values
    # 找 <= buy_date 的最后一个
    idx = np.searchsorted(arr, bd, side="right") - 1
    if idx < 0:
        return pd.Series([np.nan] * 3)
    r = g.iloc[idx]
    return pd.Series([r["ep"], r["bp"], r["roe"]])


T["code"] = T["code"].astype(str)
T["buy_date"] = T["buy_date"].astype(str)
M = T.copy()
M[["ep", "bp", "roe"]] = M.apply(align_pit, axis=1)
M = M.dropna(subset=["ep", "bp"])
print(f"PIT 对齐后 {len(M)} 笔（ep/bp 有值）")

# 4. 叠加组合验证
def combo_stats(sub, name, K=5):
    if len(sub) < 100:
        print(f"  {name}: 样本不足({len(sub)})")
        return
    sel = sub.sort_values(["buy_date", "ret20"]).groupby("buy_date").head(K)
    m = sel.groupby(sel["buy_date"].str[:6])["ret"].mean()
    sharpe = m.mean() / m.std() * np.sqrt(12) if m.std() > 0 else 0
    print(f"  {name}: n={len(sel)} 月均{m.mean()*100:.2f}% Sharpe{sharpe:.2f} 年化{m.mean()*12*100:.1f}% 胜率{(sel['ret']>0).mean()*100:.1f}%")


print(f"\n== S2 displacement 确认 × bp/ep 叠加 (K=5/天) ==")
combo_stats(M, "全部确认信号(对照)")
# ep 高分（价值股）= ep 排名前 50%
M["ep_rank"] = M.groupby("buy_date")["ep"].rank(pct=True)
M["bp_rank"] = M.groupby("buy_date")["bp"].rank(pct=True)
M["value_score"] = (M["ep_rank"] + M["bp_rank"]) / 2
combo_stats(M[M["value_score"] >= 0.5], "价值高分(EP+BP前50%)")
combo_stats(M[M["value_score"] >= 0.7], "价值高分(EP+BP前30%)")
combo_stats(M[M["roe"] > 0], "ROE正(盈利)")
combo_stats(M[M["roe"] > 0.1], "ROE>10%(高质量)")
# 交叉：价值高分 + ROE正
combo_stats(M[(M["value_score"] >= 0.5) & (M["roe"] > 0)], "价值前50%+ROE正")
combo_stats(M[(M["value_score"] >= 0.5) & (M["roe"] > 0.1)], "价值前50%+ROE>10%")

# 按价值打分排序选（不是按ret20最深）
print("\n== 按价值打分选（非ret20最深）==")
for vs in [0.5, 0.7]:
    sub = M[M["value_score"] >= vs].copy()
    sel = sub.sort_values(["buy_date", "value_score"], ascending=[True, False]).groupby("buy_date").head(5)
    m = sel.groupby(sel["buy_date"].str[:6])["ret"].mean()
    sharpe = m.mean() / m.std() * np.sqrt(12) if m.std() > 0 else 0
    print(f"  价值>={vs*100:.0f}% 按价值选前5: n={len(sel)} 月均{m.mean()*100:.2f}% Sharpe{sharpe:.2f} 年化{m.mean()*12*100:.1f}% 胜率{(sel['ret']>0).mean()*100:.1f}%")

print(f"\n总耗时 {time.time()-t0:.0f}s")
