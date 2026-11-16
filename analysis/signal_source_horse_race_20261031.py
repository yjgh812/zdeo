# -*- coding: utf-8 -*-
"""signal_source_horse_race_20261031.py

历史执行台账(全部BUY)回填T+20前向收益 + HMM环境标注。
产出两张表:
  1) 信号源赛马扩展版(实盘T+20结算的提前版, 样本=全部历史BUY)
  2) 环境闸选择力验证(上涨趋势日 vs 其他环境日的信号前向收益差)
口径: entry=信号日(trade_date)收盘, exit=+20交易日收盘, 前向收益未计费用。
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
con = sqlite3.connect("file:" + QFQ + "?mode=ro", uri=True)
scon = sqlite3.connect("file:data/swing_labels.db?mode=ro", uri=True)

mkt = dict(scon.execute("select date, label from mkt_state").fetchall())
scon.close()

buys = []
seen = set()
for line in open("data/executor_orders.jsonl", encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    if r.get("action") != "BUY" or not r.get("source"):
        continue
    code = str(r.get("code", ""))[-6:]
    td = int(r.get("trade_date", 0))
    src = r.get("source", "?")
    px = float(r.get("price", 0) or 0)
    key = (src, code, td)  # 2026-10-31 修复: 按源去重(原全局(code,td)去重会吞同票同日多源买入, 提前参考表偏差)
    if key in seen:
        continue
    seen.add(key)
    buys.append((code, td, src, px))
print("BUY笔数(去重):", len(buys), flush=True)

rows_out = []
n_nodata = 0
for code, td, src, px in buys:
    rows = con.execute(
        "select date, close from stock_daily_meta_qfq where code=? and date>=? order by date limit 80",
        (code, td)).fetchall()
    if len(rows) < 2:
        n_nodata += 1
        continue
    cls = [float(r[1]) for r in rows]
    entry = cls[0]
    if entry <= 0:
        n_nodata += 1
        continue
    full = len(cls) >= 21
    if full:
        fwd = (cls[20] / entry - 1.0) * 100.0
    else:
        fwd = (cls[-1] / entry - 1.0) * 100.0  # 未满窗: 至今浮盈
    env = "无状态"
    for off in range(6):
        if td - off in mkt:
            env = mkt[td - off]
            break
    rows_out.append((src, code, td, fwd, env, full))

con.close()
print("有效样本:", len(rows_out), "无价剔除:", n_nodata, flush=True)

# 表1: 信号源赛马(按均值降序, 最少2笔)
from collections import defaultdict
by_src = defaultdict(list)
for src, code, td, fwd, env, full in rows_out:
    by_src[src].append(fwd)
print("")
print("=== 表1: 信号源赛马(历史全部BUY, T+20满窗; 未满窗计至今浮盈, 未计费) ===")
rank = sorted(by_src.items(), key=lambda kv: -(np.mean(kv[1]) if kv[1] else -99))
for src, lst in rank:
    if len(lst) < 2:
        continue
    a = np.array(lst)
    print(f"{src:<26} {len(a):>3}笔 均{a.mean():+6.2f}% 胜率{(a>0).mean()*100:4.1f}% 中位{np.median(a):+6.2f}%")

# 表2: 环境闸选择力
by_env = defaultdict(list)
for src, code, td, fwd, env, full in rows_out:
    by_env[env].append(fwd)
print("")
print("=== 表2: HMM环境闸选择力(同一批信号按环境分组) ===")
for env in ("上涨趋势", "宽幅震荡", "窄幅震荡", "下跌趋势", "无状态"):
    lst = by_env.get(env, [])
    if not lst:
        continue
    a = np.array(lst)
    print(f"{env:<6} {len(a):>4}笔 均{a.mean():+6.2f}% 胜率{(a>0).mean()*100:4.1f}% 中位{np.median(a):+6.2f}%")
n_full = sum(1 for r in rows_out if r[5])
print(f"(满窗{sum(1 for r in rows_out if r[5])}笔/未满窗{len(rows_out)-n_full}笔)")
print("耗时 %.0fs" % (time.time() - t0))
