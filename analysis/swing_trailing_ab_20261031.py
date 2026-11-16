# -*- coding: utf-8 -*-
"""swing_trailing_ab_20261031.py

移动止盈(trailing)三档 vs 现行A规则的历史A/B — 承接 swing_sell_rule_ab_20261031 的发现
(A现行期望最高+0.11%, D上界+5.76%未捕捉; 外部文献: trailing价值=降回撤+理论最优解)。

同一入场口径: up段确认后20日内首个回踩<=起点x1.08。
规则:
  A(基线): -8%硬止损(盘中触发) + T20收盘强平
  T5/T8/T12: -8%止损 + 自入场最高收盘回撤X%离场(收盘判定) + T20收盘封顶
  T8L: 同T8但T60封顶(测延长持有)
另输出每笔入场后窗口内最大不利偏移(MAE)均值 — 验证trailing的降回撤属性。
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
    "where seg_type='up' and days>=5 and end_date>=20161001"
).fetchall()
print("up段样本:", len(segs), flush=True)

codes = sorted(set(s[0] for s in segs))
px = {}
for code in codes:
    rows = con.execute(
        "select date,close,high,low from stock_daily_meta_qfq "
        "where code=? and date>=20150901 order by date",
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

HOLD = 20
HOLD_LONG = 60
STOP = 0.08
TRAIL = {"T5": 0.05, "T8": 0.08, "T12": 0.12}

res = {k: [] for k in ["A", "T5", "T8", "T12", "T8L"]}
mae = []

for code, sd, ed in segs:
    if code not in px:
        continue
    dts, cls, his, los = px[code]
    i = int(np.searchsorted(dts, ed))
    j = int(np.searchsorted(dts, sd))
    if i >= len(dts) or dts[i] != ed or j >= len(dts) or dts[j] != sd:
        continue
    entry_i = None
    for h0 in range(1, 21):
        k0 = i + h0
        if k0 >= len(dts):
            break
        if cls[k0] <= cls[j] * 1.08:
            entry_i = k0
            break
    if entry_i is None:
        continue
    if entry_i + HOLD_LONG + 2 >= len(dts):
        continue
    entry = cls[entry_i]
    if entry <= 0:
        continue

    # A: -8% + T20
    a = None
    for h in range(1, HOLD + 1):
        k = entry_i + h
        if (los[k] / entry - 1.0) <= -STOP:
            a = -8.0
            break
    if a is None:
        a = (cls[entry_i + HOLD] / entry - 1.0) * 100.0
    res["A"].append(a)

    # trailing 变体
    for name, tx in TRAIL.items():
        hold = HOLD
        r = None
        peak = entry
        for h in range(1, hold + 1):
            k = entry_i + h
            peak = max(peak, cls[k - 1])
            if (los[k] / entry - 1.0) <= -STOP:
                r = -8.0
                break
            if cls[k] <= peak * (1.0 - tx):
                r = (cls[k] / entry - 1.0) * 100.0
                break
        if r is None:
            r = (cls[entry_i + hold] / entry - 1.0) * 100.0
        res[name].append(r)

    # T8L: 同T8, 60日封顶
    r = None
    peak = entry
    for h in range(1, HOLD_LONG + 1):
        k = entry_i + h
        peak = max(peak, cls[k - 1])
        if (los[k] / entry - 1.0) <= -STOP:
            r = -8.0
            break
        if cls[k] <= peak * (1.0 - 0.08):
            r = (cls[k] / entry - 1.0) * 100.0
            break
    if r is None:
        r = (cls[entry_i + HOLD_LONG] / entry - 1.0) * 100.0
    res["T8L"].append(r)

    # MAE: 入场后20日最低low相对entry的最大不利偏移
    lo_min = los[entry_i + 1:entry_i + HOLD + 1].min()
    mae.append((lo_min / entry - 1.0) * 100.0)


def st(k):
    arr = np.array(res[k], dtype=float)
    if len(arr) == 0:
        return "无样本"
    m = arr.mean()
    w = (arr > 0).mean() * 100.0
    med = np.median(arr)
    return f"{len(arr)}段 均值{m:+.2f}% 胜率{w:.1f}% 中位{med:+.2f}%"


print("")
print("=== trailing A/B (同一入场口径, 十年) ===")
for k in ("A", "T5", "T8", "T12", "T8L"):
    print(k + ": " + st(k))
m = np.array(mae)
print(f"MAE均值{m.mean():.2f}% 中位{np.median(m):.2f}% (入场后20日最大不利偏移)")
print("耗时 %.0fs" % (time.time() - t0))
