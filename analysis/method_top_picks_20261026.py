# -*- coding: utf-8 -*-
"""每种选股方法选3只胜率最高的股票(主人令) 20261026 — 只读零交易权。
方法清单(仅存活方法+排雷): 6种正向 + 3种负向排雷 + 1种综合最优。
每法选样规则: 方法条件内确定性排序(成交额降序/条件强度降序), 取前3。
截面: 20261023收盘; 全市场6/0/3剔688/非ST/≤30元/上市>60日。
输出: analysis/method_top_picks_20261026_result.json + stdout
"""
import json
import sqlite3
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/method_top_picks_20261026_result.json"
ASOF = 20261023


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = __import__("time").time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq "
    f"WHERE date>=20250601 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]") & ~df["code6"].str.startswith("688")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["volume"] > 0)]
scon = sqlite3.connect(STDB, uri=True)
# 修复20261026: 用最新记录而非精确日匹配(联创光电教训: 精确日匹配在数据不完整日漏检)
st_latest = scon.execute("""
    SELECT code, is_st FROM st_daily s1
    WHERE date = (SELECT MAX(date) FROM st_daily s2 WHERE s2.code = s1.code)
""").fetchall()
scon.close()
st_codes = set()  # 当前is_st=1的code集合
for raw_code, is_st in st_latest:
    if is_st == 1:
        dg = "".join(c for c in str(raw_code) if c.isdigit())
        st_codes.add(dg[-6:] if len(dg) >= 6 else dg)
mkt = df.groupby("d")["close"].mean().sort_index()
m20 = float(mkt.iloc[-1] / mkt.iloc[-21] - 1)

# 全截面画像
profiles = pd.read_csv("data/stock_3y_phase_profiles.csv", dtype={"code": str})
pmap = dict(zip(profiles["code"], profiles["label"]))

