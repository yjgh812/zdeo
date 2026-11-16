# -*- coding: utf-8 -*-
"""质量因子(ROE)主臂 + 杜邦分解对照臂 · PIT月度TopN预注册检验 (20261025, 主人「继续」令)。

依据: data/PREREG_QUALITY_DUPONT_20261025.json (跑前冻结, 指纹=md5(rule_frozen))。
设计: 月末PIT快照(TopN) → 次日收盘入场 → ①事件级t20e(-8%止损/T+20, 匹配日期基准)
      ②组合级月频轮动含双边0.3%费(全宇宙等权基准) ③bp_ep同月篮子正交检验。
主臂=roe(唯一检验臂, 四门禁); 对照臂=droe/roa/pm/ato/lev(描述不判定)。
只读三库(qfq/st_history/factor_pit), 零交易权。输出: analysis/quality_dupont_20261025_result.json
"""
import hashlib
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
PITDB = r"file:D:\zcodesj\factor_pit.db?mode=ro"
PREREG = r"D:\zcodesj\data\PREREG_QUALITY_DUPONT_20261025.json"
OUT = r"D:\zcodesj\analysis\quality_dupont_20261025_result.json"

START, END = 20160101, 20261023
TOPN = 20
COST_RT = 0.003
HOLD, STOP = 20, 0.92

t0 = time.time()


def log(m):
    print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)


def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    return d[-6:] if len(d) >= 6 else d


