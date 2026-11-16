# -*- coding: utf-8 -*-
"""全量测试补遗 (20261025 深夜, 主人「继续」)。

三件事:
  A. 勘误: 主报告 v4_composite 腿的"断供5个月"说法有误 — stock_signals_v4 为已登记死表
     (check_db_freshness.py:53, 2026-09-10 移出巡检), 生产 v4 信号 = 管线CSV(新鲜至20261022)
     + stock_signals_v43。本脚本补测真生产腿。
  B. v4csv_leg: reports/daily_signals/signal_*.csv (master/hist 日选股, 20260817-20261022)
     次日收盘入场 t20e/t10e 前向, 同日全宇宙匹配基准。小样本如实报n。
  C. baya 加固: ①规则级对照=本框架复放事件(截至qfq最新日) vs 官方扫描器
     data/baya_pattern_scan_latest.json 同日命中(口径差=688/ST过滤法); ②出场敏感性=
     无止损fwd20/60日、止损0.90/0.92/0.95, 验证-19.6pp非止损口径伪影。

只读(qfq/st_history/baya json/csv), 零交易权。输出: analysis/all_methods_fulltest_supp_20261025_result.json
"""
import glob
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
BAYA_JSON = r"D:\zcodesj\data\baya_pattern_scan_latest.json"
CSV_DIR = r"D:\zcodesj\reports\daily_signals"
OUT = r"D:\zcodesj\analysis\all_methods_fulltest_supp_20261025_result.json"

t0 = time.time()


def log(m):
    print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)


def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    return d[-6:] if len(d) >= 6 else d


# ---------- 数据 ----------
st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
st_con.close()
st_by_code = {}
for c, d in zip(st_rows["code"].map(norm_code), st_rows["date"].astype("int64")):
    st_by_code.setdefault(c, set()).add(int(d))

con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE date>='20170101' ORDER BY code,date", con)
con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6", "0", "3"]) & ~df["code"].str.startswith(("688", "689"))]
df = df[(df["close"] > 0) & (df["volume"] > 0)].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].reset_index(drop=True)
df["d"] = df["date"].astype("int64")
log(f"qfq 行={len(df)} 股={df['code'].nunique()}")

code_arrays = {code: (g["d"].to_numpy(dtype="int64"), g["close"].to_numpy(dtype=float))
               for code, g in df.groupby("code", sort=False)}


def fwd_from_next_close(code, sig_d, hold=20, stop=None):
    """信号日sig_d → 次一bar收盘入场; stop=None=纯持有到期; 先触止损否则到期收盘。"""
    if code not in code_arrays:
        return None
    dts, c = code_arrays[code]
    i = int(np.searchsorted(dts, sig_d))
    if i >= len(dts) or dts[i] != sig_d:
        return None
    e = i + 1
    if e + hold >= len(c):
        return None
    entry = c[e]
    if entry <= 0:
        return None
    if stop is not None:
        w = c[e + 1: e + 1 + hold]
        broke = w <= entry * stop
        exit_px = w[int(np.argmax(broke))] if broke.any() else c[e + hold]
    else:
        exit_px = c[e + hold]
    return (bool(exit_px > entry), exit_px / entry - 1.0)


def universe_baseline_multi(dates_needed, holds=(10, 20)):
    """同日全宇宙(≤30/非ST/6-0-3) 次日收盘入场纯持有到期: 每个hold返回(按日均值df, 全体win均值)。"""
    dt_arr = np.array(sorted(set(int(x) for x in dates_needed)), dtype="int64")
    out = {h: [] for h in holds}
    for code, (dts, c) in code_arrays.items():
        hit = np.isin(dts, dt_arr)
        if not hit.any():
            continue
        st_d = st_by_code.get(code, ())
        n = len(c)
        for i in np.nonzero(hit)[0]:
            if st_d and int(dts[i]) in st_d:
                continue
            if c[i] > 30.0:
                continue
            e = i + 1
            entry = c[e] if e < n else None
            if entry is None or entry <= 0:
                continue
            for h in holds:
                if e + h < n:
                    out[h].append((int(dts[i]), c[e + h] > entry, c[e + h] / entry - 1.0))
    res = {}
    for h in holds:
        b = pd.DataFrame(out[h], columns=["d", "win", "ret"])
        res[h] = (b.groupby("d").agg(bw=("win", "mean"), br=("ret", "mean")),
                  float(b["win"].mean()) if len(b) else None)
    return res


