# -*- coding: utf-8 -*-
"""PREREG_LOW_FRESH_MAINLINE_20261021 runner (指纹运行时实算)
H1: G_FRESH(低位∩主线首日) − G_STALE(低位∩主线尾声streak>=4) 主口径 ≥+3pp 且 CI不含0
合同: 四组对称抽样(每日≤20); 冷却{3,5,10}×出场{t20e,hold20} 6变体; 双段(2024+/2022-23); 退市段并入。
输出: analysis/low_fresh_mainline_result_20261021.json
"""
import json, sys, sqlite3, hashlib
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DLDB = "data/delisted_segments_baostock.db"
PREREG = "data/PREREG_LOW_FRESH_MAINLINE_20261021.json"
FP16 = hashlib.sha256(open(PREREG, "rb").read()).hexdigest()[:16]
OUT = "analysis/low_fresh_mainline_result_20261021.json"
rng = np.random.default_rng(42)


def build(df):
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
        entry = close; stop_line = entry * 0.92
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


def load_delisted(beg):
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
    for col in ("close", "open"):
        m[col] = m[col] * m["backAdjustFactor"]
    m = m.drop(columns=["code"]).rename(columns={"code6": "code"})[["code", "date", "open", "close", "volume", "amount"]]
    return m[m["date"] >= int(beg)][m["code"].str[0].isin(["6", "0", "3"])]


def build_pool(beg, end, lookback):
    db = sqlite3.connect(QFQ, uri=True)
    main = pd.read_sql_query(
        f"SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='{lookback}' ORDER BY code,date", db)
    db.close()
    main = main[main["code"].str[0].isin(["6", "0", "3"])]
    dl = load_delisted(beg)
    im, idl = build(main), build(dl)
    last = dl.sort_values("date").groupby("code")["close"].last()
    nm = idl["t20e"].isna() & idl["is_A"].notna()
    idl.loc[nm, "t20e"] = idl.loc[nm, "code"].map(last) / idl.loc[nm, "close"] - 1
    nm2 = idl["hold20"].isna() & idl["is_A"].notna()
    idl.loc[nm2, "hold20"] = idl.loc[nm2, "code"].map(last) / idl.loc[nm2, "close"] - 1
    pool = pd.concat([im, idl], ignore_index=True)
    pool["date"] = pool["date"].astype(str)
    return pool[(pool["date"] >= beg) & (pool["date"] <= end) &
                pool["t20e"].notna() & (pool["volume"] > 0) & (pool["open"] > 0)].copy()


def add_mainline(pool):
    df = pool.merge(l1map, on="code", how="left")
    df["vol_l1"] = df.groupby(["l1", "date"])["volume"].transform("sum")
    ind = df.drop_duplicates(["date", "l1"])[["date", "l1", "vol_l1"]].merge(
        df.groupby(["date", "l1"])["chg5"].median().rename("icm").reset_index(), on=["date", "l1"])
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
    df["main_hit"] = [(l1v == l1v) and (l1v in hot_set.get(dt, set())) for dt, l1v in zip(df["date"], df["l1"])]
    df["streak"] = [streak.get((dt, l1v), 0) if (l1v == l1v) else 0 for dt, l1v in zip(df["date"], df["l1"])]
    return df


def sym_sample(pool, mask_fn, cooldown, cap=20):
    """对称抽样: 同规则每日≤20, 冷却按组内股计数"""
    sub = pool[mask_fn(pool)].copy()
    last_seen, keep = {}, []
    c = sub["code"].to_numpy(); dp = sub["dpos"].to_numpy()
    for i in range(len(sub)):
        ls = last_seen.get(c[i])
        if ls is not None and dp[i] - ls < cooldown:
            continue
        last_seen[c[i]] = dp[i]
        keep.append(i)
    sub = sub.iloc[keep]
    sub = sub.assign(rn=rng.random(len(sub))).sort_values(["date", "rn"]).groupby("date").head(cap)
    return sub


