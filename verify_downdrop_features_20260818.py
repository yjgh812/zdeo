# -*- coding: utf-8 -*-
"""
下跌特征网上说法验证（2026-08-18，与涨停特征验证同方法论）
事件定义 → 次日/T+5 表现 vs 全市场基准

① 放量下跌(量≥2×5日均量且跌≥2%) → 网说"高位见顶信号"（搜狐）
   分高位(pos60≥0.9)/低位(pos60≤0.7)两档
② 缩量阴跌(量≤0.7×均量且跌≥1%且MA5<MA20) → 网说"下跌中继，莫当底"（搜狐）
③ 高位放量滞涨(20日涨≥20%+量≥1.5×前日+涨幅<2%) → 网说"量价背离出货"（搜狐）
④ 断头铡刀(一阴跌破MA5/10/20三线) → 网说"主力集中出货"（网易）
⑤ 长上影阳线(上影≥4%且收阳) → 网说"次日低开连续阴跌"（搜狐）
⑥ 炸板(盘中触涨停未封住) → 涨停特征自然延伸

数据: qfq 库 20251001 起(60日均线预热), 事件统计窗口 20260101~20260814
"""
import sqlite3
import pandas as pd
import numpy as np

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"

print("== 1/3 加载数据 ==", flush=True)
con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code, date, open, high, low, close, volume, amount, change_pct "
    "FROM stock_daily_meta_qfq WHERE date >= 20251001", con)
con.close()
print(f"rows={len(df)}", flush=True)
df["date"] = pd.to_numeric(df["date"], errors="coerce")
df = df.dropna(subset=["date"])
df["date"] = df["date"].astype(int)

print("== 2/3 预计算特征 ==", flush=True)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)
for n in (5, 10, 20, 60):
    df[f"ma{n}"] = g["close"].transform(lambda s, n=n: s.rolling(n).mean())
df["vol_ma5"] = g["volume"].transform(lambda s: s.rolling(5).mean())
df["vol_prev"] = g["volume"].shift(1)
df["ret20"] = g["close"].transform(lambda s: s.pct_change(20))
df["high60"] = g["high"].transform(lambda s: s.rolling(60).max())
df["pos60"] = df["close"] / df["high60"]
# 昨日状态(断头铡刀用)
df["prev_close"] = g["close"].shift(1)
for n in (5, 10, 20):
    df[f"prev_ma{n}"] = g[f"ma{n}"].shift(1)
# 前瞻收益
df["next_chg"] = g["change_pct"].shift(-1)
df["next_open_gap"] = g["open"].shift(-1) / df["close"] - 1
df["chg5"] = g["close"].transform(lambda s: s.shift(-5)) / df["close"] - 1
# 盘中最高涨幅(炸板判定)
df["prev_close_calc"] = df["close"] / (1 + df["change_pct"] / 100)
df["high_pct"] = (df["high"] / df["prev_close_calc"] - 1) * 100

def limit_th(code):
    if code.startswith(("300", "301", "688")):
        return 19.5
    if code.startswith(("8", "4", "92")):
        return 29.5
    return 9.5

df["limit_th"] = df["code"].map(limit_th)

# 事件统计窗口: 有完整次日数据
stat = df[(df["date"] >= 20260101) & (df["date"] <= 20260814)].copy()
base_next = stat["next_chg"].dropna()
base_gap = stat["next_open_gap"].dropna()
base5 = stat["chg5"].dropna()
print(f"统计窗口行数={len(stat)} 基准: 次日涨幅均值={base_next.mean():.3f}% 中位={base_next.median():.3f}% "
      f"下跌占比={(base_next < 0).mean() * 100:.1f}% T+5均值={base5.mean():.3f}%", flush=True)

events = {}

m = (stat["volume"] >= 2 * stat["vol_ma5"]) & (stat["change_pct"] <= -2)
events["①a 高位放量下跌(pos60≥0.9)"] = m & (stat["pos60"] >= 0.9)
events["①b 低位放量下跌(pos60≤0.7)"] = m & (stat["pos60"] <= 0.7)
events["② 缩量阴跌(下跌趋势)"] = (
    (stat["volume"] <= 0.7 * stat["vol_ma5"]) & (stat["change_pct"] <= -1)
    & (stat["ma5"] < stat["ma20"]))
events["③ 高位放量滞涨"] = (
    (stat["ret20"] >= 0.20) & (stat["volume"] >= 1.5 * stat["vol_prev"])
    & (stat["change_pct"] >= 0) & (stat["change_pct"] <= 2))
events["④ 断头铡刀(破3线)"] = (
    (stat["change_pct"] <= -3)
    & (stat["close"] < stat[["ma5", "ma10", "ma20"]].min(axis=1))
    & (stat["prev_close"] > stat[["prev_ma5", "prev_ma10", "prev_ma20"]].max(axis=1)))
stat["upper_shadow"] = (stat["high"] - stat[["open", "close"]].max(axis=1)) / stat["close"]
events["⑤ 长上影阳线(≥4%)"] = (stat["upper_shadow"] >= 0.04) & (stat["change_pct"] > 0)
events["⑥ 炸板(触板未封)"] = (
    (stat["high_pct"] >= stat["limit_th"]) & (stat["change_pct"] < stat["limit_th"]))

print("\n== 3/3 事件统计 ==", flush=True)
rows = []
for name, mask in events.items():
    sub = stat[mask]
    nc = sub["next_chg"].dropna()
    gap = sub["next_open_gap"].dropna()
    c5 = sub["chg5"].dropna()
    if len(nc) < 10:
        print(f"{name}: 样本不足({len(nc)})")
        continue
    rows.append({
        "事件": name, "样本": len(nc),
        "次日低开占比%": round((gap < 0).mean() * 100, 1),
        "次日均涨%": round(nc.mean(), 3), "次日中位%": round(nc.median(), 3),
        "次日跌占比%": round((nc < 0).mean() * 100, 1),
        "次日跌≥3%占比%": round((nc <= -3).mean() * 100, 1),
        "T+5均%": round(c5.mean(), 3), "T+5跌占比%": round((c5 < 0).mean() * 100, 1),
    })
out = pd.DataFrame(rows)
print(out.to_string(index=False), flush=True)
out.to_csv(r"D:/zcodesj/verify_result_downdrop_events.csv", index=False, encoding="utf-8-sig")
print("\n基准对照: 次日均涨{:.3f}% 跌占比{:.1f}% T+5均{:.3f}%".format(
    base_next.mean(), (base_next < 0).mean() * 100, base5.mean()), flush=True)
print("DONE", flush=True)