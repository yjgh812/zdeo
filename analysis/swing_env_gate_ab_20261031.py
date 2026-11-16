# -*- coding: utf-8 -*-
"""swing_env_gate_ab_20261031.py

环境闸选择力最终裁决: 把"up段确认->回踩介入->T20退出"框架(十年2026段)
按信号日HMM环境(mkt_state)分组对比期望。
若上涨趋势组显著优于其他 → 环境闸应推广到所有买入;
若无差异 → 深潜+9.51%为孤例, 环境闸不神化。
"""
import os
import sys
import sqlite3
import time
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"

t0 = time.time()
con = sqlite3.connect("file:" + QFQ + "?mode=ro", uri=True)
scon = sqlite3.connect("file:data/swing_labels.db?mode=ro", uri=True)

mkt = dict(scon.execute("select date, label from mkt_state").fetchall())
segs = scon.execute(
    "select code,start_date,end_date from swing_segments "
    "where seg_type='up' and days>=5 and end_date>=20161001"
).fetchall()
scon.close()
print("up段样本:", len(segs), flush=True)

codes = sorted(set(s[0] for s in segs))
px = {}
for code in codes:
    rows = con.execute(
        "select date,close,low from stock_daily_meta_qfq "
        "where code=? and date>=20150901 order by date",
        (code,)).fetchall()
    if len(rows) >= 30:
        px[code] = (
            [int(r[0]) for r in rows],
            np.array([float(r[1]) for r in rows]),
            np.array([float(r[2]) for r in rows]),
        )
con.close()
print("有价code:", len(px), flush=True)

res = {}
HOLD = 20
STOP = 0.08

for code, sd, ed in segs:
    if code not in px:
        continue
    dts, cls, los = px[code]
    i = int(np.searchsorted(dts, ed))
    j = int(np.searchsorted(dts, sd))
    if i >= len(dts) or dts[i] != ed or j >= len(dts) or dts[j] != sd:
        continue
    if i + HOLD + 2 >= len(dts):
        continue
    entry_i = None
    for h0 in range(1, 21):
        k0 = i + h0
        if k0 >= len(dts):
            break
        if cls[k0] <= cls[j] * 1.08:
            entry_i = k0
            break
    if entry_i is None or entry_i + HOLD >= len(dts):
        continue
    entry = cls[entry_i]
    if entry <= 0:
        continue
    env = "无状态"
    for off in range(6):
        if dts[entry_i] - off in mkt:
            env = mkt[dts[entry_i] - off]
            break
    r = None
    for h in range(1, HOLD + 1):
        k = entry_i + h
        if (los[k] / entry - 1.0) <= -STOP:
            r = -8.0
            break
    if r is None:
        r = (cls[entry_i + HOLD] / entry - 1.0) * 100.0
    res.setdefault(env, []).append(r)


print("")
print("=== 环境闸最终裁决: 回踩介入框架按HMM环境分组 (A规则退出, 十年) ===")
total = 0
for env in ("上涨趋势", "宽幅震荡", "窄幅震荡", "下跌趋势", "无状态"):
    lst = res.get(env, [])
    if not lst:
        print(env + ": 无样本")
        continue
    a = np.array(lst)
    total += len(a)
    print(f"{env:<6} {len(a):>5}段 均{a.mean():+6.2f}% 胜率{(a>0).mean()*100:5.1f}% 中位{np.median(a):+6.2f}%")
print(f"合计 {total} 段")
print("耗时 %.0fs" % (time.time() - t0))
