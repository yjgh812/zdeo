# -*- coding: utf-8 -*-
"""所有选股方式·统一口径全量测试 (20261025, 主人令「所有选股方式，全量测试」)。

定位: 描述性统一横评, 零交易权, 只读四库(qfq/st_history/industry_map/dragon_quant+factor_pit)。
  - 统一宇宙: 6/0/3 且剔688/689(交易口径), 上市>60日, 非ST日, volume>0; 基准宇宙另加 close<=30。
  - 统一出场: t20e = 20交易日持有, -8%收盘止损(0.92), 先触止损否则到期收盘离场。
  - 两种入场: c0=信号日收盘(与lowconsol族脚本同口径, 锚定对照);
              c1=次交易日收盘(诚实口径, 全腿主口径)。
  - 基准: 同信号日全宇宙同口径均值(匹配日期基准); 牛/熊相位=等权指数>MA60。
  - 纪律: 本轮不设新判定闸(多重比较防伪); 判定性结论只引用既有prereg/证伪档案。

方法清单(规则源逐条注明出处):
  规则腿 R01 lowconsol_A / R02 lowconsol_A_full19(锚点腿,2019+全窗) / R03 A∩量比主线 /
        R04 A∩主线首日 / R05 v5.2六层全档 / R06 v5.2最优档off5-10 / R07 baya前夕形态
  证伪对照 F trifecta_V1/V2, pullback_A, chase_ref, pullback_ml, chase_ml (昨日prereg已判死,
        本跑仅为统一出场口径对照, 非翻案; 档案 trifecta/mainline_pullback_event_study_20261024)
  评分腿 S ml_v22_top20(样本内注脚必附) / ml_v25_top20 / v4_composite_top20(表断供20260528) /
        v43_top20(短窗)
  因子腿 B bp_ep_pit_top20(纯因子) / bp_ep_pit_top15ind(生产口径 行业<=4) — PIT v2重建
  禁测(铁律7): hotspot_breakout/smc_s2_roe/突破族9口径/box_breakout_lowpos/mainline_startup/
        upprob_ab/gap_up/t1_sealed/dip_probe/low_open/limit_continuation/trend_following/
        fresh_screen/北向/行业动量轮动 等(PROJECT_BOUNDARY §3)
  无法复放: turnover_mvp/mainline_radar/pattern_match/mass_scan/early_board(_r1)/dragon_r3/
        full_market — admit标签在册但无全史信号存档(daily_picks仅12日)
  已有全量档案不重跑: sold_rebuy_deep8(13年355,640笔t=147三门全过, prereg c52fdf8b888c8b32)

输出: analysis/all_methods_fulltest_20261025_result.json + stdout 对照表。
"""
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
INDDB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
METADB = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro"
DRAGON = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro"
PITDB = r"file:D:\zcodesj\factor_pit.db?mode=ro"
OUT = r"D:\zcodesj\analysis\all_methods_fulltest_20261025_result.json"

LOAD_START = "20170101"     # v52/baya需约750日预热
EVAL_START = 20200104
EVAL_END = 20261023
HOLD = 20
STOP = 0.92

t0 = time.time()


def log(msg):
    print(f"[{time.time()-t0:7.1f}s] {msg}", flush=True)


def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    return d[-6:] if len(d) >= 6 else d


# ============================================================ 1. 加载
st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
st_con.close()
st_by_code = {}
for c, d in zip(st_rows["code"].map(norm_code), st_rows["date"].astype("int64")):
    st_by_code.setdefault(c, set()).add(int(d))
log(f"ST股日标记 {sum(len(v) for v in st_by_code.values())}")

con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    f"SELECT code,date,close,volume,change_pct FROM stock_daily_meta_qfq "
    f"WHERE date>='{LOAD_START}' ORDER BY code,date", con)
con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6", "0", "3"]) & ~df["code"].str.startswith(("688", "689"))]
df = df[(df["close"] > 0) & (df["volume"] > 0)].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].reset_index(drop=True)   # 行号==位置
df["d"] = df["date"].astype("int64")
log(f"qfq 行={len(df)} 股={df['code'].nunique()}")

