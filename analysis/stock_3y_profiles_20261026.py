# -*- coding: utf-8 -*-
"""全量个股近三年上涨下跌画像(主人令: 每只个股近三年涨跌类型及特征) 20261026 — 只读零交易权。
窗口: 20231023~20261023 (3年); 对象: 全市场6/0/3剔688非ST≤无价限(全量画像不限30元)。
每股产出: 阶段分布(15类时间占比)/涨跌周期数/最大波段涨跌/3年总收益/最大回撤/性格标签。
性格标签规则(确定性): 趋势牛(涨时>45%且3年翻倍)/阴跌熊(跌时>55%且3年-30%)/横盘者(平>50%)
  /脉冲妖(加速赶顶段>=3)/V反转型(超跌→主升转移>=2)/普通震荡。
输出: data/stock_3y_phase_profiles.csv (全量) + analysis/stock_3y_analysis_20261026_result.json + stdout
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT_CSV = "data/stock_3y_phase_profiles.csv"
OUT_JSON = "analysis/stock_3y_analysis_20261026_result.json"
D0, D1 = 20231023, 20261023

NAMES = {("低","大跌"):"超跌探底",("低","跌"):"阴跌",("低","平"):"筑底横盘",("低","涨"):"低位启动",
         ("低","强涨"):"低位急拉",("中","大跌"):"破位下跌",("中","跌"):"中位回调",("中","平"):"中位震荡",
         ("中","涨"):"温和上行",("中","强涨"):"主升段",("高","跌"):"高位回落",("高","大跌"):"高位崩落",
         ("高","平"):"高位滞涨",("高","涨"):"高位续涨",("高","强涨"):"加速赶顶"}
UP_STATES = {"低位启动","低位急拉","温和上行","主升段","高位续涨","加速赶顶"}
DOWN_STATES = {"超跌探底","阴跌","破位下跌","中位回调","高位回落","高位崩落"}


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    f"SELECT code,date,close,volume FROM stock_daily_meta_qfq "
    f"WHERE date>='{D0-20000}' ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]") & ~df["code6"].str.startswith("688")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["volume"] > 0)]
scon = sqlite3.connect(STDB, uri=True)
st = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", scon)
scon.close()
st_by = {}
for c, d in zip(st["code"].map(norm), st["date"].astype("int64")):
    st_by.setdefault(c, set()).add(int(d))
print(f"[1/3] qfq行={len(df)} 股={df['code6'].nunique()} ({time.time()-t0:.0f}s)")


def profile(g):
    g = g[g["d"].between(D0, D1)].sort_values("d")
    c = g["close"].to_numpy(float)
    d = g["d"].to_numpy("int64")
    n = len(c)
    if n < 400:  # 3年至少~550交易日, 容忍缺勤
        return None
    st_days = st_by.get(code, set())
    if sum(1 for x in d if x in st_days) > n * 0.2:
        return {"code": code, "label": "ST主导(剔除画像)"}
    s_full = pd.Series(g_full := g["close"].to_numpy(float))
    # 阶段序列(用250日窗位置, 3年窗内)
    sp = pd.Series(c)
    mn = sp.rolling(250, min_periods=120).min().to_numpy()
    mx = sp.rolling(250, min_periods=120).max().to_numpy()
    pos = np.where(mx > mn, (c - mn) / (mx - mn) * 100, 50.0)
    mom20 = np.full(n, np.nan)
    mom20[20:] = c[20:] / c[:-20] - 1
    pl = np.where(pos < 25, "低", np.where(pos < 70, "中", "高"))
    ml = np.where(np.isnan(mom20), "平",
                  np.where(mom20 < -0.15, "大跌", np.where(mom20 < -0.04, "跌",
                  np.where(mom20 < 0.04, "平", np.where(mom20 < 0.15, "涨", "强涨")))))
    states = np.array([NAMES.get((a, b), "中位震荡") for a, b in zip(pl, ml)])
    # 时间占比
    from collections import Counter
    cnt = Counter(states)
    up_pct = sum(v for k, v in cnt.items() if k in UP_STATES) / n
    dn_pct = sum(v for k, v in cnt.items() if k in DOWN_STATES) / n
    # 波段: 连续同状态段的最大涨跌
    best_up = worst_dn = 0.0
    i = 0
    n_accel = 0
    transitions_v = 0
    prev_state = ""
    while i < n:
        j = i
        while j + 1 < n and states[j + 1] == states[i]:
            j += 1
        seg_ret = c[j] / max(c[max(0, i - 1)], 1e-9) - 1
        if seg_ret > best_up:
            best_up = seg_ret
        if seg_ret < worst_dn:
            worst_dn = seg_ret
        if states[i] == "加速赶顶":
            n_accel += 1
        if prev_state == "超跌探底" and states[i] in ("主升段", "加速赶顶", "低位急拉"):
            transitions_v += 1
        prev_state = states[i]
        i = j + 1
    # 总收益与最大回撤
    total_ret = c[-1] / c[0] - 1
    peak = np.maximum.accumulate(c)
    mdd = float(((c - peak) / peak).min())
    # 性格标签(优先级: 趋势牛/阴跌熊/V反转 > 脉冲妖(收紧n>=4且波段>=100%) > 横盘 > 普通)
    if up_pct > 0.45 and total_ret > 1.0:
        label = "三年趋势牛"
    elif dn_pct > 0.55 and total_ret < -0.30:
        label = "三年阴跌熊"
    elif transitions_v >= 2:
        label = "V反转型"
    elif n_accel >= 4 and best_up >= 1.0:
        label = "多次脉冲妖"
    elif (1 - up_pct - dn_pct) > 0.45:
        label = "三年横盘者"
    else:
        label = "普通震荡"
    return {"code": code, "days": n, "up_pct": round(up_pct, 3), "dn_pct": round(dn_pct, 3),
            "flat_pct": round(1 - up_pct - dn_pct, 3), "best_wave": round(best_up, 3),
            "worst_wave": round(worst_dn, 3), "total_ret_3y": round(total_ret, 3),
            "mdd": round(mdd, 3), "n_accel_tops": n_accel, "n_v_reversals": transitions_v,
            "dominant_phase": cnt.most_common(1)[0][0], "label": label}


rows = []
for code, gg in df.groupby("code6", sort=False):
    r = profile(gg)
    if r:
        rows.append(r)
print(f"[2/3] 画像完成 {len(rows)}只 ({time.time()-t0:.0f}s)")

pdf = pd.DataFrame(rows)
pdf.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
lab_dist = pdf["label"].value_counts().to_dict()
agg = {}
for lab, g in pdf.groupby("label"):
    agg[lab] = {"n": len(g), "mean_3y_ret": round(float(g["total_ret_3y"].mean()), 3),
                "median_3y_ret": round(float(g["total_ret_3y"].median()), 3),
                "mean_mdd": round(float(g["mdd"].mean()), 3),
                "mean_best_wave": round(float(g["best_wave"].mean()), 3)}
res = {"window": f"{D0}~{D1}", "n_stocks": len(pdf), "label_distribution": lab_dist,
       "label_stats": agg}
json.dump(res, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT_CSV} + {OUT_JSON}\n")
print(f"全量画像: {len(pdf)}只 | 性格分布: {lab_dist}\n")
print(f"{'性格':<10}{'n':>6}{'3年均值':>9}{'3年中位':>9}{'均MDD':>8}{'均最大波段':>10}")
for lab, v in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
    print(f"{lab:<10}{v['n']:>6}{v['mean_3y_ret']:>+9.0%}{v['median_3y_ret']:>+9.0%}"
          f"{v['mean_mdd']:>+8.0%}{v['mean_best_wave']:>+10.0%}")
