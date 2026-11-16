# -*- coding: utf-8 -*-
"""
量价关系学习指南 v3（完整版）实证脚本 20261017
v3 新增且可用日线数据检验的 4 件（只读，零接线，零交易权，描述性统计）：
  E13 换手率 x 流通市值分层（误区七 + 指南数字「20亿以下日均换手~7% / 500亿以上<1.5%」+
       高换手警戒阈值按市值分层的预测力检验；数据 daily_basic 2020-01~2026-10 + qfq 收益）
  E14 活力指数 + 资金集中度（市场周期部分：MA200 上个股占比牛市 90% / 熊市<10% / 集中度前4%个股
       成交额占比 10%=牛市特征 15%=牛转熊节点；qfq 2016-2026 全市场自算）
  E15 天量四案例事实核验（东方财富 20241009=900.38亿/次日-17.47%；中国石油 20071105=699.91亿
       【库内不可核验声明：601857 在 qfq 真源库起始 2016-01-04】；中国平安 20150707=679亿；
       新易盛 20260424=501亿/"空中加油"52交易日+20%）
  E16 市场温度六数之「首板数<20家=情绪低迷」阈值检验（首板数分桶 vs 后续市场等权收益）
口径：与前两轮一致（qfq 单一真源 wb 库、持有语义、剔科创/北交/ST未滤声明）；
  daily_basic 真源=D:\\workbuddyspace\\databases\\fundamental_data.db（20200102~20261016, 5749只）；
  市场等权代理=全市场当日 change_pct 中位数（等权收益指数的稳健代理）。
"""
import json
import sqlite3

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FDB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = r"D:\zcodesj\analysis\volume_price_guide_v3_empirical_result_20261017.json"

# ---------- qfq 加载（2014 起，为 MA200 预热） ----------
conn = sqlite3.connect(r"file:" + QFQ + "?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20140101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
    "AND substr(code,1,2) NOT IN ('42','83','87','92')",
    conn, params=(),
)
conn.close()
df = df[(df["volume"] > 0) & (df["open"] > 0)].copy()
df["date"] = df["date"].astype(str)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)
df["ma200"] = g["close"].transform(lambda s: s.rolling(200, min_periods=200).mean())
zt_i = ((df["change_pct"] >= np.where((df["code"].str.startswith(("300", "301"))) & (df["date"] >= "20200824"), 19.5, 9.7))
        & (df["close"] >= df["high"] - 1e-9)).astype(int)