stocks = []
for code, gg in df.groupby("code6", sort=False):
    gg = gg.sort_values("d")
    c = gg["close"].to_numpy(float)
    v = gg["volume"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 120 or ASOF not in d:
        continue
    idx = int(np.where(d == ASOF)[0][0])
    if code in st_codes or c[idx] > 30 or c[idx] < 2:
        continue
    days_listed = idx + 1
    if days_listed <= 60:
        continue
    s = pd.Series(c[:idx + 1])
    mn = s.rolling(250, min_periods=120).min().iloc[-1]
    mx = s.rolling(250, min_periods=120).max().iloc[-1]
    pos = (c[idx] - mn) / (mx - mn) * 100 if mx > mn else 50.0
    mom20 = c[idx] / c[max(0, idx - 20)] - 1
    mom5 = c[idx] / c[max(0, idx - 5)] - 1
    chg1 = c[idx] / c[max(0, idx - 1)] - 1
    chg5 = (c[idx] / c[max(0, idx - 5)] - 1) * 100
    vol20 = s.pct_change().rolling(20, min_periods=20).std().iloc[-1] * np.sqrt(250)
    volr = v[idx] / np.mean(v[max(0, idx - 60):idx]) if idx >= 60 else 1.0
    amt = float(gg["amount"].iloc[idx])
    pl = "低" if pos < 25 else ("中" if pos < 70 else "高")
    ml = ("大跌" if mom20 < -0.15 else "跌" if mom20 < -0.04 else
          "平" if mom20 < 0.04 else "涨" if mom20 < 0.15 else "强涨")
    PHASES = {("低","大跌"):"超跌探底",("低","跌"):"阴跌",("低","平"):"筑底横盘",("低","涨"):"低位启动",
              ("低","强涨"):"低位急拉",("中","大跌"):"破位下跌",("中","跌"):"中位回调",("中","平"):"中位震荡",
              ("中","涨"):"温和上行",("中","强涨"):"主升段",("高","跌"):"高位回落",("高","大跌"):"高位崩落",
              ("高","平"):"高位滞涨",("高","涨"):"高位续涨",("高","强涨"):"加速赶顶"}
    phase = PHASES.get((pl, ml), "中位震荡")
    char = pmap.get(code, "普通震荡")
    stocks.append({"code": code, "close": round(float(c[idx]), 2), "pos250": round(float(pos), 1),
                   "mom20": round(float(mom20), 3), "mom5": round(float(mom5), 3),
                   "chg1": round(float(chg1), 3), "chg5": round(float(chg5), 1),
                   "vol20": round(float(vol20), 3), "volr": round(float(volr), 2),
                   "amt_yi": round(amt / 1e8, 1), "phase": phase, "char3y": char})

ev = pd.DataFrame(stocks)
# 名称补全
codes = ev["code"].tolist()
try:
    r = subprocess.run([sys.executable, "stock_name_lookup.py"] + codes[:100],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    names = {}
    for ln in r.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            names[k.strip()] = v.strip()
    ev["name"] = ev["code"].map(names).fillna("?")
except Exception:
    ev["name"] = "?"
print(f"[1/2] 截面{n}={len(ev)}, m20={m20:+.1%} ({__import__('time').time()-t0:.0f}s)")

# ---- 方法定义+选样 ----
def pick(sub, sort_col, ascending=False, n=3, label=""):
    if len(sub) == 0:
        return []
    top = sub.sort_values(sort_col, ascending=ascending).head(n)
    return [{"name": x["name"], "code": x["code"], "close": x["close"],
             "pos250": x["pos250"], "vol20": x["vol20"], "mom20": x["mom20"],
             "phase": x["phase"], "char3y": x["char3y"], "amt_yi": x["amt_yi"],
             "chg1": x["chg1"]} for _, x in top.iterrows()]

methods = {}

# 1. HIGHWIN cohort: A格 & vol>0.45 & m20<=-5% — 按vol20降序(波动最大=燃料最足)
m1 = ev[(ev["pos250"] < 30) & (ev["vol20"] > 0.45) & (abs(ev["chg5"]) < 3)]
methods["①HIGHWIN(相位×燃料×阶段) 预测71.5%"] = pick(m1, "vol20", False)

# 2. 超跌探底: pos<25 & mom20<-15% — 按|mom20|降序(跌最狠=弹最猛)
m2 = ev[ev["pos250"] < 25 & (ev["mom20"] < -0.15)] if False else ev[(ev["pos250"] < 25) & (ev["mom20"] < -0.15)]
methods["②超跌探底阶段 预测+6.94%"] = pick(m2.assign(abs_mom=m2["mom20"].abs()), "abs_mom", False)

# 3. 低位启动日: pos<30 & |chg5|<3 & chg1 in [2%,8%] — 按chg1降序(启动力度最强)
m3 = ev[(ev["pos250"] < 30) & (ev["chg5"].abs() < 3) & (ev["chg1"] >= 0.02) & (ev["chg1"] < 0.08)]
methods["③低位启动日 预测+4.14%"] = pick(m3, "chg1", False)

# 4. 性格×阶段最优: 脉冲妖/低位平 + 趋势牛/低位跌 — 按amt降序
m4a = ev[(ev["char3y"] == "多次脉冲妖") & (ev["pos250"] < 30) & (ev["mom20"].abs() < 0.04)]
m4b = ev[(ev["char3y"] == "三年趋势牛") & (ev["pos250"] < 25) & (ev["mom20"] < -0.04)]
m4 = pd.concat([m4a, m4b])
methods["④脉冲妖筑底+趋势牛回调 预测+5.6~6.3%"] = pick(m4, "amt_yi", False)

# 5. 横盘者超跌(胜率冠军): char=横盘者 & pos<30 & mom20<-4% — 按|mom20|降序
m5 = ev[(ev["char3y"] == "三年横盘者") & (ev["pos250"] < 30) & (ev["mom20"] < -0.04)]
methods["⑤横盘者超跌(胜率56.3%)"] = pick(m5.assign(abs_mom=m5["mom20"].abs()), "abs_mom", False)

# 6. 排雷反向(R42商誉+R43大宗双清): 排雷最干净的低位票 — 按pos250升序
from pre_trade_check_v2 import check_r42_goodwill_high, check_r43_block_trade_anomaly
clean_codes = []
for _, x in ev[ev["pos250"] < 30].iterrows():
    r42 = check_r42_goodwill_high(x["code"])
    r43 = check_r43_block_trade_anomaly(x["code"])
    if r42["status"] == "clear" and r43["status"] == "clear":
        clean_codes.append(x["code"])
m6 = ev[ev["code"].isin(clean_codes)]
methods["⑥排雷双清低位(零雷区)"] = pick(m6, "vol20", False)

json.dump(methods, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[2/2] 完成 → {OUT}\n")
for mname, picks in methods.items():
    print(f"== {mname} ==")
    if not picks:
        print("  (无候选)")
        continue
    for p in picks:
        print(f"  {p['code']} {p['name']:<6} 收{p['close']:>7} pos{p['pos250']:>5.0f} vol{p['vol20']:.2f} "
              f"mom20{p['mom20']:+.0%} {p['phase']} [{p['char3y']}] 额{p['amt_yi']}亿")
