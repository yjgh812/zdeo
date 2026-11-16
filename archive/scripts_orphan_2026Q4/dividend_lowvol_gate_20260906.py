# -*- coding: utf-8 -*-
"""P5 红利低波域划界闸 · stage-1 域内存在性 + stage-2b bp_ep 池内划界（PREREG 先落盘后运行）。
PREREG: reports/PREREG_DIVIDEND_LOWVOL_GATE_20260906.md（因子 LOCKED: DL=0.5*rank(dv_ratio)+0.5*rank(-vol60)）
自检 fail -> exit 2。数据管道与 turnover_ic_stage1/turnover_tilt_stage2b 同源。"""
import sqlite3, sys, hashlib, json
import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
PREREG = r"D:\zcodesj\reports\PREREG_DIVIDEND_LOWVOL_GATE_20260906.md"
OUT = r"D:\zcodesj\reports\DIVIDEND_LOWVOL_GATE_RESULT_20260906.md"
WIN_START = 20240101          # v2修订: dv_ratio 实测202401起才有值(原20200115作废)
SUBWIN = 20250101             # v2修订: 子窗=2025段(约20个月)
POOL_N = 50

def log(m): print(m, flush=True)

def ic_stats(s):
    s = s.dropna()
    if len(s) < 10:
        return {"n": len(s), "mean_ic": None, "t": None}
    t = s.mean() / (s.std(ddof=1) / len(s) ** 0.5)
    return {"n": len(s), "mean_ic": round(float(s.mean()), 5), "t": round(float(t), 2),
            "pos_rate": round(float((s > 0).mean()), 3)}

# ---------- 1. 宇宙 ----------
con = sqlite3.connect(FUND_DB)
ths = pd.read_sql("SELECT code,name FROM industry_map_ths", con)
ths["ok"] = ths["code"].str[:2].isin(("60", "00", "30")) & ~ths["name"].str.contains("ST", na=False)
uni = set(ths.loc[ths["ok"], "code"])
dbasic = pd.read_sql("SELECT ts_code,trade_date,dv_ratio FROM daily_basic", con)
con.close()
dbasic["code"] = dbasic["ts_code"].str[:6]
dbasic = dbasic[dbasic["code"].isin(uni)].copy()
dbasic["td"] = pd.to_numeric(dbasic["trade_date"], errors="coerce").dropna().astype(int)
dbasic["dv_ratio"] = pd.to_numeric(dbasic["dv_ratio"], errors="coerce")
log(f"[1] universe={len(uni)} dv行={len(dbasic)}")

# ---------- 2. qfq + vol60 ----------
qcon = sqlite3.connect(QFQ_DB)
qfq = pd.read_sql("SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE substr(code,1,2) IN ('60','00','30')", qcon)
qcon.close()
qfq["date"] = qfq["date"].astype(int)
qfq = qfq.sort_values(["code", "date"]).reset_index(drop=True)
qfq["bar_no"] = qfq.groupby("code").cumcount()
qfq["ret"] = qfq.groupby("code")["close"].pct_change()
qfq["vol60"] = qfq.groupby("code")["ret"].transform(lambda s: s.rolling(60, min_periods=40).std())
qfq["ym"] = qfq["date"] // 100
me = qfq.groupby("ym")["date"].max().sort_values().reset_index()
me.columns = ["ym", "mdate"]
me["next_mdate"] = me["mdate"].shift(-1)

last = me.merge(qfq, left_on="mdate", right_on="date", how="inner", suffixes=("_me", ""))
last = last[last["volume"] > 0]
fb = qfq.groupby("code")["bar_no"].min()
last["fb"] = last["code"].map(fb)
last = last[(last["bar_no"] - last["fb"]) >= 60]
last = last[last["date"] >= WIN_START]
last = last.merge(qfq[["code", "date", "close"]].rename(columns={"date": "next_mdate", "close": "close_next"}),
                  on=["code", "next_mdate"], how="left")
last = last.dropna(subset=["close_next", "vol60"])
last["fwd"] = last["close_next"] / last["close"] - 1
last = last[last["code"].isin(uni)]
last = last.merge(dbasic[["code", "td", "dv_ratio"]], left_on=["code", "date"], right_on=["code", "td"], how="left")
last = last.dropna(subset=["dv_ratio"])   # DL 域 = dv 可得
log(f"[2] DL域行={len(last)} 月数={last['ym_me'].nunique()}")

# 自检
n_months = last["ym_me"].nunique()
sec_min = last.groupby("ym_me").size().min()
if n_months < 30 or sec_min < 800:
    log(f"FAIL 自检: months={n_months}<30 or min_sec={sec_min}<800")
    sys.exit(2)

# ---------- 3. DL 复合因子 (月内截面 pct_rank 等权) ----------
def add_dl(g):
    g = g.copy()
    g["r_dv"] = g["dv_ratio"].rank(pct=True)
    g["r_lv"] = (-g["vol60"]).rank(pct=True)
    g["DL"] = 0.5 * g["r_dv"] + 0.5 * g["r_lv"]
    return g
last = last.groupby("ym_me", group_keys=False).apply(add_dl)

def ic_table(df, col):
    rows = []
    for ym, g in df.groupby("ym_me"):
        d = g[[col, "fwd"]].dropna()
        if len(d) >= 100:
            rows.append({"ym": int(ym), "ic": d[col].rank().corr(d["fwd"].rank())})
    return pd.DataFrame(rows)

