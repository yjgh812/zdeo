# -*- coding: utf-8 -*-
"""低位横盘事件区分特征第二轮: 相对强度/主线新鲜度/价格档 (探索2024+ → OOS 2022-23复验)
回应主人质疑的系统性收尾: 三个先验最强维度逐一检验跨相位稳定性。
输出: analysis/lowconsol_discrim_round2_result_20261021.json
"""
import json, sys, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DLDB = "data/delisted_segments_baostock.db"
OUT = "analysis/lowconsol_discrim_round2_result_20261021.json"


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
    pool = pd.concat([im, idl], ignore_index=True)
    pool["date"] = pool["date"].astype(str)
    return pool[(pool["date"] >= beg) & (pool["date"] <= end) &
                pool["t20e"].notna() & (pool["volume"] > 0) & (pool["open"] > 0)].copy()


def add_mainline_feats(pool):
    df = pool.merge(l1map, on="code", how="left")
    df["vol_l1"] = df.groupby(["l1", "date"])["volume"].transform("sum")
    ind = df.drop_duplicates(["date", "l1"])[["date", "l1", "vol_l1"]].merge(
        df.groupby(["date", "l1"])["chg5"].median().rename("ind_chg5_med").reset_index(), on=["date", "l1"])
    ind = ind.sort_values(["l1", "date"])
    ind["vol5"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.rolling(5).sum())
    ind["vol5p"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
    ind["volr_l1"] = ind["vol5"] / ind["vol5p"]
    ind["chg5_5d"] = ind.groupby("l1")["ind_chg5_med"].transform(lambda s: s.rolling(5).mean())
    ind["onboard"] = ((ind["chg5_5d"] > 0) & (ind["volr_l1"] == ind.groupby("date")["volr_l1"].transform(
        lambda s: s.where(s.rank(ascending=False) <= 3, np.nan)).notna())).astype(int)
    # 主线榜(量比Top3且涨幅>0) + 连续在榜天数
    ind["hot"] = ((ind["chg5_5d"] > 0) & (ind["volr_l1"].rank("dense", ascending=False) <= 3)
                  .groupby(ind["date"]).transform(lambda x: x) ).astype(int) if False else np.nan
    hot_set = {}
    for dt, sub in ind.groupby("date"):
        s = sub.dropna(subset=["volr_l1", "chg5_5d"])
        hot_set[dt] = set(s[s["chg5_5d"] > 0].nlargest(3, "volr_l1")["l1"])
    dates_sorted = sorted(hot_set)
    streak_map, prev = {}, {}
    for dt in dates_sorted:
        for l1 in hot_set[dt]:
            streak_map[(dt, l1)] = prev.get((dt, l1), 0) + 1
        prev = {**{k: v for k, v in prev.items() if k[0] != dt}, **{}}  # keep history
        # streak: 连续计数需要昨日状态
    # 重写streak: 逐日滚动
    streak_map = {}
    cur = {}
    for dt in dates_sorted:
        nxt = {}
        for l1 in hot_set[dt]:
            cur_v = cur.get(l1, 0) + 1
            nxt[l1] = cur_v
            streak_map[(dt, l1)] = cur_v
        cur = nxt
    df["main_hit"] = [(l1v == l1v) and (l1v in hot_set.get(dt, set())) for dt, l1v in zip(df["date"], df["l1"])]
    df["main_streak"] = [streak_map.get((dt, l1v), 0) if (l1v == l1v) else 0 for dt, l1v in zip(df["date"], df["l1"])]
    # 行业内相对强度: 个股chg5 - 行业chg5中位
    df["rel_str"] = df["chg5"] - df.groupby(["date", "l1"])["chg5"].transform("median")
    return df


def tier(x):
    if x <= -1.5: return "弱<-1.5"
    if x < 1.5: return "中性"
    return "强>=1.5"


def stratify(pool, seg):
    ev = pool[pool["is_A"]].copy()
    out = {"seg": seg, "n": int(len(ev))}
    # D1 板块内相对强度
    ev["rel_tier"] = ev["rel_str"].apply(tier)
    out["D1_相对强度"] = {t: {"n": int(len(s)), "win": round(float((s["t20e"] > 0).mean() * 100), 2),
                              "mean": round(float(s["t20e"].mean() * 100), 2)}
                          for t, s in ev.groupby("rel_tier") if len(s) >= 30}
    # D2 主线新鲜度(仅主线命中事件): 首日(streak=1) vs 2-3日 vs 4日+
    mh = ev[ev["main_hit"]].copy()
    mh["fresh"] = pd.cut(mh["main_streak"], [0, 1, 3, 99], labels=["首日", "2-3日", "4日+"])
    out["D2_主线新鲜度"] = {str(k): {"n": int(len(s)), "win": round(float((s["t20e"] > 0).mean() * 100), 2),
                                     "mean": round(float(s["t20e"].mean() * 100), 2)}
                            for k, s in mh.groupby("fresh", observed=True) if len(s) >= 30}
    out["D2_n"] = int(len(mh))
    # D3 价格档
    ev["px_tier"] = pd.cut(ev["close"], [0, 5, 10, 15, 99], labels=["<5元", "5-10元", "10-15元", ">15元"])
    out["D3_价格档"] = {str(k): {"n": int(len(s)), "win": round(float((s["t20e"] > 0).mean() * 100), 2),
                                 "mean": round(float(s["t20e"].mean() * 100), 2)}
                        for k, s in ev.groupby("px_tier", observed=True) if len(s) >= 30}
    print(f"===== [{seg}] n={len(ev)} 主线命中n={len(mh)} =====")
    for k in ("D1_相对强度", "D2_主线新鲜度", "D3_价格档"):
        print(f" {k}:")
        for b, v in out[k].items():
            print(f"   {b}: {v}")
    return out


l1c = sqlite3.connect("data/industry_group_map_snapshot.db")
l1map = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", l1c)
l1map["code"] = l1map["code"].astype(str)
l1c.close()

print("===== 探索段 2024+ =====")
p24 = add_mainline_feats(build_pool("20240101", "20261020", "20230101"))
r24 = stratify(p24, "2024+")
print("===== OOS段 2022-23 =====")
p22 = add_mainline_feats(build_pool("20220101", "20231231", "20210101"))
r22 = stratify(p22, "2022-23")
out = {"exploration_2024plus": r24, "oos_2022_23": r22,
       "verdict_rule": "区分特征须两段方向一致且量级相近; 方向反转=相位特异伪影",
       "caveats": ["三特征族×3-4档, 多重比较有限; 含退市段; 主线代理非方向层实测"]}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[done] {OUT}")
