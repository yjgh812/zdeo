# -*- coding: utf-8 -*-
"""daily_attribution_pipeline.py — 每日异动归因管线(生产版, 2026-11-06建)

主人令20261106归因框架落地: 每日盘后(qfq更新后)一次跑全链:
  ①异动池(涨>=5%/跌<=-7%, 主板+创业板剔ST/科创/北交)
  ②四因子收益拆解(市场/行业/风格/特质) ③异动密度开关(跌>=7%家数>15/30/50, 涨>=7%>30)
  ④持仓龙虎榜净卖出扫描(dragon_tiger_new当日×westock持仓, 净卖出>2千万=离场参考信号)
输出: data/daily_attribution_YYYYMMDD.json + stdout人话摘要
用法: python analysis/daily_attribution_pipeline.py [--date YYYYMMDD]
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
FUND = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"

ap = argparse.ArgumentParser()
ap.add_argument("--date", default="")
a = ap.parse_args()
T = int(a.date) if a.date else None

con = sqlite3.connect(QFQ, uri=True)
if T is None:
    T = int(con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0])
rows = con.execute(
    "select code,date,close,volume,amount,change_pct from stock_daily_meta_qfq "
    "where date>=? order by code,date", ((T // 10000 - 1) * 10000 + 101,)).fetchall()
con.close()
g = defaultdict(lambda: {"d": [], "c": [], "v": [], "a": [], "p": []})
for code, d, c, v, amt, p in rows:
    s = g[code]
    s["d"].append(int(d)); s["c"].append(float(c or 0)); s["v"].append(float(v or 0))
    s["a"].append(float(amt or 0)); s["p"].append(float(p or 0) if p is not None else 0.0)

icon = sqlite3.connect("file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro", uri=True)
ind = {str(k)[-6:]: v for k, v in icon.execute("select code,l1 from l1_member").fetchall()}
icon.close()
scon = sqlite3.connect("file:D:/zcodesj/data/st_history.db?mode=ro", uri=True)
st_set = {str(c)[-6:] for c, f in scon.execute(
    "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)"
).fetchall() if f == 1}
scon.close()

# ---- ①异动池 + ②横截面拆解 ----
data = []
for code, s in g.items():
    c6 = code[-6:]
    if c6[:3] in ("688", "689") or c6[0] in ("4", "8", "9") or c6 in st_set:
        continue
    idx = next((i for i, x in enumerate(s["d"]) if x == T), None)
    if idx is None or idx < 25 or s["c"][idx] <= 0 or not (0.5 < s["c"][idx] <= 200):
        continue
    r = s["p"][idx] / 100.0
    if abs(r) > 0.35:
        continue
    ma20 = float(np.mean(s["c"][idx - 20:idx]))
    vseg = [x for x in s["v"][idx - 20:idx] if x > 0]
    volr = s["v"][idx] / float(np.mean(vseg)) if vseg and s["v"][idx] > 0 else 99
    size = float(np.log(np.mean([x for x in s["a"][idx - 20:idx] if x > 0]) + 1)) if any(s["a"][idx - 20:idx]) else 0
    mom = s["c"][idx - 1] / s["c"][idx - 21] - 1 if s["c"][idx - 21] > 0 else 0
    rets = [s["p"][idx - k] / 100.0 for k in range(1, 11) if s["c"][idx - k] > 0]
    vol20 = float(np.std(rets)) if len(rets) >= 5 else 0
    data.append({"code": c6, "ind": ind.get(c6, "未知"), "r": r, "volr": volr, "mom": mom, "vol20": vol20, "size": size})

r_mkt = float(np.mean([d["r"] for d in data]))
for k in ("mom", "vol20", "size"):
    v = np.array([d[k] for d in data]); mu, sd = float(np.mean(v)), float(np.std(v)) or 1
    for d in data:
        d[k + "_z"] = (d[k] - mu) / sd
ind_cnt = defaultdict(int)
for d in data:
    ind_cnt[d["ind"]] += 1
inds = sorted(k for k, v in ind_cnt.items() if v >= 10)
ind_idx = {k: i for i, k in enumerate(inds)}
D = []
for d in data:
    row = [0.0] * len(inds)
    i = ind_idx.get(d["ind"])
    if i is not None:
        row[i] = 1.0
    D.append(row)
X = np.hstack([np.array(D), np.array([[d["mom_z"], d["vol20_z"], d["size_z"]] for d in data])])
y = np.array([d["r"] - r_mkt for d in data])
beta, *_ = np.linalg.lstsq(X, y, rcond=None)
resid = y - X @ beta
for j, d in enumerate(data):
    i = ind_idx.get(d["ind"])
    d["idio"] = float(resid[j])
    d["idio_ratio"] = abs(resid[j]) / max(abs(d["r"]), 1e-9)

up = sorted([d for d in data if d["r"] >= 0.05], key=lambda x: -x["r"])
dn = sorted([d for d in data if d["r"] <= -0.07], key=lambda x: x["r"])
n_seal = sum(1 for code, s in g.items()
             if code[-6:] not in st_set and code[-6:][0] not in "489" and code[-6:][:3] not in ("688", "689")
             and T in s["d"] and (lambda i: s["c"][i] / s["c"][i - 1] - 1 >= (0.199 if code[-6:][:3] in ("300", "301") else 0.099) if i and s["c"][i - 1] > 0 else False)(s["d"].index(T) if T in s["d"] else -1))

# ---- ③密度开关 ----
n_down7 = len(dn)
n_up7 = sum(1 for d in data if d["r"] >= 0.07)
density = {
    "down7": n_down7, "up7": n_up7, "seal": n_seal,
    "down_level": "空仓区(>50)" if n_down7 > 50 else "降仓区(>30)" if n_down7 > 30 else "谨慎区(>15)" if n_down7 > 15 else "正常区",
    "overheat": n_up7 > 30,
}

# ---- ④持仓龙虎榜净卖出 ----
lhb_hits = []
try:
    from stop_loss_executor import _fetch_positions_via_mcp
    pos = _fetch_positions_via_mcp() or []
    held = {str(p["code"])[-6:]: p for p in pos if int(p.get("quantity", 0) or 0) > 0}
    if held:
        fcon = sqlite3.connect(FUND, uri=True)
        td = str(T)
        for r in fcon.execute(
            "select ts_code, SUM(net_amount) from dragon_tiger_new where trade_date=? group by ts_code", (td,)):
            c6 = r[0][:6]
            if c6 in held and r[1] is not None and r[1] < -20000000:
                lhb_hits.append({"code": c6, "name": held[c6].get("name", ""), "net_qianwan": round(r[1] / 1e7, 1)})
        fcon.close()
except Exception as e:
    lhb_hits = [{"error": str(e)[:80]}]

out = {
    "T": T,
    "market": {"n": len(data), "eq_ret_pct": round(r_mkt * 100, 2)},
    "density": density,
    "up_n": len(up), "dn_n": len(dn),
    "up_top15": [{"code": d["code"], "ind": d["ind"], "pct": round(d["r"] * 100, 1),
                  "idio_pct": round(d["idio"] * 100, 1), "idio_ratio": round(d["idio_ratio"], 2),
                  "volr": round(d["volr"], 2)} for d in up[:15]],
    "dn_top15": [{"code": d["code"], "ind": d["ind"], "pct": round(d["r"] * 100, 1),
                  "idio_pct": round(d["idio"] * 100, 1), "idio_ratio": round(d["idio_ratio"], 2)} for d in dn[:15]],
    "held_lhb_net_sell": lhb_hits,
    "built_at": __import__("time").strftime("%Y-%m-%d %H:%M"),
}
outp = os.path.join(ROOT, "data", "daily_attribution_%d.json" % T)
json.dump(out, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("==== 每日归因 %d ====" % T)
print("全市场%d票 等权%+.2f%% | 大涨%d 大跌%d 封板%d" % (len(data), r_mkt * 100, len(up), len(dn), n_seal))
print("密度开关: 跌>=7%%=%d家[%s] 涨>=7%%=%d家 %s" % (n_down7, density["down_level"], n_up7, "过热!" if density["overheat"] else ""))
if up:
    idio_up = float(np.median([d["idio_ratio"] for d in up]))
    print("大涨组特质占比中位%.0f%%" % (idio_up * 100))
if dn:
    idio_dn = float(np.median([d["idio_ratio"] for d in dn]))
    print("大跌组特质占比中位%.0f%%" % (idio_dn * 100))
print("持仓龙虎榜净卖出命中:", lhb_hits if lhb_hits else "无")
print("产物:", outp)