l1c = sqlite3.connect("data/industry_group_map_snapshot.db")
l1map = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", l1c)
l1map["code"] = l1map["code"].astype(str)
l1c.close()

SEGS = [("2024+", "20240101", "20261020", "20230101"), ("2022-23", "20220101", "20231231", "20210101")]
RET_COLS = {"t20e": "t20e止损", "hold20": "纯T+20持有"}
results = {"prereg": "PREREG_LOW_FRESH_MAINLINE_20261021", "fp16": FP16, "segments": {}}

for seg_name, beg, end, lookback in SEGS:
    print(f"===== 段 {seg_name} =====")
    pool = add_mainline(build_pool(beg, end, lookback))
    pool["dpos"] = pool["date"].rank(method="dense").astype(int)
    seg = {}
    for cooldown in (3, 5, 10):
        fresh = sym_sample(pool, lambda p: p["is_A"] & p["main_hit"] & (p["streak"] == 1), cooldown)
        stale = sym_sample(pool, lambda p: p["is_A"] & p["main_hit"] & (p["streak"] >= 4), cooldown)
        lowall = sym_sample(pool, lambda p: p["is_A"], cooldown)
        rnd = sym_sample(pool, lambda p: ~p["is_A"], cooldown)
        for rcol, rname in RET_COLS.items():
            key = f"cd{cooldown}_{rcol}"
            a = fresh[fresh[rcol].notna()][rcol].to_numpy()
            b = stale[stale[rcol].notna()][rcol].to_numpy()
            if len(a) < 30 or len(b) < 30:
                seg[key] = {"n_fresh": int(len(a)), "n_stale": int(len(b)), "note": "n<30"}
                continue
            eff = float((a > 0).mean() * 100 - (b > 0).mean() * 100)
            boots = [float((rng.choice(a, len(a)) > 0).mean() * 100 - (rng.choice(b, len(b)) > 0).mean() * 100)
                     for _ in range(1000)]
            ci = (round(float(np.percentile(boots, 2.5)), 2), round(float(np.percentile(boots, 97.5)), 2))
            seg[key] = {"n_fresh": int(len(a)), "n_stale": int(len(b)),
                        "win_fresh": round(float((a > 0).mean() * 100), 2), "win_stale": round(float((b > 0).mean() * 100), 2),
                        "eff_pp": round(eff, 2), "ci95": ci, "pos": eff > 0,
                        "n_lowall": int(len(lowall)), "n_rnd": int(len(rnd))}
            print(f"  cd{cooldown} {rname}: fresh={seg[key]['win_fresh']}% stale={seg[key]['win_stale']}% eff={eff:+.2f} CI={ci}")
    # 主判定 + 变体计数
    main_v = seg.get("cd5_t20e", {})
    var_pos = sum(1 for k, v in seg.items() if isinstance(v, dict) and v.get("pos") is True)
    var_tot = sum(1 for k, v in seg.items() if isinstance(v, dict) and "pos" in v)
    g1 = main_v.get("n_fresh", 0) >= 30
    g2 = main_v.get("eff_pp", -99) >= 3.0 and main_v.get("ci95", (1, 0))[0] > 0
    g3 = var_pos >= 5
    seg["gates"] = {"G1": g1, "G2_eff>=3pp&CI>0": g2, "G3_matrix>=5/6": g3, "matrix_pos": f"{var_pos}/{var_tot}"}
    seg["verdict"] = "PASS" if (g1 and g2 and g3) else ("KILL" if main_v.get("eff_pp", -99) <= 0 else "FAIL_GATES")
    print(f"  [{seg_name} VERDICT] {seg['verdict']} | gates={seg['gates']}")
    results["segments"][seg_name] = seg

both = all(s["verdict"] == "PASS" for s in results["segments"].values())
results["final"] = "CROSS_PHASE_CONFIRMED" if both else "NOT_CONFIRMED"
results["caveats"] = ["主线代理非方向层实测", "退市段并入(双复权+强制平仓)", "探索性来源=d88b429分层扫描, 本件为其确认协议"]
json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[FINAL] {results['final']} | {OUT}")
