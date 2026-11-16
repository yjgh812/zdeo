# -*- coding: utf-8 -*-
"""B4 stage-2a · 市值/行业正交检验 (F3=21日均额)
预注册: reports/PREREG_TURNOVER_TILT_STAGE2A_20260903.md (先落盘后运行)
自检 fail -> exit 2。"""
import sqlite3, sys, hashlib, json
import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
PREREG = r"D:\zcodesj\reports\PREREG_TURNOVER_TILT_STAGE2A_20260903.md"
OUT = r"D:\zcodesj\reports\TURNOVER_TILT_STAGE2A_ORTH_20260903.md"
SUBWIN_START = 20220901
ASOF_DAYS = 40          # 日历日容差 (≈25交易日)
BUCKETS = 5
MIN_INDU = 50

def log(m): print(m, flush=True)

# ---------- 1-3. 数据 (与 stage-1 同源) ----------
con = sqlite3.connect(FUND_DB)
ths = pd.read_sql("SELECT code,name,industry FROM industry_map_ths", con)
ths["ok"] = ths["code"].str[:2].isin(("60", "00", "30")) & ~ths["name"].str.contains("ST", na=False)
uni = set(ths.loc[ths["ok"], "code"])
ind_map = dict(zip(ths["code"], ths["industry"].astype(str).str.split("-").str[0]))
dbasic = pd.read_sql("SELECT ts_code,trade_date,total_mv,turnover_rate FROM daily_basic", con)
con.close()
dbasic["code"] = dbasic["ts_code"].str[:6]
dbasic = dbasic[dbasic["code"].isin(uni)].copy()
dbasic["td"] = pd.to_numeric(dbasic["trade_date"], errors="coerce")
dbasic = dbasic.dropna(subset=["td"])
dbasic["td"] = dbasic["td"].astype(int)
dbasic["total_mv"] = pd.to_numeric(dbasic["total_mv"], errors="coerce")
dbasic["dtime"] = pd.to_datetime(dbasic["td"].astype(str))
dbasic = dbasic.dropna(subset=["total_mv"]).sort_values(["code", "td"])

qcon = sqlite3.connect(QFQ_DB)
qfq = pd.read_sql("SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq WHERE substr(code,1,2) IN ('60','00','30')", qcon)
qcon.close()
qfq["date"] = qfq["date"].astype(int)
qfq = qfq.sort_values(["code", "date"]).reset_index(drop=True)
qfq["bar_no"] = qfq.groupby("code").cumcount()
qfq["ym"] = qfq["date"] // 100
me = qfq.groupby("ym")["date"].max().sort_values().reset_index()
me.columns = ["ym", "mdate"]
me["next_mdate"] = me["mdate"].shift(-1)
qfq["amt21"] = qfq.groupby("code")["amount"].transform(lambda s: s.rolling(21, min_periods=15).mean())

last = me.merge(qfq, left_on="mdate", right_on="date", how="inner", suffixes=("_me", ""))
last = last[last["volume"] > 0].copy()
fb = qfq.groupby("code")["bar_no"].min()
last["fb"] = last["code"].map(fb)
last = last[(last["bar_no"] - last["fb"]) >= 60]
last = last.merge(qfq[["code", "date", "close"]].rename(columns={"date": "next_mdate", "close": "close_next"}), on=["code", "next_mdate"], how="left")
last = last.dropna(subset=["close_next"])
last["fwd"] = last["close_next"] / last["close"] - 1
# F3 域: PREREG v3 窗口 20160801 起
last = last[(last["date"] >= 20160801)]
last["amt21"] = last["amt21"]  # 已随 merge 带? 需确认列来自qfq侧
last = last.dropna(subset=["amt21"]).copy()

