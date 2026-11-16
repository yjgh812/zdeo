# ⚠️ 2026-08-22 bps/sps 修复警示: 本脚本 bps 消费基于旧口径(close/bps, 且当时 factor_pit.bps=ref_close/bp 失真~50倍)。
# factor_pit.db 已修复(bps=bp×ref_close), 如需重跑本脚本请将 close/bps 改为 bps/close(仅历史审计, 非生产链路)。
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_combo_extension_test_20260815.py — bp_ep+id_vol 组合变体扩展测试

目的: 灰度轨道9/1启动前, 检验更优变体:
  Base  bp_ep (生产基线)
  V1    bp_ep + id_vol                     (现役灰度组合, 基准参照)
  V2    bp_ep + id_vol + alpha015          (3因子)
  V3    bp_ep + id_vol + alpha006          (3因子)
  V4    bp_ep + id_vol + alpha003          (3因子)
  V5    bp_ep + id_vol_deCorr              (净化版波动率, 滞后6期回归残差)
  V6    bp_ep + id_vol_deCorr + alpha015   (3因子净化版)

口径: 与增量测试完全一致 (月末信号→次bar, ≤30元/非ST/行业≤4/Top20/成本0.1%, 2018-01~2026-07)
打分: 各成分pct-rank等权平均 (id_vol系取 -rank)
用法: python D:/zcodesj/factor_combo_extension_test_20260815.py
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

_spec = importlib.util.spec_from_file_location("sp", r"D:\zcodesj\aurumq_alpha101_spotcheck_20260815.py")
sp = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sp)
_spec2 = importlib.util.spec_from_file_location("mt", r"D:\zcodesj\factor_methodology_tools_20260815.py")
mt = importlib.util.module_from_spec(_spec2); _spec2.loader.exec_module(mt)
_spec3 = importlib.util.spec_from_file_location("qc", r"D:\zcodesj\quantbyqlib_comparison_20260815.py")
qc = importlib.util.module_from_spec(_spec3); _spec3.loader.exec_module(qc)

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")
MAIN_END_TS = pd.Timestamp("2026-07-31")
SIG_FIRST = pd.Timestamp("2018-01-31")
COST = 0.001
TOPN, CAND_POOL, MAX_PER_IND = 20, 60, 4
MAX_STALE_DAYS, MIN_CROSS = 400, 50

