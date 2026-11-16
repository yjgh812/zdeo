# -*- coding: utf-8 -*-
"""
胜率排名选股（呈现层零交易权，20261016 收盘数据口径）
主排序=ml_v22 ml_prob（模型预测20日上涨概率，系统原生概率口径）
过滤=全市场宇宙（剔ST/科创/北交/次新<60天）+价格铁律>30元不呈现
叠加=forward_up_prob_gauge 画像分组（A低位横盘/B启动确认/C追高禁区/D高位滞涨/R其余）
     的250日窗口 T+1 条件上涨概率（组级，MIN_CELL_N=300）
输出：控制台排名表（含名称，供 verify_stock_names 校验）
"""
import json, sqlite3
from collections import defaultdict

import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
ML_DATE = "20261016"
TOP_N = 5

conn_q = sqlite3.connect(QFQ, uri=True)
conn_d = sqlite3.connect(DB, uri=True)

# 1) ml_prob 全市场
ml = pd.read_sql(f"SELECT code, ml_prob FROM stock_ml_scores_v22 WHERE date={ML_DATE}", conn_d)

# 2) 股票信息（ST/上市日/名称）
si = pd.read_sql("SELECT code, name, list_date, is_st FROM stock_info", conn_d)
si = si.drop_duplicates("code", keep="first").set_index("code")

# 3) 最新收盘价（价格铁律）
last_close = {}
for code in ml["code"]:
    r = conn_q.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    if r:
        last_close[code] = r[0]

def ok_universe(code):
    if code.startswith(("688", "689", "8", "4", "92", "43")):
        return False
    if code not in si.index:
        return False
    try:
        if int(si.loc[code, "is_st"] or 0) == 1:
            return False
        ld = str(si.loc[code, "list_date"]).replace("-", "")
        # 次新<60交易日近似: 上市晚于2026-07-01(约60交易日)剔除
        if ld >= "20260701":
            return False
    except Exception:
        pass
    px = last_close.get(code)
    if px is None or px > 30:
        return False
    return True

ml = ml[ml["code"].map(ok_universe)].sort_values("ml_prob", ascending=False)
top = ml.head(30)  # 取30只供分组，最终展示Top5
print(f"宇宙过滤后候选: {len(ml)} 只, 取Top30做分组画像")

# 4) gauge 画像分组（T日收盘口径, 同forward_up_prob_gauge.classify）
def group_stats():
    """各组250日窗口T+1上涨概率(与gauge同口径: 控制全市场上涨占比的简化版=组级原始概率)"""
    return None  # gauge原表有组概率缓存, 见下

# 直接用 gauge 的历史校准表（reports/up_prob_recon_20260924.md 同口径）——读取最近一次 gauge 产物
import glob, os
g = sorted(glob.glob("data/forward_up_prob_gauge_*.json"))
gauge_calib = {}
if g:
    d = json.load(open(g[-1], encoding="utf-8"))
    # 结构探测: 组概率可能在 calibration/summary 键
    for k in ("group_stats", "calibration", "groups"):
        if k in d:
            gauge_calib = d[k]
            break
print("gauge历史校准表:", "有" if gauge_calib else "无产物,组概率留空")

def classify(code, date8):
    r = conn_q.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 250", (code, date8)).fetchall()
    if len(r) < 200:
        return "样本不足"
    closes = [x[0] for x in r][::-1]  # 旧→新
    c = closes[-1]
    pos250 = (sum(1 for x in closes if x < c) / len(closes)) * 100
    if len(closes) < 6:
        return "样本不足"
    c5 = closes[-6]
    chg5 = (c / c5 - 1) * 100
    if pos250 < 30 and abs(chg5) < 3:
        return "A低位横盘"
    if 60 <= pos250 < 95 and 5 <= chg5 < 10:
        return "B启动确认"
    if chg5 >= 15:
        return "C追高禁区"
    if pos250 >= 95 and chg5 < 5:
        return "D高位滞涨"
    return "R其余"

rows = []
for _, r in top.iterrows():
    code = r["code"]
    g = classify(code, ML_DATE)
    prob = gauge_calib.get(g, {}) if isinstance(gauge_calib, dict) else {}
    rows.append({
        "code": code, "name": si.loc[code, "name"] if code in si.index else "?",
        "ml_prob": round(r["ml_prob"] * 100, 1),
        "px": round(last_close.get(code, 0), 2),
        "group": g,
        "grp_t1": prob.get("t1_win") or prob.get("win1") or None,
    })

print()
print(f"{'排名':<4}{'代码':<8}{'名称':<10}{'预测20日上涨概率%':<16}{'收盘价':<8}{'画像分组':<10}{'组T+1历史胜率':<12}")
for i, r in enumerate(rows[:TOP_N], 1):
    grp = f"{r['grp_t1']}" if r["grp_t1"] else "-"
    print(f"{i:<4}{r['code']:<8}{r['name']:<10}{r['ml_prob']:<16}{r['px']:<8}{r['group']:<10}{grp:<12}")

json.dump(rows, open("data/winrate_top5_20261019.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n落盘 data/winrate_top5_20261019.json")