# ---------- 4. 市值 as-of ----------
last["dtime"] = pd.to_datetime(last["date"].astype(str))
mv_use = dbasic[["code", "dtime", "total_mv", "td"]].sort_values("dtime")
last_s = last.sort_values("dtime")
asof = pd.merge_asof(last_s, mv_use, on="dtime", by="code", tolerance=pd.Timedelta(days=ASOF_DAYS), direction="backward")
asof = asof.dropna(subset=["total_mv"]).copy()
asof["td_int"] = asof["td"].astype("int64")
asof["lag_cal"] = (asof["dtime"] - pd.to_datetime(asof["td_int"].astype(str))).dt.days
asof["lag_td_approx"] = asof["lag_cal"] * 0.7
asof["log_mv"] = np.log(asof["total_mv"].clip(lower=1e5))
asof["ind1"] = asof["code"].map(ind_map)
orth_dom = asof  # 市值正交域
n_months_o = orth_dom["ym_me"].nunique() if "ym_me" in orth_dom.columns else orth_dom["ym"].nunique()
ymcol = "ym_me" if "ym_me" in orth_dom.columns else "ym"
sec_min_o = orth_dom.groupby(ymcol).size().min()
mean_lag = float(orth_dom["lag_td_approx"].mean())
log(f"[4] 正交域行={len(orth_dom)} 月数={n_months_o} min截面={sec_min_o} as-of平均时滞={mean_lag:.1f}交易日")

# ---------- 自检 ----------
if n_months_o < 55 or sec_min_o < 800 or mean_lag > 10:
    log(f"FAIL 自检: months={n_months_o}<55 or min_sec={sec_min_o}<800 or lag={mean_lag:.1f}>10")
    sys.exit(2)

# ---------- 5. 三检验 ----------
def sp(x, y):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    return d["x"].rank().corr(d["y"].rank()) if len(d) >= 100 else np.nan

def agg_stats(vals, label):
    s = pd.Series(vals).dropna()
    if len(s) < 10:
        return {"label": label, "n": len(s), "mean_ic": np.nan, "t": np.nan}
    t = s.mean() / (s.std(ddof=1) / len(s) ** 0.5)
    return {"label": label, "n": len(s), "mean_ic": round(float(s.mean()), 5),
            "t": round(float(t), 2), "pos_rate": round(float((s > 0).mean()), 3)}

