# -*- coding: utf-8 -*-
"""t1_cap_slice_ci_20261018.py — T1 低位缩量破跌停 市值切片 + 日聚簇胜率CI (胜率方案B第一档·序1-3补全+序5-1)

补全 V9 七分域缺口: 市值切片(daily_basic.total_mv join, 万元)。附带 T1 日聚簇 win CI(序5-1)。
T1 抽取与 low_repair_family_arm_compare_20261017.py 逐位同源(锚核断言)。零交易权纯研究。
输出: analysis/t1_cap_slice_ci_20261018_out.md + .json
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
ANCHOR = r"D:\zcodesj\analysis\low_repair_family_arm_compare_result_20261017.json"
OUT_MD = r"D:\zcodesj\analysis\t1_cap_slice_ci_20261018_out.md"
OUT_JSON = r"D:\zcodesj\analysis\t1_cap_slice_ci_20261018_out.json"
N_REP, SEED = 1000, 42

conn = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20160101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
    "AND substr(code,1,2) NOT IN ('42','83','87','92')", conn)
conn.close()
df = df[(df["volume"] > 0) & (df["open"] > 0)].copy()
df["date"] = df["date"].astype(str)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)
df["ma20v"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
df["vr20"] = df["volume"] / df["ma20v"]
df["min250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).min())
df["max250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).max())
df["pos250"] = (df["close"] - df["min250"]) / (df["max250"] - df["min250"])
df["prev_close"] = g["close"].shift(1)
df["rowid"] = df.groupby("code").cumcount()
gem20 = (df["code"].str[-6:].str.startswith(("300", "301"))) & (df["date"] >= "20200824")
thr = pd.Series(np.where(gem20, 19.5, 9.7), index=df.index)
lo_pct = df["low"] / df["prev_close"] * 100 - 100
dt_close = (df["change_pct"] <= -thr) & (df["close"] <= df["low"] + 1e-9)
df["fwd20"] = g["close"].transform(lambda s: s.shift(-20) / s - 1)
base = df.dropna(subset=["vr20", "pos250", "change_pct"]).copy()
base = base[base["fwd20"].notna()]
lo_b = lo_pct.reindex(base.index)
thr_b = thr.reindex(base.index)
dtc_b = dt_close.reindex(base.index).fillna(False)
t1 = base[(base["pos250"] <= 0.3) & (lo_b <= -thr_b)
          & ~dtc_b & (base["vr20"] < 1.5)].copy()


def dedup5(sub):
    keep = np.zeros(len(sub), dtype=bool)
    last = {}
    codes = sub["code"].to_numpy()
    rows = sub["rowid"].to_numpy()
    for i in range(len(sub)):
        c = codes[i]
        if rows[i] - last.get(c, -10**9) >= 5:
            keep[i] = True
            last[c] = rows[i]
    return sub[keep]


t1 = dedup5(t1)

# 锚核
anchor = json.load(open(ANCHOR, encoding="utf-8"))
a_t1 = anchor.get("T1_低位缩量破跌停", {})
my_med = round(float(t1["fwd20"].median()) * 100, 2)
assert abs(len(t1) - a_t1.get("n", 0)) <= a_t1.get("n", 0) * 0.02, "n 漂移超2%"
print(f"[锚核] T1 n={len(t1)} med20={my_med}% PASS")

# 市值 join: daily_basic.total_mv(万元), ts_code=code6+.SZ/.SH, trade_date=YYYYMMDD
fc = sqlite3.connect("file:" + FUND.replace("\\", "/") + "?mode=ro", uri=True)
codes6 = t1["code"].str[-6:]
t1 = t1.assign(code6=codes6)
con_map = {"0": "SZ", "3": "SZ", "6": "SH"}
t1["ts_code"] = t1["code6"] + "." + t1["code6"].str[0].map(con_map)
t1["td8"] = t1["date"].str.replace("-", "", regex=False)
mv_rows = []
fc_con = sqlite3.connect("file:" + FUND.replace("\\", "/") + "?mode=ro", uri=True)
# 逐码区间查询市值(事件~7千靠 dict 点查)
mv_lookup = {}
for code6, grp in t1.groupby("code6"):
    suffix = grp["ts_code"].iloc[0].split(".")[1]
    q = fc_con.execute(
        "select trade_date, total_mv from daily_basic where ts_code=? and trade_date>=? and trade_date<=?",
        (code6 + "." + suffix, "20160101", "20261231"))
    for td8, tmv in q:
        mv_lookup[(code6, td8)] = tmv
fc_con.close()

t1["total_mv_wan"] = [mv_lookup.get((c6, td8)) for c6, td8 in zip(t1["code6"], t1["td8"])]
cov = t1["total_mv_wan"].notna().mean()


def daycluster_win(sub, n_rep=N_REP, seed=SEED):
    dates = sub["date"].tolist()
    wins = (sub["fwd20"] > 0).to_numpy().astype(float)
    uniq = sorted(set(dates))
    by_day = {}
    for i, d in enumerate(dates):
        by_day.setdefault(d, []).append(i)
    idx_list = [by_day[d] for d in uniq]
    rng = np.random.default_rng(seed)
    n = len(uniq)
    stats = np.empty(n_rep)
    for r in range(n_rep):
        days = rng.integers(0, n, n)
        pool = [j for di in days for j in idx_list[di]]
        stats[r] = np.mean([wins[j] for j in pool])
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return {"n": int(len(sub)), "win_point": round(float(np.mean(wins)) * 100, 2),
            "ci95": [round(lo * 100, 2), round(hi * 100, 2)],
            "med20": round(float(sub["fwd20"].median()) * 100, 2)}


def cap_bucket(v):
    if v is None or pd.isna(v):
        return "缺失"
    y = v / 10000.0  # 万元→亿元
    if y < 20:
        return "<20亿"
    if y < 50:
        return "20-50亿"
    if y < 100:
        return "50-100亿"
    return ">=100亿"


t1["cap_bucket"] = [cap_bucket(v) for v in t1["total_mv_wan"]]
whole = daycluster_win(t1)
slices = {b: daycluster_win(t1[t1["cap_bucket"] == b], )
          for b in ("<20亿", "20-50亿", "50-100亿", ">=100亿", "缺失")}

out = {"anchor": {"n": a_t1.get("n"), "med20": a_t1.get("fwd20", {}).get("med")},
       "t1_daycluster_win": whole, "cap_coverage": round(float(cov) * 100, 1),
       "cap_slices": slices,
       "note": "total_mv=事件日收盘市值(万元,baostock/tushare口径daily_basic); 前复权价不影响市值口径(取自daily_basic原表)"}
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

md = ["# T1 市值切片 + 日聚簇胜率CI（胜率方案B第一档·序1-3补全+序5-1）", "",
      f"> 脚本 analysis/t1_cap_slice_ci_20261018.py 直出 | 锚核 n={len(t1)}(锚{a_t1.get('n')}) med20={my_med}%(锚) | 市值覆盖 {cov * 100:.1f}%",
      "> 零交易权纯研究 | 市值=daily_basic.total_mv 事件日收盘(万元→亿元)", "",
      "## T1 全域日聚簇胜率 CI（1000次按日重采样）", "",
      f"- win={whole['win_point']}%（事件 {len(t1)} / 日簇 {t1['date'].nunique()}）"
      f" 95%CI **[{whole['ci95'][0]:.2f}%, {whole['ci95'][1]:.2f}%]**",
      "- 与 naive 二项 CI 对比：日聚簇区间更宽——同日截面相关使 naive 高估显著性（V9 med CI 先例同构）。", "",
      "## 市值分桶（med20 / win / n）", "",
      "| 市值桶 | n | med20 | win |", "|---|---|---|---|"]
for b in ("<20亿", "20-50亿", "50-100亿", ">=100亿", "缺失"):
    s = slices[b]
    md.append(f"| {b} | {s['n']} | {s['med20']:+.2f}% | {s['win_point']:.1f}% |")
md += ["", "## 判读边界", "",
       "- 分桶为事后探索性切片：任一桶的反常须新样本/prereg 复核，禁直接当定谳或接线依据；",
       "- T1 prereg（10/20 起 OOS）判据冻结不变——本件为口径/测量补全（胜率方案 B 第一档），不触任何冻结判据。"]
with open(OUT_MD, "w", encoding="utf-8") as f:
    f.write("\n".join(md) + "\n")
print(f"[OK] 市值覆盖{cov*100:.1f}% 分桶: " + " / ".join(f"{b}={slices[b]['n']}({slices[b]['med20']:+.2f}%)" for b in slices))
print(f"[OK] 全域win CI [{whole['ci95'][0]}, {whole['ci95'][1]}]")
print(f"[OK] 已直出 {OUT_MD}")
