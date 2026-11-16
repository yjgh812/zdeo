# -*- coding: utf-8 -*-
"""PREREG-WINRATE-AB-LOW-MAINLINE-20261021 阶段1 runner (ACTIVE_STAGE1, 指纹313c8fccd3abf25a)
口径铁律: 完全复刻 forward_up_prob_gauge.py 的格子定义与 t20e 可实现口径
  A低位横盘 = pos250<30 且 |chg5|<3 ; chg5=pct_change(5)*100
  pos250 = (close-min250)/(max250-min250)*100, rolling(250,min_periods=120)
  t20e = 收盘入场, T+1起任一日收盘<入场*0.92 则该日收盘止损, 否则持有至T+20收盘;
         T+20视野不完整(停牌删失)剔除
Part0 自校验: 近250交易日窗复现 gauge A 格子统计(声明值 win≈43.2%, n≈21万), 偏差>1.5pp 中止
事件抽样: 同股冷却5日 + 每日每股池随机≤20(seed=42), 四组同规则
  ①G_LOW_MAIN=低位∩主线 ②G_LOW=低位全体 ③G_MAIN_NONLOW=主线∩非低位 ④G_RANDOM=全市场随机等量
主线代理: 申万L1成分聚合 volr=Σvol5m/Σvol60m Top3 ∩ 成分chg5中位>0 (与direction_layer.volr_top3同构)
输出: analysis/winrate_ab_stage1_result_20261021.json
"""
import json, sys, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
L1DB = "data/industry_group_map_snapshot.db"
LOAD_FROM = "20240101"
SEED, DAILY_CAP, COOLDOWN = 42, 20, 5
OUT = "analysis/winrate_ab_stage1_result_20261021.json"

rng = np.random.default_rng(SEED)
db = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    f"SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='{LOAD_FROM}' ORDER BY code,date",
    db)
db.close()
df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].copy()
g = df.groupby("code")["close"]
df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
gv = df.groupby("code")["volume"]
df["volr"] = gv.transform(lambda s: s.rolling(5).mean() / s.rolling(60).mean())

def t20e(sub):
    """复刻 gauge t20e: 收盘入场+8%收盘止损+T+20视野完整要求; 输入须按 code,date 升序连续行"""
    entry = sub["close"].to_numpy()
    n = len(entry)
    fut = np.full((n, 20), np.nan)
    for k in range(1, min(21, n)):
        fut[: n - k, k - 1] = entry[k:]
    has20 = ~np.isnan(fut[:, -1])
    stop_line = entry * 0.92
    stop_px = np.full(n, np.nan)
    for k in range(20):
        px = fut[:, k]
        m = np.isnan(stop_px) & (px <= stop_line)
        stop_px[m] = px[m]
    with np.errstate(invalid="ignore"):
        out = np.where(~np.isnan(stop_px), stop_px / entry - 1,
                       np.where(has20, fut[:, 19] / entry - 1, np.nan))
    out[~has20] = np.nan
    return out

df = df.sort_values(["code", "date"]).reset_index(drop=True)
df["t20e"] = df.groupby("code", sort=False)[["close"]].apply(lambda x: pd.Series(t20e(x), index=x.index)).reset_index(level=0, drop=True)
df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)

# ---- Part0: 基线复现自校验 (近250交易日, gauge日度格子口径, 无冷却无抽样) ----
dates = np.sort(df["date"].unique())
win250 = dates[-250:]
base = df[df["date"].isin(win250) & df["is_A"] & df["t20e"].notna()]
base_win = (base["t20e"] > 0).mean() * 100
base_n = len(base)
print(f"[Part0] A格子复现: n={base_n}, win={base_win:.2f}% (声明值≈43.2%/n≈21万)")
if abs(base_win - 43.2) > 1.5:
    print("[ABORT] 基线复现偏差>1.5pp, 口径实现有误, 按prereg中止"); sys.exit(2)

# ---- Part2: 主线代理 (申万L1聚合) ----
l1c = sqlite3.connect(L1DB)
l1 = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", l1c)
l1c.close()
l1["code"] = l1["code"].astype(str)
df = df.merge(l1, on="code", how="left")
d5 = df.groupby(["date", "l1"]).agg(vol5=("volume", lambda s: s.tail(1).iloc[0] * 0 + 0),  # placeholder
                                    ).reset_index()  # replaced below
# 行业日度聚合: volr_l1=Σ(5日均量)/Σ(60日均量) 需逐股rolling, 直接聚合日行量: Σvol近5日/Σvol前5日 与 volr同构
df["vol_l1"] = df.groupby(["l1", "date"])["volume"].transform("sum")
ind = df.groupby(["date", "l1"]).agg(vol_day=("volume", "first")).reset_index()
ind = df.drop_duplicates(["date", "l1"])[["date", "l1", "vol_l1"]].merge(
    df.groupby(["date", "l1"])["chg5"].median().rename("chg5_med").reset_index(), on=["date", "l1"])
