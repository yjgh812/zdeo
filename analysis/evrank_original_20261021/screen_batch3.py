# -*- coding: utf-8 -*-
"""第三批选股(只读): 可买约束全量嵌入——排除已持/行业空间/R10/胜率框架不变."""
import sqlite3
import pandas as pd
import numpy as np

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
MAP = r"file:D:\zcodesj\data\industry_group_map_snapshot.db?mode=ro"

# 今日强势方向全集(量能/资金/涨幅确认)
POOL = ["电子", "通信", "传媒", "计算机", "国防军工", "农林牧渔", "煤炭",
        "非银金融", "银行", "机械设备", "家用电器", "食品饮料", "电力设备"]
# 已持全部标的(排除重复建仓)
EXCLUDE = {
    "002292", "601939",                                    # 今日已买
    "003006", "601919", "300817", "600035", "600018", "301370", "600626",
    "603209", "601866", "002966", "600284", "603098", "600603", "000503",
    "002053", "600000", "600380", "605338", "001287", "300364", "301500",
}
# 行业已占市值(元, 含建行委托) → R30b 25%上限=211,070(总资产844,279)
INDUSTRY_USED = {"传媒": 178_454, "银行": 180_302}
TOTAL_ASSET = 844_279.0
IND_CAP = 0.25 * TOTAL_ASSET
ASOF = "20261020"

con_m = sqlite3.connect(MAP, uri=True)
mem = pd.read_sql(
    "SELECT code, name, l1 FROM l1_member WHERE l1 IN (%s) AND ambiguous_flag=0"
    % ",".join("?" * len(POOL)), con_m, params=POOL)
con_m.close()
mem = mem[~mem["code"].isin(EXCLUDE)]
mem = mem[~mem["name"].str.contains("ST", na=False)]
mem = mem[~mem["code"].str.startswith(("688", "689", "8", "4", "9"))]
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
    up20 = (g["change_pct"].iloc[i-19:i+1] > 0).mean()
    rows.append(dict(code=code, close=close, ret20=ret20, ret60=ret60, dd250=close / hi250 - 1,
                     dd20=dd20, amp20=((h - lo) / c).iloc[i-19:i+1].mean(), rsi14=rsi14, vr=vr,
                     win5=win5, bias20=close / ma20 - 1, up20=up20,
                     bull=ma5 > ma10 > ma20 and close > ma20 > ma60))
res = pd.DataFrame(rows).merge(mem, on="code").merge(meta_early, on="code")
ref = pd.to_datetime(ASOF, format="%Y%m%d")
res["list_days"] = (ref - pd.to_datetime(res["first_date"], format="%Y%m%d")).dt.days

flt = res[
    res["bull"]
    & (res["ret20"] > 0) & (res["ret60"] > 0)
    & (res["dd250"] >= -0.15)
    & (res["up20"] >= 0.50)
    & (res["dd20"] >= -0.10)
    & (res["amp20"] <= 0.055)
    & (res["rsi14"] <= 78)
    & (res["vr"] >= 0.7) & (res["vr"] <= 3.0)
    & (res["bias20"] <= 0.12)
    & (res["close"] <= 30)
    & (res["list_days"] >= 90)
    & res["win5"].notna()
].copy()
print(f"硬过滤后={len(flt)}")

flt["score"] = (100 * (0.5 * flt["win5"] + 0.3 * flt["ret60"].clip(upper=0.6) / 0.6
                       + 0.2 * (1 - flt["dd250"].abs().clip(upper=0.15) / 0.15))).round(1)
flt["ind_headroom"] = flt["l1"].map(lambda x: IND_CAP - INDUSTRY_USED.get(x, 0))
# 行业剩余空间<3万的(无法建有意义仓位)剔除
flt = flt[flt["ind_headroom"] >= 30_000]
flt = flt.sort_values("score", ascending=False)
cols = ["code", "name", "l1", "close", "score", "win5", "ret20", "ret60", "dd250",
        "dd20", "rsi14", "vr", "bias20", "ind_headroom"]
pd.set_option("display.width", 260, "display.max_columns", 30, "display.float_format", lambda x: f"{x:.3f}")
print(flt[cols].head(20).to_string(index=False))