ic_all = ic_table(last, "DL")
s1 = ic_stats(ic_all["ic"])
s1_sub = ic_stats(ic_all[ic_all["ym"] >= SUBWIN // 100]["ic"])
yearly = {str(y): round(float(g["ic"].mean()), 4) for y, g in ic_all.groupby(ic_all["ym"] // 100)}
log(f"[3] stage-1 域内: {s1} | 近4年: {s1_sub}")

gate1 = (s1["mean_ic"] is not None and abs(s1["mean_ic"]) >= 0.02 and abs(s1["t"]) >= 2
         and s1_sub["mean_ic"] is not None and np.sign(s1_sub["mean_ic"]) == np.sign(s1["mean_ic"]))
log(f"[gate1] 域内存在性: {gate1}")

# ---------- 4. stage-2b: canonical bp_ep Top50 池内划界 ----------
sys.path.insert(0, r"D:\zcodesj\multifactor")
from bp_ep_monthly_picker import load_bp_ep_scores  # noqa: E402
scores = load_bp_ep_scores(start=str(WIN_START), end="20260905")
sc = scores.copy()
sc["date"] = sc["date"].astype(int)
pool = last.merge(sc.rename(columns={"date": "mdate"})[["code", "mdate", "composite_score"]],
                  on=["code", "mdate"], how="inner").dropna(subset=["composite_score"])
log(f"[4] 池内截面行={len(pool)} 月数={pool['ym_me'].nunique()}")
ics, ics_sub, lays = [], [], []
for ym, g in pool.groupby("ym"):
    top = g.nlargest(POOL_N, "composite_score")
    if len(top) < 40:
        continue
    d = top[["DL", "fwd"]].dropna()
    if len(d) >= 30:
        ic = d["DL"].rank().corr(d["fwd"].rank())
        ics.append(ic)
        if ym >= SUBWIN // 100:
            ics_sub.append(ic)
        dq = d.copy()
        dq["q"] = pd.qcut(dq["DL"].rank(method="first"), 5, labels=False)
        lays.append(dq.groupby("q")["fwd"].mean())
s2 = ic_stats(pd.Series(ics))
s2_sub = ic_stats(pd.Series(ics_sub))
lay_mean = pd.concat(lays, axis=1).mean(axis=1) if lays else pd.Series(dtype=float)
log(f"[4] stage-2b 池内: {s2} | 近4年: {s2_sub}")

indep = (s2["t"] is not None and abs(s2["t"]) >= 2 and s2_sub["mean_ic"] is not None
         and np.sign(s2_sub["mean_ic"]) == np.sign(s2["mean_ic"]))
verdict = ("DOMAIN_SIGNAL_NO_POOL_INCREMENT" if gate1 and not indep
           else "PASS_CANDIDATE独立增量" if indep
           else "FAIL_DOMAIN_NO_SIGNAL")
log(f"[VERDICT] {verdict}")

# ---------- 5. 落盘 ----------
fp = hashlib.sha256(open(PREREG, "rb").read()).hexdigest()[:16]
q = "\n".join(f"| Q{i+1} | {v:+.4%} |" for i, v in enumerate(lay_mean)) if len(lay_mean) else "-"
yr = "\n".join(f"| {y} | {v:+.4f} |" for y, v in yearly.items())
md = f"""# 红利低波域划界闸结果（P5 · PREREG 指纹 sha256[:16] = **{fp}**）

> 运行: 2026-09-06 | 因子 DL=0.5*rank(dv_ratio)+0.5*rank(-vol60)（LOCKED）| 域窗 {WIN_START} 起（dv 数据源限制）

## stage-1 域内存在性

- 全域 {s1['n']} 月: meanIC={s1['mean_ic']} t={s1['t']} 正率={s1['pos_rate']} | 近4年: meanIC={s1_sub['mean_ic']} t={s1_sub['t']}
- gate1(|IC|≥0.02 且 |t|≥2 且子窗同号) = **{gate1}**

## stage-2b bp_ep Top50 池内划界（决定性）

- 池内 {s2['n']} 月: meanIC={s2['mean_ic']} t={s2['t']} | 近4年: meanIC={s2_sub['mean_ic']} t={s2_sub['t']}
- 独立增量判据(|t|≥2 且子窗同号) = **{indep}**

## 池内 DL 五分位月均收益（Q1=最高股息低波）

| 分位 | 月均 |
|---|---|
{q}

## VERDICT = **{verdict}**

- DOMAIN_SIGNAL_NO_POOL_INCREMENT → 登记"红利低波域=bp_ep 池内子集，无独立增量"收档（预期结局）
- PASS_CANDIDATE独立增量 → 另立接线 prereg
- FAIL_DOMAIN_NO_SIGNAL → 域内无信号收档

## 分年域内 IC

| 年 | meanIC |
|---|---|
{yr}
"""
open(OUT, "w", encoding="utf-8").write(md)
json.dump({"verdict": verdict, "stage1": s1, "stage1_sub": s1_sub, "stage2b": s2,
           "stage2b_sub": s2_sub, "gate1": bool(gate1), "indep": bool(indep),
           "prereg_fp": fp}, open(OUT.replace(".md", ".json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
log(f"[5] 已写入 {OUT}")