t0 = time.time()


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main() -> int:
    log("[1/6] 加载数据...")
    o = sp.load_data()
    prices = o["close"]
    ret = prices.pct_change().clip(-0.21, 0.21)
    st_codes = sp.load_st_codes()
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    info = pd.read_sql_query("SELECT code, industry FROM stock_info", con); con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    ind_map = dict(zip(info["code"], info["industry"].fillna("UNKNOWN")))
    pit = mt.load_pit_events()
    cal = prices.index
    ym = cal.to_period("M")
    month_ends = sorted(pd.Series(cal, index=ym).groupby(level=0).max())
    month_ends = [d for d in month_ends if SIG_FIRST <= d <= MAIN_END_TS]
    rc_idx = sp.load_raw_close([int(d.strftime("%Y%m%d")) for d in month_ends])

    elig_at, pit_snap = {}, {}
    for s in month_ends:
        rc = rc_idx.get(s)
        if rc is None:
            elig_at[s] = pd.Index([])
        else:
            elig_at[s] = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                                  & rc.index.str[:2].isin(("00", "30", "60", "68"))].intersection(prices.columns)
        sub = pit[pit["avail_ts"] <= s]
        sub = sub.sort_values(["code", "avail_ts"]).groupby("code", as_index=False).tail(1)
        sub = sub[(s - sub["avail_ts"]).dt.days <= MAX_STALE_DAYS]
        pit_snap[s] = sub.set_index("code")

    log("[2/6] 计算因子 (id_vol / id_vol_deCorr / alpha015/006/003)...")
    t1 = time.time()
    iv = mt.id_vol_monthly(prices, month_ends)
    iv_dc = mt.decorr_factor(iv.reindex(month_ends), N=6)
    f_alpha = {n: fn(o) for n, fn in (("alpha015", sp.alpha015),
                                      ("alpha006", sp.alpha006),
                                      ("alpha003", sp.alpha003))}
    log("      done (%ds)" % (time.time() - t1))

    # 月度面板
    panel = {}
    for s in month_ends:
        elig = elig_at[s]; snap = pit_snap[s]; rc = rc_idx.get(s)
        if snap.empty or rc is None or len(elig) < MIN_CROSS or s not in iv.index:
            continue
        eps = snap["eps"].reindex(elig); pb = rc.reindex(elig) / snap["bps"].reindex(elig)
        cs = pd.DataFrame({"eps": eps, "pb": pb}).dropna()
        cs = cs[(cs["eps"] > 0) & (cs["pb"] > 0)]
        if len(cs) < MIN_CROSS:
            continue
        cs["bp_ep"] = (cs["eps"].rank(pct=True) + cs["pb"].rank(pct=True)) / 2
        cs["iv"] = iv.loc[s].reindex(cs.index)
        if s in iv_dc.index:
            cs["iv_dc"] = iv_dc.loc[s].reindex(cs.index)
        for f, src in f_alpha.items():
            cs[f] = src.loc[s].reindex(cs.index)
        panel[s] = cs

    # 相关性: alpha系 vs id_vol
    log("[3/6] 相关性 (alpha系 vs id_vol)...")
    for f in ("alpha015", "alpha006", "alpha003"):
        cs_list = []
        for s, cs in panel.items():
            sub = cs[["iv", f]].dropna()
            if len(sub) >= MIN_CROSS:
                cs_list.append(stats.spearmanr(sub["iv"], sub[f])[0])
        log("      %s vs id_vol: %+.3f" % (f, float(np.mean(cs_list))))

    # 持有期收益
    execs = []
    for s in month_ends:
        pos = cal.searchsorted(s, side="right")
        execs.append(cal[pos] if pos < len(cal) and cal[pos] > s else None)
    fr = {}
    for i, s in enumerate(month_ends):
        e = execs[i]
        end_e = execs[i + 1] if i + 1 < len(execs) else MAIN_END_TS
        if e is None:
            continue
        p0 = prices.loc[e]
        p1_raw = prices.loc[(prices.index > e) & (prices.index <= end_e)]
        p1 = p1_raw.iloc[-1] if len(p1_raw) else pd.Series(dtype=float)
        f = (p1 / p0).replace([np.inf, -np.inf], np.nan) - 1
        fr[s] = f.dropna()

    log("[4/6] 构建6臂分数与组合回测...")
    ARMS = [("Base", ["bp_ep"]),
            ("V1_bpep_iv", ["bp_ep", "iv"]),
            ("V2_bpep_iv_a15", ["bp_ep", "iv", "alpha015"]),
            ("V3_bpep_iv_a06", ["bp_ep", "iv", "alpha006"]),
            ("V4_bpep_iv_a03", ["bp_ep", "iv", "alpha003"]),
            ("V5_bpep_ivdc", ["bp_ep", "iv_dc"]),
            ("V6_bpep_ivdc_a15", ["bp_ep", "iv_dc", "alpha015"])]
    scores = {name: {} for name, _ in ARMS}
    for s, cs in panel.items():
        for name, comps in ARMS:
            sub = cs[comps].dropna()
            if len(sub) < MIN_CROSS:
                continue
            parts = []
            for c in comps:
                r = sub[c].rank(pct=True)
                if c in ("iv", "iv_dc"):
                    r = -r
                parts.append(r)
            scores[name][s] = sum(parts) / len(parts)

    def pick_top(score):
        sig = {}
        for s in month_ends:
            if s not in score or score[s] is None:
                sig[s] = []; continue
            if len(score[s]) < MIN_CROSS:
                sig[s] = []; continue
            sc = score[s].sort_values(ascending=False)
            pos = cal.searchsorted(s, side="right")
            e = cal[pos] if pos < len(cal) and cal[pos] > s else None
            price_avail = set(prices.loc[e].dropna().index) if e is not None else set()
            picks_, used = [], {}
            for code in sc.index[:CAND_POOL]:
                if len(picks_) >= TOPN:
                    break
                if code not in price_avail:
                    continue
                ind = ind_map.get(code, "UNKNOWN")
                if used.get(ind, 0) >= MAX_PER_IND:
                    continue
                used[ind] = used.get(ind, 0) + 1
                picks_.append(code)
            sig[s] = picks_
        return sig

    results = {}
    for name, _ in ARMS:
        out = qc.run_engine(pick_top(scores[name]), prices, ret, COST, MAIN_END_TS)
        results[name] = out
        log("      %-16s CAGR%+.1f%% Sharpe%.2f MDD%.1f%% 换手%.0f%% n=%.1f" % (
            name, out["cagr"] * 100, out["sharpe"], out["mdd"] * 100,
            out["avg_month_turnover"] * 100, out["avg_n"]))

    log("[5/6] 组合分月度IC + 分年...")
    ic_rows = []
    for s in month_ends:
        if s not in fr:
            continue
        f = fr[s]
        row = {"date": str(int(s.strftime("%Y%m%d")))}
        for name, _ in ARMS:
            if s not in scores[name]:
                continue
            v = scores[name][s].reindex(f.index).dropna()
            if len(v) >= MIN_CROSS:
                ic = stats.spearmanr(v, f.reindex(v.index))[0]
                row[name] = ic if ic == ic else np.nan
        ic_rows.append(row)
    ic_df = pd.DataFrame(ic_rows).set_index("date")
    for c in ic_df.columns:
        v = ic_df[c].dropna()
        log("      IC[%-16s] %+.4f (ICIR%.2f, 正率%.0f%%)" % (
            c, v.mean(), v.mean() / v.std() if v.std() > 0 else 0, (v > 0).mean() * 100))

    # 最佳非Base臂分年
    best = max((k for k in results if k != "Base"), key=lambda k: results[k]["sharpe"])
    nav = results[best]["nav"]
    yr = (1 + nav.pct_change().dropna()).groupby(nav.pct_change().dropna().index.year).prod() - 1
    log("      最佳臂 %s 分年: %s" % (best, ", ".join(
        "%d%+.0f%%" % (y, v * 100) for y, v in sorted(yr.items()))))

    log("[6/6] 写报告...")
    L = []
    L.append("# bp_ep+id_vol 组合变体扩展测试 — 2026-08-15\n")
    L.append("- 口径: 月末信号→次bar; ≤30元/非ST/行业≤4/Top20/成本0.1%%; 2018-01~2026-07\n")
    L.append("| 臂 | 构成 | CAGR | Sharpe | MDD | 月均换手 |")
    L.append("|---|---|---|---|---|---|")
    for name, comps in ARMS:
        out = results[name]
        L.append("| %s | %s | %+.1f%% | %.2f | %.1f%% | %.0f%% |" % (
            name, "+".join(comps), out["cagr"] * 100, out["sharpe"],
            out["mdd"] * 100, out["avg_month_turnover"] * 100))
    L.append("\n## 组合分月度IC\n")
    L.append("| 臂 | 月度IC | ICIR | 正率 | 月数 |")
    L.append("|---|---|---|---|---|")
    for c in ic_df.columns:
        v = ic_df[c].dropna()
        L.append("| %s | %+.4f | %.2f | %.0f%% | %d |" % (
            c, v.mean(), v.mean() / v.std() if v.std() > 0 else 0,
            (v > 0).mean() * 100, len(v)))
    L.append("\n## 最佳臂分年 (%s)\n" % best)
    L.append("| 年 | %s |" % best)
    L.append("|---|---|")
    for y, v in sorted(yr.items()):
        L.append("| %d | %+.1f%% |" % (y, v * 100))
    L.append("\n## 解读\n")
    L.append("（由主流程人工补写）")
    (REPORT_DIR / "FACTOR_COMBO_EXTENSION_20260815.md").write_text(
        "\n".join(L), encoding="utf-8")
    log("完成 → reports/FACTOR_COMBO_EXTENSION_20260815.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
