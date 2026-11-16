# -*- coding: utf-8 -*-
"""B7b 事件口径功效测量臂 — PREREG_MAINLINE_EVENTFEAS_20261021 v1 冻结契约执行件。

契约: reports/PREREG_MAINLINE_EVENTFEAS_20261021.md (指纹 mlc-eventfeas-20261021-v1)
定位: B7 主检验臂 INCONCLUSIVE(功效不足) 的响应臂 — BIAS 网格{4,5,6,7}% x P 网格{10,15,20}
      全网格报告(防挑阈值); 主口径=5%/10; 非盲已披露(2020/2024 两事件 B7 已见)。
边界: 只读(生产库零写入, mode=ro+query_only+参数绑定); 产出仅 analysis/; TDX 走 tdx_reader。
"""
from __future__ import annotations

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
import mainline_backtest_20261021 as b7  # 复用只读工厂/哈希/加载器/TDX 接线(主入口有 guard, 导入安全)

SEED = b7.SEED
N_BOOT = b7.N_BOOT
SAMPLE_START = 20100101
DD_TH = -0.015
LOOKBACK = 20
TOP_CAND = 3
BIAS_GRID = (0.04, 0.05, 0.06, 0.07)
P_GRID = (10, 15, 20)
P_PRIMARY = 10
BIAS_PRIMARY = 0.05
WIN_INS_END = 20241231
N_MIN = 30

SW_DB = b7.SW_DB
QFQ_DB = b7.QFQ_DB
IDX_FILE = b7.IDX_FILE
OUT_DIR = b7.OUT_DIR
TAG = "mainline_eventfeas_20261021"

P = {"BIAS_GRID": list(BIAS_GRID), "P_GRID": list(P_GRID), "BIAS_PRIMARY": BIAS_PRIMARY,
     "P_PRIMARY": P_PRIMARY, "DD_TH": DD_TH, "LOOKBACK": LOOKBACK, "TOP_CAND": TOP_CAND,
     "SAMPLE_START": SAMPLE_START, "WIN_INS_END": WIN_INS_END, "N_MIN": N_MIN,
     "N_BOOT": N_BOOT, "SEED": SEED}


def load_sw2(level: str, since: str) -> pd.DataFrame:
    con = b7.ro(SW_DB)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code,name,trade_date,close,amount FROM industry_kline_sw "
            "WHERE level=? AND trade_date>=? ORDER BY ts_code,trade_date",
            con, params=(level, since))
    finally:
        con.close()
    df["d"] = df["trade_date"].str.replace("-", "", regex=False).astype(int)
    return df[["ts_code", "name", "d", "close", "amount"]]


def detect_events_gen(idx: pd.DataFrame, bias_th: float, start: int) -> list:
    c = idx["c"].values
    ret = np.append(np.nan, c[1:] / c[:-1] - 1)
    ma20 = pd.Series(c).rolling(20).mean().values
    with np.errstate(invalid="ignore"):
        bias = (c - ma20) / ma20
    out, in_seg, fired = [], False, False
    for i in range(len(idx)):
        d = int(idx["d"].iloc[i])
        if d < start:
            continue
        b = bias[i]
        if not math.isnan(b) and b > bias_th:
            if not in_seg:
                in_seg, fired = True, False
            if (not fired) and (not math.isnan(ret[i])) and ret[i] <= DD_TH:
                out.append({"d": d, "pos": i, "dd_ret": float(ret[i]), "bias": float(b)})
                fired = True
        else:
            in_seg = False
    return out


