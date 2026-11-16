# -*- coding: utf-8 -*-
"""B4 stage-1 IC 实测 · 换手率/流动性因子截面选股
预注册: reports/PREREG_TURNOVER_LIQUIDITY_TILT_20260903.md (先落盘后运行)
自检 fail -> exit 2, 不产出结果。"""
import sqlite3, sys, hashlib, json
import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
PREREG = r"D:\zcodesj\reports\PREREG_TURNOVER_LIQUIDITY_TILT_20260903.md"
OUT = r"D:\zcodesj\reports\TURNOVER_TILT_IC_STAGE1_20260903.md"
SUBWIN_START = 20220901  # 近4年子窗
NEWBAR_DAYS = 60         # 次新过滤(交易日)

def log(msg):
    print(msg, flush=True)

# ---------- 1. 宇宙 ----------
con = sqlite3.connect(FUND_DB)
ths = pd.read_sql("SELECT code,name FROM industry_map_ths", con)
ths["ok"] = ths["code"].str[:2].isin(("60", "00", "30")) & ~ths["name"].str.contains("ST", na=False)
uni = set(ths.loc[ths["ok"], "code"])
log(f"[1] universe={len(uni)} (ths总{len(ths)})")

# ---------- 2. daily_basic ----------
dbasic = pd.read_sql("SELECT ts_code,trade_date,turnover_rate,turnover_rate_f FROM daily_basic", con)
con.close()
dbasic["code"] = dbasic["ts_code"].str[:6]
dbasic = dbasic[dbasic["code"].isin(uni)].copy()
dbasic["trade_date"] = dbasic["trade_date"].astype(int)
dbasic["turnover_rate"] = pd.to_numeric(dbasic["turnover_rate"], errors="coerce")
dbasic["turnover_rate_f"] = pd.to_numeric(dbasic["turnover_rate_f"], errors="coerce")
f2_zero_pct = float((dbasic["turnover_rate_f"] == 0).mean())
log(f"[2] daily_basic宇宙内行={len(dbasic)} F2零值占比={f2_zero_pct:.1%}")

# ---------- 3. qfq ----------
qcon = sqlite3.connect(QFQ_DB)
qfq = pd.read_sql(
    "SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq "
    f"WHERE substr(code,1,2) IN ('60','00','30')", qcon)
qcon.close()
qfq["date"] = qfq["date"].astype(int)
qfq = qfq.sort_values(["code", "date"]).reset_index(drop=True)
log(f"[3] qfq行={len(qfq)}")

# 月末bar + bar序号(次新用)
qfq["bar_no"] = qfq.groupby("code").cumcount()
qfq["ym"] = qfq["date"] // 100
me = qfq.groupby("ym")["date"].max().sort_values().reset_index()
me.columns = ["ym", "mdate"]
me["next_mdate"] = me["mdate"].shift(-1)
qfq["amt21"] = qfq.groupby("code")["amount"].transform(lambda s: s.rolling(21, min_periods=15).mean())

# 月末截面行
last = me.merge(qfq, left_on="mdate", right_on="date", how="inner", suffixes=("_me", ""))
last = last[last["volume"] > 0].copy()  # 停牌过滤
# 次新过滤: 该bar序号-该股首bar序号 >= 60
fb = qfq.groupby("code")["bar_no"].min()
last["fb"] = last["code"].map(fb)
last = last[(last["bar_no"] - last["fb"]) >= NEWBAR_DAYS]
# 前瞻收益: merge 下月末close
last = last.merge(
    qfq[["code", "date", "close"]].rename(columns={"date": "next_mdate", "close": "close_next"}),
    on=["code", "next_mdate"], how="left")
last = last.dropna(subset=["close_next"])
last["fwd"] = last["close_next"] / last["close"] - 1
# 因子
last = last.merge(dbasic[["code", "trade_date", "turnover_rate", "turnover_rate_f"]],
                  left_on=["code", "date"], right_on=["code", "trade_date"], how="left")
