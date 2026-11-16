# -*- coding: utf-8 -*-
"""B7 两维回测(资金+分歧日) — PREREG_MAINLINE_LIFECYCLE_20261021 v1.2 冻结契约执行件。

契约: reports/PREREG_MAINLINE_LIFECYCLE_20261021.md (指纹 mlc-lifecycle-20261021-v1.2)
三段式: m1 供数(内建) -> m2 分析(本脚本) -> m7 校验(独立审阅)
边界: 研究件零交易权; 生产库零写入(全部 mode=ro + query_only); 产出仅 analysis/ 白名单;
      SQL 一律参数绑定; TDX 读取走 tdx_reader 唯一入口。

冻结参数(改参数=改预注册, 须主人显式指令):
  事件: 上证 20MA 乖离>7% 上涨段内首个单日跌幅>=1.5% 的交易日
  候选: 回撤日前 20 交易日涨幅前 3 且回撤日当日涨幅前 10 (L2=131 主宇宙)
        L1 敏感性臂: top=ceil(31*0.08)=3
  DV  : T+5 相对中证全指超额(T+10 仅敏感性, 不得救活主判定)
  三窗: 设计 2016-2021(仅调参) / 样本内 2022-2024(G1) / 样本外 2025-起(只跑一次)
  G1  : 样本内 n>=30 且 T+5 中位数 bootstrap95%CI 下界<=0 -> 整线终止; n<30 -> 仅描述统计不结论
  G2  : 拥挤扫描(仅设计窗) {80,90,95,99}%x{120d,3y}; 仅"≥95%档 mean<0 且 |t|>2"可入退潮预警参数集
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from tdx_reader import read_day  # noqa: E402

# ---- 冻结参数 ----
SEED = 20261021
N_BOOT = 1000
SAMPLE_START = 20160101
BIAS_TH = 0.07
DD_TH = -0.015
LOOKBACK = 20
TOP_CAND = 3
TOP_PERSIST = 10
TOP_PCT_L1 = 0.08
T_PLUSES = (5, 10)
WIN_DESIGN = (20160101, 20211231)
WIN_IN = (20220101, 20241231)
WIN_SEED_OFFSET = {"design": 11, "in_sample": 22, "oos": 33}  # 确定性种子偏移(禁盐化hash)
TIER_GRID = (0.80, 0.90, 0.95, 0.99)
PCT_WINDOWS = {"w120": 120, "w3y": 756}
SWEEP_FWD = 20
COV_MIN = 0.99
NULL_MAX = 0.001
OUTLIER_RET = 0.20
WIN_SUFF = 0.90
N_MIN = 30

SW_DB = r"D:\workbuddyspace\databases\sw_industry.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
IDX_FILE = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
BENCH_FILE = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000985.day"
OUT_DIR = r"D:\zcodesj\analysis"
TAG = "mainline_backtest_20261021"

P = {"BIAS_TH": BIAS_TH, "DD_TH": DD_TH, "LOOKBACK": LOOKBACK,
     "TOP_CAND_L2": TOP_CAND, "TOP_PERSIST_L2": TOP_PERSIST, "TOP_PCT_L1": TOP_PCT_L1,
     "T_PLUSES": list(T_PLUSES), "WIN_DESIGN": WIN_DESIGN, "WIN_IN": WIN_IN,
     "WIN_OOS_START": WIN_OOS_START if (WIN_OOS_START := 20250101) else 20250101,
     "TIER_GRID": list(TIER_GRID), "PCT_WINDOWS": PCT_WINDOWS, "SWEEP_FWD": SWEEP_FWD,
     "N_BOOT": N_BOOT, "SEED": SEED, "SAMPLE_START": SAMPLE_START, "N_MIN": N_MIN}


def sha16(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def load_sw(level: str) -> pd.DataFrame:
    con = ro(SW_DB)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code,name,trade_date,close,amount FROM industry_kline_sw "
            "WHERE level=? AND trade_date>=? ORDER BY ts_code,trade_date",
            con, params=(level, "2012-06-01"))
    finally:
        con.close()
    df["d"] = df["trade_date"].str.replace("-", "", regex=False).astype(int)
    return df[["ts_code", "name", "d", "close", "amount"]]


def to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["d", "o", "h", "l", "c", "amount", "vol"])
    return df[["d", "c"]].drop_duplicates("d").sort_values("d").reset_index(drop=True)


def load_bench(dl: list):
    """基准加载: 首选中证全指(vipdoc), 覆盖不足则按契约 v1.0 预授权降级=等权全A(qfq派生)。"""
    bench = to_df(read_day("sh", "000985"))
    sample_dates = [d for d in dl if d >= SAMPLE_START]
    cov985 = (sum(1 for d in sample_dates if d in set(bench["d"])) / max(len(sample_dates), 1)
              if len(bench) else 0.0)
    if cov985 >= COV_MIN:
        return bench, f"中证全指(vipdoc sh000985)", False, round(cov985, 4)
    con = ro(QFQ_DB)
    try:
        rows = con.execute(
            "SELECT date, AVG(ret) FROM ("
            " SELECT date, close*1.0/LAG(close) OVER (PARTITION BY code ORDER BY date)-1 AS ret"
            " FROM stock_daily_meta_qfq WHERE date>=? AND close>0"
            ") WHERE ret IS NOT NULL GROUP BY date", ("20140601",)).fetchall()
    finally:
        con.close()
    d = pd.DataFrame(rows, columns=["d", "ret"])
    d["d"] = d["d"].astype(int)
    d = d.sort_values("d").reset_index(drop=True)
    d["c"] = (1.0 + d["ret"]).cumprod() * 1000.0
    return (d[["d", "c"]],
            "等权全A(qfq派生cumprod; 降级原因: 本地sh000985仅覆盖2005-2016已停更, "
            "契约v1.0预授权降级路径)", True, round(cov985, 4))


def boot_ci_median(x: np.ndarray, seed: int):
    n = len(x)
    if n == 0:
        return None, None, None, None
    rng = random.Random(seed)
    meds = np.sort(np.array([np.median([x[rng.randrange(n)] for _ in range(n)])
                             for _ in range(N_BOOT)]))
    lo, hi = float(meds[int(0.025 * N_BOOT)]), float(meds[int(0.975 * N_BOOT)])
    p = max(2 * min(float(np.mean(meds <= 0)), float(np.mean(meds >= 0))), 1.0 / N_BOOT)
    return lo, hi, p, float(np.median(x))


def detect_events(idx: pd.DataFrame) -> list:
    c = idx["c"].values
    ret = np.append(np.nan, c[1:] / c[:-1] - 1)
    ma20 = pd.Series(c).rolling(20).mean().values
    with np.errstate(invalid="ignore"):
        bias = (c - ma20) / ma20
    events, in_seg, fired = [], False, False
    for i in range(len(idx)):
        d = int(idx["d"].iloc[i])
        if d < SAMPLE_START:
            continue
        b = bias[i]
        if not math.isnan(b) and b > BIAS_TH:
            if not in_seg:
                in_seg, fired = True, False
            if (not fired) and (not math.isnan(ret[i])) and ret[i] <= DD_TH:
                events.append({"d": d, "pos": i, "dd_ret": float(ret[i]), "bias": float(b)})
                fired = True
        else:
            in_seg = False
    return events


def run_universe(level: str, top_cand: int, top_persist: int,
                 idx: pd.DataFrame, dl: list, dpos: dict,
                 bclose: dict, degradation: dict):
    sw = load_sw(level)
    close_w = sw.pivot_table(index="d", columns="ts_code", values="close").reindex(dl)
    amt_w = sw.pivot_table(index="d", columns="ts_code", values="amount").reindex(dl)
    names = sw.drop_duplicates("ts_code").set_index("ts_code")["name"].to_dict()

    ret1 = close_w / close_w.shift(1) - 1
    degradation[f"{level}_outlier_cells_absret_gt20pct"] = int((ret1.abs() > OUTLIER_RET).sum().sum())

    events = detect_events(idx)
    rows, diag = [], []
    dropped = {"seg_miss_dl": 0, "no_lookback": 0, "no_fwd": 0, "outlier": 0, "bench_miss": 0}
    for ev in events:
        d = ev["d"]
        if d not in dpos:
            dropped["seg_miss_dl"] += 1
            continue
        pos = dpos[d]
        if pos < LOOKBACK + 1 or pos + max(T_PLUSES) >= len(dl):
            dropped["no_lookback"] += 1
            continue
        if d not in bclose:
            dropped["bench_miss"] += 1
            continue
        prev = close_w.iloc[pos - 1]
        base = close_w.iloc[pos - LOOKBACK - 1]
        ret20 = prev / base - 1
        day_ret = close_w.iloc[pos] / prev - 1
        if len(ret20.dropna()) < top_cand:
            dropped["no_lookback"] += 1
            continue
        cands = set(ret20.dropna().nlargest(top_cand).index) & \
            set(day_ret.dropna().nlargest(top_persist).index)
        diag.append({"event_d": d, "dd_ret_idx": round(ev["dd_ret"], 6),
                     "bias_at_ev": round(ev["bias"], 4),
                     "top_ret20": "|".join(ret20.dropna().nlargest(top_cand).index),
                     "top_dayret": "|".join(day_ret.dropna().nlargest(top_persist).index),
                     "n_cand": len(cands)})
        for code in sorted(cands):
            r_day = day_ret.get(code, np.nan)
            if pd.notna(r_day) and abs(r_day) > OUTLIER_RET:
                dropped["outlier"] += 1
                continue
            rec = {"event_d": d, "code": code, "name": names.get(code, ""),
                   "ret20": float(ret20[code]), "dd_day_ret": float(r_day),
                   "dd_ret_idx": ev["dd_ret"], "bias_at_ev": ev["bias"]}
            ok = True
            for tp in T_PLUSES:
                d5 = dl[pos + tp]
                if d5 not in bclose:
                    dropped["bench_miss"] += 1
                    ok = False
                    break
                c0, c5 = close_w.iloc[pos][code], close_w.iloc[pos + tp][code]
                if pd.isna(c0) or pd.isna(c5):
                    dropped["no_fwd"] += 1
                    ok = False
                    break
                rec[f"t{tp}_excess"] = float(c5 / c0 - 1 - (bclose[d5] / bclose[d] - 1))
            if ok:
                rows.append(rec)
    ev_df = pd.DataFrame(rows)

    share = amt_w.div(amt_w.sum(axis=1).replace(0, np.nan), axis=0)
    share_ma = share.rolling(20).mean()
    pct = {w: share_ma.rolling(wlen, min_periods=int(wlen * WIN_SUFF)).rank(
        method="max", pct=True) for w, wlen in PCT_WINDOWS.items()}
    if len(ev_df):
        for w in PCT_WINDOWS:
            pw = pct[w]
            ev_df[f"tier_{w}"] = [pw.at[r.event_d, r.code]
                                  if (r.event_d in pw.index and r.code in pw.columns) else np.nan
                                  for r in ev_df.itertuples()]
    degradation[f"{level}_dropped"] = {k: int(v) for k, v in dropped.items()}
    return ev_df, pct, len(set(sw["ts_code"])), pd.DataFrame(diag)


def tier_label(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "NA"
    if v < 0.80:
        return "<80"
    if v < 0.95:
        return "80-95"
    return ">=95"


def main() -> int:
    t0 = time.time()
    rng = random.Random(SEED)
    degradation = {}

    idx = to_df(read_day("sh", "000001"))
    if idx.empty:
        print("[FAIL] 上证指数 vipdoc 读取为空")
        return 2
    sw_probe = load_sw("L2")
    dl = sorted(sw_probe["d"].unique().tolist())
    dpos = {d: i for i, d in enumerate(dl)}
    bench, bench_desc, bench_degraded, cov985 = load_bench(dl)
    if bench.empty:
        print("[FAIL] 基准(含降级路径)读取为空")
        return 2

    # ---- m1 preflight (v1.2 六项门禁) ----
    con = ro(SW_DB)
    try:
        dup = con.execute(
            "SELECT COUNT(*) FROM (SELECT ts_code,trade_date,COUNT(*) c FROM industry_kline_sw "
            "WHERE trade_date>=? GROUP BY ts_code,trade_date HAVING c>1)",
            ("2016-01-01",)).fetchone()[0]
        nn, tt = con.execute(
            "SELECT SUM(CASE WHEN close IS NULL OR close<=0 OR amount IS NULL THEN 1 ELSE 0 END),"
            "COUNT(*) FROM industry_kline_sw WHERE trade_date>=? AND level='L2'",
            ("2016-01-01",)).fetchone()
    finally:
        con.close()
    null_rate = (nn or 0) / max(tt, 1)
    idx_in = [d for d in idx["d"] if SAMPLE_START <= d <= dl[-1]]
    cov = sum(1 for d in idx_in if d in dpos) / max(len(idx_in), 1)
    mono = bool(idx["d"].is_monotonic_increasing and idx["d"].is_unique
                and bench["d"].is_monotonic_increasing and bench["d"].is_unique)
    con = ro(QFQ_DB)
    try:
        q = pd.read_sql_query(
            "SELECT date, SUM(amount) s FROM stock_daily_meta_qfq WHERE date>=? GROUP BY date",
            con, params=("20160101",))
        nrows_qfq = con.execute("SELECT COUNT(*) FROM stock_daily_meta_qfq").fetchone()[0]
    finally:
        con.close()
    q["d"] = q["date"].astype(int)
    l1_sum = load_sw("L1").groupby("d")["amount"].sum()
    j = pd.concat([q.set_index("d")["s"], l1_sum], axis=1, keys=["qfq", "l1"]).dropna()
    j = j[j.index <= dl[-1]]
    corr = float(j["qfq"].corr(j["l1"])) if len(j) > 30 else float("nan")
    ratio = (j["l1"] / j["qfq"])
    ratio_cv = float(ratio.std() / ratio.mean()) if len(j) else float("nan")
    denom = "qfq" if (corr == corr and corr >= 0.9 and ratio_cv < 0.05) else "L1_sum"
    if denom != "qfq":
        notes = [f"分母降级为ΣL1(qfq一致性 corr={corr:.3f}/cv={ratio_cv:.3f} 未达门槛), 已按契约披露"]

    checks = {
        "date_coverage": {"pass": cov >= COV_MIN, "value": round(cov, 4)},
        "duplicates": {"pass": dup == 0, "count": int(dup)},
        "null_rate": {"pass": null_rate < NULL_MAX, "rate": round(null_rate, 6)},
        "monotonic_unique": {"pass": mono},
        "bench_coverage": {"pass": True, "chosen": bench_desc, "degraded": bench_degraded,
                           "sh000985_sample_cov": cov985},
        "denominator_consistency": {"pass": denom == "qfq",
                                    "corr_qfq_vs_L1sum": round(corr, 4),
                                    "ratio_cv": round(ratio_cv, 4), "chosen": denom},
        "window_sufficiency": {"pass": True,
                               "note": "min_periods=窗长x0.9内建; 降级行以NaN排除并计数"},
    }
    overall = "PASS" if all(c["pass"] for c in checks.values()) else "FAIL"
    with open(os.path.join(OUT_DIR, f"preflight_{TAG}.json"), "w", encoding="utf-8") as f:
        json.dump({"task": "B7", "run_at": datetime.now().isoformat(timespec="seconds"),
                   "inputs": {"sw_db": SW_DB, "qfq_db": QFQ_DB,
                              "idx": IDX_FILE, "bench": bench_desc},
                   "checks": checks, "outliers_policy": "flag+exclude+count",
                   "overall": overall}, f, ensure_ascii=False, indent=1)
    print(f"[PREFLIGHT] overall={overall} cov={cov:.4f} dup={dup} null={null_rate:.6f} "
          f"denom={denom}(corr={corr:.4f},cv={ratio_cv:.4f})")
    if overall == "FAIL":
        print("[FAIL] preflight 未过 -> 零产出退出(仅留 preflight json 证据)")
        return 2

    bclose = dict(zip(bench["d"].astype(int), bench["c"]))

    # ---- 主宇宙 L2 ----
    ev_l2, _, n_uni, diag_l2 = run_universe("L2", TOP_CAND, TOP_PERSIST,
                                            idx, dl, dpos, bclose, degradation)

    def window_of(d: int) -> str:
        if WIN_DESIGN[0] <= d <= WIN_DESIGN[1]:
            return "design"
        if WIN_IN[0] <= d <= WIN_IN[1]:
            return "in_sample"
        return "oos"

    if not ev_l2.empty:
        ev_l2["window"] = ev_l2["event_d"].map(window_of)
        ev_l2["tier"] = ev_l2["tier_w3y"].map(tier_label)

    # ---- G1 (样本内主判定, 契约冻结) ----
    ins = ev_l2[ev_l2["window"] == "in_sample"] if not ev_l2.empty else ev_l2
    x = ins["t5_excess"].dropna().values if not ins.empty else np.array([])
    if len(x) < N_MIN:
        g1 = f"INCONCLUSIVE(n={len(x)}<{N_MIN}, 按契约仅描述统计不结论, 不合并窗口凑样本)"
    else:
        lo, hi, p, med = boot_ci_median(x, SEED + WIN_SEED_OFFSET["in_sample"])
        g1 = ("TERMINATE(整线终止: 样本内T+5中位数CI下界<=0; T+10不得救活)"
              if lo <= 0 else
              f"PASS(样本内 T+5 中位={med:.4%}, CI=[{lo:.4%},{hi:.4%}], p={p:.3f})")

    # ---- 三窗统计 ----
    stat_rows = []
    for wname in ("design", "in_sample", "oos"):
        sub = ev_l2[ev_l2["window"] == wname] if not ev_l2.empty else ev_l2
        xx = sub["t5_excess"].dropna().values if not sub.empty else np.array([])
        if len(xx) == 0:
            stat_rows.append({"window": wname, "n": 0, "median": None, "mean": None,
                              "ci_lo": None, "ci_hi": None, "p_boot": None, "t10_median": None})
            continue
        lo, hi, p, med = boot_ci_median(xx, SEED + WIN_SEED_OFFSET[wname])
        t10 = (float(sub["t10_excess"].median())
               if "t10_excess" in sub and sub["t10_excess"].notna().any() else None)
        stat_rows.append({"window": wname, "n": len(xx), "median": med,
                          "mean": float(np.mean(xx)),
                          "ci_lo": lo, "ci_hi": hi, "p_boot": p, "t10_median": t10})

    # ---- 拥挤度阈值扫描(仅设计窗, G2) ----
    sw_full = load_sw("L2")
    close_w = sw_full.pivot_table(index="d", columns="ts_code", values="close").reindex(dl)
    share = (sw_full.pivot_table(index="d", columns="ts_code", values="amount").reindex(dl)
             .div(sw_full.pivot_table(index="d", columns="ts_code", values="amount")
                  .reindex(dl).sum(axis=1).replace(0, np.nan), axis=0))
    share_ma = share.rolling(20).mean()
    pct = {w: share_ma.rolling(wlen, min_periods=int(wlen * WIN_SUFF)).rank(
        method="max", pct=True) for w, wlen in PCT_WINDOWS.items()}
    fwd_ret = close_w.shift(-SWEEP_FWD) / close_w - 1
    bench_f = bench.set_index("d")["c"]
    bench_fwd = bench_f.shift(-SWEEP_FWD) / bench_f - 1
    sweep_rows = []
    for wname, pw in pct.items():
        for tier in TIER_GRID:
            cross = (pw.shift(1) < tier) & (pw >= tier)
            cross = cross.loc[(cross.index >= WIN_DESIGN[0])
                              & (cross.index <= WIN_DESIGN[1])]
            xs = []
            for i, jc in zip(*np.where(cross.values)):
                d5i = i + SWEEP_FWD
                if d5i >= len(dl):
                    continue
                bf = bench_fwd.get(dl[i])
                if bf is None or pd.isna(bf) or pd.isna(fwd_ret.iloc[i, jc]):
                    continue
                xs.append(float(fwd_ret.iloc[i, jc] - bf))
            if xs:
                xs = np.array(xs)
                tstat = (float(np.mean(xs) / (np.std(xs, ddof=1) / math.sqrt(len(xs))))
                         if len(xs) > 2 else float("nan"))
                sweep_rows.append({"pct_window": wname, "tier": tier, "n": len(xs),
                                   "mean_fwd20_excess": round(float(np.mean(xs)), 6),
                                   "winrate": round(float(np.mean(xs > 0)), 4),
                                   "t": round(tstat, 3)})
    g2_ok = [r for r in sweep_rows
             if r["tier"] >= 0.95 and r["mean_fwd20_excess"] < 0 and abs(r["t"]) > 2]
    g2 = (f"QUALIFIED(达到退潮预警参数条件的档: {g2_ok})" if g2_ok
          else "NONE(全网格无档满足 -> 按契约拥挤度降为纯呈现)")

    # ---- 随机基准(样本内+样本外) ----
    sub = ev_l2[ev_l2["window"].isin(("in_sample", "oos"))] if not ev_l2.empty else ev_l2
    rnd_rows = []
    if not sub.empty:
        for d, grp in sub.groupby("event_d"):
            pos = dpos[d]
            p5 = pos + 5
            if p5 >= len(dl) or dl[p5] not in bclose:
                continue
            others = [c for c in close_w.columns if c not in set(grp["code"])]
            for code in rng.sample(others, min(len(grp), len(others))):
                c0, c5 = close_w.iloc[pos][code], close_w.iloc[p5][code]
                if pd.isna(c0) or pd.isna(c5):
                    continue
                rnd_rows.append({"event_d": d, "code": code,
                                 "t5_excess": float(c5 / c0 - 1 - (bclose[dl[p5]] / bclose[d] - 1))})
    rnd = pd.DataFrame(rnd_rows)

    # ---- L1 敏感性臂 ----
    n_l1_top = math.ceil(31 * TOP_PCT_L1)
    ev_l1, _, n_l1, diag_l1 = run_universe("L1", n_l1_top, n_l1_top,
                                           idx, dl, dpos, bclose, degradation)
    if not ev_l1.empty:
        ev_l1["window"] = ev_l1["event_d"].map(window_of)
    l1x = ev_l1[ev_l1["window"] == "in_sample"] if not ev_l1.empty else ev_l1

    # ---- 产出(仅 analysis/ 白名单) ----
    def outp(name: str) -> str:
        p = os.path.join(OUT_DIR, name)
        assert os.path.normpath(p).startswith(os.path.normpath(OUT_DIR)), "越权写路径"
        return p

    ev_csv = outp(f"{TAG}_events.csv")
    ev_l2.to_csv(ev_csv, index=False, encoding="utf-8-sig")
    sw_csv = outp(f"{TAG}_sweep.csv")
    pd.DataFrame(sweep_rows).to_csv(sw_csv, index=False, encoding="utf-8-sig")
    rn_csv = outp(f"{TAG}_randombench.csv")
    rnd.to_csv(rn_csv, index=False, encoding="utf-8-sig")
    dg_csv = outp(f"{TAG}_eventdiag.csv")
    pd.concat([diag_l2.assign(universe="L2"), diag_l1.assign(universe="L1")],
              ignore_index=True).to_csv(dg_csv, index=False, encoding="utf-8-sig")

    def pct_s(v):
        return "-" if v is None else f"{v:.4%}"

    lines = [f"# B7 两维回测报告({TAG}) {datetime.now():%Y-%m-%d %H:%M}", ""]
    lines.append(f"- 契约: PREREG_MAINLINE_LIFECYCLE_20261021 v1.2 | seed={SEED} "
                 f"| 主宇宙=申万二级L2({n_uni}) | 基准={bench_desc}")
    if bench_degraded:
        lines.append(f"- ⚠️ **基准已按契约 v1.0 预授权路径降级**: 本地 sh000985 样本期覆盖率仅 "
                     f"{cov985:.0%}(停更于2016-06), 切换等权全A(qfq派生)")
    lines.append(f"- preflight: **{overall}** | 分母={denom}(qfq vs ΣL1: corr={corr:.4f}, "
                 f"ratio_cv={ratio_cv:.4f}) | L1敏感性臂宇宙={n_l1}(top={n_l1_top})")
    lines.append(f"- 事件检测: 上证样本内中阴线事件 {len(detect_events(idx))} 个 "
                 f"| L2 候选-事件对 n={len(ev_l2)}")
    lines.append(f"- 丢弃/极端值计数: {json.dumps(degradation, ensure_ascii=False)}")
    lines.append("- 事件诊断(逐事件, 明细见 eventdiag CSV):")
    for _, r in diag_l2.iterrows():
        lines.append(f"  - {r['event_d']}: 20日涨幅前三=[{r['top_ret20']}] "
                     f"回撤日当日涨幅前十=[{r['top_dayret']}] 交集={r['n_cand']} "
                     f"(上证当日 {r['dd_ret_idx']:.2%}, 乖离 {r['bias_at_ev']:.1%})")
    lines.append("", )
    lines.append("## 一、三窗统计(L2, T+5 相对中证全指超额)")
    lines.append("| 窗口 | n | 中位数 | 均值 | CI95 | p_boot | T+10中位 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in stat_rows:
        if r["n"] == 0:
            lines.append(f"| {r['window']} | 0 | - | - | - | - | - |")
        else:
            lines.append(f"| {r['window']} | {r['n']} | {pct_s(r['median'])} "
                         f"| {pct_s(r['mean'])} | [{pct_s(r['ci_lo'])}, {pct_s(r['ci_hi'])}] "
                         f"| {r['p_boot']:.4f} | {pct_s(r['t10_median'])} |")
    lines.append("")
    lines.append(f"## 二、G1 主判定(样本内 2022-2024)  \n**{g1}**")
    lines.append("")
    lines.append("## 三、拥挤度阈值扫描(仅设计窗 2016-2021, G2)")
    lines.append("| 分位窗 | 档 | n | 后20日超额均值 | 胜率 | t |")
    lines.append("|---|---|---|---|---|---|")
    for r in sweep_rows:
        lines.append(f"| {r['pct_window']} | {r['tier']:.2f} | {r['n']} "
                     f"| {r['mean_fwd20_excess']:.4%} | {r['winrate']:.1%} | {r['t']} |")
    lines.append("")
    lines.append(f"**G2 判定: {g2}**")
    lines.append("")
    if not sub.empty:
        lines.append("## 四、拥挤度分层(样本内+样本外候选, w3y 口径)")
        lines.append("| tier | n | T+5中位 |")
        lines.append("|---|---|---|")
        for tname, g in sub.groupby("tier"):
            lines.append(f"| {tname} | {len(g)} | {g['t5_excess'].median():.4%} |")
        lines.append("")
        if not rnd.empty:
            lines.append(f"## 五、随机基准对照  \n候选 T+5 中位 {sub['t5_excess'].median():.4%} "
                         f"vs 同事件日随机等量行业中位 {rnd['t5_excess'].median():.4%} "
                         f"(随机n={len(rnd)})")
            lines.append("")
    l1s = (f"样本内 T+5 中位 {l1x['t5_excess'].median():.4%} (n={len(l1x)})"
           if (not l1x.empty and l1x["t5_excess"].notna().any()) else "样本内无足样本")
    lines.append(f"## 六、L1 敏感性臂  \nL1 候选-事件对 n={len(ev_l1)}; {l1s}")
    lines.append("")
    lines.append("## 七、局限披露(契约强制)")
    lines.append("1. 天风事件稀疏: 各窗 n 可能触 n<30 条款(仅描述统计不结论), 未合并窗口凑样本;")
    lines.append("2. SW 指数为 2021 版分类的历史回溯序列, 行业构成非 PIT;")
    lines.append("3. 「回撤日当日仍前十」实现为回撤日当日行业涨幅排名前十(天风原文的机械解读, 契约冻结);")
    lines.append("4. qfq 分母与 SW 成分覆盖存在口径差(以 corr/ratio_cv 披露);")
    lines.append("5. L2=131 与天风 124 存在数量差, 主宇宙保持天风原始 3/10 名次未换算。")
    lines.append("")
    lines.append("## 八、复现命令")
    lines.append("```bash")
    lines.append("python analysis/mainline_backtest_20261021.py  # 全参数冻结于脚本头, seed=20261021")
    lines.append("```")
    lines.append(f"耗时 {time.time()-t0:.1f}s | 事件CSV底稿: {os.path.basename(ev_csv)}")
    rep = "\n".join(lines)
    if overall != "PASS":
        rep = ("⚠️ **PASS_WITH_DEGRADATION: preflight 存在非通过项, 结论须带降级标注解读**\n\n"
               + ("\n".join(notes) + "\n\n" if notes else "") + rep)
    rep_path = outp(f"{TAG}.md")
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write(rep)

    man = {"task": "B7", "params": P,
           "input_snapshots": {
               "sw_db": {"path": SW_DB,
                          "mtime": datetime.fromtimestamp(os.path.getmtime(SW_DB)).isoformat(timespec="seconds"),
                          "sha16": sha16(SW_DB)},
               "qfq_db": {"path": QFQ_DB,
                           "mtime": datetime.fromtimestamp(os.path.getmtime(QFQ_DB)).isoformat(timespec="seconds"),
                           "rows": int(nrows_qfq), "sha16": sha16(QFQ_DB)},
               "idx_day": {"path": IDX_FILE, "sha16": sha16(IDX_FILE)},
               "bench_day": {"path": BENCH_FILE, "sha16": sha16(BENCH_FILE),
                              "chosen": bench_desc}},
           "code": {"file": os.path.abspath(__file__), "sha16": sha16(os.path.abspath(__file__))},
           "random_seed": SEED,
           "runtime_s": round(time.time() - t0, 1),
           "outputs": {os.path.basename(p): {"sha16": sha16(p)}
                       for p in (ev_csv, sw_csv, rn_csv, dg_csv, rep_path)}}
    with open(outp(f"manifest_{TAG}.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)

    print(f"[B7] L2候选-事件对={len(ev_l2)} | G1={g1}")
    print(f"[B7] G2={g2[:140]}")
    print(f"[OUT] {rep_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
