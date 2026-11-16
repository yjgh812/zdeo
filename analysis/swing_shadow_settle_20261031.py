# -*- coding: utf-8 -*-
"""swing_shadow_settle_20261031.py

60分钟闸影子对照结算: 同一批扫描里"通过组 vs 死叉否决组"的T+20前向收益对比。
这是"60分闸是否有选择力"的最终实盘裁决工具。
用法: python analysis/swing_shadow_settle_20261031.py <picks_json路径> [hold_days]
数据: picks json 的 methods[src].picks(通过组) + methods[src].p60_reject_list(否决组);
      qfq 收盘价, entry=信号日收盘, 满窗T+20, 未满窗计至今浮盈(标注口径)。
"""
import os
import sys
import json
import sqlite3
import time
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"

t0 = time.time()
picks_path = sys.argv[1] if len(sys.argv) > 1 else "data/revival_wave23_picks_20261028.json"
HOLD = int(sys.argv[2]) if len(sys.argv) > 2 else 20

doc = json.load(open(picks_path, encoding="utf-8"))
sig_date = int(doc.get("signal_date", 0))
print(f"picks: {picks_path} | signal_date={sig_date} | hold={HOLD}日")

con = sqlite3.connect("file:" + QFQ + "?mode=ro", uri=True)

pass_list = []
rej_list = []
for src, v in doc.get("methods", {}).items():
    if isinstance(v, dict):
        for x in (v.get("picks") or []):
            pass_list.append((str(x.get("code", ""))[-6:], x.get("name", "?"), float(x.get("price", 0) or 0)))
        for x in (v.get("p60_reject_list") or []):
            rej_list.append((str(x.get("code", ""))[-6:], x.get("name", "?"), float(x.get("price", 0) or 0)))
    elif isinstance(v, list):
        for x in v:
            pass_list.append((str(x.get("code", ""))[-6:], x.get("name", "?"), float(x.get("price", 0) or 0)))

print(f"通过组: {len(pass_list)}只 | 否决组: {len(rej_list)}只")


def fwd(code, entry):
    rows = con.execute(
        "select close from stock_daily_meta_qfq where code=? and date>=? order by date limit ?",
        (code, sig_date, HOLD + 1)).fetchall()
    if not rows or entry <= 0:
        return None
    c0 = float(rows[0][0])
    if c0 <= 0:
        return None
    idx = HOLD if len(rows) >= HOLD + 1 else len(rows) - 1
    full = len(rows) >= HOLD + 1
    return (float(rows[idx][0]) / c0 - 1.0) * 100.0, full


def settle(name, lst):
    vals, n_full = [], 0
    for code, nm, px in lst:
        r = fwd(code, px)
        if r is None:
            continue
        vals.append(r[0])
        if r[1]:
            n_full += 1
    if not vals:
        print(name + ": 无样本")
        return
    a = np.array(vals)
    print(f"{name:<10} {len(a):>3}只 均{a.mean():+6.2f}% 胜率{(a>0).mean()*100:5.1f}% "
          f"中位{np.median(a):+6.2f}% (满窗{n_full})")


settle("通过组", pass_list)
settle("否决组", rej_list)
con.close()
print("耗时 %.0fs" % (time.time() - t0))