f1dom = last.dropna(subset=["turnover_rate"]).copy()   # F1/F2 域 = basic 非空 (20200115起)
f3dom = last[(last["date"] >= 20160801) & last["amt21"].notna()].copy()  # F3 域 = PREREG v3 窗口 20160801 起
log(f"[4] 截面bar行={len(last)} F1域行={len(f1dom)} F1域月数={f1dom['ym_me'].nunique()} F3域行={len(f3dom)}")

# ---------- 自检 (v2: 主判定域 = F3, qfq 全窗) ----------
MAIN = "F3"
main_dom = f3dom
n_months = main_dom["ym_me"].nunique()
sec_min = main_dom.groupby("ym_me").size().min()
if n_months < 60 or sec_min < 800:
    log(f"FAIL 自检: {MAIN}域 months={n_months}<60 or min_sec={sec_min}<800")
    sys.exit(2)

# ---------- 5. IC ----------
def ic_table(df, col):
    rows = []
    for ym, g in df.groupby("ym_me"):
        d = g[[col, "fwd"]].dropna()
        rows.append({"ym": int(ym), "n": len(d),
                     "ic": d[col].rank().corr(d["fwd"].rank()) if len(d) >= 100 else np.nan})
    return pd.DataFrame(rows)

def stats(s):
    s = s.dropna()
    t = s.mean() / (s.std(ddof=1) / len(s) ** 0.5) if len(s) > 2 else np.nan
    return {"n_months": len(s), "mean_ic": round(float(s.mean()), 5),
            "t": round(float(t), 2), "icir": round(float(s.mean() / s.std(ddof=1)), 3),
            "pos_rate": round(float((s > 0).mean()), 3)}

ic_f1 = ic_table(f1dom, "turnover_rate")
ic_f2 = ic_table(f1dom, "turnover_rate_f")
ic_f3 = ic_table(f3dom, "amt21")

