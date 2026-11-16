# -*- coding: utf-8 -*-
"""swing_sell_rule_ab_20261031.py

卖出规则历史A/B: 回答主人"能否找到卖点/买卖区域"。
同一入场口径(近一年up段确认日收盘买入,价格<=起点x1.05)对比四种退出规则。
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

segs = scon.execute(
    "select code,start_date,end_date from swing_segments "
    "where seg_type='up' and days>=5 and end_date>=20251001"
).fetchall()
print("up段样本:", len(segs), flush=True)

codes = sorted(set(s[0] for s in segs))
px = {}
for code in codes:
    rows = con.execute(
        "select date,close,high,low from stock_daily_meta_qfq "
        "where code=? and date>=20250901 order by date",
        (code,)).fetchall()
    if len(rows) >= 30:
        px[code] = (
            [int(r[0]) for r in rows],
            np.array([float(r[1]) for r in rows]),
            np.array([float(r[2]) for r in rows]),
            np.array([float(r[3]) for r in rows]),
        )
con.close()
scon.close()
print("有价code:", len(px), flush=True)

res = {"A": [], "B": [], "C": [], "D": []}
STOP = 0.08

for code, sd, ed in segs:
    if code not in px:
        continue
    dts, cls, his, los = px[code]
    i = np.searchsorted(dts, ed)
    j = np.searchsorted(dts, sd)
    i = int(i)
    j = int(j)
    if i >= len(dts) or dts[i] != ed:
        continue
    if j >= len(dts) or dts[j] != sd:
        continue
    if i + 41 >= len(dts):
        continue
    # 入场=确认后20日内首个回踩(收盘<=起点x1.08): 模拟"介入区域"语义
    entry_i = None
    for h0 in range(1, 21):
        k0 = i + h0
        if cls[k0] <= cls[j] * 1.08:
            entry_i = k0
            break
    if entry_i is None:
        continue
    if entry_i + 21 >= len(dts):
        continue
    entry = cls[entry_i]
    if entry <= 0:
        continue
    lo_start = cls[j]
    i = entry_i  # 退出模拟从入场次日起算

    a = None
    b = None
    c = None
    for h in range(1, 21):
        k = i + h
        hit_stop = (los[k] / entry - 1.0) <= -STOP
        if a is None and hit_stop:
            a = -8.0
        if b is None and (hit_stop or cls[k] < cls[k-19:k+1].mean()):
            if hit_stop:
                b = -8.0
            else:
                b = (cls[k] / entry - 1.0) * 100.0
        if c is None and (hit_stop or cls[k] < lo_start):
            if hit_stop:
                c = -8.0
            else:
                c = (cls[k] / entry - 1.0) * 100.0
        if a is not None and b is not None and c is not None:
            break
    if a is None:
        a = (cls[i+20] / entry - 1.0) * 100.0
    if b is None:
        b = (cls[i+20] / entry - 1.0) * 100.0
    if c is None:
        c = (cls[i+20] / entry - 1.0) * 100.0
    d = (cls[i+1:i+21].max() / entry - 1.0) * 100.0
    res["A"].append(a)
    res["B"].append(b)
    res["C"].append(c)
    res["D"].append(d)

def st(k):
    arr = np.array(res[k], dtype=float)
    if len(arr) == 0:
        return "无样本"
    m = arr.mean()
    w = (arr > 0).mean() * 100.0
    med = np.median(arr)
    return f"{len(arr)}段 均值{m:+.2f}% 胜率{w:.1f}% 中位{med:+.2f}%"

print("")
print("=== 同一入场口径, 四种退出规则 ===")
for k in ("A", "B", "C", "D"):
    print(k + ": " + st(k))
print("耗时 %.0fs" % (time.time() - t0))