# ---------- prereg 指纹 ----------
pre = json.load(open(PREREG, encoding="utf-8"))
fp = hashlib.md5(json.dumps(pre["rule_frozen"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
pre["fingerprint"] = fp
json.dump(pre, open(PREREG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log(f"prereg指纹={fp}")

# ---------- 数据 ----------
st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
st_con.close()
st_by_code = {}
for c, d in zip(st_rows["code"].map(norm_code), st_rows["date"].astype("int64")):
    st_by_code.setdefault(c, set()).add(int(d))

con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,close,volume FROM stock_daily_meta_qfq ORDER BY code,date", con)
con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6", "0", "3"]) & ~df["code"].str.startswith(("688", "689"))]
df = df[(df["close"] > 0) & (df["volume"] > 0)].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].reset_index(drop=True)
df["d"] = df["date"].astype("int64")
log(f"qfq 行={len(df)} 股={df['code'].nunique()}")

# 月末日期 & 次日入场行 & t20e 结果(复用全量测试机械)
mdates = sorted(df.loc[(df["d"] >= START) & (df["d"] <= END), "d"].unique())
ym = pd.Series(mdates).astype(str).str[:6]
me_dates = [int(g.iloc[-1]) for _, g in pd.Series(mdates, index=ym).groupby(level=0, sort=True)]
me_dates = [d for d in me_dates if d >= START and d <= END]
log(f"月末数={len(me_dates)} 首={me_dates[0]} 末={me_dates[-1]}")

piv_close = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
d_sorted = piv_close.index.to_numpy()
code_arr_close = {c: piv_close[c].to_numpy(dtype=float) for c in piv_close.columns}


def fwd_from_date(code, sig_d, hold=HOLD, stop=STOP):
    """信号日sig_d(月末) → 次一bar收盘入场 → 先触止损否则到期收盘。"""
    if code not in code_arr_close:
        return None
    dts, c = d_sorted, code_arr_close[code]
    i = int(np.searchsorted(dts, sig_d))
    if i >= len(dts) or dts[i] != sig_d:
        return None
    e = i + 1
    if e + hold >= len(c):
        return None
    entry = c[e]
    if not np.isfinite(entry) or entry <= 0:
        return None
    w = c[e + 1: e + 1 + hold]
    broke = w <= entry * stop
    exit_px = w[int(np.argmax(broke))] if broke.any() else c[e + hold]
    if not np.isfinite(exit_px):
        return None
    return (bool(exit_px > entry), exit_px / entry - 1.0)


def nxt_month_ret(code, sig_d):
    """组合级: 次日收盘买入 → 下一月末收盘卖出(不足下一月末则最后可得bar, 标注)。"""
    if code not in code_arr_close:
        return None, False
    dts, c = d_sorted, code_arr_close[code]
    i = int(np.searchsorted(dts, sig_d))
    if i >= len(dts) or dts[i] != sig_d:
        return None, False
    e = i + 1
    if e >= len(c) or not np.isfinite(c[e]) or c[e] <= 0:
        return None, False
    j = int(np.searchsorted(dts, me_after.get(sig_d, 10 ** 9)))
    if j >= len(dts):
        j = len(dts) - 1
    if j <= e or not np.isfinite(c[j]):
        return None, False
    return c[j] / c[e] - 1.0, (dts[j] == me_after.get(sig_d, -1))


me_after = {}
for k, d in enumerate(me_dates):
    me_after[d] = me_dates[k + 1] if k + 1 < len(me_dates) else None

# 全宇宙月度基准: T月末篮子 → 次日收盘入场 → 下一月末收盘(与组合臂同机械)
log("全宇宙月度基准…")
uni_mret = {}
fwd_rows = []
for k in range(len(me_dates) - 1):
    T, T2 = me_dates[k], me_dates[k + 1]
    row_T = piv_close.loc[T]
    row_E = piv_close.shift(-1).loc[T]
    row_T2 = piv_close.loc[T2]
    ok = (row_T > 0) & (row_T <= 30.0) & row_E.notna() & (row_E > 0) & row_T2.notna() & (row_T2 > 0)
    codes_ok = ok[ok].index
    rets = (row_T2[codes_ok] / row_E[codes_ok] - 1.0)
    for cx, r in zip(codes_ok, rets):
        uni_mret.setdefault(T, {})[cx] = float(r)
    fwd_rows.append((T, float(rets.mean()), len(codes_ok)))
uni_month = pd.DataFrame(fwd_rows, columns=["d", "mret", "n"])
log(f"全宇宙月度基准完成 月数={len(uni_month)}")

# t20e全宇宙逐股逐月末(事件级基准): 用逐(code,月末)调用
log("全宇宙t20e事件级基准(逐月末)…")
uni_t20_by_date = {}
for k, T in enumerate(me_dates[:-1]):
    wins = []
    row_T = piv_close.loc[T]
    for cx in row_T.index[(row_T > 0) & (row_T <= 30.0)]:
        st_d = st_by_code.get(cx, ())
        if st_d and T in st_d:
            continue
        r = fwd_from_date(cx, T)
        if r:
            wins.append(r[0])
    uni_t20_by_date[T] = (float(np.mean(wins)) if wins else None, len(wins))
log("t20e基准完成")

# ---------- PIT 快照 & TopN ----------
from build_factor_pit_20260815 import get_pit_factor_v2

pit_con = sqlite3.connect(PITDB, uri=True)
LEGS = {"roe": False, "droe": False, "roa": False, "pm": False, "ato": False, "lev": True}
baskets = {leg: {} for leg in LEGS}
bp_baskets = {}
for T in me_dates[:-1]:
    snap = get_pit_factor_v2(pit_con, T)
    if snap is None or snap.empty or T not in piv_close.index:
        continue
    row_c = piv_close.loc[T]
    snap = snap.assign(close=snap["code"].map(row_c))
    snap = snap[(snap["close"] > 0) & (snap["close"] <= 30.0)
                & ~snap["code"].str.startswith(("688", "689"))
                & snap["code"].str[0].isin(["6", "0", "3"])]
    snap = snap[~((snap["snap_rule"] == "tail") & (snap["ref_date"] < snap["pit_avail_date"]))]
    snap = snap.dropna(subset=["roe", "bps", "eps"])
    snap = snap[snap["close"].notna()]
    if len(snap) < 200:
        continue
    # 剔ST日
    keep = []
    for cx in snap["code"]:
        st_d = st_by_code.get(cx, ())
        if not (st_d and T in st_d):
            keep.append(cx)
    snap = snap[snap["code"].isin(keep)]
    for leg, asc in LEGS.items():
        s2 = snap.dropna(subset=[leg]).sort_values(leg, ascending=asc)
        baskets[leg][T] = s2["code"].head(TOPN).tolist()
    # bp_ep同月篮子(正交参照, 与全量测试同法)
    snap["ep_"] = snap["eps"]
    snap["bp_"] = snap["bps"] / snap["close"]
    snap["combo"] = (snap["ep_"].rank(pct=True) + snap["bp_"].rank(pct=True)) / 2
    bp_baskets[T] = snap.sort_values("combo", ascending=False)["code"].head(TOPN).tolist()
pit_con.close()
log(f"PIT月末篮子完成 月数={len(baskets['roe'])}")


def leg_stats(leg):
    ev_w, ev_r = [], []
    for T, codes in baskets[leg].items():
        for cx in codes:
            r = fwd_from_date(cx, T)
            if r:
                ev_w.append((T, r[0]))
                ev_r.append(r[1])
    pf_month = {}
    for T, codes in baskets[leg].items():
        rs = []
        for cx in codes:
            mr, _ = nxt_month_ret(cx, T)
            if mr is not None:
                rs.append(mr)
        if rs:
            pf_month[T] = (float(np.mean(rs)) - COST_RT, len(rs))
    merged = []
    for T, (r, n) in pf_month.items():
        b = uni_month[uni_month["d"] == T]
        if len(b):
            merged.append({"d": T, "year": int(str(T)[:4]), "ret": r,
                           "base": float(b["mret"].iloc[0]), "excess": r - float(b["mret"].iloc[0])})
    md = pd.DataFrame(merged)
    years = sorted(md["year"].unique())
    by_year = {int(y): {"n_months": int((md["year"] == y).sum()),
                        "excess": round(float(md.loc[md["year"] == y, "excess"].mean()) * 100, 2)}
               for y in years}
    total_ret = float((1 + md["ret"]).prod() - 1) if len(md) else None
    n_years = len(md) / 12 if len(md) else 1
    ann = (1 + total_ret) ** (1 / max(n_years, 0.1)) - 1 if total_ret is not None else None
    base_total = float((1 + md["base"]).prod() - 1) if len(md) else None
    base_ann = (1 + base_total) ** (1 / max(n_years, 0.1)) - 1 if base_total is not None else None
    win_excess_by_year = {y: v["excess"] for y, v in by_year.items()}
    n_pos_years = int(np.sum([v > 0 for v in win_excess_by_year.values()]))
    ev_df = pd.DataFrame(ev_w, columns=["d", "win"])
    ev_base = [uni_t20_by_date.get(T, (None, 0))[0] for T in ev_df["d"]]
    ev_base = [b for b in ev_base if b is not None]
    ovs = []
    for T, codes in baskets[leg].items():
        bb = bp_baskets.get(T)
        if bb:
            ovs.append(len(set(codes) & set(bb)) / TOPN)
    return {"n_months": len(md), "events": len(ev_df),
            "win_event": round(float(ev_df["win"].mean()), 4) if len(ev_df) else None,
            "win_base_matched": round(float(np.mean(ev_base)), 4) if ev_base else None,
            "ann_return": round(ann, 4) if ann is not None else None,
            "base_ann_return": round(base_ann, 4) if base_ann is not None else None,
            "ann_excess_pp": round((ann - base_ann) * 100, 2) if (ann is not None and base_ann is not None) else None,
            "by_year_excess": win_excess_by_year, "n_pos_years": n_pos_years,
            "mean_ret_event": round(float(np.mean(ev_r)), 4) if ev_r else None,
            "overlap_bp_ep_mean": round(float(np.mean(ovs)), 4) if ovs else None}


log("主臂与对照臂统计…")
results = {leg: leg_stats(leg) for leg in LEGS}

# 正交第二判据: 主臂相对bp_ep篮子的月均超额
rel = []
for T, codes in baskets["roe"].items():
    bb = bp_baskets.get(T)
    if not bb:
        continue
    ra = [nxt_month_ret(cx, T)[0] for cx in codes]
    rb = [nxt_month_ret(cx, T)[0] for cx in bb]
    ra = [x for x in ra if x is not None]
    rb = [x for x in rb if x is not None]
    if ra and rb:
        rel.append(float(np.mean(ra)) - float(np.mean(rb)))
rel_excess = round(float(np.mean(rel)) * 100, 2) if rel else None

main = results["roe"]
gates = {
    "G1_portfolio(ann_excess>=+3.0pp)": (main["ann_excess_pp"] is not None and main["ann_excess_pp"] >= 3.0),
    "G2_by_year(>=7/11正)": (main["n_pos_years"] >= 7),
    "G3_orthogonality(重叠<=30%或相对bp_ep>0)": ((main["overlap_bp_ep_mean"] is not None and main["overlap_bp_ep_mean"] <= 0.30)
                                                  or (rel_excess is not None and rel_excess > 0)),
    "G4_event(win超额>=+1.5pp)": (main["win_event"] is not None and main["win_base_matched"] is not None
                                    and (main["win_event"] - main["win_base_matched"]) * 100 >= 1.5),
}
verdict = "PASS_CANDIDATE" if all(gates.values()) else "FAIL"

out = {"meta": {"generated_at": "2026-10-25", "order": "继续→第一梯队开测(质量因子首位)",
                "prereg": PREREG, "fingerprint": fp,
                "gates_LOCKED": pre["acceptance_gates_LOCKED"], "zero_trade_authority": True},
       "main_arm_roe": main, "control_arms": {k: v for k, v in results.items() if k != "roe"},
       "orthogonality": {"overlap_mean": main["overlap_bp_ep_mean"],
                         "rel_excess_vs_bp_ep_pp_monthly": rel_excess},
       "gates": {k: bool(v) for k, v in gates.items()}, "verdict": verdict}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
log(f"完成 → {OUT}")
print(json.dumps({"verdict": verdict, "gates": gates, "main": main,
                  "overlap": out["orthogonality"]}, ensure_ascii=False, indent=1, default=str))
