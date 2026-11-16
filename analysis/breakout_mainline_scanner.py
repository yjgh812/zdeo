# -*- coding: utf-8 -*-
"""突破+主线共振扫描器(prereg PREREG_BREAKOUT_MAINLINE_20261026) 20261026 — 零交易权。
条件: 当日创20日新高 + 量>=1.5x前5日均量 + 涨幅>0 + 所属行业涨幅>大盘
选样: 成交额降序前5只, 同行业最多1只, ≤30元非ST
输出: data/breakout_mainline_latest.json + stdout
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
IMAP = "data/industry_group_map_snapshot.db"

def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg

con = sqlite3.connect(QFQ, uri=True)
latest = int(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
# 90个自然日窗(YYYYMMDD整数减法=日历错误, 只取到当月致n<25全 skip 的bug修复)
win_start = (datetime.strptime(str(latest), "%Y%m%d") - timedelta(days=90)).strftime("%Y%m%d")
df = pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq "
    f"WHERE date>='{win_start}' ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]") & ~df["code6"].str.startswith("688")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["volume"] > 0)]

# 行业映射
icon = sqlite3.connect(IMAP)
imap = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", icon)
icon.close()
imap["code6"] = imap["code"].astype(str).str[-6:]
l1_map = dict(zip(imap["code6"], imap["l1"]))

# ST
scon = sqlite3.connect("file:data/st_history.db?mode=ro", uri=True)
st_latest = scon.execute("SELECT code,is_st FROM st_daily s1 WHERE date=(SELECT MAX(date) FROM st_daily s2 WHERE s2.code=s1.code)").fetchall()
scon.close()
st_codes = {norm(c) for c, s in st_latest if s == 1}

# 行业映射加到df
df["l1"] = df["code6"].map(l1_map)

# 大盘当日涨跌(全市场等权)
mkt = df.groupby("d")["close"].mean().sort_index()
mkt_today = float(mkt.iloc[-1] / mkt.iloc[-2] - 1)

# 各行业当日涨跌
today_d = int(mkt.index[-1])
cutoff60 = int((datetime.strptime(str(latest), "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d"))
ind_today = {}
for l1, grp in df[df["d"] == today_d].groupby("l1"):
    ind_today[l1] = float(grp["close"].mean() / grp["close"].mean() - 1)  # placeholder
# 用更精确的: 行业等权
ind_c = df.groupby(["l1", "d"])["close"].mean().reset_index()
ind_piv = ind_c.pivot(index="d", columns="l1", values="close").sort_index()
if len(ind_piv) >= 2:
    ind_today = (ind_piv.iloc[-1] / ind_piv.iloc[-2] - 1).to_dict()

# 扫描突破+主线共振
results_all = []  # 过滤前全集(仅个股三条件, A/B实验A组选样源)
results = []      # 主线共振过滤后(B组)
for code, gg in df.groupby("code6", sort=False):
    if code in st_codes:
        continue
    gg = gg.sort_values("d")
    c = gg["close"].to_numpy(float)
    h = gg["high"].to_numpy(float)
    v = gg["volume"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 25 or d[-1] != today_d:
        continue
    if d[0] > cutoff60:  # 上市>60日(prereg universe): 窗内首根K线晚于60日前=次新剔除
        continue
    if c[-1] > 30 or c[-1] < 2:
        continue
    # 突破: 当日收盘 >= 前20日最高收盘(不含当日)
    if n < 21:
        continue
    prior_high = max(c[-21:-1])
    is_breakout = c[-1] >= prior_high
    # 放量: 当日量 >= 1.5x 前5日均量
    vol5 = np.mean(v[-6:-1])
    is_volume = v[-1] >= 1.5 * vol5 if vol5 > 0 else False
    # 涨幅>0
    chg1 = c[-1] / c[-2] - 1 if n >= 2 else 0
    is_up = chg1 > 0
    # 行业共振: 所属行业当日涨幅 > 大盘
    l1 = l1_map.get(code, "")
    ind_ret = ind_today.get(l1, -99)
    is_mainline = ind_ret > mkt_today

    if is_breakout and is_volume and is_up:
        amt = float(gg["amount"].iloc[-1])
        rec = {"code": code, "l1": l1, "close": round(float(c[-1]), 2),
               "chg1": round(float(chg1) * 100, 1), "ind_ret": round(ind_ret * 100, 2),
               "mkt_ret": round(mkt_today * 100, 2),
               "vol_ratio": round(float(v[-1] / vol5), 1),
               "amt_yi": round(amt / 1e8, 1), "is_mainline": bool(is_mainline)}
        results_all.append(rec)
        if is_mainline:
            results.append(rec)

ev = pd.DataFrame(results)
if len(ev) > 0:
    ev = ev.sort_values("amt_yi", ascending=False)
else:
    ev = pd.DataFrame(columns=["code", "l1", "close", "chg1", "ind_ret", "mkt_ret", "vol_ratio", "amt_yi"])

# 同行业最多1只
picked = []
used_l1 = set()
for _, r in ev.iterrows():
    if r["l1"] in used_l1:
        continue
    used_l1.add(r["l1"])
    picked.append(r.to_dict())
    if len(picked) >= 5:
        break

# 台账追加(同日重跑幂等, prereg revision_20261027 window_protocol 的判定数据源)
LEDGER = "data/breakout_mainline_ledger.jsonl"
rec = {"date": today_d, "scan_ts": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
       "mkt_today": round(mkt_today * 100, 2), "candidates_found": len(results),
       "candidates_prefilter": results_all, "candidates": results, "picked": picked}
rows = []
if os.path.exists(LEDGER):
    with open(LEDGER, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
rows = [r for r in rows if r.get("date") != today_d]
rows.append(rec)
rows.sort(key=lambda r: r.get("date", 0))
with open(LEDGER, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

out = {"asof": latest, "mkt_today": round(mkt_today * 100, 2),
       "candidates_found": len(results), "prefilter_found": len(results_all),
       "candidates_prefilter": results_all, "picked": picked,
       "note": "突破20日新高+放量1.5x+涨幅>0+行业跑赢大盘"}

json.dump(out, open("data/breakout_mainline_latest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print(f"突破+主线共振扫描 | 基准日={latest} | 大盘{mkt_today:+.2%}")
print(f"候选={len(results)}只(过滤前全集{len(results_all)}只) | 取前5只(同行业最多1只):")
for p in picked:
    print(f"  {p['code']} pos={p['close']} chg={p['chg1']:+.1f}% 行业={p['l1']}({p['ind_ret']:+.2f}%) "
          f"量比{p['vol_ratio']}x 额{p['amt_yi']}亿")