SUB = lambda df: df[df["ym"] >= (SUBWIN_START // 100)]
res = {
    "F1": {"full": stats(ic_f1["ic"]), "sub4y": stats(SUB(ic_f1)["ic"])},
    "F2": {"full": stats(ic_f2["ic"]), "sub4y": stats(SUB(ic_f2)["ic"])},
    "F3": {"full": stats(ic_f3["ic"]), "sub4y": stats(SUB(ic_f3)["ic"])},
}
res["yearly_F3"] = {str(int(ym)): round(float(g["ic"].mean()), 4)
                    for ym, g in ic_f3.groupby(ic_f3["ym"] // 100)}
res["F1_dense_sub"] = stats(ic_f1[ic_f1["ym"] >= 202401]["ic"])  # 辅助: F1 高密度子窗
# 五分层 (v2: F3 域, 每月末五分位等权月均收益)
def quint(g):
    d = g[["amt21", "fwd"]].dropna()
    d["q"] = pd.qcut(d["amt21"].rank(method="first"), 5, labels=False)
    return d.groupby("q")["fwd"].mean()
lay = f3dom.groupby("ym_me").apply(quint).unstack()  # index=ym, columns=q(0..4)
res["quintile_F1"] = {f"Q{i+1}": round(float(lay[i].mean()), 5) for i in range(5)}
res["spread_Q1_Q5"] = round(float(lay[0].mean() - lay[4].mean()), 5)
res["selfcheck"] = {"f1_months": n_months, "f1_min_sec": int(sec_min),
                    "f3_months": int(f3dom["ym_me"].nunique()),
                    "F2_zero_pct": round(f2_zero_pct, 3),
                    "sub4y_months_F1": int(len(SUB(ic_f1)))}
log("[5] F1 " + json.dumps(res["F1"], ensure_ascii=False))
log("    F3 " + json.dumps(res["F3"], ensure_ascii=False))
log("    五分层 " + json.dumps(res["quintile_F1"], ensure_ascii=False))

# ---------- 6. 判定 (v2 预注册§5: 只看F3) ----------
f1, f1s = res["F3"]["full"], res["F3"]["sub4y"]
gate1 = abs(f1["mean_ic"]) >= 0.02 and abs(f1["t"]) >= 2
gate2 = (np.sign(f1s["mean_ic"]) == np.sign(f1["mean_ic"])) and abs(f1s["mean_ic"]) >= 0.01
verdict = "PASS_CANDIDATE" if (gate1 and gate2) else "FAIL_FALSIFIED"
log(f"VERDICT F1: gate_full={gate1} gate_sub4y={gate2} -> {verdict}")

# ---------- 7. 落盘 ----------
fp = hashlib.sha256(open(PREREG, "rb").read()).hexdigest()[:16]
yr = "\n".join(f"| {y} | {v:+.4f} |" for y, v in res["yearly_F3"].items())
q = "\n".join(f"| {k} | {v:+.4%} |" for k, v in res["quintile_F1"].items())
fd = res["F1_dense_sub"]
def srow(name, d, note=""):
    return "| {} | {} | {:.4f} | {:.2f} | {:.3f} | {:.1%} | {} |".format(
        name, d["n_months"], d["mean_ic"], d["t"], d["icir"], d["pos_rate"], note)
md = f"""# TURNOVER TILT · B4 stage-1 IC 结果（PREREG v2 内回填）

> 运行: 2026-09-03 深夜 | 脚本: turnover_ic_stage1.py | PREREG-v2指纹 sha256[:16] = **{fp}**
> 预注册: reports/PREREG_TURNOVER_LIQUIDITY_TILT_20260903.md（v2 主判定=F3 均额；F1 降级辅助，修订原因见 v2 头注）
> 自检: F3域 months={n_months} min截面n={int(sec_min)} F1高密度域月数={fd['n_months']} F2零值占比={f2_zero_pct:.1%} -> {'PASS' if n_months>=60 and sec_min>=800 else 'FAIL'}

## 主判定 (F3 = 21日均额，预期方向负IC=低成交额高收益)

- 全窗(qfq 2016起, {f1['n_months']}月): meanIC={f1['mean_ic']} t={f1['t']} ICIR={f1['icir']} IC正率={f1['pos_rate']}
- 近4年子窗(≥{SUBWIN_START}, {f1s['n_months']}月): meanIC={f1s['mean_ic']} t={f1s['t']}
- v2 预注册门槛: |IC|>=0.02 且 |t|>=2 且 子窗同号(|IC|>=0.01) -> **{verdict}**

| 因子 | 域 | 月数 | meanIC | t | ICIR | IC正率 | 注 |
|---|---|---|---|---|---|---|---|
{srow('F3全窗', f1, '主判定(v2)')}
{srow('F3近4年', f1s, '子窗一致性闸')}
{srow('F1高密度子窗', fd, '辅助证据 turnover_rate ≥202401, 无判定门槛')}
{srow('F2全窗', res['F2']['full'], f"稳健性臂;零值占比{f2_zero_pct:.0%}" + (' 低质量' if f2_zero_pct > 0.2 else ''))}

## F3 分年 IC (Spearman 月均)

| 年 | meanIC |
|---|---|
{yr}

## F3 五分位月均收益 (Q1=最低成交额)

| 分位 | 月均收益 |
|---|---|
{q}

- Q1−Q5 多空差 = {res['spread_Q1_Q5']:+.4%}/月（外部"尾部集中"检验：若 Q1 显著优于 Q2-Q5 则支持非线性/尾部构造）

## 裁决与处置（PREREG v2 §5 钉死）

- VERDICT = **{verdict}**
- PASS_CANDIDATE → 仅登记候选；stage-2（bp_ep 池内 tilt 增量）另行预注册；绝不直接接线生产
- FAIL_FALSIFIED → 登记禁复活；重测须新预注册+新指纹+引用本档案
"""
open(OUT, "w", encoding="utf-8").write(md)
log(f"[7] 已写入 {OUT}")