ind_con = sqlite3.connect(f"file:{INDDB}?mode=ro", uri=True)
l1df = pd.read_sql_query("SELECT code,name,l1 FROM l1_member WHERE ambiguous_flag=0", ind_con)
ind_con.close()
l1df["code"] = l1df["code"].astype(str).map(norm_code)
name_map = dict(zip(l1df["code"], l1df["name"]))
l1_map = dict(zip(l1df["code"], l1df["l1"]))
df["name"] = df["code"].map(name_map)
df["l1"] = df["code"].map(l1_map)

# ============================================================ 2. 主线两套 + 相位
# 2a 动量主线(mainline_pullback_event_study定义)
ind = df[df["l1"].notna()].groupby(["l1", "d"]).agg(
    r=("change_pct", "mean"), n=("change_pct", "size")).reset_index()
piv_r = ind.pivot(index="d", columns="l1", values="r").sort_index() / 100.0
piv_n = ind.pivot(index="d", columns="l1", values="n").sort_index()
idxlvl = (1.0 + piv_r.fillna(0.0)).cumprod()
mom20 = idxlvl / idxlvl.shift(20) - 1.0
mom20[piv_n < 10] = np.nan
ml_mom_by_date = {}
for d, row in zip(mom20.index.to_numpy(), mom20.itertuples(index=False)):
    pairs = sorted(((v, mom20.columns[i]) for i, v in enumerate(row) if not np.isnan(v)),
                   key=lambda x: (-x[0], x[1]))
    ml_mom_by_date[int(d)] = {nm for _, nm in pairs[:3]}
log(f"动量主线Top3 日数={len(ml_mom_by_date)}")

# 相位: 等权指数>MA60
mkt = df.groupby("d")["change_pct"].mean().reset_index().sort_values("d")
idx_eq = (1.0 + mkt["change_pct"].fillna(0.0) / 100.0).cumprod()
ma60 = idx_eq.rolling(60, min_periods=60).mean()
bull_by_d = dict(zip(mkt["d"].astype("int64"), (idx_eq > ma60).to_numpy()))

# 2b 量比主线(lowconsol_signal_daily定义)
w = df[df["l1"].notna()].copy()
w["chg5_tmp"] = w.groupby("code")["close"].pct_change(5) * 100
agg = w.groupby(["d", "l1"]).agg(vol_l1=("volume", "sum"),
                                 icm=("chg5_tmp", "median")).reset_index()
