# -*- coding: utf-8 -*-
"""dip_shadow_scan_daily.py — 深潜影子每日扫描器(生产版, PREREG_DIP_ENV_ABS_ACTIVE_20261029)
口径(指纹 177e7b7a...): 弱市开关(等权指数<MA20) + 20日回撤>=20% 事件。
输出: data/dip_shadow_scan_latest.json (开关状态+全信号+Top10随机抽样[seed=scan_date], 零买入零接线)。
影子协议: 仅落盘记账; 模拟盘买入需主人另行批准。kill条件: 滚动窗胜率<45%或中位<-1.0%暂停。"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
OUT = os.path.join(ROOT, "data", "dip_shadow_scan_latest.json")
WIN = 20
DD = -0.20
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute("select distinct date from stock_daily_meta_qfq where date>=20240101 order by date")})
didx = {d: i for i, d in enumerate(dates_all)}
N = len(dates_all)
rows = con.execute("select code,date,open,close from stock_daily_meta_qfq where date>=20240101 order by code,date").fetchall()
con.close()
H = defaultdict(list)
for code, d, o, c in rows:
    H[code].append((didx[int(d)], o, c))
codes_sorted = sorted(H.keys())
cidx = {c: i for i, c in enumerate(codes_sorted)}
C = np.full((len(codes_sorted), N), np.nan, dtype=np.float64)
for code, arr in H.items():
    ci = cidx[code]
    for gi, o, c in arr:
        C[ci, gi] = c
eq = np.zeros(N); cnt = np.zeros(N)
for code, arr in H.items():
    ci = cidx[code]
    for gi, o, c in arr:
        eq[gi] += c; cnt[gi] += 1
eq /= np.maximum(cnt, 1)
for i in range(1, N):
    if np.isnan(eq[i]):
        eq[i] = eq[i-1]
cs = np.cumsum(np.insert(eq, 0, 0.0))
eq_ma20 = np.full(N, np.nan)
eq_ma20[19:] = (cs[20:] - cs[:-20]) / 20
today_i = N - 1
scan_date = dates_all[today_i]
env_on = bool(eq[today_i] < eq_ma20[today_i])
print(f"scan_date={scan_date} switch={'ON' if env_on else 'OFF'} eq={eq[today_i]:.2f} ma20={eq_ma20[today_i]:.2f}", flush=True)

from numpy.lib.stride_tricks import sliding_window_view
signals = []
for code, arr in H.items():
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    n = len(arr)
    if n < WIN:
        continue
    g = [a[0] for a in arr]
    cl = np.array([a[2] for a in arr])
    if g[-1] != today_i:
        continue  # 当日停牌
    sw = sliding_window_view(cl, WIN)
    dd = -(cl[-1] / sw[-1].max() - 1)
    if dd >= 0.20:
        signals.append({"code": code, "drawdown20_pct": round(dd * 100, 1), "close": round(float(cl[-1]), 2)})
signals.sort(key=lambda x: -x["drawdown20_pct"])

import random
rng = random.Random(scan_date)
sample10 = rng.sample(signals, min(10, len(signals))) if signals else []
from stock_name_lookup import get_name
for s in sample10:
    s["name"] = get_name(s["code"]) or ""

result = {"scan_date": scan_date, "built_at": time.strftime("%Y-%m-%d %H:%M"),
          "env_switch": "ON" if env_on else "OFF",
          "eq": round(float(eq[today_i]), 2), "eq_ma20": round(float(eq_ma20[today_i]), 2),
          "n_signals": len(signals),
          "shadow_sample_top10_random_seed_scan_date": sample10,
          "prereg": "PREREG_DIP_ENV_ABS_ACTIVE_20261029 fp=177e7b7a",
          "note": "shadow-only zero-buy; simulated buy needs owner approval"}
json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"signals={len(signals)} sample={len(sample10)} written={OUT} ({time.time()-t0:.0f}s)")
