# -*- coding: utf-8 -*-
"""B4 stage-2b · bp_ep Top50 池内 F3 增量检验（最后一闸）
预注册: reports/PREREG_TURNOVER_TILT_STAGE2B_20260903.md (先落盘后运行)
打分 = canonical multifactor.bp_ep_monthly_picker.load_bp_ep_scores（禁重写）
自检 fail -> exit 2。只读研究，不写任何库。"""
import sys, os, hashlib, json, sqlite3
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj\multifactor")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
PREREG = r"D:\zcodesj\reports\PREREG_TURNOVER_TILT_STAGE2B_20260903.md"
OUT = r"D:\zcodesj\reports\TURNOVER_TILT_STAGE2B_POOL_20260903.md"
N50_CSV = r"D:\zcodesj\multifactor\reports\daily_top20\bp_ep_monthly_20260901_20260903_n50.csv"
WIN_START, SUBWIN_START, POOL_N = 20160801, 20220901, 50

def log(m): print(m, flush=True)

# ---------- 1. canonical 打分 (整窗一次加载, PIT) ----------
from bp_ep_monthly_picker import load_bp_ep_scores  # noqa: E402
scores = load_bp_ep_scores(start=str(WIN_START), end="20260903")
assert "composite_score" in scores.columns and len(scores) > 0, "canonical打分加载失败"
log(f"[1] canonical打分行={len(scores)} 日期数={scores['date'].nunique()}")

# ---------- 2. 宇宙+截面+因子 (与stage-1同源) ----------
con = sqlite3.connect(FUND_DB)
ths = pd.read_sql("SELECT code,name FROM industry_map_ths", con)
ths["ok"] = ths["code"].str[:2].isin(("60", "00", "30")) & ~ths["name"].str.contains("ST", na=False)
uni = set(ths.loc[ths["ok"], "code"])
dbasic = pd.read_sql("SELECT ts_code,trade_date,turnover_rate FROM daily_basic", con)
con.close()
dbasic["code"] = dbasic["ts_code"].str[:6]
dbasic = dbasic[dbasic["code"].isin(uni)]
dbasic["td"] = pd.to_numeric(dbasic["trade_date"], errors="coerce").dropna().astype(int)

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
last = last[last["volume"] > 0]
fb = qfq.groupby("code")["bar_no"].min()
last["fb"] = last["code"].map(fb)
last = last[(last["bar_no"] - last["fb"]) >= 60]
last = last[last["date"] >= WIN_START]
last = last.merge(qfq[["code", "date", "close"]].rename(columns={"date": "next_mdate", "close": "close_next"}), on=["code", "next_mdate"], how="left")
last = last.dropna(subset=["close_next", "amt21"])
last["fwd"] = last["close_next"] / last["close"] - 1
last = last[last["code"].isin(uni) & last["code"].isin(set(dbasic["code"]))]

# canonical打分切片: 月末截面日的打分
sc = scores.copy()
sc["date"] = sc["date"].astype(int)
pool = last.merge(sc.rename(columns={"date": "mdate"})[["code", "mdate", "composite_score"]],
                  on=["code", "mdate"], how="inner")
pool = pool.dropna(subset=["composite_score"])

# ---------- 3. Top50池 + 池内检验 ----------
def ic_stats(s):
    s = s.dropna()
    if len(s) < 10:
        return {"n": len(s), "mean_ic": np.nan, "t": np.nan}
    t = s.mean() / (s.std(ddof=1) / len(s) ** 0.5)
    return {"n": len(s), "mean_ic": round(float(s.mean()), 5), "t": round(float(t), 2),
            "pos_rate": round(float((s > 0).mean()), 3)}

