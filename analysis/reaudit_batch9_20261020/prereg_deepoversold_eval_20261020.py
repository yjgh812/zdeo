# -*- coding: utf-8 -*-
"""PREREG-DEEP-OVERSOLD-BOUNCE-V1 (指纹9c51209fea8142ed) 一次跑执行器.

预注册判据(data/prereg_deep_oversold_bounce_DRAFT_20261020.json, 跑前钉死):
  事件: ret20<=-10% 收盘判定, 逐票冷却5日; 宇宙: 前缀+剔ST+剔次新60bar+剔c>30
  entry=次开, fwd20=c[t+20]/o[t+1]-1; 窗口2016-01~最新
  G1 med>=+0.8%且win>=52% | G2 市场超额med>0 | G3 分年正med年占比>=0.7 | G4 扣双边0.3%后med>0
  左尾控制臂: 剔信号日pct<=-9.5与距250日高回撤>60%后单独报G1-G4
  kill: 任一门不达 -> 登记终结不复活
输出: analysis/reaudit_batch9_20261020/prereg_deepoversold_result.json
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PREREG = r"D:\zcodesj\data\prereg_deep_oversold_bounce_DRAFT_20261020.json"
OUT = os.path.join(HERE, "prereg_deepoversold_result.json")
PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")
COST = 0.003

pre = json.load(open(PREREG, encoding="utf-8"))
SEM = ("hypothesis", "universe", "event", "entry_exit", "main_gate_PASS_all",
       "left_tail_control_arm", "kill")
body = {k: pre[k] for k in SEM}
import hashlib
fp = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
assert fp == pre["fingerprint"], "FAIL-CLOSED: 语义契约指纹不符"

con = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
ph = ",".join("?" * len(PREFIXES))
df = pd.read_sql(
    "SELECT code,date,open,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>=? AND substr(code,1,3) IN (%s)" % ph,
    con, params=("20150601",) + PREFIXES)
con.close()
df["date"] = df["date"].astype(str)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
st_cache = r"D:\zcodesj\attribution\stock_info_cache.json"
st_codes = set()
if os.path.exists(st_cache):
    st = json.load(open(st_cache, encoding="utf-8"))["stocks"]
    st_codes = {k for k, v in st.items() if v.get("is_st")}

ev_rows, mkt_rows = [], []
for code, g in df.groupby("code", sort=False):
    if code in st_codes:
        continue
    n = len(g)
    if n < 280:
        continue
    o = g["open"].to_numpy(float); c = g["close"].to_numpy(float)
    v = g["volume"].to_numpy(float); d = g["date"].to_numpy()
    pct = g["change_pct"].to_numpy(float)
    s = pd.Series(c)
    ret20 = c / s.shift(20).to_numpy() - 1.0
    hi250 = s.rolling(250, min_periods=250).max().to_numpy()
    f20 = np.full(n, np.nan)
    for i in range(n - 21):
        j = i + 1
        if o[j] > 0 and np.isfinite(o[j]) and np.isfinite(c[i + 20]):
            f20[i] = c[i + 20] / o[j] - 1.0
    m20 = np.full(n, np.nan)  # 同窗市场: 在主循环外按日聚合, 此处先记entry日
    last = -99
    for i in range(60, n - 21):
        if d[i] < "20160101" or not np.isfinite(f20[i]) or not np.isfinite(ret20[i]):
            continue
        if i - last < 5:
            continue
        if ret20[i] > -0.10:
            continue
        if c[i] > 30.0:
            continue
        last = i
        dd = (c[i] / hi250[i] - 1.0) if np.isfinite(hi250[i]) else np.nan
        ev_rows.append((d[i], code, f20[i], pct[i], dd))
    for i in range(60, n - 21, 9):
        if d[i] >= "20160101" and np.isfinite(f20[i]):
            mkt_rows.append((d[i], f20[i]))

ev = pd.DataFrame(ev_rows, columns=["date", "code", "f20", "pct", "dd250"])
mkt = pd.DataFrame(mkt_rows, columns=["date", "f20"]).groupby("date")["f20"].mean()
ev["mkt"] = ev["date"].map(mkt)
ev = ev.dropna(subset=["mkt"])
ev["excess"] = ev["f20"] - ev["mkt"]
ev["year"] = ev["date"].str[:4]

def gates(sub, label):
    med = float(sub["f20"].median()) * 100
    win = float((sub["f20"] > 0).mean()) * 100
    med_x = float(sub["excess"].median()) * 100
    med_cost = float((sub["f20"] - COST).median()) * 100
    ymed = sub.groupby("year")["f20"].median()
    share = float((ymed > 0).mean())
    g = {"G1_med>=0.8&win>=52": bool(med >= 0.8 and win >= 52),
         "G2_excess_med>0": bool(med_x > 0),
         "G3_yearly_share>=0.7": bool(share >= 0.7),
         "G4_med_net_cost>0": bool(med_cost > 0)}
    return {"arm": label, "n": int(len(sub)), "med_pct": round(med, 3), "win_pct": round(win, 2),
            "excess_med_pct": round(med_x, 3), "med_net_cost_pct": round(med_cost, 3),
            "yearly_share_pos": round(share, 3), "n_years": int(ymed.shape[0]),
            "yearly_med_pct": {k: round(float(v) * 100, 3) for k, v in ymed.items()},
            "gates": g, "PASS": all(g.values())}

main = gates(ev, "main")
lt = ev[(ev["pct"] > -9.5) & ~(ev["dd250"] <= -0.60)].dropna(subset=["dd250"])
ltg = gates(lt, "left_tail_control")
verdict = "PASS" if main["PASS"] else "FAIL_KILL"
res = {
    "prereg_id": pre["prereg_id"], "fingerprint": pre["fingerprint"],
    "window": [ev["date"].min(), ev["date"].max()], "verdict": verdict,
    "main": main, "left_tail_control": ltg,
    "overlap_proxy_limitdown_share": round(float((ev["pct"] <= -9.5).mean()), 4),
    "caveats": ["全量一次性预注册执行, 零调参自由度; 与LOW_REPAIR_FAMILY(破跌停族)重叠以信号日跌停占比为代理"],
}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("VERDICT:", verdict)
print("MAIN:", json.dumps({k: main[k] for k in ["n", "med_pct", "win_pct", "excess_med_pct",
                                                "med_net_cost_pct", "yearly_share_pos", "PASS"]}, ensure_ascii=False))
print("LT  :", json.dumps({k: ltg[k] for k in ["n", "med_pct", "win_pct", "PASS"]}, ensure_ascii=False))
print("limitdown share:", res["overlap_proxy_limitdown_share"])
