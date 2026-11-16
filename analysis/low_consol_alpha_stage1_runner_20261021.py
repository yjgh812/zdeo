# -*- coding: utf-8 -*-
"""PREREG-LOW-CONSOLIDATION-ALPHA-20261021 阶段1 runner (指纹1a160f793cdeb7bb)
假设H1: 低位横盘事件(gauge官方口径) t20e 胜率 > 全市场随机对照 ≥+2pp 且 CI不含0
v1教训前置: ①退市段阶段0并入(源内自洽收益, 不跨源拼价; 视野不足用最后收盘强制平仓) ②12变体稳健性矩阵 ③kill可触发
主口径 = 冷却5 + t20e(收盘入场+0.92收盘止损) + 含退市; 探索发现(+3.53pp)须过矩阵确认
输出: analysis/low_consol_alpha_stage1_result_20261021.json
"""
import json, sys, sqlite3, hashlib
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DLDB = "data/delisted_segments_baostock.db"
L1_NOT_USED = None  # 本假设无主线维度
LOAD_FROM = "20240101"
OUT = "analysis/low_consol_alpha_stage1_result_20261021.json"
PREREG = "data/PREREG_LOW_CONSOLIDATION_ALPHA_20261021.json"
FP16 = hashlib.sha256(open(PREREG, "rb").read()).hexdigest()[:16]  # m7: 运行时实算, 杜绝硬编码失绑


def build_indicators(df):
    """复刻 gauge 口径: chg5/pos250/t20e(收盘入场+0.92收盘止损)+纯持有+T+1开盘四收益列; 输入须 code,date 升序"""
    df = df.copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    g = df.groupby("code")["close"]
    df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
    mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
    df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)
    df["ret"] = df.groupby("code")["close"].transform(lambda s: s / s.shift(1) - 1)  # T+1涨停近似判定备用
    out = []
    for code, sub in df.groupby("code", sort=False):
        close = sub["close"].to_numpy(); op = sub["open"].to_numpy()
        n = len(close)
        fut = np.full((n, 20), np.nan); futopen = np.full((n, 20), np.nan)
        for k in range(1, min(21, n)):
            fut[: n - k, k - 1] = close[k:]; futopen[: n - k, k - 1] = op[k:]
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
        hold20 = np.where(has20, fut[:, -1] / entry - 1, np.nan)  # 纯持有(主库=视野不足剔除)
        # T+1开盘入场: 次根open>0为entry, 出场=entry根后第20收盘, 止损窗同移
        t1o = np.full(n, np.nan); t1e_hold = np.full(n, np.nan); t1e_stop = np.full(n, np.nan)
        for i in range(n - 1):
            j = i + 1
            if not (op[j] > 0):
                continue
            e = op[j]
            t1o[i] = e
            if j + 19 < n:
                t1e_hold[i] = close[j + 19] / e - 1
            sl = e * 0.92
            for k in range(20):
                if j + k < n and close[j + k] <= sl:
                    t1e_stop[i] = close[j + k] / e - 1
                    break
            else:
                if j + 19 < n:
                    t1e_stop[i] = close[j + 19] / e - 1
        sub = sub.assign(t20e=t20e, hold20=hold20, t1_hold=t1e_hold, t1_stop=t1e_stop, t1_open=t1o)
        out.append(sub)
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
    raw = raw.sort_values(["code6", "date"])
    fac = fac.sort_values(["code6", "date"])
    m = pd.merge_asof(raw.sort_values("date"), fac[["code6", "date", "backAdjustFactor"]].sort_values("date"),
                      on="date", by="code6", direction="backward")
    m["backAdjustFactor"] = m["backAdjustFactor"].fillna(1.0)
    m["close"] = m["close"] * m["backAdjustFactor"]  # 源内后复权自洽
    m["open"] = m["open"] * m["backAdjustFactor"]  # m7-20261021: open同步复权(修复t1变体因子水平伪影)
    m = m.drop(columns=["code"]).rename(columns={"code6": "code"})[["code", "date", "open", "close", "volume", "amount"]]
    m = m[m["date"] >= 20240101]  # m7-20261021: 限prereg预注册宇宙(2024+窗口99只), 杜绝超窗
    m = m[m["code"].str[0].isin(["6", "0", "3"])]
    return m


print("[load] 主库...")
db = sqlite3.connect(QFQ, uri=True)
main = pd.read_sql_query(
    f"SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='{LOAD_FROM}' ORDER BY code,date", db)
db.close()
main = main[main["code"].str[0].isin(["6", "0", "3"])]
print("[load] 退市库...")
dl = load_delisted()
print(f"  退市股并入: {dl['code'].nunique()}只 {len(dl)}行 (2024+: {dl[dl['date']>=20240101]['code'].nunique()}只)")
print("[ind] 主库指标...")
ind_main = build_indicators(main)
print("[ind] 退市库指标...")
ind_dl = build_indicators(dl)

# ---- Part0: 基线复现自校验(主库, 近250交易日, 无冷却全样本) ----
dates_all = np.sort(ind_main["date"].unique())
win250 = set(dates_all[-250:])
b = ind_main[ind_main["date"].isin(win250) & ind_main["is_A"] & ind_main["t20e"].notna()]
base_win = float((b["t20e"] > 0).mean() * 100)
print(f"[Part0] A格子复现: n={len(b)}, win={base_win:.2f}% (声明≈43.2%)")
if abs(base_win - 43.2) > 1.5:
    print("[ABORT] 基线复现偏差>1.5pp"); sys.exit(2)