def main() -> int:
    t0 = time.time()
    rng = random.Random(SEED)

    idx = b7.to_df(b7.read_day("sh", "000001"))
    if idx.empty:
        print("[FAIL] 上证 vipdoc 读取为空")
        return 2
    sw = load_sw2("L2", "2009-01-01")
    dl = sorted(sw["d"].unique().tolist())
    dpos = {d: i for i, d in enumerate(dl)}
    close_w = sw.pivot_table(index="d", columns="ts_code", values="close").reindex(dl)
    names = sw.drop_duplicates("ts_code").set_index("ts_code")["name"].to_dict()

    # 等权全A 敏感性基准(qfq 派生, ≈2016 起覆盖)
    con = b7.ro(QFQ_DB)
    try:
        rows = con.execute(
            "SELECT date, AVG(ret) FROM ("
            " SELECT date, close*1.0/LAG(close) OVER (PARTITION BY code ORDER BY date)-1 AS ret"
            " FROM stock_daily_meta_qfq WHERE date>=? AND close>0"
            ") WHERE ret IS NOT NULL GROUP BY date", ("20140601",)).fetchall()
        nrows_qfq = con.execute("SELECT COUNT(*) FROM stock_daily_meta_qfq").fetchone()[0]
    finally:
        con.close()
    ew = pd.DataFrame(rows, columns=["d", "ret"])
    ew["d"] = ew["d"].astype(int)
    ew = ew.sort_values("d").reset_index(drop=True)
    ew["lvl"] = (1.0 + ew["ret"]).cumprod() * 1000.0
    ew_lvl = dict(zip(ew["d"], ew["lvl"]))

    # ---- preflight ----
    con = b7.ro(SW_DB)
    try:
        dup = con.execute(
            "SELECT COUNT(*) FROM (SELECT ts_code,trade_date,COUNT(*) c FROM industry_kline_sw "
            "WHERE trade_date>=? GROUP BY ts_code,trade_date HAVING c>1)",
            ("2010-01-01",)).fetchone()[0]
        nn, tt = con.execute(
            "SELECT SUM(CASE WHEN close IS NULL OR close<=0 THEN 1 ELSE 0 END),COUNT(*) "
            "FROM industry_kline_sw WHERE trade_date>=? AND level='L2'",
            ("2010-01-01",)).fetchone()
    finally:
        con.close()
    null_rate = (nn or 0) / max(tt, 1)
    idx_in = [d for d in idx["d"] if SAMPLE_START <= d <= dl[-1]]
    cov = sum(1 for d in idx_in if d in dpos) / max(len(idx_in), 1)
    mono = bool(idx["d"].is_monotonic_increasing and idx["d"].is_unique)
    early = {y: int(sw[(sw["d"] >= y * 10000) & (sw["d"] < (y + 1) * 10000)]["ts_code"].nunique())
             for y in range(2010, 2016)}
    checks = {"date_coverage": {"pass": cov >= 0.99, "value": round(cov, 4)},
              "duplicates": {"pass": dup == 0, "count": int(dup)},
              "null_rate": {"pass": null_rate < 0.001, "rate": round(null_rate, 6)},
              "monotonic_unique": {"pass": mono},
              "bench_note": {"pass": True,
                             "sh": "上证指数(vipdoc, 全史, 贴天风原文口径)",
                             "ew": f"等权全A(qfq派生, 行数={len(ew)}, ≈2016起, 敏感性)"},
              "early_years_industries": {"pass": True, "counts": early}}
    overall = "PASS" if all(c["pass"] for c in checks.values()) else "FAIL"
    with open(os.path.join(OUT_DIR, f"preflight_{TAG}.json"), "w", encoding="utf-8") as f:
        json.dump({"task": "B7b", "run_at": datetime.now().isoformat(timespec="seconds"),
                   "checks": checks, "overall": overall}, f, ensure_ascii=False, indent=1)
    print(f"[PREFLIGHT] overall={overall} cov={cov:.4f} dup={dup} null={null_rate:.6f} "
          f"early={early}")
    if overall == "FAIL":
        print("[FAIL] preflight 未过 -> 零产出退出")
        return 2

    sh_close = dict(zip(idx["d"].astype(int), idx["c"]))

    # ---- 功效网格: BIAS x P ----
    grid_rows, ev_cache = [], {}
    for bias in BIAS_GRID:
        evs = detect_events_gen(idx, bias, SAMPLE_START)
        ev_cache[bias] = evs
        rank_sets = []
        for ev in evs:
            d = ev["d"]
            if d not in dpos:
                continue
            pos = dpos[d]
            if pos < LOOKBACK + 1:
                continue
            prev = close_w.iloc[pos - 1]
            ret20 = prev / close_w.iloc[pos - LOOKBACK - 1] - 1
            day_ret = close_w.iloc[pos] / prev - 1
            rank_sets.append((d, pos, set(ret20.dropna().nlargest(TOP_CAND).index), day_ret))
        for Pg in P_GRID:
            pairs = [(d, pos, c) for (d, pos, t3, dr) in rank_sets
                     for c in sorted(t3 & set(dr.dropna().nlargest(Pg).index))]
            n2016 = sum(1 for (d, _, _) in pairs if d >= 20160101)
            grid_rows.append({"bias": bias, "P": Pg, "n_events": len(rank_sets),
                              "n_pairs_2010": len(pairs), "n_pairs_2016": n2016})
    grid = pd.DataFrame(grid_rows)

    # ---- 主口径 (5%,10) 事件底稿 + T+5 双基准 ----
    main_rows = []
    ev_main = detect_events_gen(idx, BIAS_PRIMARY, SAMPLE_START)
    for ev in ev_main:
        d = ev["d"]
        if d not in dpos or d not in sh_close:
            continue
        pos = dpos[d]
        if pos < LOOKBACK + 1 or pos + 5 >= len(dl):
            continue
        prev = close_w.iloc[pos - 1]
        ret20 = prev / close_w.iloc[pos - LOOKBACK - 1] - 1
        day_ret = close_w.iloc[pos] / prev - 1
        t3 = set(ret20.dropna().nlargest(TOP_CAND).index)
        cands = t3 & set(day_ret.dropna().nlargest(P_PRIMARY).index)
        d5 = dl[pos + 5]
        for code in sorted(cands):
            c0, c5 = close_w.iloc[pos][code], close_w.iloc[pos + 5][code]
            if pd.isna(c0) or pd.isna(c5):
                continue
            ex_sh = float(c5 / c0 - 1 - (sh_close[d5] / sh_close[d] - 1))
            ex_ew = (float(c5 / c0 - 1 - (ew_lvl[d5] / ew_lvl[d] - 1))
                     if (d in ew_lvl and d5 in ew_lvl) else np.nan)
            main_rows.append({"event_d": d, "code": code, "name": names.get(code, ""),
                              "ret20": float(ret20[code]), "dd_day_ret": float(day_ret[code]),
                              "dd_ret_idx": ev["dd_ret"], "bias_at_ev": ev["bias"],
                              "window": ("in_sample" if d <= WIN_INS_END else "oos"),
                              "t5_excess_sh": ex_sh, "t5_excess_ew": ex_ew})
    main_df = pd.DataFrame(main_rows)
    ins = main_df[main_df["window"] == "in_sample"] if not main_df.empty else main_df
    x = ins["t5_excess_sh"].dropna().values if not ins.empty else np.array([])

    # ---- 判定 R1/R2/R3 (冻结) ----
    def pairs_at(bias: float, Pg: int) -> int:
        m = (grid["bias"] == bias) & (grid["P"] == Pg)
        sub = grid.loc[m]
        return int(sub["n_pairs_2010"].iloc[0]) if len(sub) else 0
    n5 = pairs_at(BIAS_PRIMARY, P_PRIMARY)
    n4 = pairs_at(0.04, P_PRIMARY)
    if n5 >= N_MIN:
        lo, hi, p, med = b7.boot_ci_median(x, SEED)
        g1b = ("TERMINATE(主检验臂正式证伪: CI下界<=0, 升级入falsified_rules)"
               if lo <= 0 else
               f"PASS_CANDIDATE(in_sample T+5(vs上证) 中位={med:.4%}, CI=[{lo:.4%},{hi:.4%}], "
               f"p={p:.3f}; 登记前向OOS复核点, 接线须影子期+主人批准)")
    elif n4 >= N_MIN:
        g1b = (f"DESCRIPTIVE_ONLY(主口径5%档 n={n5}<30, 仅最宽4%档 n={n4} 达标 -> "
               f"阈值系可得性驱动, quasi性质升级披露, 判定降为描述统计)")
    else:
        g1b = (f"INFEASIBLE(5%档 n={n5}<30 且 4%档 n={n4}<30 -> 该事件研究范式在本地数据不可行, "
               f"主检验臂关停)")

    # ---- 产出 ----
    def outp(name: str) -> str:
        p = os.path.join(OUT_DIR, name)
        assert os.path.normpath(p).startswith(os.path.normpath(OUT_DIR)), "越权写路径"
        return p

    grid_csv = outp(f"{TAG}_grid.csv")
    grid.to_csv(grid_csv, index=False, encoding="utf-8-sig")
    main_csv = outp(f"{TAG}_events.csv")
    main_df.to_csv(main_csv, index=False, encoding="utf-8-sig")

    lines = [f"# B7b 事件口径功效测量报告({TAG}) {datetime.now():%Y-%m-%d %H:%M}", ""]
    lines.append(f"- 契约: PREREG_MAINLINE_EVENTFEAS_20261021 v1(指纹 mlc-eventfeas-20261021-v1) "
                 f"| seed={SEED} | 宇宙=申万二级L2 | 主基准=上证(贴天风原文) | 敏感性=等权全A")
    lines.append(f"- preflight: **{overall}** | 2010-2015 逐年行业数={early}")
    lines.append(f"- 非盲披露: 2020-07-10/2024-10-09 已在 B7 观察到, 本臂=功效测量+描述统计")
    lines.append("")
    lines.append("## 一、功效网格(BIAS x P, 候选-事件对数, 2010 起 / 其中 2016 起)")
    lines.append("| BIAS | P | 事件数 | 对数(2010+) | 对数(2016+) |")
    lines.append("|---|---|---|---|---|")
    for r in grid_rows:
        lines.append(f"| {r['bias']:.0%} | {r['P']} | {r['n_events']} "
                     f"| {r['n_pairs_2010']} | {r['n_pairs_2016']} |")
    lines.append("")
    lines.append(f"## 二、主口径判定(5%/10)  \n**{g1b}**")
    lines.append("")
    if not main_df.empty:
        lines.append("## 三、主口径事件底稿(vs上证 / vs等权全A)")
        lines.append("| 事件日 | 窗口 | 行业 | 20日涨幅 | T+5(vs上证) | T+5(vs等权) |")
        lines.append("|---|---|---|---|---|---|")
        for r in main_df.itertuples():
            ew_s = f"{r.t5_excess_ew:.4%}" if not (isinstance(r.t5_excess_ew, float)
                                                   and math.isnan(r.t5_excess_ew)) else "NA(覆盖外)"
            lines.append(f"| {r.event_d} | {r.window} | {r.code} {r.name} "
                         f"| {r.ret20:.2%} | {r.t5_excess_sh:.4%} | {ew_s} |")
        lines.append("")
        oos = main_df[main_df["window"] == "oos"]
        lines.append(f"- in_sample n={len(ins)} (vs上证中位 "
                     f"{ins['t5_excess_sh'].median():.4%})"
                     if len(ins) else "- in_sample n=0")
        lines.append(f"- oos(2025+) n={len(oos)}")
        ew_cov = main_df["t5_excess_ew"].notna().sum()
        lines.append(f"- 等权全A基准覆盖 {ew_cov}/{len(main_df)} 对(其余超出qfq覆盖, 计数披露)")
    lines.append("")
    lines.append("## 四、复现命令")
    lines.append("```bash")
    lines.append("python analysis/mainline_eventfeas_20261021.py  # 全参数冻结, seed=20261021")
    lines.append("```")
    lines.append(f"耗时 {time.time()-t0:.1f}s | 底稿: {os.path.basename(main_csv)}")
    rep = "\n".join(lines)
    rep_path = outp(f"{TAG}.md")
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write(rep)

    man = {"task": "B7b", "params": P,
           "input_snapshots": {
               "sw_db": {"path": SW_DB, "sha16": b7.sha16(SW_DB)},
               "qfq_db": {"path": QFQ_DB, "rows": int(nrows_qfq), "sha16": b7.sha16(QFQ_DB)},
               "idx_day": {"path": IDX_FILE, "sha16": b7.sha16(IDX_FILE)}},
           "code": {"file": os.path.abspath(__file__), "sha16": b7.sha16(os.path.abspath(__file__))},
           "random_seed": SEED, "runtime_s": round(time.time() - t0, 1),
           "outputs": {os.path.basename(p): {"sha16": b7.sha16(p)}
                       for p in (grid_csv, main_csv, rep_path)}}
    with open(outp(f"manifest_{TAG}.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)

    print(f"[B7b] grid head: {grid_rows[:4]}")
    print(f"[B7b] main pairs={len(main_df)} (in_sample={len(ins)}) | 判定: {g1b}")
    print(f"[OUT] {rep_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
