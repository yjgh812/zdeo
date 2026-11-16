# -*- coding: utf-8 -*-
"""拥挤臂 OOS 一次性验证 — PREREG_MAINLINE_CROWDING_OOS_20261021 v1 冻结契约执行件。

契约: reports/PREREG_MAINLINE_CROWDING_OOS_20261021.md (指纹 mlc-crowding-oos-20261021-v1)
判定参数集(冻结): (w120,0.95) / (w120,0.99), 主基准=等权全A(qfq派生); 判定只看这两档,
其余档(w3y/80/90/上证基准)仅背景披露。OOS 窗=2022-01-01~执行日, 一次性不调参。
边界: 只读(mode=ro+query_only+参数绑定); 产出仅 analysis/; 纯呈现零交易权。
"""
from __future__ import annotations

import json
import math
import os
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
import mainline_backtest_20261021 as b7

TIER_GRID = (0.80, 0.90, 0.95, 0.99)
PCT_WINDOWS = {"w120": 120, "w3y": 756}
FROZEN = {"w120": (0.95, 0.99)}          # 判定参数集
OOS_START = 20220101
SWEEP_FWD = 20
WIN_SUFF = 0.90
SEED = b7.SEED

SW_DB = b7.SW_DB
QFQ_DB = b7.QFQ_DB
OUT_DIR = b7.OUT_DIR
TAG = "mainline_crowding_oos_20261021"

P = {"TIER_GRID": list(TIER_GRID), "PCT_WINDOWS": PCT_WINDOWS, "FROZEN": FROZEN,
     "OOS_START": OOS_START, "SWEEP_FWD": SWEEP_FWD, "WIN_SUFF": WIN_SUFF, "SEED": SEED}