agg = agg.sort_values(["l1", "d"])
agg["v5"] = agg.groupby("l1")["vol_l1"].transform(lambda s: s.rolling(5).sum())
agg["v5p"] = agg.groupby("l1")["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
agg["volr_l1"] = agg["v5"] / agg["v5p"]
agg["c5"] = agg.groupby("l1")["icm"].transform(lambda s: s.rolling(5).mean())
hot_by_date, cur, streak_map = {}, {}, {}
for d, sub in agg.groupby("d"):
    s_ = sub.dropna(subset=["volr_l1", "c5"])
    hot_by_date[int(d)] = set(s_[s_["c5"] > 0].nlargest(3, "volr_l1")["l1"])
for d in sorted(hot_by_date):
    nxt = {}
    for l1 in hot_by_date[d]:
        nxt[l1] = cur.get(l1, 0) + 1
        streak_map[(d, l1)] = nxt[l1]
    cur = nxt
log(f"量比主线hot 日数={len(hot_by_date)}")

# ============================================================ 3. 逐股一次遍历
METHOD_RULES = ["lowconsol_A", "lowconsol_A_full19", "lowconsol_A_mainline",
                "lowconsol_A_ml_day1", "v52_deepdrop", "v52_sweet", "baya_pattern",
                "trifecta_V1", "trifecta_V2", "pullback_A", "chase_ref",
                "pullback_ml", "chase_ml"]
sig_cols = {m: np.zeros(len(df), dtype=bool) for m in METHOD_RULES}
win_c0 = np.full(len(df), np.nan)
ret_c0 = np.full(len(df), np.nan)
win_c1 = np.full(len(df), np.nan)
ret_c1 = np.full(len(df), np.nan)
uni_mask = np.zeros(len(df), dtype=bool)

SWV = np.lib.stride_tricks.sliding_window_view


def t20e_all(close):
    """全索引前向: 入场=close[i], 止损/到期窗口=close[i+1..i+20], 到期价=close[i+20]。"""
    n = len(close)
    if n <= HOLD:
        return np.zeros(0), np.zeros(0)
    w20 = SWV(close, HOLD)[1:]          # w20[i] = close[i+1 .. i+20]
    entry = close[: n - HOLD]
    stop = entry * STOP
    broke = w20 <= stop[:, None]
    has = broke.any(axis=1)
    first = np.argmax(broke, axis=1)
    rows = np.arange(len(entry))
    exit_px = np.where(has, w20[rows, first], close[HOLD:])
    return (exit_px > entry).astype(float), exit_px / entry - 1.0


def cooldown_pick(idxs, cd):
    keep, last = [], -10 ** 9
    for i in idxs:
        if i - last > cd:
            keep.append(i)
            last = i
    return keep


grouped = list(df.groupby("code", sort=False))
log(f"逐股遍历开始 股数={len(grouped)}")
for gi, (code, g) in enumerate(grouped):
    rows_i = g.index.to_numpy()          # reset后==位置, 已按date升序
    c = g["close"].to_numpy(dtype=float)
    v = g["volume"].to_numpy(dtype=float)
    dts = g["d"].to_numpy(dtype="int64")
    n = len(c)
    s = pd.Series(c)
    sv = pd.Series(v)

    w0, r0 = t20e_all(c)
    win_c0[rows_i[: len(w0)]] = w0
    ret_c0[rows_i[: len(r0)]] = r0
    if n > HOLD + 1:
        w1, r1 = t20e_all(c[1:])         # 入场=close[i+1], 窗口=close[i+2..i+21]
        win_c1[rows_i[: len(w1)]] = w1
        ret_c1[rows_i[: len(r1)]] = r1

    st_d = st_by_code.get(code, ())
    if st_d:
        st_arr = np.fromiter(st_d, dtype="int64", count=len(st_d))
        st_ok = ~np.isin(dts, st_arr)
    else:
        st_ok = np.ones(n, dtype=bool)
    uni_mask[rows_i] = st_ok & (c <= 30.0)
    inwin = (dts >= EVAL_START) & (dts <= EVAL_END)

    # ---- lowconsol族 ----
    chg5 = s.pct_change(5) * 100
    mn250 = s.rolling(250, min_periods=120).min()
    mx250 = s.rolling(250, min_periods=120).max()
    pos250 = np.where(mx250 > mn250, (s - mn250) / (mx250 - mn250) * 100, np.nan)
    isA = np.nan_to_num(np.asarray((pos250 < 30) & (chg5.abs() < 3), dtype=float),
                        nan=0.0).astype(bool)
    prevA = np.append([False], isA[:-1])
    evA = isA & ~prevA & st_ok & (c <= 30.0)
    l1arr = g["l1"].to_numpy()
    hot = np.array([l1 in hot_by_date.get(int(dd), set()) if l1 == l1 else False
                    for l1, dd in zip(l1arr, dts)])
    stk = np.array([streak_map.get((int(dd), l1), 0) if l1 == l1 else 0
                    for l1, dd in zip(l1arr, dts)])
    sig_cols["lowconsol_A"][rows_i[cooldown_pick(np.nonzero(evA & inwin)[0], 5)]] = True
    sig_cols["lowconsol_A_full19"][rows_i[cooldown_pick(np.nonzero(evA & (dts >= 20190701))[0], 5)]] = True
    sig_cols["lowconsol_A_mainline"][rows_i[cooldown_pick(np.nonzero(evA & hot & inwin)[0], 5)]] = True
    sig_cols["lowconsol_A_ml_day1"][rows_i[cooldown_pick(np.nonzero(evA & hot & (stk == 1) & inwin)[0], 5)]] = True

    # ---- 突破/回调族 ----
    dif = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    dif_a, dea_a = dif.to_numpy(), dea.to_numpy()
    mx20 = s.rolling(20, min_periods=20).max()
    brk = (s >= mx20).to_numpy()
    vol5 = sv.rolling(5, min_periods=5).mean().shift(1).to_numpy()
    chgp = g["change_pct"].to_numpy(dtype=float)
    up = chgp > 0
    trend_up = dif_a > dea_a
    hh_prior = mx20.shift(1).to_numpy()
    pull = (c <= hh_prior * 0.97) & (c >= hh_prior * 0.88)
    inmom = np.array([l1 in ml_mom_by_date.get(int(dd), set()) if l1 == l1 else False
                      for l1, dd in zip(l1arr, dts)])
    sig1 = trend_up & (dif_a > 0) & brk & (v >= 1.5 * vol5) & up
    sig2 = trend_up & brk & (v >= 1.5 * vol5) & up
    sigp = trend_up & pull & up & (v >= vol5)
    base_ok = uni_mask[rows_i] & inwin
    sig_cols["trifecta_V1"][rows_i[cooldown_pick(np.nonzero(sig1 & base_ok)[0], 10)]] = True
    sig_cols["trifecta_V2"][rows_i[cooldown_pick(np.nonzero(sig2 & base_ok)[0], 10)]] = True
    sig_cols["pullback_A"][rows_i[cooldown_pick(np.nonzero(sigp & base_ok)[0], 10)]] = True
    sig_cols["chase_ref"][rows_i[cooldown_pick(np.nonzero(sig2 & base_ok)[0], 10)]] = True
    sig_cols["pullback_ml"][rows_i[cooldown_pick(np.nonzero(sigp & inmom & base_ok)[0], 10)]] = True
    sig_cols["chase_ml"][rows_i[cooldown_pick(np.nonzero(sig2 & inmom & base_ok)[0], 10)]] = True

    # ---- v5.2六层 ----
    ma20 = s.rolling(20, min_periods=20).mean().to_numpy()
    ma60 = s.rolling(60, min_periods=60).mean().to_numpy()
    v20 = sv.rolling(20, min_periods=20).mean().to_numpy()
    pre60max = s.rolling(60, min_periods=40).max().to_numpy()
    pre60min = s.rolling(60, min_periods=40).min().to_numpy()
    drop_ok = np.nan_to_num((pre60min / pre60max - 1) * 100, nan=0.0) <= -25
    cheap = drop_ok & (c >= 2) & (c <= 30) & (ma20 > ma60) & st_ok
    if cheap.any() and n > 260:
        chg_day = np.zeros(n)
        chg_day[1:] = (c[1:] / np.maximum(c[:-1], 1e-12) - 1) * 100
        S = (np.nan_to_num(v / v20, nan=0.0) >= 2.0) & (chg_day >= 3.0)
        S[:20] = False
        lastS = np.maximum.accumulate(np.where(S, np.arange(n), -1))
        for i in np.nonzero(cheap & inwin)[0]:
            if i < 250:
                continue
            lo_g = i - 249 + int(np.argmin(c[i - 249: i + 1]))
            j = int(lastS[i])
            if j < 1 or j <= max(i - 40, lo_g):
                continue
            off = (c[i] / c[j] - 1) * 100
            if off > 17 or off < -3 or (i - j) > 40:
                continue
            yr = c[max(0, i - 749): i + 1]
            if len(yr) < 500:
                continue
            yq = np.sort(yr)
            if np.searchsorted(yq, c[i]) / len(yq) * 100 >= 55:
                continue
            if (c[i] / c[lo_g] - 1) * 100 >= 80:
                continue
            if ma20[i] > 0 and (c[i] / ma20[i] - 1) * 100 > 3:
                continue
            vv = np.sort(v[max(0, i - 749): i + 1])
            if len(vv) >= 500 and np.searchsorted(vv, v[i]) / len(vv) >= 0.80:
                continue
            sig_cols["v52_deepdrop"][rows_i[i]] = True
            if 5.0 <= off <= 10.0:
                sig_cols["v52_sweet"][rows_i[i]] = True

    # ---- baya前夕形态 ----
    if n >= 420:
        mns = s.rolling(250, min_periods=250).min()
        mxs = s.rolling(250, min_periods=250).max()
        poss = np.where(mxs > mns, (s - mns) / (mxs - mns) * 100, np.nan)
        lowmask = np.nan_to_num(poss, nan=100.0) < 20
        runlen = np.zeros(n, dtype=int)
        run = 0
        for k in range(n - 1, -1, -1):
            run = run + 1 if lowmask[k] else 0
            runlen[k] = run
        v120 = sv.rolling(120, min_periods=120).mean().to_numpy()
        v5m = sv.rolling(5, min_periods=5).mean().to_numpy()
        v20m = sv.rolling(20, min_periods=20).mean().to_numpy()
        hi250 = mxs.to_numpy()
        ret20 = np.full(n, np.nan)
        ret20[20:] = (c[20:] / c[:-20] - 1) * 100
        with np.errstate(invalid="ignore", divide="ignore"):
            bay = ((c >= 2) & (c <= 20) & (poss <= 15)
                   & ((c / hi250 - 1) * 100 <= -35)
                   & (runlen >= 90)
                   & (v5m / v120 >= 1.2) & (v20m / v120 >= 0.95)
                   & (ret20 < 10) & st_ok)
        bay = np.nan_to_num(bay.astype(float), nan=0.0).astype(bool)
        sig_cols["baya_pattern"][rows_i[cooldown_pick(np.nonzero(bay & inwin)[0], 20)]] = True

    if (gi + 1) % 800 == 0:
        log(f"  ... {gi+1}/{len(grouped)}")

for m in METHOD_RULES:
    df[m] = sig_cols[m]
log("逐股遍历完成")

# ============================================================ 4. 基准
df["win_c0"], df["ret_c0"] = win_c0, ret_c0
df["win_c1"], df["ret_c1"] = win_c1, ret_c1
df["uni"] = uni_mask
df["bull"] = df["d"].map(bull_by_d)
base_df = df[df["uni"] & df["win_c1"].notna()
             & (df["d"] >= EVAL_START) & (df["d"] <= EVAL_END)]
base_by_d = base_df.groupby("d").agg(bw1=("win_c1", "mean"), br1=("ret_c1", "mean"),
                                     bw0=("win_c0", "mean"), bn=("win_c1", "size"))
log(f"基准 日数={len(base_by_d)} 股日={int(base_by_d['bn'].sum())} "
    f"c1胜率={base_df['win_c1'].mean():.4f} c0胜率={base_df['win_c0'].mean():.4f}")


def leg_stats(ev):
    if ev is None or len(ev) == 0 or ev["win_c1"].dropna().empty:
        return {"n": 0}
    ev = ev.dropna(subset=["win_c1"])
    out = {"n": int(len(ev))}
    out["win_c1"] = round(float(ev["win_c1"].mean()), 4)
    out["ret_c1_mean"] = round(float(ev["ret_c1"].mean()), 4)
    out["win_c0"] = round(float(ev["win_c0"].mean()), 4)
    out["ret_c0_mean"] = round(float(ev["ret_c0"].mean()), 4)
    b = ev["d"].map(base_by_d["bw1"])
    br = ev["d"].map(base_by_d["br1"])
    out["base_win_c1_matched"] = round(float(b.mean()), 4)
    out["base_ret_c1_matched"] = round(float(br.mean()), 4)
    out["excess_win_pp"] = round((out["win_c1"] - out["base_win_c1_matched"]) * 100, 2)
    out["excess_ret_pp"] = round((out["ret_c1_mean"] - out["base_ret_c1_matched"]) * 100, 2)
    eb = ev["bull"] == True  # noqa: E712
    if eb.any():
        out["win_bull"] = round(float(ev.loc[eb, "win_c1"].mean()), 4)
        out["n_bull"] = int(eb.sum())
    if (~eb).any():
        out["win_bear"] = round(float(ev.loc[~eb, "win_c1"].mean()), 4)
        out["n_bear"] = int((~eb).sum())
    out["by_year"] = {int(y): {"n": int(len(s_)), "win_c1": round(float(s_["win_c1"].mean()), 4)}
                      for y, s_ in ev.groupby(ev["d"] // 10000)}
    return out


# ============================================================ 5. 评分腿
def score_leg(tbl, col, topn=20, eval_from=None):
    con = sqlite3.connect(DRAGON, uri=True)
    sc = pd.read_sql_query(
        f"SELECT code,date,{col} AS prob FROM {tbl} WHERE date>=? AND date<=?",
        con, params=(int(eval_from or EVAL_START), EVAL_END))
    con.close()
    if sc.empty:
        return {"n": 0, "note": "no rows"}
    sc["code"] = sc["code"].map(norm_code)
    keep = df.loc[df["uni"] & df["win_c1"].notna(),
                  ["code", "d", "win_c1", "ret_c1", "win_c0", "ret_c0", "bull"]]
    m = sc.merge(keep, left_on=["code", "date"], right_on=["code", "d"], how="inner")
    m = m.dropna(subset=["prob"]).sort_values(["date", "prob"], ascending=[True, False])
    top = m.groupby("date").head(topn)
    return leg_stats(top)


# ============================================================ 6. bp_ep PIT腿
def bp_ep_legs():
    from build_factor_pit_20260815 import get_pit_factor_v2
    mdates = sorted(df.loc[(df["d"] >= EVAL_START) & (df["d"] <= EVAL_END), "d"].unique())
    ym = pd.Series(mdates).astype(str).str[:6]
    me_dates = [int(g.iloc[-1]) for _, g in pd.Series(mdates, index=ym).groupby(level=0, sort=True)]
    con = sqlite3.connect(METADB, uri=True)
    sw = pd.read_sql_query("SELECT code,sector_name FROM stock_sector_sw1", con)
    con.close()
    indm = dict(zip(sw["code"].str[-6:], sw["sector_name"]))
    piv_close = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
    pit_con = sqlite3.connect(PITDB, uri=True)
    legs = {"bp_ep_pit_top20": [], "bp_ep_pit_top15ind": []}
    last_basket = None
    for T in me_dates:
        snap = get_pit_factor_v2(pit_con, int(T))
        if snap is None or snap.empty or T not in piv_close.index:
            continue
        row_c = piv_close.loc[T]
        snap = snap.assign(close=snap["code"].map(row_c))
        snap = snap[snap["close"] > 0]
        snap = snap[snap["eps"].notna() & snap["bps"].notna() & (snap["close"] <= 30.0)]
        snap = snap[~snap["code"].str.startswith(("688", "689"))]
        snap = snap[snap["code"].str[0].isin(["6", "0", "3"])]
        if len(snap) < 60:
            continue
        snap["ep"] = snap["eps"]
        snap["bp"] = snap["bps"] / snap["close"]
        snap["combo"] = (snap["ep"].rank(pct=True) + snap["bp"].rank(pct=True)) / 2
        snap = snap.sort_values("combo", ascending=False)
        legs["bp_ep_pit_top20"].extend((T, c_) for c_ in snap["code"].head(20))
        picks, used = [], {}
        for c_ in snap["code"]:
            if len(picks) >= 15:
                break
            sector = indm.get(c_, "UNKNOWN")
            if used.get(sector, 0) >= 4:
                continue
            used[sector] = used.get(sector, 0) + 1
            picks.append(c_)
        legs["bp_ep_pit_top15ind"].extend((T, c_) for c_ in picks)
        top15 = snap.head(15)
        last_basket = {"month_end": int(T),
                       "top15": [{"code": r, "name": name_map.get(r, "")}
                                 for r in top15["code"]]}
    pit_con.close()
    log(f"bp_ep 月末数={len(me_dates)}")
    key = df.set_index(["code", "d"])[["win_c1", "ret_c1", "win_c0", "ret_c0", "bull"]]
    res = {}
    for legname, pairs in legs.items():
        rows = []
        for T, c_ in pairs:
            try:
                rows.append({"d": T, **key.loc[(c_, T)].to_dict()})
            except KeyError:
                pass
        res[legname] = leg_stats(pd.DataFrame(rows))
    return res, last_basket


# ============================================================ 7. 汇总输出
results = {
    "meta": {
        "generated_at": "2026-10-25", "order": "所有选股方式，全量测试",
        "universe": "6/0/3, 剔688/689(交易口径), 上市>60日, 非ST, volume>0; 基准宇宙 close<=30",
        "exit": f"t20e: T+20收盘 or 先触-8%收盘止损 (HOLD={HOLD}, STOP={STOP})",
        "entry": "c0=信号日收盘(锚定口径) / c1=次交易日收盘(主口径)",
        "eval_window": [EVAL_START, EVAL_END], "qfq_max_date": int(df["d"].max()),
        "discipline": "描述性统一横评, 不设新判定闸; 判定性结论仅引用既有prereg/证伪档案",
        "zero_trade_authority": True,
    },
    "rule_legs": {}, "score_legs": {}, "bp_ep_legs": {},
}

rule_src = {
    "lowconsol_A": "低位横盘A格 pos250<30&|chg5|<3 (lowconsol_signal_daily, 存活候选: PREREG_LOW_CONSOLIDATION OOS_CONFIRMED)",
    "lowconsol_A_full19": "锚点腿: 同规则20190701+全窗(今日perstock为含688口径, 本腿剔688)",
    "lowconsol_A_mainline": "A格∩量比主线Top3 streak>=1",
    "lowconsol_A_ml_day1": "A格∩量比主线首日 (PREREG_LOW_FRESH_MAINLINE_20261021: 相位依赖NOT_CONFIRMED)",
    "v52_deepdrop": "v5.2六层全档 off∈[-3,17]% (backtest_v53_sensitivity复刻)",
    "v52_sweet": "v5.2最优介入档 off∈[5,10]%",
    "baya_pattern": "百亚前夕形态7条件 (tools_tb/baya_pattern_scan v1.1复刻, 10/3主人令验证源)",
    "trifecta_V1": "三重共振严格[证伪对照: trifecta_event_study_20261024 FALSIFIED]",
    "trifecta_V2": "三重共振宽松[证伪对照: 同上]",
    "pullback_A": "回调企稳全市场[证伪对照: mainline_pullback_event_study_20261024 A FALSIFIED]",
    "chase_ref": "追高全市场[证伪对照: 同上 B_ref FALSIFIED]",
    "pullback_ml": "回调∩动量主线[证伪对照: 同上 C FALSIFIED]",
    "chase_ml": "追高∩动量主线[证伪对照: 同上 B FALSIFIED]",
}

sub = df[df["uni"] & (df["d"] >= EVAL_START) & (df["d"] <= EVAL_END)]
for m in METHOD_RULES:
    st = leg_stats(sub[sub[m]])
    st["rule"] = rule_src[m]
    results["rule_legs"][m] = st
    log(f"腿 {m}: n={st.get('n', 0)}")

results["score_legs"]["ml_v22_top20"] = dict(
    score_leg("stock_ml_scores_v22", "ml_prob"),
    note="样本内口径(round11: 训练窗与打分窗重叠, 引用历史数字必须附样本内注脚)")
results["score_legs"]["ml_v25_top20"] = dict(
    score_leg("stock_ml_scores_v25_round12wf", "ml_prob"),
    note="round12wf维持观察腿(v25裁决20261024: 维持观察不接线; 2026真窗-0.02%)")
results["score_legs"]["v4_composite_top20"] = dict(
    score_leg("stock_signals_v4", "composite_score"),
    note="表MAX=20260528: 生产v4管线(master_hist)信号断供约5个月, 评估窗自然截断")
results["score_legs"]["v43_top20"] = dict(
    score_leg("stock_signals_v43", "v43_prob", eval_from=20260701),
    note="v43历史参考表(20260701起)短窗小样本, 主人8/23裁决保留为参考表")

bp_res, last_basket = bp_ep_legs()
results["bp_ep_legs"] = bp_res
if last_basket:
    results["bp_ep_latest_basket"] = last_basket

try:
    prev = json.load(open(r"D:\zcodesj\analysis\perstock_universe_reverify_20261025_result.json",
                          encoding="utf-8"))
    results["anchor_repro"] = {
        "reference": "perstock_universe_reverify_20261025(含688, LOAD 20190101, 逐股等权均值)",
        "my_leg": "lowconsol_A_full19(剔688, 20190701+, 事件池化口径)",
        "ref_dist_n20_mean_win": prev.get("dist_n20", {}).get("mean_win"),
        "note": "口径差=688剔除+池化vs逐股等权, 量级对照非逐位对账",
    }
except Exception as e:
    results["anchor_repro"] = {"error": str(e)}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1, default=str)
log(f"完成 → {OUT}")

print("\n" + "=" * 118)
print(f"{'腿':<24}{'n':>8}{'win_c1':>9}{'基准c1':>9}{'超额pp':>9}{'win_c0':>9}{'牛相':>8}{'熊相':>8}")
print("-" * 118)


def prow(name, st):
    if not st or st.get("n", 0) == 0:
        print(f"{name:<26}{0:>8}  (无事件/无行)")
        return
    print(f"{name:<26}{st['n']:>8}{st.get('win_c1', float('nan')):>9.4f}"
          f"{st.get('base_win_c1_matched', float('nan')):>9.4f}"
          f"{st.get('excess_win_pp', float('nan')):>+9.2f}"
          f"{st.get('win_c0', float('nan')):>9.4f}"
          f"{st.get('win_bull', float('nan')):>8.2f}{st.get('win_bear', float('nan')):>8.2f}")


for grp in ("rule_legs", "score_legs", "bp_ep_legs"):
    for k, v in results[grp].items():
        prow(k, v)
print("=" * 118)
print(f"基线: 全宇宙股日={int(base_by_d['bn'].sum())} c1={base_df['win_c1'].mean():.4f} "
      f"c0={base_df['win_c0'].mean():.4f} | 禁测/无法复放/已档不重跑清单见文件头注释与JSON meta")
