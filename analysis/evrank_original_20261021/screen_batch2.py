# -*- coding: utf-8 -*-
"""第二批选股(只读): 同今日主线/次主线池, 趋势底线不变, 胜率代理放宽一档, 排除第一批5只."""
import sqlite3
import pandas as pd
import numpy as np

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
MAP = r"file:D:\zcodesj\data\industry_group_map_snapshot.db?mode=ro"
MAINLINES = ["电子", "通信", "传媒"]
SECOND = ["计算机", "国防军工", "农林牧渔", "煤炭", "非银金融"]
EXCLUDE = {"601225", "600901", "000719", "601098", "002292"}  # 第一批5只
ASOF = "20261020"

con_m = sqlite3.connect(MAP, uri=True)
mem = pd.read_sql(
    "SELECT code, name, l1 FROM l1_member WHERE l1 IN (%s) AND ambiguous_flag=0"
    % ",".join("?" * len(MAINLINES + SECOND)), con_m, params=MAINLINES + SECOND)
con_m.close()
mem = mem[~mem["code"].isin(EXCLUDE)]
mem["is_main"] = mem["l1"].isin(MAINLINES)
codes = mem["code"].tolist()

con = sqlite3.connect(QFQ, uri=True)
ph = ",".join("?" * len(codes))
df = pd.read_sql(
    f"SELECT code, date, high, low, close, volume, change_pct FROM stock_daily_meta_qfq "
    f"WHERE code IN ({ph}) AND date>='20250601' ORDER BY code, date", con, params=codes)
meta_early = pd.read_sql(
    f"SELECT code, MIN(date) AS first_date FROM stock_daily_meta_qfq WHERE code IN ({ph}) GROUP BY code",
    con, params=codes)
con.close()

rows = []
for code, g in df.groupby("code"):
    if len(g) < 130:
        continue
    c = g["close"].reset_index(drop=True); v = g["volume"].reset_index(drop=True)
    h = g["high"].reset_index(drop=True); lo = g["low"].reset_index(drop=True)
    i = len(c) - 1; close = c[i]
    ma5, ma10, ma20 = c.rolling(5).mean()[i], c.rolling(10).mean()[i], c.rolling(20).mean()[i]
    ma60, ma120 = c.rolling(60).mean()[i], c.rolling(120).mean()[i]
    if any(pd.isna(x) for x in (ma20, ma60, ma120)):
        continue
    ret20, ret60 = close / c[i-20] - 1, close / c[i-60] - 1
    hi250 = h[max(0, i-249):i+1].max()
    roll_hi = c.rolling(20).max()
    dd20 = ((c - roll_hi) / roll_hi).iloc[i-19:i+1].min()
    d = c.diff()
    rs = d.clip(lower=0).rolling(14).mean() / (-d.clip(upper=0)).rolling(14).mean()
    rsi14 = 100 - 100 / (1 + rs[i])
    vr = v.rolling(5).mean()[i] / v.rolling(60).mean()[i]
    fwd5 = (c.shift(-5) / c - 1)
    w5 = fwd5.iloc[max(0, i-119):i+1].dropna()
    win5 = (w5 > 0).mean() if len(w5) >= 60 else np.nan
    # 趋势底线不变: 短多排列+价在MA20/MA60上方+双周期正收益
    bull = ma5 > ma10 > ma20 and close > ma20 > ma60
    bias20 = close / ma20 - 1
    up20 = (g["change_pct"].iloc[i-19:i+1] > 0).mean()
    rows.append(dict(code=code, close=close, ma60=ma60, ma120=ma120, ret20=ret20, ret60=ret60,
                     dd250=close / hi250 - 1, dd20=dd20, amp20=((h - lo) / c).iloc[i-19:i+1].mean(),
                     rsi14=rsi14, vr=vr, win5=win5, bias20=bias20, bull=bull, up20=up20))
res = pd.DataFrame(rows).merge(mem, on="code").merge(meta_early, on="code")
ref = pd.to_datetime(ASOF, format="%Y%m%d")
res["list_days"] = (ref - pd.to_datetime(res["first_date"], format="%Y%m%d")).dt.days

# 趋势底线(不变) + 胜率代理放宽一档 + 规则
flt = res[
    res["bull"]
    & (res["ret20"] > 0) & (res["ret60"] > 0)
    & (res["dd250"] >= -0.18)
    & (res["up20"] >= 0.45)
    & (res["dd20"] >= -0.12)
    & (res["amp20"] <= 0.060)
    & (res["rsi14"] <= 80)
    & (res["vr"] >= 0.6) & (res["vr"] <= 3.2)
    & (res["bias20"] <= 0.15)
    & (res["close"] <= 30)
    & (res["list_days"] >= 90)
    & (~res["name"].str.contains("ST", na=False))
    & (~res["code"].str.startswith(("688", "689", "8", "4", "9")))
    & res["win5"].notna()
].copy()
print(f"放宽档硬过滤后={len(flt)}")

flt["score"] = (100 * (0.55 * flt["win5"] + 0.25 * flt["ret60"].clip(upper=0.6) / 0.6
                       + 0.20 * (1 - flt["dd250"].abs().clip(upper=0.18) / 0.18))).round(1)
flt = flt.sort_values("score", ascending=False)
pick, cnt = [], {}
for _, r in flt.iterrows():
    if cnt.get(r["l1"], 0) >= 2:
        continue
    pick.append(r); cnt[r["l1"]] = cnt.get(r["l1"], 0) + 1
    if len(pick) == 5:
        break
cols = ["code", "name", "l1", "close", "score", "win5", "ret20", "ret60", "dd250",
        "dd20", "amp20", "rsi14", "vr", "bias20", "list_days"]
pd.set_option("display.width", 250, "display.max_columns", 30, "display.float_format", lambda x: f"{x:.3f}")
print(pd.DataFrame(pick)[cols].to_string(index=False))
print("\n候选6-12:")
rest = flt[~flt["code"].isin([r["code"] for r in pick])].head(8)
print(rest[cols].to_string(index=False))