# ================================================== B. v4csv_leg
csv_files = sorted(glob.glob(f"{CSV_DIR}\\signal_*.csv"))
recs = []
for fp in csv_files:
    d = int(fp[-12:-4])
    try:
        t = pd.read_csv(fp, dtype={"code": str})
    except Exception as e:
        log(f"  skip {fp}: {e}")
        continue
    for _, r in t.iterrows():
        recs.append({"d": d, "code": norm_code(r["code"])})
sig_df = pd.DataFrame(recs).dropna(subset=["code"])
log(f"v4csv 信号日={sig_df['d'].nunique()} 笔={len(sig_df)}")

specs = {"t20e_stop92": (20, 0.92), "t10e_stop92": (10, 0.92), "t20_nostop": (20, None)}
outs = {k: [] for k in specs}
for r in sig_df.itertuples():
    for k, (h, sp) in specs.items():
        x = fwd_from_next_close(r.code, int(r.d), h, sp)
        if x:
            outs[k].append((int(r.d), x[0], x[1]))

all_dates = [o[0] for v in outs.values() for o in v]
bases = universe_baseline_multi(all_dates, holds=(10, 20))


def agg(out, bmap, base_all):
    if not out:
        return {"n": 0}
    wins = [o[1] for o in out]
    rets = [o[2] for o in out]
    bw = [float(bmap.loc[o[0], "bw"]) for o in out if o[0] in bmap.index]
    br = [float(bmap.loc[o[0], "br"]) for o in out if o[0] in bmap.index]
    return {"n": len(out), "win": round(float(np.mean(wins)), 4),
            "ret_mean": round(float(np.mean(rets)), 4),
            "base_win": round(float(np.mean(bw)), 4) if bw else None,
            "base_ret": round(float(np.mean(br)), 4) if br else None,
            "excess_win_pp": round((float(np.mean(wins)) - float(np.mean(bw))) * 100, 2) if bw else None,
            "excess_ret_pp": round((float(np.mean(rets)) - float(np.mean(br))) * 100, 2) if br else None}


v4csv = {"note": "生产v4管线CSV(master/hist日选股) 20260817-20261022, 次日收盘入场; "
                 "t20窗仅早期信号日完整, 小样本低置信",
         "t20e_stop92": agg(outs["t20e_stop92"], bases[20][0], bases[20][1]),
         "t10e_stop92": agg(outs["t10e_stop92"], bases[10][0], bases[10][1]),
         "t20_nostop": agg(outs["t20_nostop"], bases[20][0], bases[20][1]),
         "dates_t20e": sorted({o[0] for o in outs["t20e_stop92"]})}
log(f"v4csv: t20e={v4csv['t20e_stop92']} t10e={v4csv['t10e_stop92']}")

# ================================================== C. baya 加固
baya = json.load(open(BAYA_JSON, encoding="utf-8"))
scan_hits = {norm_code(h["code"]): h for h in baya.get("hits", [])}
max_d = int(df["d"].max())
mine_lastday = set()
for code, g in df.groupby("code", sort=False):
    c = g["close"].to_numpy(dtype=float)
    v = g["volume"].to_numpy(dtype=float)
    dts = g["d"].to_numpy(dtype="int64")
    n = len(c)
    if n < 420 or int(dts[-1]) != max_d:
        continue
    s = pd.Series(c)
    mns = s.rolling(250, min_periods=250).min()
    mxs = s.rolling(250, min_periods=250).max()
    if not (mxs.iloc[-1] > mns.iloc[-1]) or c[-1] > 20 or c[-1] < 2:
        continue
    pos = (c[-1] - mns.iloc[-1]) / (mxs.iloc[-1] - mns.iloc[-1]) * 100
    if pos > 15 or (c[-1] / mxs.iloc[-1] - 1) * 100 > -35:
        continue
    poss = ((s - mns) / (mxs - mns) * 100).to_numpy()
    low = 0
    for k in range(n - 1, 249, -1):
        if np.isfinite(poss[k]) and poss[k] < 20:
            low += 1
        else:
            break
    if low < 90:
        continue
    v120 = v[-120:].mean()
    if v[-5:].mean() / v120 < 1.2 or v[-20:].mean() / v120 < 0.95:
        continue
    if (c[-1] / c[-21] - 1) * 100 >= 10:
        continue
    mine_lastday.add(code)
