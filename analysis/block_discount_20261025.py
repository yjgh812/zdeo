# -*- coding: utf-8 -*-
"""大宗交易折溢率五分组单调性检验 (事件流水线E5, 20261025)。
依据: data/PREREG_BLOCK_DISCOUNT_20261025.json (跑前冻结)。
五组: <=-10%/-10~-5%/-5~0%/0~+5%/>+5%; 入场=大宗日次一收盘; t20e; 匹配基准。
只读qfq+st_history+staged csv, 零交易权。输出: analysis/block_discount_20261025_result.json
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
STAGE = r"D:\zcodesj\data\block_trade_stage_2016_2026.csv"
PREREG = r"D:\zcodesj\data\PREREG_BLOCK_DISCOUNT_20261025.json"
OUT = r"D:\zcodesj\analysis\block_discount_20261025_result.json"

START, END = 20160101, 20261023
COST_RT = 0.003
HOLD, STOP = 20, 0.92

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

# ---------- 大宗交易数据 ----------
bt = pd.read_csv(STAGE, dtype={"证券代码": str})
bt["code"] = bt["证券代码"].map(norm_code)
bt = bt[~bt["code"].str.startswith(("688", "689")) & bt["code"].str[0].isin(["6", "0", "3"])]
bt["disc"] = pd.to_numeric(bt["折溢率"], errors="coerce")
bt["amt"] = pd.to_numeric(bt["成交总额"], errors="coerce")
bt["d"] = pd.to_datetime(bt["交易日期"]).dt.strftime("%Y%m%d").astype("int64")
bt = bt[(bt["d"] >= START) & (bt["d"] <= END)].dropna(subset=["disc", "amt"])
# 同股同日取成交总额最大笔
bt = bt.sort_values("amt", ascending=False).drop_duplicates(subset=["code", "d"], keep="first")
# 同股90日历日冷却(取折溢率绝对值最大的笔)
bt = bt.reindex(bt["disc"].abs().sort_values(ascending=False).index)
seen, dedup = {}, []
for _, r in bt.iterrows():
    c = r["code"]
    if c in seen and (int(r["d"]) - seen[c]) < 10000000:
        continue
    seen[c] = int(r["d"])
    dedup.append(r)
bt = pd.DataFrame(dedup)
log(f"大宗事件(去重冷却)={len(bt)}")

# 五分组
bins = [-1, -0.10, -0.05, 0.0, 0.05, 1]
labels = ["deep_disc(<=-10%)", "mid_disc(-10~-5%)", "slight_disc(-5~0%)",
          "slight_prem(0~+5%)", "high_prem(>+5%)"]
bt["bucket"] = pd.cut(bt["disc"], bins=bins, labels=labels, include_lowest=True)
log("分组: " + str(bt["bucket"].value_counts().to_dict()))

# ---------- 价格底座 ----------
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
piv = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
d_sorted = piv.index.to_numpy(dtype="int64")
codes_list = list(piv.columns)
close_by = {c: piv[c].to_numpy(dtype=float) for c in codes_list}
pos_of = {int(x): i for i, x in enumerate(d_sorted)}
log(f"qfq 股={len(codes_list)}")

st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
st_con.close()
st_set = set(zip(st_rows["code"].map(norm_code), st_rows["date"].astype("int64")))


def event_outcome(code, d_ev):
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
    end = min(e + HOLD, len(arr) - 1)
    if end <= e:
        return None
    w = arr[e + 1: end + 1]
    broke = w <= entry * STOP
    xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
    if not np.isfinite(xp):
        return None
    return {"d": int(d_sorted[i]), "win": bool(xp > entry), "ret": xp / entry - 1.0}


# 计算事件结果
results = []
for _, r in bt.iterrows():
    out = event_outcome(r["code"], int(r["d"]))
    if out:
        results.append({"code": r["code"], "bucket": str(r["bucket"]), "disc": r["disc"], **out})
ev = pd.DataFrame(results)
log(f"有效事件={len(ev)}")

# ---------- 匹配基准 ----------
need = sorted(set(int(x) for x in ev["d"]))
log(f"基准日期数={len(need)}")
base_by_d = {}
for T in need:
    i = pos_of.get(T)
    if i is None:
        continue
    entry_arr = piv.iloc[i].to_numpy(dtype=float)
    wins = []
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
        end = min(e + HOLD, len(arr) - 1)
        if end <= e:
            continue
        w = arr[e + 1: end + 1]
        broke = w <= entry * STOP
        xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
        if np.isfinite(xp):
            wins.append(xp > entry)
    if wins:
        base_by_d[T] = float(np.mean(wins))
log("基准完成")


def bucket_stat(label):
    sub = ev[ev["bucket"] == label]
    out = {"n": int(len(sub))}
    if len(sub) == 0:
        return out
    bw = sub["d"].map(lambda x: base_by_d.get(x))
    ok = bw.notna()
    if ok.sum() == 0:
        return out
    s = sub[ok]
    out["win"] = round(float(s["win"].mean()), 4)
    out["base"] = round(float(bw[ok].mean()), 4)
    out["exc_pp"] = round((out["win"] - out["base"]) * 100, 2)
    out["net_exc_pp"] = round((float(s["ret"].mean()) - COST_RT) * 100, 2)
    s2 = s.assign(year=s["d"] // 10000)
    by = {}
    for y, g in s2.groupby("year"):
        if len(g) >= 10:
            b = g["d"].map(lambda x: base_by_d.get(x)).mean()
            by[int(y)] = round((float(g["win"].mean()) - float(b)) * 100, 2)
    out["by_year"] = by
    return out


bucket_results = {label: bucket_stat(label) for label in labels}

# ---------- 门禁 ----------
from scipy.stats import spearmanr
bucket_order = list(range(5))
excs = [bucket_results[l].get("exc_pp", 0) for l in labels]
mono_corr, _ = spearmanr(bucket_order, excs)

deep = bucket_results[labels[0]]
high = bucket_results[labels[4]]
gates = {
    "G1_monotonicity(corr<=-0.8)": mono_corr <= -0.8,
    "G2_deep(exc>=+2.0pp)": deep.get("exc_pp", -99) >= 2.0,
    "G3_high_prem(exc<=-1.0pp)": high.get("exc_pp", 99) <= -1.0,
    "G4_deep_net(>=+1.0pp)": deep.get("net_exc_pp", -99) >= 1.0,
}
if all(gates.values()):
    verdict = "PASS_CANDIDATE"
elif not gates["G1_monotonicity(corr<=-0.8)"]:
    verdict = "FAIL(单调性不存在)"
else:
    verdict = "部分确认(单调性在但量级不足)"

out = {"meta": {"generated_at": "2026-10-25", "order": "继续→大宗折价单调性",
                "prereg": PREREG, "fingerprint": fp,
                "data_note": "akshare stock_dzjy_mrtj全史103,672笔staged",
                "zero_trade_authority": True},
       "buckets": bucket_results, "monotonicity_corr": round(float(mono_corr), 3),
       "bucket_exc_sequence": dict(zip(labels, excs)),
       "gates": {k: bool(v) for k, v in gates.items()}, "verdict": verdict}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
log(f"完成 → {OUT}")
print(json.dumps({"verdict": verdict, "monotonicity_corr": out["monotonicity_corr"],
                  "bucket_exc": out["bucket_exc_sequence"], "gates": out["gates"],
                  "deep_detail": deep, "high_detail": high},
                 ensure_ascii=False, indent=1, default=str))
