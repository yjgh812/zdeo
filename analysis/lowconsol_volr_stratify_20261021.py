# -*- coding: utf-8 -*-
"""低位横盘事件内 volr 分层验证 (回应主人质疑: 同条件不同结果必有区分特征)
假设(探索): 低位横盘 ∩ 量比激活(volr) 决定主线弹性; 探索段2024+找结构, OOS段2022-23复验同分桶。
口径: 复刻 gauge A格子 + 主线代理(申万L1近5日量比Top3∩涨幅>0, 与v1同构) + 退市段并入(源内后复权)。
输出: analysis/lowconsol_volr_stratify_result_20261021.json
"""
import json, sys, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DLDB = "data/delisted_segments_baostock.db"
OUT = "analysis/lowconsol_volr_stratify_result_20261021.json"
BINS = [0, 0.8, 1.2, 1.6, 2.5, 99]
LABELS = ["<0.8", "0.8-1.2", "1.2-1.6", "1.6-2.5", ">2.5"]


def build(df):
    df = df.copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    g = df.groupby("code")["close"]
    df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
    mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
    gv = df.groupby("code")["volume"]
    df["volr"] = gv.transform(lambda s: s.rolling(5).mean() / s.rolling(60).mean())
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
        out.append(sub.assign(t20e=t20e))
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
    return m[m["date"] >= beg][m["code"].str[0].isin(["6", "0", "3"])]


def build_pool(beg, end, lookback):
    db = sqlite3.connect(QFQ, uri=True)
    main = pd.read_sql_query(
        f"SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='{lookback}' ORDER BY code,date", db)
    db.close()
    main = main[main["code"].str[0].isin(["6", "0", "3"])]
    dl = load_delisted(int(beg))
    im = build_indicators_safe(main); idl = build_indicators_safe(dl)
    last = dl.sort_values("date").groupby("code")["close"].last()
    for col in ("t20e",):
        nm = idl[col].isna() & idl["is_A"].notna()
        idl.loc[nm, col] = idl.loc[nm, "code"].map(last) / idl.loc[nm, "close"] - 1
    pool = pd.concat([im, idl], ignore_index=True)
    pool["date"] = pool["date"].astype(str)
    return pool[(pool["date"] >= beg) & (pool["date"] <= end) &
                pool["t20e"].notna() & (pool["volume"] > 0) & (pool["open"] > 0)].copy()


def build_indicators_safe(df):
    try:
        return build(df)
    except Exception as e:
        print(f"[warn] build失败: {e}"); raise


def add_mainline(pool, l1map):
    df = pool.merge(l1map, on="code", how="left")
    df["vol_l1"] = df.groupby(["l1", "date"])["volume"].transform("sum")
    ind = df.drop_duplicates(["date", "l1"])[["date", "l1", "vol_l1"]].merge(
        df.groupby(["date", "l1"])["chg5"].median().rename("chg5_med").reset_index(), on=["date", "l1"])
    ind = ind.sort_values(["l1", "date"])
    ind["vol5"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.rolling(5).sum())
    ind["vol5p"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
    ind["volr_l1"] = ind["vol5"] / ind["vol5p"]
    ind["chg5_5d"] = ind.groupby("l1")["chg5_med"].transform(lambda s: s.rolling(5).mean())
    top3 = {}
    for dt, sub in ind.groupby("date"):
        s = sub.dropna(subset=["volr_l1", "chg5_5d"])
        top3[dt] = set(s[s["chg5_5d"] > 0].nlargest(3, "volr_l1")["l1"])
    df["main_hit"] = [(l1v == l1v) and (l1v in top3.get(dt, set())) for dt, l1v in zip(df["date"], df["l1"])]
    return df


def stratify(pool, seg):
    ev = pool[pool["is_A"]].copy()
    ev["bucket"] = pd.cut(ev["volr"], BINS, labels=LABELS)
    out = {"seg": seg, "n_total": int(len(ev)), "buckets": {}}
    for b in LABELS:
        s = ev[ev["bucket"] == b]
        if len(s) < 30:
            out["buckets"][b] = {"n": int(len(s)), "note": "n<30"}
            continue
        out["buckets"][b] = {"n": int(len(s)), "win": round(float((s["t20e"] > 0).mean() * 100), 2),
                             "mean": round(float(s["t20e"].mean() * 100), 2)}
    mh = ev[ev["main_hit"]]
    out["main_hit_n"] = int(len(mh))
    if len(mh) >= 60:
        mh2 = mh.copy(); mh2["bucket"] = pd.cut(mh2["volr"], BINS, labels=LABELS)
        out["main_hit_buckets"] = {}
        for b in LABELS:
            s = mh2[mh2["bucket"] == b]
            if len(s) >= 30:
                out["main_hit_buckets"][b] = {"n": int(len(s)), "win": round(float((s["t20e"] > 0).mean() * 100), 2),
                                              "mean": round(float(s["t20e"].mean() * 100), 2)}
    print(f"[{seg}] 低位横盘 n={len(ev)}; 主线命中 n={len(mh)}")
    for b, v in out["buckets"].items():
        print(f"  volr {b:>7}: {v}")
    if "main_hit_buckets" in out:
        print(f"  [主线命中内部分层]")
        for b, v in out["main_hit_buckets"].items():
            print(f"  volr {b:>7}: {v}")
    return out


l1c = sqlite3.connect("data/industry_group_map_snapshot.db")
l1map = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", l1c)
l1map["code"] = l1map["code"].astype(str)
l1c.close()

print("===== 探索段 2024-01 ~ 2026-10 =====")
pool24 = add_mainline(build_pool("20240101", "20261020", "20230101"), l1map)
r24 = stratify(pool24, "2024+")
print("===== OOS段 2022-2023 (同分桶复验) =====")
pool22 = add_mainline(build_pool("20220101", "20231231", "20210101"), l1map)
r22 = stratify(pool22, "2022-23")
out = {"exploration_2024plus": r24, "oos_2022_23": r22,
       "caveats": ["探索段找结构+OOS同分桶复验, 复验以OOS桶间方向一致为标准", "多重比较风险: 单特征族, 分桶5档, 较v1链11特征扫描已收窄",
                   "含退市段(源内后复权), 主线代理非方向层实测"]}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[done] {OUT}")