brk = (zt_i != zt_i.shift(1)) | (df["code"] != df["code"].shift(1))
gid = brk.cumsum()
df["board_k"] = zt_i.groupby([df["code"], gid]).cumsum() * zt_i
for k in (5, 20):
    df[f"fwd{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k) / s - 1)

A0 = "20160101"
ana = df[df["date"] >= A0].copy()

# ---------- 市场日度聚合 ----------
mkt = ana.groupby("date").agg(med_chg=("change_pct", "median"), tot_amt=("amount", "sum"),
                              n_stocks=("code", "count"), first_boards=("board_k", lambda s: int((s == 1).sum())),
                              above_ma200=("ma200", lambda s: float((s.notna() & (ana.loc[s.index, "close"] > s)).mean())))
mkt = mkt.sort_index()
lr = np.log1p(mkt["med_chg"] / 100.0)
for k in (5, 20):
    mkt[f"mfwd{k}"] = np.expm1(lr.shift(-k).rolling(k).sum())

# 活力指数（close>MA200 占比）
mkt["activity"] = ana.groupby("date").apply(
    lambda x: float((x["close"] > x["ma200"]).sum() / x["ma200"].notna().sum()), include_groups=False)

# 资金集中度：当日成交额前 4% 个股的成交额占比
ana["_amt_rank_pct"] = ana.groupby("date")["amount"].rank(method="first", ascending=False)
ana["_n_per_day"] = ana.groupby("date")["code"].transform("count")
top4 = ana[ana["_amt_rank_pct"] <= np.maximum(1, ana["_n_per_day"] * 0.04)]
conc = top4.groupby("date")["amount"].sum() / mkt["tot_amt"]
mkt["conc"] = conc

R = {"meta": {"qfq_rows": int(len(df)), "ana_rows": int(len(ana)),
              "dates": int(mkt.shape[0]), "dmin": ana["date"].min(), "dmax": ana["date"].max()}}

# ---------- E14 活力指数 + 集中度 ----------
e14 = {}
act_bins = [(0.0, 0.2, "<20%(熊市域)"), (0.2, 0.5, "20-50%"), (0.5, 0.8, "50-80%"), (0.8, 1.01, ">=80%(牛市域)")]
for lo, hi, name in act_bins:
    m = (mkt["activity"] >= lo) & (mkt["activity"] < hi)
    sub = mkt[m]
    f5, f20 = sub["mfwd5"].dropna(), sub["mfwd20"].dropna()
    e14[name] = {"n_days": int(m.sum()),
                 "mfwd5_med": round(float(f5.median()) * 100, 2) if len(f5) else None,
                 "mfwd20_med": round(float(f20.median()) * 100, 2) if len(f20) else None,
                 "mfwd20_mean": round(float(f20.mean()) * 100, 2) if len(f20) else None}
conc_bins = [(0.0, 0.08, "<8%"), (0.08, 0.10, "8-10%"), (0.10, 0.12, "10-12%"),
             (0.12, 0.15, "12-15%"), (0.15, 2.0, ">=15%(牛转熊节点主张)")]
for lo, hi, name in conc_bins:
    m = (mkt["conc"] >= lo) & (mkt["conc"] < hi)
    sub = mkt[m]
    f20 = sub["mfwd20"].dropna()
    e14["集中度|" + name] = {"n_days": int(m.sum()),
                             "mfwd20_med": round(float(f20.median()) * 100, 2) if len(f20) else None,
                             "mfwd20_win": round(float((f20 > 0).mean()) * 100, 1) if len(f20) else None,
                             "mfwd20_mean": round(float(f20.mean()) * 100, 2) if len(f20) else None}
# 90% / 10% 事件段长度（交易日）
def run_lengths(mask):
    runs, cur = [], 0
    for v in mask:
        if v:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs
hi_runs = run_lengths((mkt["activity"] >= 0.9).tolist())
lo_runs = run_lengths((mkt["activity"] < 0.1).tolist())
e14["runs>=90%"] = {"n": len(hi_runs), "len_trading_days_p50": float(np.median(hi_runs)) if hi_runs else None,
                    "len_p90": float(np.percentile(hi_runs, 90)) if hi_runs else None, "longest": int(max(hi_runs)) if hi_runs else 0}
e14["runs<10%"] = {"n": len(lo_runs), "len_trading_days_p50": float(np.median(lo_runs)) if lo_runs else None,
                   "longest": int(max(lo_runs)) if lo_runs else 0}
e14["conc_series_max"] = round(float(mkt["conc"].max()) * 100, 2)
e14["conc_ge15_days"] = int((mkt["conc"] >= 0.15).sum())
e14["conc_ge15_recent_dates"] = list(mkt.index[mkt["conc"] >= 0.15])[-12:]
R["E14"] = e14

# ---------- E16 首板数阈值 ----------
e16 = {}
fb_bins = [(-1, 20, "首板<20(情绪低迷主张)"), (20, 40, "首板20-40"), (40, 60, "首板40-60"), (60, 10**9, "首板>=60")]
for lo, hi, name in fb_bins:
    m = (mkt["first_boards"] > lo) & (mkt["first_boards"] <= hi)
    sub = mkt[m]
    f5, f20 = sub["mfwd5"].dropna(), sub["mfwd20"].dropna()
    e16[name] = {"n_days": int(m.sum()),
                 "mfwd5_med": round(float(f5.median()) * 100, 3) if len(f5) else None,
                 "mfwd20_med": round(float(f20.median()) * 100, 2) if len(f20) else None,
                 "mfwd20_win": round(float((f20 > 0).mean()) * 100, 1) if len(f20) else None}
e16["first_boards_p10_p50_p90"] = [round(float(np.percentile(mkt["first_boards"], q)), 0) for q in (10, 50, 90)]
e16["recent_10d"] = {d: int(v) for d, v in mkt["first_boards"].tail(10).items()}
R["E16"] = e16

# ---------- E13 换手率 x 市值分层（daily_basic 2020+） ----------
conn = sqlite3.connect(r"file:" + FDB + "?mode=ro", uri=True)
db = pd.read_sql("SELECT ts_code,trade_date,turnover_rate,circ_mv FROM daily_basic WHERE trade_date>='20200102'", conn)
conn.close()
db["code"] = db["ts_code"].str.split(".").str[0]
db["date"] = db["trade_date"].astype(str)
db = db.dropna(subset=["turnover_rate", "circ_mv"])
qfq_sub = ana[["code", "date", "fwd5", "fwd20", "close"]].copy()
db = db.merge(qfq_sub, on=["code", "date"], how="inner")
db = db[db["fwd20"].notna()]
bins = [(0, 200000, "<20亿"), (200000, 1000000, "20-100亿"), (1000000, 5000000, "100-500亿"), (5000000, 10**12, ">=500亿")]
e13 = {}
for lo, hi, name in bins:
    sub = db[(db["circ_mv"] > lo) & (db["circ_mv"] <= hi)]
    tr = sub["turnover_rate"]
    cell = {"n_rows": int(len(sub)),
            "turnover_mean": round(float(tr.mean()), 2), "turnover_med": round(float(tr.median()), 2),
            "turnover_p90": round(float(tr.quantile(0.9)), 2)}
    # 桶内横截面换手五分位（按日分层秩）Q1/Q5
    sub = sub.copy()
    sub["_rk"] = sub.groupby("date")["turnover_rate"].rank(pct=True)
    for qlabel, qm in (("Q5最高", sub["_rk"] >= 0.8), ("Q1最低", sub["_rk"] <= 0.2)):
        s20 = sub[qm]["fwd20"].dropna()
        cell[qlabel] = {"n": int(len(s20)),
                        "fwd20_med": round(float(s20.median()) * 100, 2) if len(s20) else None,
                        "fwd20_win": round(float((s20 > 0).mean()) * 100, 1) if len(s20) else None}
    e13[name] = cell
R["E13"] = e13

# ---------- E15 天量四案例核验 ----------
def qrow(code, date):
    r = df[(df["code"] == code) & (df["date"] == date)]
    return r.iloc[0] if len(r) else None

e15 = {}
# 东方财富(300059) 20241009
r0 = qrow("300059", "20241009")
r1 = qrow("300059", "20241010")
e15["东方财富(300059)_20241009"] = {
    "amount_yi": round(float(r0["amount"]) / 1e8, 2) if r0 is not None else None,
    "claimed_amount_yi": 900.38,
    "next_chg_pct": round(float(r1["change_pct"]), 2) if r1 is not None else None,
    "claimed_next_chg_pct": -17.47}
# 中国石油(601857) 20071105 —— 库内不可核验
r_min = df[df["code"] == "601857"]["date"].min()
e15["中国石油(601857)_20071105"] = {"qfq_min_date": r_min, "verifiable": bool(r_min <= "20071105"),
                                    "claimed_amount_yi": 699.91, "claimed_7d_pct": "<=-16"}
# 中国平安(601318) 20150707
r_min_p = df[df["code"] == "601318"]["date"].min()
r0 = qrow("601318", "20150707")
e15["中国平安(601318)_20150707"] = {"qfq_min_date": r_min_p, "verifiable": bool(r_min_p <= "20150707"),
                                    "row_found": r0 is not None,
                                    "amount_yi": round(float(r0["amount"]) / 1e8, 2) if r0 is not None else None,
                                    "claimed_amount_yi": 679.0,
                                    "chg_pct": round(float(r0["change_pct"]), 2) if r0 is not None else None}
# 新易盛(300502) 20260424
r0 = qrow("300502", "20260424")
seq = df[(df["code"] == "300502") & (df["date"] >= "20260424")].sort_values("date")
f52 = (seq["close"].iloc[52] / seq["close"].iloc[0] - 1) * 100 if len(seq) > 52 else None
e15["新易盛(300502)_20260424"] = {
    "amount_yi": round(float(r0["amount"]) / 1e8, 2) if r0 is not None else None,
    "claimed_amount_yi": 501.0,
    "chg_pct": round(float(r0["change_pct"]), 2) if r0 is not None else None,
    "claimed_chg_pct": -11.67,
    "fwd52_pct": round(float(f52), 2) if f52 is not None else None,
    "claimed_fwd52_pct": ">+20"}
R["E15"] = e15

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)

print("== meta ==", R["meta"])
print("== E13 ==")
for k, v in e13.items():
    print("E13", k, v)
print("== E14 ==")
for k, v in e14.items():
    print("E14", k, v)
print("== E16 ==")
for k, v in e16.items():
    print("E16", k, v)
print("== E15 ==")
for k, v in e15.items():
    print("E15", k, v)
print("DONE ->", OUT)
