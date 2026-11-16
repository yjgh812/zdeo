# -*- coding: utf-8 -*-
"""业绩预告正向+跳空确认·断层升格检验 (事件流水线E4-A升格, 20261025)。

依据: data/PREREG_FORECAST_GAP_20261025.json (跑前冻结, 六门+单调性检查)。
主臂=预告{预增,扭亏,略增}x幅度>=100 x次日开盘跳空>=2%; 入场=跳空日次一收盘(诚实口径)。
对照臂=跳空>=3%/1-2%/无跳空(单调性); 出场=t20e/t60e(-8%止损)。
只读qfq+st_history+staged csv, 零交易权。输出: analysis/forecast_gap_20261025_result.json
"""
import hashlib
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
STAGE = r"D:\zcodesj\data\yjyg_stage_2016_2026.csv"
PREREG = r"D:\zcodesj\data\PREREG_FORECAST_GAP_20261025.json"
OUT = r"D:\zcodesj\analysis\forecast_gap_20261025_result.json"

START, END = 20160101, 20261023
COST_RT = 0.003
HOLDS = {"t20e": 20, "t60e": 60}
STOP = 0.92

t0 = time.time()


def log(m):
    print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)


def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    return d[-6:] if len(d) >= 6 else d


pre = json.load(open(PREREG, encoding="utf-8"))
fp = hashlib.md5(json.dumps(pre["rule_frozen"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
pre["fingerprint"] = fp
json.dump(pre, open(PREREG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log(f"prereg指纹={fp}")

# ---------- 预告事件 ----------
yj = pd.read_csv(STAGE, dtype={"股票代码": str})
yj["code"] = yj["股票代码"].map(norm_code)
yj = yj[~yj["code"].str.startswith(("688", "689")) & yj["code"].str[0].isin(["6", "0", "3"])]
yj["amp"] = pd.to_numeric(yj["业绩变动幅度"], errors="coerce")
yj["d"] = pd.to_datetime(yj["公告日期"]).dt.strftime("%Y%m%d").astype("int64")
yj = yj[(yj["d"] >= START) & (yj["d"] <= END)].dropna(subset=["amp"])
pos = yj[yj["预告类型"].isin(["预增", "扭亏", "略增"]) & (yj["amp"] >= 100)]
pos = pos.drop_duplicates(subset=["code", "d"])
log(f"正向预告={len(pos)}")

# ---------- 价格底座 ----------
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq ORDER BY code,date", con)
con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6", "0", "3"]) & ~df["code"].str.startswith(("688", "689"))]
df = df[(df["close"] > 0) & (df["volume"] > 0)].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].reset_index(drop=True)
df["d"] = df["date"].astype("int64")
piv_c = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
piv_o = df.pivot_table(index="d", columns="code", values="open", aggfunc="last")
d_sorted = piv_c.index.to_numpy(dtype="int64")
codes_list = list(piv_c.columns)
close_by = {c: piv_c[c].to_numpy(dtype=float) for c in codes_list}
open_by = {c: piv_o[c].to_numpy(dtype=float) for c in codes_list}
pos_of = {int(x): i for i, x in enumerate(d_sorted)}
log(f"qfq 股={len(codes_list)}")

st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
st_con.close()
st_set = set(zip(st_rows["code"].map(norm_code), st_rows["date"].astype("int64")))


def event_outcome(code, d_ev):
    """跳空日次一bar收盘入场: d_ev=公告日, i=公告bar, e=跳空bar(i+1), entry=e+1"""
    if code not in close_by:
        return None, None
    arr_c, arr_o = close_by[code], open_by[code]
    i = int(np.searchsorted(d_sorted, d_ev))
    if i >= len(d_sorted) or i + 2 >= len(arr_c):
        return None, None
    if (code, int(d_sorted[i])) in st_set:
        return None, None
    # 跳空
    gap = (arr_o[i + 1] / arr_c[i] - 1.0) if (np.isfinite(arr_o[i + 1]) and arr_c[i] > 0) else None
    if gap is None:
        return None, None
    # 入场=跳空日次一bar收盘
    entry_i = i + 2
    entry = arr_c[entry_i]
    if not np.isfinite(entry) or entry <= 0 or entry > 30.0:
        return gap, None
    if (code, int(d_sorted[entry_i])) in st_set:
        return gap, None
    # t20e/t60e
    result = {}
    for tag, h in HOLDS.items():
        end = min(entry_i + h, len(arr_c) - 1)
        if end <= entry_i:
            result[tag] = None
            continue
        w = arr_c[entry_i + 1: end + 1]
        broke = w <= entry * STOP
        xp = w[int(np.argmax(broke))] if broke.any() else arr_c[end]
        if np.isfinite(xp):
            result[tag] = (bool(xp > entry), xp / entry - 1.0)
        else:
            result[tag] = None
    r20 = result.get("t20e")
    r60 = result.get("t60e")
    out = {"d": int(d_sorted[entry_i]), "gap": round(gap * 100, 2),
           "t20e_win": r20[0] if r20 else None, "t20e_ret": r20[1] if r20 else None,
           "t60e_win": r60[0] if r60 else None, "t60e_ret": r60[1] if r60 else None}
    return gap, out


# ---------- 提取事件 ----------
pos_sorted = pos.sort_values(["code", "d"])
events, last_c = [], {}
for _, r in pos_sorted.iterrows():
    c, d = r["code"], int(r["d"])
    if c in last_c and (d - last_c[c]) < 10000000:  # 90日历日冷却
        continue
    gap, out = event_outcome(c, d)
    if out is None:
        if gap is not None:
            last_c[c] = d
        continue
    events.append({"code": c, **out})
    last_c[c] = d
ev = pd.DataFrame(events)
ev = ev[ev["t20e_win"].notna()]
log(f"事件={len(ev)} (有跳空数据)")

# 分臂
main = ev[ev["gap"] >= 2.0]
ctrl_3 = ev[ev["gap"] >= 3.0]
ctrl_1_2 = ev[(ev["gap"] >= 1.0) & (ev["gap"] < 2.0)]
ctrl_0 = ev[ev["gap"] < 1.0]
log(f"主臂(跳空>=2%)={len(main)} | >=3%={len(ctrl_3)} | 1-2%={len(ctrl_1_2)} | <1%={len(ctrl_0)}")

# ---------- 基准 ----------
need = sorted(set(int(x) for x in ev["d"]))
log(f"基准日期数={len(need)}")
base_by_d = {}
for T in need:
    i = pos_of.get(T)
    if i is None:
        continue
    entry_arr = piv_c.iloc[i].to_numpy(dtype=float)
    w20, w60 = [], []
    for ci in range(len(codes_list)):
        e_val = entry_arr[ci]
        if not np.isfinite(e_val) or e_val <= 0 or e_val > 30.0:
            continue
        cx = codes_list[ci]
        if (cx, T) in st_set:
            continue
        arr = close_by[cx]
        e = i + 1
        if e >= len(arr) or not np.isfinite(arr[e]) or arr[e] <= 0 or arr[e] > 30.0:
            continue
        entry = arr[e]
        end = min(e + 60, len(arr) - 1)
        if end <= e:
            continue
        w = arr[e + 1: end + 1]
        broke = w <= entry * STOP
        xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
        if not np.isfinite(xp):
            continue
        w60.append(xp > entry)
        if e + 20 < len(arr):
            w20v = arr[e + 1: e + 21]
            b20 = w20v <= entry * STOP
            xp20 = w20v[int(np.argmax(b20))] if b20.any() else arr[e + 20]
            if np.isfinite(xp20):
                w20.append(xp20 > entry)
    if w20:
        base_by_d[T] = {"bw20": float(np.mean(w20)),
                        "bw60": float(np.mean(w60)) if w60 else None}
log("基准完成")


def stat(sub):
    out = {"n": int(len(sub))}
    if len(sub) == 0:
        return out
    ok = sub["d"].map(lambda x: base_by_d.get(x, {}).get("bw20"))
    s = sub[ok.notna()]
    if len(s) == 0:
        return out
    out["t20e"] = {
        "win": round(float(s["t20e_win"].mean()), 4),
        "base": round(float(s["d"].map(lambda x: base_by_d[x]["bw20"]).mean()), 4)}
    out["t20e"]["exc_pp"] = round((out["t20e"]["win"] - out["t20e"]["base"]) * 100, 2)
    out["t20e"]["net_mean_exc_pp"] = round((float(s["t20e_ret"].mean()) - COST_RT) * 100, 2)
    s60 = s[s["t60e_win"].notna()]
    if len(s60):
        b60 = s60["d"].map(lambda x: base_by_d.get(x, {}).get("bw60"))
        m60 = b60.notna()
        if m60.sum() > 0:
            out["t60e"] = {
                "win": round(float(s60[m60]["t60e_win"].mean()), 4),
                "base": round(float(b60[m60].mean()), 4)}
            out["t60e"]["exc_pp"] = round((out["t60e"]["win"] - out["t60e"]["base"]) * 100, 2)
    s2 = s.assign(year=s["d"] // 10000)
    by = {}
    for y, g in s2.groupby("year"):
        if len(g) >= 15:
            b = g["d"].map(lambda x: base_by_d[x]["bw20"]).mean()
            by[int(y)] = round((float(g["t20e_win"].mean()) - float(b)) * 100, 2)
    out["by_year_exc"] = by
    out["n_pos_years"] = int(np.sum([v > 0 for v in by.values()]))
    return out


res_main = stat(main)
res_ctrl = {"ctrl_gap3": stat(ctrl_3), "ctrl_gap1_2": stat(ctrl_1_2), "ctrl_no_gap": stat(ctrl_0)}

# ---------- 门禁 ----------
m20 = res_main.get("t20e", {})
m60 = res_main.get("t60e", {})
gates = {
    "G1(n>=200)": res_main["n"] >= 200,
    "G2_t20e(exc>=+4.0pp)": m20.get("exc_pp", -99) >= 4.0,
    "G3_net(>=+1.0pp)": m20.get("net_mean_exc_pp", -99) >= 1.0,
    "G4_t60e(exc>=+1.5pp)": m60.get("exc_pp", -99) >= 1.5,
    "G5_years(>=5正)": res_main.get("n_pos_years", 0) >= 5,
}
# G6 单调性: 跳空>=3% 臂超额 >= 主臂
c3_exc = res_ctrl["ctrl_gap3"].get("t20e", {}).get("exc_pp", -99)
gates["G6_monotonicity(gap3>=main)"] = c3_exc >= m20.get("exc_pp", 0)

verdict = "PASS_CANDIDATE(提请主人)" if all(gates.values()) else "FAIL"

out = {"meta": {"generated_at": "2026-10-25",
                "order": "继续→断层臂升格prereg",
                "prereg": PREREG, "fingerprint": fp,
                "selection_bias_note": pre["selection_bias_note"],
                "zero_trade_authority": True},
       "main_arm": res_main, "control_arms": res_ctrl,
       "gates": {k: bool(v) for k, v in gates.items()},
       "monotonicity": {"gap3_exc": c3_exc, "main_exc": m20.get("exc_pp")},
       "verdict": verdict}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
log(f"完成 → {OUT}")
print(json.dumps({"verdict": verdict, "gates": out["gates"],
                  "main": {"n": res_main["n"], "t20e": m20, "t60e": m60,
                           "n_pos_years": res_main.get("n_pos_years"),
                           "by_year": res_main.get("by_year_exc")},
                  "monotonicity": out["monotonicity"],
                  "ctrl_summary": {k: {"n": v.get("n"),
                                       "t20_exc": v.get("t20e", {}).get("exc_pp")}
                                   for k, v in res_ctrl.items()}},
                 ensure_ascii=False, indent=1, default=str))
