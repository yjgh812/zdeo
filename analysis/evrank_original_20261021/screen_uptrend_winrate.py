# -*- coding: utf-8 -*-
"""一次性选股研究脚本(只读): 主线板块内上涨趋势+高胜率筛选.
价格源=qfq真源库(ro); 行业映射=industry_group_map_snapshot.db l1_member(ro).
"""
import sqlite3
import pandas as pd
import numpy as np

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
MAP = r"file:D:\zcodesj\data\industry_group_map_snapshot.db?mode=ro"

MAINLINES = ["电子", "通信", "传媒"]  # 今日(1021盘中)主线: 资金流入+涨幅确认
# 今日次线: 量能延续或板块联动(计算机/军工/农业/煤炭/非银)
SECOND = ["计算机", "国防军工", "农林牧渔", "煤炭", "非银金融"]
ASOF = "20261020"

# ---------- 1. 主线板块成分 ----------
con_m = sqlite3.connect(MAP, uri=True)
mem = pd.read_sql(
    "SELECT code, name, l1 FROM l1_member WHERE l1 IN (%s) AND ambiguous_flag=0"
    % ",".join("?" * len(MAINLINES + SECOND)), con_m, params=MAINLINES + SECOND)
con_m.close()
mem["is_main"] = mem["l1"].isin(MAINLINES)
codes = mem["code"].tolist()
print(f"[1] 主线板块成分: {dict(mem.groupby('l1').size())}, 共{len(codes)}码")

# ---------- 2. qfq 拉行情 ----------
con = sqlite3.connect(QFQ, uri=True)
ph = ",".join("?" * len(codes))
df = pd.read_sql(
    f"SELECT code, date, open, high, low, close, volume, amount, change_pct "
    f"FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20250601' ORDER BY code, date",
    con, params=codes)
meta_early = pd.read_sql(
    f"SELECT code, MIN(date) AS first_date, COUNT(*) AS n FROM stock_daily_meta_qfq "
    f"WHERE code IN ({ph}) GROUP BY code", con, params=codes)
con.close()
print(f"[2] 行情行数={len(df)}, 截止={df['date'].max()}")

# ---------- 3. 逐码指标 ----------
rows = []
for code, g in df.groupby("code"):
    if len(g) < 130:
        continue
    c = g["close"].reset_index(drop=True)
    v = g["volume"].reset_index(drop=True)
    h = g["high"].reset_index(drop=True)
    lo = g["low"].reset_index(drop=True)
    i = len(c) - 1
    close = c[i]
    ma5, ma10, ma20 = c.rolling(5).mean()[i], c.rolling(10).mean()[i], c.rolling(20).mean()[i]
    ma60, ma120 = c.rolling(60).mean()[i], c.rolling(120).mean()[i]
    if any(pd.isna(x) for x in (ma20, ma60, ma120)):
        continue
    ret20 = close / c[i-20] - 1
    ret60 = close / c[i-60] - 1
    # 距250日高点回撤
    hi250 = h[max(0, i-249):i+1].max()
    dd = close / hi250 - 1
    # 近20/60日上涨日占比(change_pct百分数口径)
    up20 = (g["change_pct"].iloc[i-19:i+1] > 0).mean()
    up60 = (g["change_pct"].iloc[i-59:i+1] > 0).mean()
    # 近20日最大回撤(close相对滚动20日最高)
    roll_hi = c.rolling(20).max()
    dd20 = ((c - roll_hi) / roll_hi).iloc[i-19:i+1].min()
    # 近20日日均振幅
    amp20 = ((h - lo) / c).iloc[i-19:i+1].mean()
    # RSI14
    d = c.diff()
    rs = d.clip(lower=0).rolling(14).mean() / (-d.clip(upper=0)).rolling(14).mean()
    rsi14 = 100 - 100 / (1 + rs[i])
    # 量比: 5日均量/60日均量
    vr = v.rolling(5).mean()[i] / v.rolling(60).mean()[i]
    # 5日持有胜率: 近120日每个收盘买入持5日盈利占比
    fwd5 = (c.shift(-5) / c - 1)
    w5 = fwd5.iloc[max(0, i-119):i+1].dropna()
    win5 = (w5 > 0).mean() if len(w5) >= 60 else np.nan
    # 均线排列: 档1主线强趋势=全多头排列; 档2次线趋势=短期多头+站上MA60
    bull = close > ma20 > ma60 and ma60 > ma120 and ma5 > ma10 > ma20
    sec = ma5 > ma10 > ma20 and close > ma60
    bias20 = close / ma20 - 1
    rows.append(dict(code=code, close=close, ma20=ma20, ma60=ma60, ma120=ma120,
                     ret20=ret20, ret60=ret60, dd250=dd, dd20=dd20, up20=up20, up60=up60,
                     amp20=amp20, rsi14=rsi14, vr=vr, win5=win5, bias20=bias20,
                     bull=bull, sec=sec))