# 1) 市值五桶内 IC
bucket_ics, sub_bucket_ics, yearly = [], [], {}
collin = []
for ym, g in orth_dom.groupby(ymcol):
    g = g[["amt21", "fwd", "log_mv"]].dropna()
    if len(g) < 800:
        continue
    collin.append(sp(g["amt21"], g["log_mv"]))
    g["b"] = pd.qcut(g["log_mv"].rank(method="first"), BUCKETS, labels=False)
    ics_m = []
    for b, gb in g.groupby("b"):
        if len(gb) < 50:
            continue
        ics_m.append(sp(gb["amt21"], gb["fwd"]))
    bucket_ics.extend(ics_m)
    yearly[int(ym // 100)] = yearly.get(int(ym // 100), []) + ics_m
    if ym >= SUBWIN_START // 100:
        sub_bucket_ics.extend(ics_m)

res1 = agg_stats(bucket_ics, "市值桶内IC全窗")
res1s = agg_stats(sub_bucket_ics, "市值桶内IC近4年")
res_yearly = {str(y): round(float(np.mean(v)), 4) for y, v in sorted(yearly.items())}

# 2) 线性残差 IC
resid_ics = []
for ym, g in orth_dom.groupby(ymcol):
    g = g[["amt21", "fwd", "log_mv"]].dropna()
    if len(g) < 800:
        continue
    x = g["log_mv"].values; y = g["amt21"].values
    beta = np.polyfit(x, y, 1)
    resid_ics.append(sp(pd.Series(y - (beta[0] * x + beta[1])), g["fwd"].reset_index(drop=True)))
res2 = agg_stats(resid_ics, "线性残差IC")

# 3) 行业内 IC
ind_ics = []
for ym, g in orth_dom.groupby(ymcol):
    for ind, gi in g.groupby("ind1"):
        if len(gi) < MIN_INDU:
            continue
        d = gi[["amt21", "fwd"]].dropna()
        if len(d) >= 50:
            ind_ics.append(sp(d["amt21"], d["fwd"]))
res3 = agg_stats(ind_ics, "行业内IC")

# 4) 共线度
res4 = agg_stats(collin, "Spearman(F3,log_mv)")

# baseline: 同域 F3 原始 IC
base_ics = []
for ym, g in orth_dom.groupby(ymcol):
    base_ics.append(sp(g["amt21"], g["fwd"]))
res0 = agg_stats(base_ics, "F3原始IC同域(2020起)")

# ---------- 判定 (PREREG §4: 只看检验1) ----------
g1 = abs(res1["mean_ic"]) >= 0.02 and abs(res1["t"]) >= 2
g2 = (np.sign(res1s["mean_ic"]) == np.sign(res1["mean_ic"])) and abs(res1s["mean_ic"]) >= 0.01
verdict = "PASS_SIZE_ORTHOGONAL" if (g1 and g2) else "FAIL_COLLINEAR_WITH_SIZE"
log(f"检验1 {res1} | 近4年 {res1s}")
log(f"检验2 {res2} | 检验3 {res3} | 共线 {res4} | baseline {res0}")
log(f"VERDICT: {verdict}")

# ---------- 落盘 ----------
fp = hashlib.sha256(open(PREREG, "rb").read()).hexdigest()[:16]
yr = "\n".join(f"| {y} | {v:+.4f} |" for y, v in res_yearly.items())
def srow(r, note=""):
    return "| {} | {} | {} | {} | {} | {} |".format(
        r["label"], r["n"],
        f"{r['mean_ic']:.4f}" if r["mean_ic"] == r["mean_ic"] else "N/A",
        f"{r['t']:.2f}" if r.get("t") == r.get("t") else "N/A",
        f"{r['pos_rate']:.1%}" if r.get("pos_rate") == r.get("pos_rate") else "N/A",
        note)
md = f"""# TURNOVER TILT · stage-2a 市值/行业正交结果（PREREG内回填）

> 运行: 2026-09-04 凌晨 | 脚本: turnover_ic_stage2_orth.py | PREREG指纹 sha256[:16] = **{fp}**
> 自检: 正交域月数={n_months_o} min截面={sec_min_o} as-of平均时滞={mean_lag:.1f}交易日(≈{mean_lag/0.7:.0f}日历日) -> {'PASS' if n_months_o>=55 and sec_min_o>=800 and mean_lag<=10 else 'FAIL'}
> 域: stage-1 F3域(20160801起) ∩ as-of市值可得(≥20200115, daily_basic覆盖所限)

## 主判定 (检验1 = log市值五桶内 F3 IC, 预期负)

- 全窗: meanIC={res1['mean_ic']} t={res1['t']} IC正率={res1['pos_rate']} (n={res1['n']}桶月)
- 近4年(≥{SUBWIN_START}): meanIC={res1s['mean_ic']} t={res1s['t']}
- PREREG门槛: |IC|>=0.02 且 |t|>=2 且子窗同号 -> **{verdict}**

| 检验 | 序列数 | meanIC | t | IC正率 | 注 |
|---|---|---|---|---|---|
{srow(res0, 'baseline: F3原始IC同域')}
{srow(res1, '主判定')}
{srow(res1s, '主判定近4年')}
{srow(res2, '稳健: 线性残差法')}
{srow(res3, '稳健: 行业内')}
{srow(res4, '共线度: F3~log_mv')}

## 主判定分年 IC (市值桶内)

| 年 | meanIC |
|---|---|
{yr}

## 裁决与处置（PREREG §4/§5 钉死）

- VERDICT = **{verdict}**
- PASS_SIZE_ORTHOGONAL → 登记，进入 stage-2b 预注册（bp_ep Top50 池内增量，另批）
- FAIL_COLLINEAR_WITH_SIZE → 登记 collinear_with_size，禁止 stage-2b，tilt 独立立项降级
"""
open(OUT, "w", encoding="utf-8").write(md)
log(f"[7] 已写入 {OUT}")
