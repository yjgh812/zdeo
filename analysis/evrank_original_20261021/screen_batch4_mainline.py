# -*- coding: utf-8 -*-
"""主线/次线四条件选股(只读): 趋势分两档(A强趋势全排列/B趋势形成中短期), 胜率+动能打分."""
import sqlite3
import pandas as pd
import numpy as np

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
MAP = r"file:D:\zcodesj\data\industry_group_map_snapshot.db?mode=ro"

# 主线=AI算力数字经济链; 次线=医药/农业/传媒(今日涨停聚集+资金确认)
MAIN = ["计算机", "电子", "通信"]
SECOND = ["医药生物", "农林牧渔", "传媒"]
EXCLUDE = {
    "002292", "601939",
    "003006", "601919", "300817", "600035", "600018", "301370", "600626",
    "603209", "601866", "002966", "600284", "603098", "600603", "000503",
    "002053", "600000", "600380", "605338", "001287", "300364", "301500",
}
INDUSTRY_USED = {"传媒": 178_454, "银行": 180_302}
TOTAL_ASSET = 844_279.0
IND_CAP = 0.25 * TOTAL_ASSET
ASOF = "20261020"

con_m = sqlite3.connect(MAP, uri=True)
mem = pd.read_sql(
    "SELECT code, name, l1 FROM l1_member WHERE l1 IN (%s) AND ambiguous_flag=0"
    % ",".join("?" * len(MAIN + SECOND)), con_m, params=MAIN + SECOND)
con_m.close()
mem = mem[~mem["code"].isin(EXCLUDE)]
mem = mem[~mem["name"].str.contains("ST", na=False)]
mem = mem[~mem["code"].str.startswith(("688", "689", "8", "4", "9"))]
codes = mem["code"].tolist()
print(f"主线/次线池={len(codes)}")

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
    ma60 = c.rolling(60).mean()[i]
    ma120 = c.rolling(120).mean()[i]
    if any(pd.isna(x) for x in (ma20, ma60)):
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
    up20 = (g["change_pct"].iloc[i-19:i+1] > 0).mean()
    bull_strong = ma5 > ma10 > ma20 and close > ma20 > ma60 and ma60 > ma120
    bull_form = ma5 > ma10 > ma20 and close > ma20          # 趋势形成档(B)
    rows.append(dict(code=code, close=close, ret20=ret20, ret60=ret60, dd250=close / hi250 - 1,
                     dd20=dd20, amp20=((h - lo) / c).iloc[i-19:i+1].mean(), rsi14=rsi14, vr=vr,
                     win5=win5, bias20=close / ma20 - 1, up20=up20,
                     tier="A强趋势" if bull_strong else ("B趋势形成" if bull_form else None)))
res = pd.DataFrame(rows).dropna(subset=["tier"]).merge(mem, on="code").merge(meta_early, on="code")
ref = pd.to_datetime(ASOF, format="%Y%m%d")
res["list_days"] = (ref - pd.to_datetime(res["first_date"], format="%Y%m%d")).dt.days
print(f"趋势成立(两档)={len(res)}")

flt = res[
    (res["ret20"] > 0)
    & (res["dd250"] >= -0.18)
    & (res["up20"] >= 0.50)
    & (res["dd20"] >= -0.12)
    & (res["amp20"] <= 0.06)
    & (res["rsi14"] <= 80)
    & (res["vr"] >= 0.7) & (res["vr"] <= 3.2)
    & (res["bias20"] <= 0.16)
    & (res["close"] <= 30)
    & (res["list_days"] >= 90)
    & res["win5"].notna()
].copy()
print(f"硬过滤后={len(flt)}")

base = (100 * (0.45 * flt["win5"] + 0.35 * flt["ret20"].clip(upper=0.30) / 0.30
               + 0.20 * (1 - flt["dd250"].abs().clip(upper=0.18) / 0.18)))
flt["tier_w"] = np.where(flt["tier"] == "A强趋势", 1.0, 0.85)
flt["score"] = (base * flt["tier_w"]).round(1)
flt["ind_headroom"] = flt["l1"].map(lambda x: IND_CAP - INDUSTRY_USED.get(x, 0))
flt = flt[flt["ind_headroom"] >= 30_000]
flt = flt.sort_values("score", ascending=False)
cols = ["code", "name", "l1", "tier", "close", "score", "win5", "ret20", "ret60", "dd250",
        "dd20", "rsi14", "vr", "bias20", "ind_headroom"]
pd.set_option("display.width", 280, "display.max_columns", 30, "display.float_format", lambda x: f"{x:.3f}")
print(flt[cols].head(18).to_string(index=False))