res = pd.DataFrame(rows).merge(mem, on="code").merge(meta_early, on="code")
print(f"[3] 有效计算={len(res)}")

# ---------- 4. 硬过滤: 趋势 + 规则 ----------
res["first_days"] = pd.to_datetime(res["first_date"], format="%Y%m%d")
ref = pd.to_datetime(ASOF, format="%Y%m%d")
res["list_days"] = (ref - res["first_days"]).dt.days
flt = res[
    (res["bull"] | (res["sec"] & (~res["is_main"])))
    & (res["ret20"] > 0) & (res["ret60"] > 0)
    & (res["dd250"] >= -0.15)
    & (res["up20"] >= 0.50) & (res["up60"] >= 0.48)
    & (res["dd20"] >= -0.10)
    & (res["amp20"] <= 0.055)
    & (res["rsi14"] <= 78)
    & (res["vr"] >= 0.7) & (res["vr"] <= 3.0)
    & (res["bias20"] <= 0.12)
    & (res["close"] <= 30)
    & (res["list_days"] >= 90)
    & (~res["name"].str.contains("ST", na=False))
    & (~res["code"].str.startswith(("688", "689", "8", "4", "9")))
    & res["win5"].notna()
]
print(f"[4] 硬过滤后={len(flt)}")

# ---------- 5. 综合打分 ----------
flt = flt.copy()
base = (100 * (0.5 * flt["win5"]
               + 0.3 * flt["ret60"].clip(upper=0.6) / 0.6
               + 0.2 * (1 - flt["dd250"].abs().clip(upper=0.15) / 0.15)))
# 主线强趋势档全分; 次线趋势档85折
flt["tier"] = np.where(flt["bull"] & flt["is_main"], "主线强趋势",
                       np.where(flt["is_main"], "主线-中期未排", "次线趋势"))
flt["score"] = (base * np.where(flt["tier"] == "次线趋势", 0.85, 1.0)).round(1)
flt = flt.sort_values("score", ascending=False)
# 同板块限2只,取前5; 候选不足5时放开板块限制补足
pick, cnt = [], {}
pool = list(flt.iterrows())
while len(pick) < 5 and pool:
    for idx, r in pool:
        if cnt.get(r["l1"], 0) >= 2:
            continue
        pick.append(r)
        cnt[r["l1"]] = cnt.get(r["l1"], 0) + 1
        pool.remove((idx, r))
        break
    else:  # 全部被板块限制挡住 -> 放开取剩余最优
        idx, r = pool.pop(0)
        pick.append(r)

cols = ["code", "name", "l1", "tier", "close", "score", "win5", "ret20", "ret60", "dd250",
        "up20", "up60", "dd20", "amp20", "rsi14", "vr", "bias20", "list_days"]
out = pd.DataFrame(pick)[cols]
pd.set_option("display.width", 250, "display.max_columns", 30, "display.float_format", lambda x: f"{x:.3f}")
print("\n===== TOP5 =====")
print(out.to_string(index=False))
print("\n===== 候选6-15 =====")
rest = flt[~flt["code"].isin(out["code"])].head(10)
print(rest[cols].to_string(index=False))
out.to_json("top5_uptrend_result.json", orient="records", force_ascii=False, indent=1)