scan_no688 = {cx for cx in scan_hits if not cx.startswith(("688", "689"))}
c1_check = {"baya_json_hits_total": len(scan_hits), "scan_hits_ex688": len(scan_no688),
            "my_hits_same_day": len(mine_lastday),
            "scan_ex688_not_in_mine": sorted(scan_no688 - mine_lastday),
            "mine_extra_count": len(mine_lastday - scan_no688)}
log(f"baya规则对照: {c1_check}")

# C2. 全史事件复算 + 多口径
events = []
for code, g in df.groupby("code", sort=False):
    c = g["close"].to_numpy(dtype=float)
    v = g["volume"].to_numpy(dtype=float)
    dts = g["d"].to_numpy(dtype="int64")
    n = len(c)
    if n < 420:
        continue
    st_d = st_by_code.get(code, ())
    s, sv = pd.Series(c), pd.Series(v)
    mns = s.rolling(250, min_periods=250).min().to_numpy()
    mxs = s.rolling(250, min_periods=250).max().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        poss = np.where(mxs > mns, (c - mns) / (mxs - mns) * 100, np.nan)
    lowmask = np.nan_to_num(poss, nan=100.0) < 20
    runlen = np.zeros(n, dtype=int)
    run = 0
    for k in range(n - 1, -1, -1):
        run = run + 1 if lowmask[k] else 0
        runlen[k] = run
    v120 = sv.rolling(120, min_periods=120).mean().to_numpy()
    v5m = sv.rolling(5, min_periods=5).mean().to_numpy()
    v20m = sv.rolling(20, min_periods=20).mean().to_numpy()
    ret20 = np.full(n, np.nan)
    ret20[20:] = (c[20:] / c[:-20] - 1) * 100
    with np.errstate(invalid="ignore", divide="ignore"):
        bay = ((c >= 2) & (c <= 20) & (poss <= 15) & ((c / mxs - 1) * 100 <= -35)
               & (runlen >= 90) & (v5m / v120 >= 1.2) & (v20m / v120 >= 0.95)
               & (ret20 < 10))
    bay = np.nan_to_num(bay.astype(float), nan=0.0).astype(bool)
    idxs = np.nonzero(bay & (dts >= 20200104) & (dts <= 20261023))[0]
    keep, last = [], -10 ** 9
    for i in idxs:
        if i - last > 20:
            keep.append(i)
            last = i
    for i in keep:
        if st_d and int(dts[i]) in st_d:
            continue
        events.append((code, int(dts[i])))
log(f"baya事件复算 n={len(events)} (主跑对账目标=1469)")

sens = {}
for tag, hold, stop in (("t20e_stop92", 20, 0.92), ("t20e_stop90", 20, 0.90),
                        ("t20e_stop95", 20, 0.95), ("t20_nostop", 20, None),
                        ("t60_nostop", 60, None)):
    out = [x for x in (fwd_from_next_close(cx, d, hold, stop) for cx, d in events) if x]
    sens[tag] = {"n": len(out), "win": round(float(np.mean([x[0] for x in out])), 4),
                 "ret_mean": round(float(np.mean([x[1] for x in out])), 4)}
bb = universe_baseline_multi([d for _, d in events], holds=(20,))
sens["baseline_t20_nostop_win_matched"] = round(bb[20][1], 4) if bb[20][1] is not None else None
log(f"baya敏感性: {sens}")

result = {"meta": {"generated_at": "2026-10-25", "order": "继续(全量测试补遗)",
                   "zero_trade_authority": True,
                   "erratum": "主报告'v4断供5个月'系误报: stock_signals_v4为已登记死表"
                              "(check_db_freshness.py:53, 2026-09-10移出巡检); 生产=v4管线CSV+v43表"},
         "v4csv_leg": v4csv,
         "baya_rule_check": c1_check,
         "baya_sensitivity": sens}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1, default=str)
log(f"完成 → {OUT}")
