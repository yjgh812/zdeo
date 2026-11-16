# -*- coding: utf-8 -*-
"""业绩预告正向事件·PEAD漂移检验 (统一事件流水线第二批E4, 20261025)。

依据: data/PREREG_PEAD_YJYG_20261025.json (跑前冻结)。
主臂=预告类型{预增,扭亏,略增}∧幅度>=100; A断层臂(+跳空>=2%)/B扭亏/C预增对照。
入场=公告日次一bar收盘(c1); t20e/t60e(-8%止损); 匹配日期全宇宙基准。
只读qfq+st_history+staged csv, 零交易权。输出: analysis/pead_yjyg_20261025_result.json
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
PREREG = r"D:\zcodesj\data\PREREG_PEAD_YJYG_20261025.json"
OUT = r"D:\zcodesj\analysis\pead_yjyg_20261025_result.json"

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

# ---------- 事件集 ----------
yj = pd.read_csv(STAGE, dtype={"股票代码": str})
yj["code"] = yj["股票代码"].map(norm_code)
yj = yj[~yj["code"].str.startswith(("688", "689")) & yj["code"].str[0].isin(["6", "0", "3"])]
yj["amp"] = pd.to_numeric(yj["业绩变动幅度"], errors="coerce")
yj["d"] = pd.to_datetime(yj["公告日期"]).dt.strftime("%Y%m%d").astype("int64")
yj = yj[(yj["d"] >= START) & (yj["d"] <= END)]
yj = yj.dropna(subset=["amp"])
pos = yj[yj["预告类型"].isin(["预增", "扭亏", "略增"]) & (yj["amp"] >= 100)]
pos = pos.drop_duplicates(subset=["code", "d"])
log(f"正向预告(主臂池)={len(pos)}")

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
piv = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
piv_o = df.pivot_table(index="d", columns="code", values="open", aggfunc="last")
d_sorted = piv.index.to_numpy(dtype="int64")
codes_list = list(piv.columns)
close_by = {c: piv[c].to_numpy(dtype=float) for c in codes_list}
open_by = {c: piv_o[c].to_numpy(dtype=float) for c in codes_list}
pos_of = {int(x): i for i, x in enumerate(d_sorted)}
log(f"qfq 股={len(codes_list)}")

st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
st_con.close()
st_set = set(zip(st_rows["code"].map(norm_code), st_rows["date"].astype("int64")))


def outcome(code, d_ev, hold):
    if code not in close_by:
        return None
    arr = close_by[code]
    i = int(np.searchsorted(d_sorted, d_ev))
    if i >= len(d_sorted):
        return None
    if (code, int(d_sorted[i])) in st_set:
        return None
    e = i + 1
    if e >= len(arr) or not np.isfinite(arr[e]) or arr[e] <= 0 or arr[e] > 30.0:
        return None
    entry = arr[e]
    end = min(e + hold, len(arr) - 1)
    if end <= e:
        return None
    w = arr[e + 1: end + 1]
    broke = w <= entry * STOP
    xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
    if not np.isfinite(xp):
        return None
    return {"d": int(d_sorted[i]), "win": bool(xp > entry), "ret": xp / entry - 1.0,
            "entry": float(entry)}


# 主臂事件(含60日冷却)
pos_sorted = pos.sort_values(["code", "d"])
events, last_c = [], {}
for _, r in pos_sorted.iterrows():
    c, d = r["code"], int(r["d"])
    if c in last_c and (d - last_c[c]) < 60_00_00_00_00 // 10**6:  # 粗略60日历日≈冷却
        pass
    # 冷却用事件索引近似: 用日期差>90天
    if c in last_c and (d - last_c[c]) < 10000000:
        continue
    r20 = outcome(c, d, 20)
    if r20 is None:
        continue
    r60 = outcome(c, d, 60)
    ev = {"code": c, "type": r["预告类型"], **r20,
          "t60_win": r60["win"] if r60 else None, "t60_ret": r60["ret"] if r60 else None}
    events.append(ev)
    last_c[c] = d
ev = pd.DataFrame(events)
log(f"主臂有效事件={len(ev)}")

# 断层臂: 公告日次日开盘跳空>=2%
def gap_ok(code, d_ev):
    if code not in close_by:
        return False
    arr_c, arr_o = close_by[code], open_by[code]
    i = int(np.searchsorted(d_sorted, d_ev))
    if i >= len(d_sorted) or i == 0:
        return False
    pc = arr_c[i - 1]
    # 事件bar=公告日(或顺延)次一bar的开盘 vs 事件bar前收? 冻结: 次日开盘跳空=事件bar次一bar的开盘/事件bar收盘-1
    e = i + 1
    if e >= len(arr_o) or not np.isfinite(arr_o[e]) or not np.isfinite(arr_c[i]) or arr_c[i] <= 0:
        return False
    return arr_o[e] / arr_c[i] - 1.0 >= 0.02


ev["gap2"] = [gap_ok(c, d) for c, d in zip(ev["code"], ev["d"])]
log(f"断层臂(跳空>=2%)={int(ev['gap2'].sum())}")

# ---------- 基准(事件日期集, t20/t60) ----------
need = sorted(set(int(x) for x in ev["d"]))
log(f"基准日期数={len(need)}")
base_by_d = {}
for T in need:
    i = pos_of.get(T)
    if i is None:
        continue
    entry_arr = piv.iloc[i].to_numpy(dtype=float)
    w20, r20s, w60 = [], [], []
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
        r60 = xp / entry - 1.0
        if e + 20 < len(arr):
            w20v = arr[e + 1: e + 21]
            b20 = w20v <= entry * STOP
            xp20 = w20v[int(np.argmax(b20))] if b20.any() else arr[e + 20]
            if np.isfinite(xp20):
                w20.append(xp20 > entry)
        w60.append(xp > entry)
        r20s.append(r60 if False else None)
        r20s[-1] = r60
    if w60:
        base_by_d[T] = {"bw20": float(np.mean(w20)) if w20 else None,
                        "bw60": float(np.mean(w60)), "br60": float(np.mean([x for x in r20s if x is not None]))}
log("基准完成")


def stat(sub):
    out = {"n": int(len(sub))}
    if len(sub) == 0:
        return out
    ok20 = sub["d"].map(lambda x: base_by_d.get(x, {}).get("bw20"))
    m = ok20.notna()
    s = sub[m]
    out["t20e"] = {
        "win": round(float(s["win"].mean()), 4),
        "base": round(float(s["d"].map(lambda x: base_by_d[x]["bw20"]).mean()), 4),
        "exc_pp": round((float(s["win"].mean()) - float(s["d"].map(lambda x: base_by_d[x]["bw20"]).mean())) * 100, 2),
        "net_mean_exc_pp": round((float(s["ret"].mean()) - COST_RT) * 100, 2)}
    s60 = s[s["t60_win"].notna()]
    if len(s60):
        out["t60e"] = {
            "win": round(float(s60["t60_win"].mean()), 4),
            "base": round(float(s60["d"].map(lambda x: base_by_d[x]["bw60"]).mean()), 4),
            "exc_pp": round((float(s60["t60_win"].mean()) - float(s60["d"].map(lambda x: base_by_d[x]["bw60"]).mean())) * 100, 2),
            "net_mean_ret_pp": round(float(s60["t60_ret"].mean()) * 100, 2)}
    s2 = s.assign(year=s["d"] // 10000)
    by = {}
    for y, g in s2.groupby("year"):
        if len(g) >= 20:
            b = g["d"].map(lambda x: base_by_d[x]["bw20"]).mean()
            by[int(y)] = round((float(g["win"].mean()) - float(b)) * 100, 2)
    out["by_year_exc"] = by
    out["n_pos_years"] = int(np.sum([v > 0 for v in by.values()]))
    return out


main = stat(ev)
arms = {"A_gap2": stat(ev[ev["gap2"]]),
        "B_nk": stat(ev[ev["type"] == "扭亏"]),
        "C_yz": stat(ev[ev["type"] == "预增"])}

gates = {
    "G1(n>=800)": main["n"] >= 800,
    "G2_t20e(exc>=1.5,net>=1.0)": (main["t20e"]["exc_pp"] >= 1.5 and main["t20e"]["net_mean_exc_pp"] >= 1.0),
    "G3_t60e(exc>=1.5)": main["t60e"]["exc_pp"] >= 1.5,
    "G4_years(>=6/11)": main["n_pos_years"] >= 6,
}
esc = (main["t20e"]["exc_pp"] >= 2.0 and main["t60e"]["exc_pp"] >= 2.0
       and main["n_pos_years"] >= 7 and main["t20e"]["net_mean_exc_pp"] >= 1.5)
verdict = ("PASS_CANDIDATE(提请)" if esc else
           ("观察登记(描述门过提请门未过)" if all(gates.values()) else "FAIL"))

out = {"meta": {"generated_at": "2026-10-25", "order": "继续→事件流水线E4(PEAD预告)",
                "prereg": PREREG, "fingerprint": fp,
                "data_note": "akshare stock_yjyg_em全史staged(133,812行), 公告日期PIT锚",
                "zero_trade_authority": True},
       "main": main, "arms": arms,
       "gates": {k: bool(v) for k, v in gates.items()}, "escalated": bool(esc),
       "verdict": verdict}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
log(f"完成 → {OUT}")
print(json.dumps({"verdict": verdict, "gates": out["gates"], "main_t20e": main["t20e"],
                  "main_t60e": main.get("t60e"), "n_pos_years": main["n_pos_years"],
                  "by_year": main["by_year_exc"],
                  "arms": {k: {"n": v.get("n"), "t20_exc": v.get("t20e", {}).get("exc_pp"),
                               "t60_exc": v.get("t60e", {}).get("exc_pp")} for k, v in arms.items()}},
                 ensure_ascii=False, indent=1, default=str))