def main() -> int:
    t0 = time.time()

    idx = b7.to_df(b7.read_day("sh", "000001"))
    if idx.empty:
        print("[FAIL] 上证 vipdoc 读取为空")
        return 2
    sw = b7.load_sw("L2")   # 2012-06 起, 120d/3y 窗在 OOS 段均有足够预热
    dl = sorted(sw["d"].unique().tolist())
    close_w = sw.pivot_table(index="d", columns="ts_code", values="close").reindex(dl)
    amt_w = sw.pivot_table(index="d", columns="ts_code", values="amount").reindex(dl)

    # 等权全A 主基准 (qfq 派生)
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
    sh_close = dict(zip(idx["d"].astype(int), idx["c"]))

    # ---- preflight ----
    con = b7.ro(SW_DB)
    try:
        dup = con.execute(
            "SELECT COUNT(*) FROM (SELECT ts_code,trade_date,COUNT(*) c FROM industry_kline_sw "
            "WHERE trade_date>=? GROUP BY ts_code,trade_date HAVING c>1)",
            ("2022-01-01",)).fetchone()[0]
        nn, tt = con.execute(
            "SELECT SUM(CASE WHEN close IS NULL OR close<=0 OR amount IS NULL THEN 1 ELSE 0 END),"
            "COUNT(*) FROM industry_kline_sw WHERE trade_date>=? AND level='L2'",
            ("2022-01-01",)).fetchone()
    finally:
        con.close()
    null_rate = (nn or 0) / max(tt, 1)
    oos_dates = [d for d in dl if d >= OOS_START]
    mono = bool(idx["d"].is_monotonic_increasing and idx["d"].is_unique)
    ew_cov = sum(1 for d in oos_dates if d in ew_lvl) / max(len(oos_dates), 1)
    checks = {"oos_rows": {"pass": len(oos_dates) > 500, "n": len(oos_dates)},
              "duplicates": {"pass": dup == 0, "count": int(dup)},
              "null_rate": {"pass": null_rate < 0.001, "rate": round(null_rate, 6)},
              "monotonic_unique": {"pass": mono},
              "ew_bench_coverage": {"pass": ew_cov >= 0.99, "value": round(ew_cov, 4)}}
    overall = "PASS" if all(c["pass"] for c in checks.values()) else "FAIL"
    with open(os.path.join(OUT_DIR, f"preflight_{TAG}.json"), "w", encoding="utf-8") as f:
        json.dump({"task": "B7-crowding-OOS", "run_at": datetime.now().isoformat(timespec="seconds"),
                   "checks": checks, "overall": overall}, f, ensure_ascii=False, indent=1)
    print(f"[PREFLIGHT] overall={overall} oos_days={len(oos_dates)} dup={dup} "
          f"null={null_rate:.6f} ew_cov={ew_cov:.4f}")
    if overall == "FAIL":
        print("[FAIL] preflight 未过 -> 零产出退出")
        return 2

    # ---- 分位与触发 ----
    share = amt_w.div(amt_w.sum(axis=1).replace(0, np.nan), axis=0)
    share_ma = share.rolling(20).mean()
    pct = {w: share_ma.rolling(wlen, min_periods=int(wlen * WIN_SUFF)).rank(
        method="max", pct=True) for w, wlen in PCT_WINDOWS.items()}
    fwd = close_w.shift(-SWEEP_FWD) / close_w - 1
    sh_fwd = idx.set_index("d")["c"]
    sh_fwd = sh_fwd.shift(-SWEEP_FWD) / sh_fwd - 1
    ew_ret = ew.set_index("d")["ret"]
    ew_fwd20 = pd.Series(1.0, index=ew_ret.index)
    for k in range(SWEEP_FWD):
        ew_fwd20 = ew_fwd20 * (1.0 + ew_ret.shift(-k))
    ew_fwd20 = ew_fwd20 - 1.0

    result_rows, detail_rows = [], []
    for wname, pw in pct.items():
        for tier in TIER_GRID:
            cross = (pw.shift(1) < tier) & (pw >= tier)
            cross = cross.loc[cross.index >= OOS_START]
            for i, jc in zip(*np.where(cross.values)):
                d0 = int(cross.index[i])
                d5i = i + SWEEP_FWD
                if d5i >= len(dl):
                    continue
                d5 = dl[d5i]
                ind = fwd.iloc[i, jc]
                be = ew_fwd20.get(d0)
                bs = sh_fwd.get(d0)
                rec = {"pct_window": wname, "tier": tier, "event_d": d0,
                       "code": cross.columns[jc]}
                if not pd.isna(ind) and be is not None and not pd.isna(be):
                    rec["t20_excess_ew"] = float(ind - be)
                if not pd.isna(ind) and bs is not None and not pd.isna(bs):
                    rec["t20_excess_sh"] = float(ind - bs)
                if "t20_excess_ew" in rec or "t20_excess_sh" in rec:
                    detail_rows.append(rec)
    det = pd.DataFrame(detail_rows)

    def agg(df: pd.DataFrame, col: str) -> dict:
        if df.empty or col not in df:
            return {"n": 0}
        x = df[col].dropna().values
        if len(x) == 0:
            return {"n": 0}
        t = (float(np.mean(x) / (np.std(x, ddof=1) / math.sqrt(len(x))))
             if len(x) > 2 else float("nan"))
        return {"n": len(x), "mean": round(float(np.mean(x)), 6),
                "winrate": round(float(np.mean(x > 0)), 4), "t": round(t, 3)}

    table_rows = []
    for wname, pw_tiers in (("w120", TIER_GRID), ("w3y", TIER_GRID)):
        for tier in pw_tiers:
            sub = det[(det["pct_window"] == wname) & (det["tier"] == tier)] \
                if not det.empty else pd.DataFrame()
            row = {"pct_window": wname, "tier": tier,
                   "ew": agg(sub, "t20_excess_ew"), "sh": agg(sub, "t20_excess_sh")}
            table_rows.append(row)

    # ---- 判定(冻结: 仅两档, 仅等权基准) ----
    frozen = [r for r in table_rows
              if r["pct_window"] == "w120" and r["tier"] in FROZEN["w120"]]
    qualified = [r for r in frozen
                 if r["ew"].get("n", 0) > 2 and r["ew"]["mean"] < 0 and abs(r["ew"]["t"]) > 2]
    verdict = ("PASS_CANDIDATE(冻结档至少一档OOS复现: "
               + json.dumps([{r['pct_window']: r['tier'], **r['ew']} for r in qualified],
                            ensure_ascii=False)
               + " -> 升格拥挤退潮预警观测件(纯呈现零交易权), 登记前向复核点; "
                 "任何接线仍须独立立项+主人批准)")
    if not qualified:
        verdict = ("DOWNGRADE(两冻结档在主基准等权全A下OOS均不复现 mean<0且|t|>2 -> "
                   "参数集降级为设计窗现象不可外推, 主线研究线干净收官)")

    def outp(name: str) -> str:
        p = os.path.join(OUT_DIR, name)
        assert os.path.normpath(p).startswith(os.path.normpath(OUT_DIR)), "越权写路径"
        return p

    det_csv = outp(f"{TAG}_triggers.csv")
    det.to_csv(det_csv, index=False, encoding="utf-8-sig")

    def fmt(d: dict) -> str:
        if d.get("n", 0) == 0:
            return "n=0"
        return f"n={d['n']}, {d['mean']:.4%}, 胜率{d['winrate']:.1%}, t={d['t']}"

    lines = [f"# 拥挤臂 OOS 一次性验证报告({TAG}) {datetime.now():%Y-%m-%d %H:%M}", ""]
    lines.append(f"- 契约: PREREG_MAINLINE_CROWDING_OOS_20261021 v1 | seed={SEED} "
                 f"| OOS窗={OOS_START}~{dl[-1]} | 判定参数集=w120·0.95/0.99(主基准=等权全A)")
    lines.append(f"- preflight: **{overall}** | OOS交易日={len(oos_dates)} | 触发明细 n={len(det)}")
    lines.append("")
    lines.append("## 一、OOS 触发后20日超额全表(背景档仅披露, 不参与判定)")
    lines.append("| 分位窗 | 档 | 等权全A基准 | 上证基准 |")
    lines.append("|---|---|---|---|")
    for r in table_rows:
        frozen_mark = " ←冻结档" if (r["pct_window"] == "w120" and r["tier"] in FROZEN["w120"]) else ""
        lines.append(f"| {r['pct_window']} | {r['tier']:.2f}{frozen_mark} | {fmt(r['ew'])} | {fmt(r['sh'])} |")
    lines.append("")
    lines.append(f"## 二、判定  \n**{verdict}**")
    lines.append("")
    lines.append("## 三、纪律声明")
    lines.append("1. OOS 窗(2022-01-01 起)从未参与 G2 设计窗(2016-2021)参数选择, 一次性不调参;")
    lines.append("2. 判定仅看两冻结档 x 主基准; 背景档(含上证基准/80-90档/w3y)只披露不改变判定;")
    lines.append("3. 本件为纯呈现观测件, 零交易权; 接线(画像/R41/仓位)须独立立项+主人批准;")
    lines.append("4. 主检验臂(分歧日事件研究)已 INFEASIBLE 关停, 本臂独立于该判定。")
    lines.append("")
    lines.append("## 四、复现命令")
    lines.append("```bash")
    lines.append("python analysis/mainline_crowding_oos_20261021.py  # 全参数冻结, seed=20261021")
    lines.append("```")
    lines.append(f"耗时 {time.time()-t0:.1f}s | 触发底稿: {os.path.basename(det_csv)}")
    rep = "\n".join(lines)
    rep_path = outp(f"{TAG}.md")
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write(rep)

    man = {"task": "B7-crowding-OOS", "params": P,
           "input_snapshots": {
               "sw_db": {"path": SW_DB, "sha16": b7.sha16(SW_DB)},
               "qfq_db": {"path": QFQ_DB, "rows": int(nrows_qfq), "sha16": b7.sha16(QFQ_DB)},
               "idx_day": {"path": b7.IDX_FILE, "sha16": b7.sha16(b7.IDX_FILE)}},
           "code": {"file": os.path.abspath(__file__), "sha16": b7.sha16(os.path.abspath(__file__))},
           "random_seed": SEED, "runtime_s": round(time.time() - t0, 1),
           "outputs": {os.path.basename(p): {"sha16": b7.sha16(p)}
                       for p in (det_csv, rep_path)}}
    with open(outp(f"manifest_{TAG}.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)

    print(f"[OOS] 冻结档:")
    for r in frozen:
        print(f"  w120·{r['tier']}: EW {fmt(r['ew'])} | SH {fmt(r['sh'])}")
    print(f"[OOS] 判定: {verdict[:150]}")
    print(f"[OUT] {rep_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
