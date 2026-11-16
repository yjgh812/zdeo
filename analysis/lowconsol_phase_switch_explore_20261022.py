# -*- coding: utf-8 -*-
"""相位判定器探索: 主线首日效应的市场状态归因 (探索性, post-hoc)
问题: v3结论"2024+有效/2022-23无效"——哪个T日可观测市场状态变量解释该差异?
方法: 全窗(2022-2026)事件按市场状态变量分桶(源内自洽: 全市场成交额活跃度/上涨家数宽度),
     每桶内 G_FRESH(streak=1) vs G_STALE(streak>=4) 胜率差。
诚实定性: 探索性分析, 任何发现的开关须前瞻验证后方可用于实盘决策。
输出: analysis/lowconsol_phase_switch_explore_20261022.json
"""
import json, sys, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = "analysis/lowconsol_phase_switch_explore_20261022.json"
rng = np.random.default_rng(42)

db = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume,amount,change_pct FROM stock_daily_meta_qfq WHERE date>='20210101' ORDER BY code,date", db)
db.close()
df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].copy()

# 市场状态变量(源内自洽, T日收盘可得)
mkt = df.groupby("date").agg(mkt_amt=("amount", "sum"),
                             up_n=("change_pct", lambda s: int((s > 0.05).sum())),
                             tot_n=("change_pct", lambda s: int((s.abs() > 0.05).sum()))).reset_index()
mkt["up_ratio"] = mkt["up_n"] / mkt["tot_n"].replace(0, np.nan)
mkt = mkt.sort_values("date")
mkt["amt_20d"] = mkt["mkt_amt"].rolling(20).mean()
mkt["upr_20d"] = mkt["up_ratio"].rolling(20).mean()
mkt["amt_q"] = mkt["amt_20d"].rank(pct=True)  # 全窗活跃度分位
mkt["upr_q"] = mkt["upr_20d"].rank(pct=True)
mkt_map_amt = dict(zip(mkt["date"], mkt["amt_q"]))
mkt_map_upr = dict(zip(mkt["date"], mkt["upr_q"]))

g = df.groupby("code")["close"]
df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)
out = []
for code, sub in df.groupby("code", sort=False):
    close = sub["close"].to_numpy(); n = len(close)
    fut = np.full((n, 20), np.nan)
    for k in range(1, min(21, n)):
        fut[: n - k, k - 1] = close[k:]
    entry = close; stop_line = entry * 0.92
    stop_px = np.full(n, np.nan)
    for k in range(20):
        m = np.isnan(stop_px) & (fut[:, k] <= stop_line)
        stop_px[m] = fut[m, k]
    has20 = ~np.isnan(fut[:, -1])
    t20e = np.where(~np.isnan(stop_px), stop_px / entry - 1,
                    np.where(has20, fut[:, -1] / entry - 1, np.nan))
    t20e[~has20] = np.nan
    out.append(pd.DataFrame({"t20e": t20e, "is_A": sub["is_A"].to_numpy(),
                             "date": sub["date"].to_numpy(), "code": code}, index=sub.index))
alld = pd.concat(out)

# 主线(streak)
l1c = sqlite3.connect("data/industry_group_map_snapshot.db")
l1map = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", l1c)
l1map["code"] = l1map["code"].astype(str)
l1c.close()
w = df.merge(l1map, on="code", how="left")
w["vol_l1"] = w.groupby(["l1", "date"])["volume"].transform("sum")
ind = w.drop_duplicates(["date", "l1"])[["date", "l1", "vol_l1"]].merge(
    w.groupby(["date", "l1"])["chg5"].median().rename("icm").reset_index(), on=["date", "l1"])
ind = ind.sort_values(["l1", "date"])
ind["v5"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.rolling(5).sum())
ind["v5p"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
ind["volr_l1"] = ind["v5"] / ind["v5p"]
ind["c5"] = ind.groupby("l1")["icm"].transform(lambda s: s.rolling(5).mean())
hot_set, cur, streak = {}, {}, {}
for dt, sub in ind.groupby("date"):
    s = sub.dropna(subset=["volr_l1", "c5"])
    hot_set[dt] = set(s[s["c5"] > 0].nlargest(3, "volr_l1")["l1"])
for dt in sorted(hot_set):
    nxt = {}
    for l1 in hot_set[dt]:
        cur_v = cur.get(l1, 0) + 1
        nxt[l1] = cur_v
        streak[(dt, l1)] = cur_v
    cur = nxt
alld["l1"] = alld["code"].map(dict(zip(l1map["code"], l1map["l1"])))
alld["main_hit"] = [(l1v == l1v) and (l1v in hot_set.get(dt, set())) for dt, l1v in zip(alld["date"], alld["l1"])]
alld["streak"] = [streak.get((dt, l1v), 0) if (l1v == l1v) else 0 for dt, l1v in zip(alld["date"], alld["l1"])]
alld["amt_q"] = alld["date"].map(mkt_map_amt)
alld["upr_q"] = alld["date"].map(mkt_map_upr)
pool = alld[alld["t20e"].notna() & (alld["is_A"]) & (alld["main_hit"])].copy()
pool = pool[pool["date"] >= "20220101"]
pool["dpos"] = pool["date"].rank(method="dense").astype(int)
pool = pool.sort_values(["code", "date"])


def sym_sample(mask, cd=5, cap=20):
    sub = pool[mask].copy()
    ls, keep = {}, []
    c = sub["code"].to_numpy(); dp = sub["dpos"].to_numpy()
    for i in range(len(sub)):
        l = ls.get(c[i])
        if l is not None and dp[i] - l < cd:
            continue
        ls[c[i]] = dp[i]; keep.append(i)
    sub = sub.iloc[keep]
    return sub.assign(rn=rng.random(len(sub))).sort_values(["date", "rn"]).groupby("date").head(cap)


fresh = sym_sample(pool["streak"] == 1)
stale = sym_sample(pool["streak"] >= 4)
fresh["grp"] = "FRESH"; stale["grp"] = "STALE"
ev = pd.concat([fresh, stale])


def bucket_stats(var):
    ev2 = ev.dropna(subset=[var]).copy()
    ev2["bk"] = pd.qcut(ev2[var], 3, labels=["低", "中", "高"], duplicates="drop")
    rows = {}
    for bk, s in ev2.groupby("bk", observed=True):
        f = s[s["grp"] == "FRESH"]["t20e"]; t = s[s["grp"] == "STALE"]["t20e"]
        rows[str(bk)] = {"n_f": int(len(f)), "n_t": int(len(t)),
                         "win_f": round(float((f > 0).mean() * 100), 2) if len(f) else None,
                         "win_t": round(float((t > 0).mean() * 100), 2) if len(t) else None,
                         "diff_pp": round(float((f > 0).mean() - (t > 0).mean()) * 100, 2) if len(f) and len(t) else None}
    return rows


res = {"vars": {
    "amt_q_活跃度": bucket_stats("amt_q"),
    "upr_q_宽度": bucket_stats("upr_q")},
    "caveat": "探索性post-hoc分析; 任何开关须前瞻验证; 抽样=冷却5+每日20对称"}
for v, rows in res["vars"].items():
    print(f"=== {v} ===")
    for bk, r in rows.items():
        print(f"  {bk}: F={r['win_f']}% (n={r['n_f']}) T={r['win_t']}% (n={r['n_t']}) diff={r['diff_pp']}pp")
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[done] {OUT}")