ics, ics_sub, yearly, lays = [], [], {}, []
sizes = []
for ym, g in pool.groupby("ym"):
    top = g.nlargest(POOL_N, "composite_score")
    if len(top) < 40:
        continue
    sizes.append(len(top))
    d = top[["amt21", "fwd"]].dropna()
    if len(d) >= 30:
        ic = d["amt21"].rank().corr(d["fwd"].rank())
        ics.append(ic)
        yearly[int(ym // 100)] = yearly.get(int(ym // 100), []) + [ic]
        if ym >= SUBWIN_START // 100:
            ics_sub.append(ic)
        dq = d.copy()
        dq["q"] = pd.qcut(dq["amt21"].rank(method="first"), 5, labels=False)
        lays.append(dq.groupby("q")["fwd"].mean())

res_full, res_sub = ic_stats(pd.Series(ics)), ic_stats(pd.Series(ics_sub))
res_yearly = {str(y): round(float(np.mean(v)), 4) for y, v in sorted(yearly.items())}
lay_df = pd.concat(lays, axis=1)
quint = {f"Q{i+1}": round(float(lay_df.loc[i].mean()), 5) for i in range(5)}
spread = round(float(lay_df.loc[0].mean() - lay_df.loc[4].mean()), 5)
log(f"[3] 有效月={len(ics)} 池均n={np.mean(sizes):.0f} 池内IC full={res_full} sub={res_sub}")
log(f"    分年={res_yearly}")
log(f"    五分位={quint} Q1-Q5={spread}")

# ---------- 4. 诊断: 与生产n50名单重合率 (最近可用截面日, 口径=月初选股vs月末截面) ----------
overlap_note = "N/A"
try:
    prod = pd.read_csv(N50_CSV)
    prod_codes = set(prod["code"].astype(str))
    cand_dates = sorted(pool["mdate"].unique(), reverse=True)
    d901 = next((d for d in cand_dates if d <= 20260903), None)
    my901 = set(pool[pool["mdate"] == d901].nlargest(POOL_N, "composite_score")["code"].astype(str))
    ov = len(prod_codes & my901)
    overlap_note = f"{ov}/{len(prod_codes)} ({ov/max(len(prod_codes),1):.0%}) 截面日{d901} vs 名单日20260901"
    log(f"[4] 池对齐生产n50: {overlap_note}")
except Exception as e:
    overlap_note = f"读取失败:{str(e)[:40]}"

# ---------- 自检 + 判定 (PREREG §4) ----------
if len(ics) < 60 or np.mean(sizes) < 40:
    log(f"FAIL 自检: months={len(ics)}<60 or pool_n={np.mean(sizes):.0f}<40")
    sys.exit(2)
g1 = res_full["mean_ic"] == res_full["mean_ic"] and res_full["mean_ic"] <= -0.02 and res_full["t"] <= -2
g2 = res_sub["mean_ic"] == res_sub["mean_ic"] and res_sub["mean_ic"] <= -0.01 and np.sign(res_sub["mean_ic"]) == np.sign(res_full["mean_ic"])
verdict = "PASS_FINAL_CANDIDATE" if (g1 and g2) else "FAIL_NO_POOL_INCREMENT"
log(f"VERDICT: {verdict} (g1={g1} g2={g2})")

# ---------- 5. 落盘 ----------
fp = hashlib.sha256(open(PREREG, "rb").read()).hexdigest()[:16]
yr = "\n".join(f"| {y} | {v:+.4f} |" for y, v in res_yearly.items())
q = "\n".join(f"| {k} | {v:+.4%} |" for k, v in quint.items())
md = f"""# TURNOVER TILT · stage-2b 池内增量结果（PREREG内回填）

> 运行: 2026-09-04 凌晨 | 脚本: turnover_tilt_stage2b.py | PREREG指纹 sha256[:16] = **{fp}**
> 打分: canonical `bp_ep_monthly_picker.load_bp_ep_scores`（PIT v2 等权rank，未重写）
> 自检: 有效月={len(ics)} 池均n={np.mean(sizes):.0f} -> {'PASS' if len(ics)>=60 and np.mean(sizes)>=40 else 'FAIL'}
> 诊断: 20260901 本Top50 与生产n50名单重合 {overlap_note}（过滤链细节差异致非100%属预期）

## 主判定 (池内 Spearman(F3, fwd)，预期负=池内低额占优)

- 全窗({res_full['n']}月): meanIC={res_full['mean_ic']} t={res_full['t']} 正率={res_full.get('pos_rate','N/A')}
- 近4年(≥{SUBWIN_START}, {res_sub['n']}月): meanIC={res_sub['mean_ic']} t={res_sub['t']}
- PREREG门槛: meanIC≤-0.02 且 t≤-2 且子窗同号(≤-0.01) -> **{verdict}**

## 池内五分位月均收益 (Q1=池内最低成交额)

| 分位 | 月均收益 |
|---|---|
{q}

- Q1−Q5 多空差 = {spread:+.4%}/月

## 分年池内 IC

| 年 | meanIC |
|---|---|
{yr}

## 裁决与处置（PREREG §5 钉死）

- VERDICT = **{verdict}**
- PASS_FINAL_CANDIDATE → 三闸全过，完整候选档案，提请主人裁决 picker 接线评估（本预注册不接线）
- FAIL_NO_POOL_INCREMENT → tilt 关闭登记（池外效应），全截面 IC 不得再作接线理由
"""
open(OUT, "w", encoding="utf-8").write(md)
log(f"[5] 已写入 {OUT}")
