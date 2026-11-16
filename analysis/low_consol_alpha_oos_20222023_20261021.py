# -*- coding: utf-8 -*-
"""PREREG-LOW-CONSOLIDATION-ALPHA-20261021 stage1b: 2022-2023 样本外验证 (OOS, 独立新件)
预注册条款见 prereg 活档 stage1b_oos (写入先于本脚本任何计算)。
口径与阶段1 runner 完全一致(复刻 gauge): A格子=pos250<30且|chg5|<3, t20e=收盘入场+0.92收盘止损,
冷却5, 含退市(2022+段, 源内后复权含open, 视野不足最后收盘强制平仓)。
判定: 主口径 effect>0 → 跨相位稳健; ≤0 → 效应限2024+相位。
输出: analysis/low_consol_alpha_oos_20222023_result_20261021.json
"""
import json, sys, sqlite3, hashlib
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DLDB = "data/delisted_segments_baostock.db"
PREREG = "data/PREREG_LOW_CONSOLIDATION_ALPHA_20261021.json"
FP16 = hashlib.sha256(open(PREREG, "rb").read()).hexdigest()[:16]
OOS_LOAD, OOS_BEG, OOS_END = "20210101", "20220101", "20231231"
OUT = "analysis/low_consol_alpha_oos_20222023_result_20261021.json"


def build_indicators(df):
    df = df.copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
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
        entry = close
        stop_line = entry * 0.92
        stop_px = np.full(n, np.nan)
        for k in range(20):
            m = np.isnan(stop_px) & (fut[:, k] <= stop_line)
            stop_px[m] = fut[m, k]
        has20 = ~np.isnan(fut[:, -1])
        t20e = np.where(~np.isnan(stop_px), stop_px / entry - 1,
                        np.where(has20, fut[:, -1] / entry - 1, np.nan))
        t20e[~has20] = np.nan
        hold20 = np.where(has20, fut[:, -1] / entry - 1, np.nan)
        out.append(sub.assign(t20e=t20e, hold20=hold20))
    return pd.concat(out)


def load_delisted():
    c = sqlite3.connect(DLDB)
    raw = pd.read_sql_query("SELECT code,date,open,close,volume,amount FROM stock_daily_delisted_raw", c)
    fac = pd.read_sql_query("SELECT code,date,backAdjustFactor FROM adjust_factor ORDER BY code,date", c)
    c.close()
    raw["code6"] = raw["code"].str.split(".").str[1]
    raw["date"] = raw["date"].str.replace("-", "").astype(int)
    fac["code6"] = fac["code"].str.split(".").str[1]
    fac["date"] = fac["date"].str.replace("-", "").astype(int)
    m = pd.merge_asof(raw.sort_values("date"), fac[["code6", "date", "backAdjustFactor"]].sort_values("date"),
                      on="date", by="code6", direction="backward")
    m["backAdjustFactor"] = m["backAdjustFactor"].fillna(1.0)
    m["close"] = m["close"] * m["backAdjustFactor"]
    m["open"] = m["open"] * m["backAdjustFactor"]
    m = m.drop(columns=["code"]).rename(columns={"code6": "code"})[["code", "date", "open", "close", "volume", "amount"]]
    m = m[(m["date"] >= 20220101) & (m["code"].str[0].isin(["6", "0", "3"]))]
    return m


print("[load] 主库(OOS回看自2021)...")
db = sqlite3.connect(QFQ, uri=True)
main = pd.read_sql_query(
    f"SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='{OOS_LOAD}' ORDER BY code,date", db)
db.close()
main = main[main["code"].str[0].isin(["6", "0", "3"])]
print("[load] 退市库(2022+)...")
dl = load_delisted()
print(f"  退市股并入: {dl['code'].nunique()}只 {len(dl)}行")
print("[ind] 指标计算...")
ind_main = build_indicators(main)
ind_dl = build_indicators(dl)
dl_last = dl.sort_values("date").groupby("code")["close"].last()
nanm = ind_dl["t20e"].isna() & ind_dl["is_A"].notna()
ind_dl.loc[nanm, "t20e"] = ind_dl.loc[nanm, "code"].map(dl_last) / ind_dl.loc[nanm, "close"] - 1
nanm2 = ind_dl["hold20"].isna() & ind_dl["is_A"].notna()
ind_dl.loc[nanm2, "hold20"] = ind_dl.loc[nanm2, "code"].map(dl_last) / ind_dl.loc[nanm2, "close"] - 1

pool = pd.concat([ind_main, ind_dl], ignore_index=True)
pool["date"] = pool["date"].astype(str)
pool = pool[(pool["date"] >= OOS_BEG) & (pool["date"] <= OOS_END)]
pool = pool[pool["t20e"].notna() & (pool["volume"] > 0) & (pool["open"] > 0)].copy()
pool["dpos"] = pool["date"].rank(method="dense").astype(int)
pool = pool.sort_values(["code", "date"])
rng = np.random.default_rng(42)
last_seen, rows = {}, []
c = pool["code"].to_numpy(); dp = pool["dpos"].to_numpy(); isA = pool["is_A"].to_numpy()
for i in range(len(pool)):
    if not isA[i]:
        continue
    ls = last_seen.get(c[i])
    if ls is not None and dp[i] - ls < 5:
        continue
    last_seen[c[i]] = dp[i]
    rows.append(i)
ev = pool.iloc[rows].copy()
rnd = pool[~pool["is_A"]]
rnd = rnd.groupby("date", group_keys=False).apply(lambda s: s.sample(n=min(20, len(s)), random_state=42))
res = {}
for col, name in [("t20e", "主口径_t20e止损"), ("hold20", "纯T+20持有")]:
    a = ev[ev[col].notna()][col].to_numpy()
    r = rnd[rnd[col].notna()][col].to_numpy()
    eff = float((a > 0).mean() * 100 - (r > 0).mean() * 100)
    boots = [float((rng.choice(a, len(a)) > 0).mean() * 100 - (rng.choice(r, len(r)) > 0).mean() * 100)
             for _ in range(1000)]
    ci = (round(float(np.percentile(boots, 2.5)), 2), round(float(np.percentile(boots, 97.5)), 2))
    res[name] = {"n_A": int(len(a)), "n_R": int(len(r)),
                 "win_A": round(float((a > 0).mean() * 100), 2), "win_R": round(float((r > 0).mean() * 100), 2),
                 "effect_pp": round(eff, 2), "ci95": ci}
    print(f"[OOS] {name}: A={res[name]['win_A']}% R={res[name]['win_R']}% eff={eff:+.2f}pp CI={ci}")
main_eff = res["主口径_t20e止损"]["effect_pp"]
verdict = "OOS_ROBUST_CONFIRM" if main_eff > 0 else "OOS_PHASE_LIMITED_2024PLUS"
out = {"prereg": "PREREG-LOW-CONSOLIDATION-ALPHA-20261021", "stage": "1b_oos_2022_2023",
       "prereg_fp16_at_exec": FP16, "results": res, "verdict": verdict,
       "clause": "pass=effect>0跨相位稳健; fail=效应限2024+相位",
       "caveats": ["2022-2023段无官方gauge基线锚, Part0复现不适用(实现与阶段1同一代码族已通过2024+复现)",
                   "退市段2022+并入, 强制平仓口径同阶段1"]}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[OOS-VERDICT] {verdict} | 主口径 eff={main_eff:+.2f}pp | {OUT}")