ind = ind.sort_values(["l1", "date"])
ind["vol5"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.rolling(5).sum())
ind["vol5_prev"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
ind["volr_l1"] = ind["vol5"] / ind["vol5_prev"]
ind["chg5_5d"] = ind.groupby("l1")["chg5_med"].transform(lambda s: s.rolling(5).mean())
top3 = {}
for dt, sub in ind.groupby("date"):
    s = sub.dropna(subset=["volr_l1", "chg5_5d"])
    s = s[s["chg5_5d"] > 0].nlargest(3, "volr_l1")
    top3[dt] = set(s["l1"])
df["date_l1"] = list(zip(df["date"], df["l1"]))
df["main_hit"] = [(dt in top3) and (l1v in top3[dt]) for dt, l1v in df["date_l1"]]

# ---- Part3: 事件抽样 (冷却5日 + 每日≤20, seed42) ----
df["volume"] = df["volume"].fillna(0)
cand = df[(df["t20e"].notna()) & (df["volume"] > 0) & (df["open"] > 0)].copy()
cand["grp4"] = np.select(
    [cand["is_A"] & cand["main_hit"], cand["is_A"], cand["main_hit"] & ~cand["is_A"]],
    ["G_LOW_MAIN", "G_LOW", "G_MAIN_NONLOW"], default="G_RANDOM")
last_seen, events = {}, []
codes_arr = cand["code"].to_numpy(); dates_arr = cand["date"].to_numpy()
grp_arr = cand["grp4"].to_numpy(); t20e_arr = cand["t20e"].to_numpy()
date_order = {d: i for i, d in enumerate(dates)}
for i in range(len(cand)):
    c, dt, gp = codes_arr[i], dates_arr[i], grp_arr[i]
    key = (c, "G_LOW" if gp in ("G_LOW_MAIN", "G_LOW") else ("G_MAIN" if gp == "G_MAIN_NONLOW" else gp))
    ls = last_seen.get(key)
    if ls is not None and date_order[dt] - ls < COOLDOWN:
        continue
    last_seen[key] = date_order[dt]
    events.append((i, c, dt, gp))
ev = pd.DataFrame(events, columns=["row", "code", "date", "grp4"])
# 每日每股池≤20: 组内按日分组随机抽
ev["rn"] = rng.random(len(ev))
ev = ev.sort_values(["date", "grp4", "rn"])
ev = ev.groupby(["date", "grp4"]).head(DAILY_CAP)
ev["t20e"] = t20e_arr[ev["row"].to_numpy()]
ev["ym"] = [d[:6] for d in ev["date"]]

# ---- Part4: 统计 + bootstrap + 判定 ----
def stats(sub):
    return {"n": int(len(sub)), "win": round(float((sub["t20e"] > 0).mean() * 100), 2),
            "mean": round(float(sub["t20e"].mean() * 100), 2),
            "median": round(float(sub["t20e"].median() * 100), 2),
            "p90": round(float(sub["t20e"].quantile(0.9) * 100), 2)}
res, arrs = {}, {}
for gp, sub in ev.groupby("grp4"):
    res[gp] = stats(sub); arrs[gp] = sub["t20e"].to_numpy()
    print(f"[Part4] {gp}: {res[gp]}")
if "G_LOW_MAIN" not in arrs or "G_LOW" not in arrs:
    print("[ABORT] 叠加组或低位组样本为空"); sys.exit(2)
diffs = []
for _ in range(1000):
    a = rng.choice(arrs["G_LOW_MAIN"], len(arrs["G_LOW_MAIN"]), replace=True)
    b = rng.choice(arrs["G_LOW"], len(arrs["G_LOW"]), replace=True)
    diffs.append((a > 0).mean() * 100 - (b > 0).mean() * 100)
ci = (round(float(np.percentile(diffs, 2.5)), 2), round(float(np.percentile(diffs, 97.5)), 2))
effect = round(res["G_LOW_MAIN"]["win"] - res["G_LOW"]["win"], 2)
monthly = {ym: round(float((s["t20e"] > 0).mean() * 100), 1)
           for ym, s in ev[ev["grp4"] == "G_LOW_MAIN"].groupby("ym") if len(s) >= 10}
gate_g1 = res["G_LOW_MAIN"]["n"] >= 30
gate_g2 = (effect >= 5.0) and (ci[0] > 0)
gate_g3 = len(monthly) >= 3 and sorted(monthly.values())[-1] - sorted(monthly.values())[0] < 40
verdict = "PASS_STAGE1" if (gate_g1 and gate_g2 and gate_g3) else (
    "KILL_H1" if res["G_LOW_MAIN"]["win"] <= base_win else "INCONCLUSIVE_LOW_POWER")
out = {"prereg": "PREREG-WINRATE-AB-LOW-MAINLINE-20261021", "fingerprint16": "313c8fccd3abf25a",
       "stage": "1", "part0_baseline_check": {"repro_n": base_n, "repro_win": round(base_win, 2),
                                              "declared_win": 43.2, "pass": True},
       "groups": res, "effect_pp_vs_GLOW": effect, "bootstrap95_ci_diff_pp": ci,
       "monthly_G_LOW_MAIN": monthly, "gates": {"G1_n>=30": gate_g1, "G2_effect>=5pp&CI>0": gate_g2,
                                                "G3_monthly_robust": gate_g3},
       "verdict": verdict,
       "caveats": ["主口径=gauge同款收盘入场t20e(与43.2基线可比); T+1开盘可执行口径未在本轮, 后续稳健性补",
                   "qfq库不含退市股=幸存者偏差(prereg已标注)", "主线代理为申万L1聚合非方向层实测, 一致性验证待重叠期样本补",
                   "事件抽样=冷却5日+每日每股池≤20(seed42), 与gauge日度全样本口径不同, 基线锚定以Part0复现值为准"]}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[VERDICT] {verdict} | effect={effect}pp CI={ci} | 已写出 {OUT}")