# ---- 事件池(3冷却 × 4收益口径), 含退市(视野不足→最后收盘强制平仓) ----
dl_last = dl.sort_values("date").groupby("code")["close"].last()


def force_exit_delisted(ind_dl):
    """退市股t20e/hold20 视野不足 → 最后收盘/entry-1? 语义=退市清算: exit=最后收盘价"""
    lastpx = ind_dl.sort_values("date").groupby("code")["close"].last()
    for col in ["t20e", "hold20"]:
        nanm = ind_dl[col].isna() & ind_dl["is_A"].notna()
        ind_dl.loc[nanm, col] = ind_dl.loc[nanm, "code"].map(lastpx) / ind_dl.loc[nanm, "close"] - 1
    return ind_dl


ind_dl = force_exit_delisted(ind_dl)
pool = pd.concat([ind_main, ind_dl], ignore_index=True)
pool["date"] = pool["date"].astype(str)
pool = pool[pool["t20e"].notna() & (pool["volume"] > 0) & (pool["open"] > 0)].copy()
pool["ym"] = pool["date"].str[:6]
date_order = {d: i for i, d in enumerate(np.sort(pool["date"].unique()))}
pool["dpos"] = pool["date"].map(date_order)
pool = pool.sort_values(["code", "date"])
rng = np.random.default_rng(42)
RET_COLS = {"t20e": "t20e止损", "hold20": "纯T+20持有", "t1_stop": "T+1开盘+t20e止损", "t1_hold": "T+1开盘+纯持有"}


def build_pool(cooldown):
    last_seen, rows = {}, []
    c = pool["code"].to_numpy(); dp = pool["dpos"].to_numpy()
    isA = pool["is_A"].to_numpy()
    for i in range(len(pool)):
        if not isA[i]:
            continue
        cd = c[i]
        ls = last_seen.get(cd)
        if ls is not None and dp[i] - ls < cooldown:
            continue
        last_seen[cd] = dp[i]
        rows.append(i)
    ev = pool.iloc[rows].copy()
    ev["rn"] = rng.random(len(ev))
    ev = ev.sort_values(["date", "rn"]).groupby("date").head(20)
    rnd = pool[~pool["is_A"]]
    rnd = rnd.groupby("date", group_keys=False).apply(
        lambda s: s.sample(n=min(20, len(s)), random_state=42))
    return ev, rnd


results = {"variants": {}, "prereg": "PREREG-LOW-CONSOLIDATION-ALPHA-20261021", "fp16": FP16}
matrix_pass = 0
total_variants = 0
for cooldown in (3, 5, 10):
    ev, rnd = build_pool(cooldown)
    main_mask = ev["code"].isin(ind_main["code"].unique())
    for rcol, rname in RET_COLS.items():
        a = ev[ev[rcol].notna()]["t20e" if False else rcol].to_numpy()
        r = rnd[rnd[rcol].notna()][rcol].to_numpy()
        if len(a) < 30:
            results["variants"][f"cd{cooldown}_{rcol}"] = {"n": len(a), "note": "样本<30跳过"}
            continue
        total_variants += 1
        eff = float((a > 0).mean() * 100 - (r > 0).mean() * 100)
        boots = [float((rng.choice(a, len(a)) > 0).mean() * 100 - (rng.choice(r, len(r)) > 0).mean() * 100)
                 for _ in range(1000)]
        ci = (round(float(np.percentile(boots, 2.5)), 2), round(float(np.percentile(boots, 97.5)), 2))
        ok = eff > 0
        matrix_pass += ok
        results["variants"][f"cd{cooldown}_{rcol}"] = {
            "n_A": int(len(a)), "n_R": int(len(r)),
            "win_A": round(float((a > 0).mean() * 100), 2), "win_R": round(float((r > 0).mean() * 100), 2),
            "effect_pp": round(eff, 2), "ci95": ci, "direction_positive": ok}
        print(f"[var] 冷却{cooldown} {rname}: A={results['variants'][f'cd{cooldown}_{rcol}']['win_A']}% "
              f"R={results['variants'][f'cd{cooldown}_{rcol}']['win_R']}% eff={eff:+.2f}pp CI={ci}")

# ---- 主判定(冷却5 + t20e, 含退市) + 幸存者偏差量化 ----
main_key = "cd5_t20e"
mv = results["variants"][main_key]
g1 = mv["n_A"] >= 30
g2 = mv["effect_pp"] >= 2.0 and mv["ci95"][0] > 0
g3 = matrix_pass >= 10
verdict = "PASS_H1" if (g1 and g2 and g3) else ("KILL_H1" if mv["effect_pp"] <= 0 else "FAIL_GATES_NO_KILL")
results.update({"verdict": verdict, "matrix_pass": f"{matrix_pass}/{total_variants}",
                "gates": {"G1": g1, "G2": g2, "G3_matrix>=10/12": g3},
                "survivorship_note": "主判定已含252只退市股(99只2024+); 视野不足用退市前最后收盘强制平仓; 不含退市对比变体见cd5_t20e仅主库版(未单列, 如需可补跑)",
                "caveats": ["探索性发现(+3.53pp)来自v1全窗seed42; 本轮12变体为其确认协议",
                            "退市库为baostock口径, 收益率源内自洽, 未与qfq拼价",
                            "T+1开盘口径不含涨停顺延近似, 已披露"]})
json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[VERDICT] {verdict} | 矩阵 {matrix_pass}/{total_variants} | 主口径 eff={mv['effect_pp']}pp CI={mv['ci95']} | {OUT}")
