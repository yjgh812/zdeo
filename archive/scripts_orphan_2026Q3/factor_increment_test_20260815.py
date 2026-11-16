# ⚠️ 2026-08-22 bps/sps 修复警示: 本脚本 bps 消费基于旧口径(close/bps, 且当时 factor_pit.bps=ref_close/bp 失真~50倍)。
# factor_pit.db 已修复(bps=bp×ref_close), 如需重跑本脚本请将 close/bps 改为 bps/close(仅历史审计, 非生产链路)。
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_increment_test_20260815.py — 候选因子 × bp+ep 相关性分析与组合增量测试

候选因子(前两轮产出): id_vol(20日市场模型残差波动率, 负向) / alpha015 / alpha006 / alpha003
口径: 月末信号→次bar成交; 60/00/30/68 非ST; 信号日raw close≤30元; qfq收益±21%截断;
成本0.1%单边×换手; 2018-01-31 ~ 2026-07-31; Top20 + 行业上限≤4 + CAND_POOL60(与B臂一致)

臂:
  Base   bp_ep 生产结构(基线)
  A      bp_ep + id_vol(-rank) 等权pct-rank合成
  B      bp_ep + alpha015(rank) 等权pct-rank合成
  C      bp_ep + alpha006(rank) 等权pct-rank合成
  D      bp_ep + alpha003(rank) 等权pct-rank合成

用法: python D:/zcodesj/factor_increment_test_20260815.py
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

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")

# 复用 spot-check 的因子函数(alpha003/006/015)与数据加载
_spec = importlib.util.spec_from_file_location(
    "sp", r"D:\zcodesj\aurumq_alpha101_spotcheck_20260815.py")
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)
_spec2 = importlib.util.spec_from_file_location(
    "mt", r"D:\zcodesj\factor_methodology_tools_20260815.py")
mt = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(mt)
_spec3 = importlib.util.spec_from_file_location(
    "qc", r"D:\zcodesj\quantbyqlib_comparison_20260815.py")
qc = importlib.util.module_from_spec(_spec3)
_spec3.loader.exec_module(qc)

PRICE_START, PRICE_END = "20160104", "20260814"
MAIN_END_TS = pd.Timestamp("2026-07-31")
SIG_FIRST = pd.Timestamp("2018-01-31")
STOCK_PREFIX = ("00", "30", "60", "68")
GHOST_DATES = {20181231, 20260627}
COST = 0.001
TOPN, CAND_POOL, MAX_PER_IND = 20, 60, 4
MAX_STALE_DAYS = 400
MIN_CROSS = 50

t0 = time.time()


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def id_vol_monthly(close: pd.DataFrame, dates: list, win=20) -> pd.DataFrame:
    """每月末: 近20日市场模型残差波动率 (与methodology脚本同式)"""
    ret = close.pct_change()
    out = {}
    for s in dates:
        r = ret.loc[:s].tail(win + 1)
        if len(r) < win:
            continue
        r = r.iloc[1:]
        mkt = r.mean(axis=1).values
        mkt_d = mkt - mkt.mean()
        denom = (mkt_d ** 2).sum()
        if denom == 0:
            continue
        r_d = r.sub(r.mean(axis=0), axis=1)
        beta = (r_d.values.T @ mkt_d) / denom
        resid = r_d.values - np.outer(mkt_d, beta)
        out[s] = pd.Series(resid.std(axis=0), index=r.columns)
    return pd.DataFrame(out).T


def main() -> int:
    log("[1/6] 加载数据...")
    o = sp.load_data()
    prices = o["close"]
    ret = prices.pct_change().clip(-0.21, 0.21)
    st_codes = sp.load_st_codes()
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    try:
        info = pd.read_sql_query("SELECT code, industry FROM stock_info", con)
    finally:
        con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    ind_map = dict(zip(info["code"], info["industry"].fillna("UNKNOWN")))
    pit = mt.load_pit_events()
    cal = prices.index
    ym = cal.to_period("M")
    month_ends = sorted(pd.Series(cal, index=ym).groupby(level=0).max())
    month_ends = [d for d in month_ends if SIG_FIRST <= d <= MAIN_END_TS]
    sig_str = [int(d.strftime("%Y%m%d")) for d in month_ends]
    rc_idx = sp.load_raw_close(sig_str)

    elig_at, pit_snap = {}, {}
    for s in month_ends:
        rc = rc_idx.get(s)
        if rc is None:
            elig_at[s] = pd.Index([])
        else:
            codes = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                             & rc.index.str[:2].isin(STOCK_PREFIX)]
            elig_at[s] = codes.intersection(prices.columns)
        sub = pit[pit["avail_ts"] <= s]
        sub = sub.sort_values(["code", "avail_ts"]).groupby("code", as_index=False).tail(1)
        sub = sub[(s - sub["avail_ts"]).dt.days <= MAX_STALE_DAYS]
        pit_snap[s] = sub.set_index("code")

    log("[2/6] 计算候选因子值...")
    t1 = time.time()
    f_alpha = {n: fn(o) for n, fn in (("alpha015", sp.alpha015),
                                      ("alpha006", sp.alpha006),
                                      ("alpha003", sp.alpha003))}
    f_idvol = id_vol_monthly(prices, month_ends)
    log("      alpha015/006/003 + id_vol done (%ds)" % (time.time() - t1))

    # 每信号日: bp_ep 合格集(ep>0&bp>0)上的 因子值面板
    log("[3/6] 构建月度面板 + 相关性...")
    panel = {}
    for s in month_ends:
        elig = elig_at[s]
        snap = pit_snap[s]
        rc = rc_idx.get(s)
        if snap.empty or rc is None or len(elig) < MIN_CROSS:
            continue
        eps = snap["eps"].reindex(elig)
        pb = rc.reindex(elig) / snap["bps"].reindex(elig)
        cs = pd.DataFrame({"eps": eps, "pb": pb}).dropna()
        cs = cs[(cs["eps"] > 0) & (cs["pb"] > 0)]
        if len(cs) < MIN_CROSS:
            continue
        cs["bp_ep"] = (cs["eps"].rank(pct=True) + cs["pb"].rank(pct=True)) / 2
        for f, src in (("id_vol", f_idvol), ("alpha015", f_alpha["alpha015"]),
                       ("alpha006", f_alpha["alpha006"]), ("alpha003", f_alpha["alpha003"])):
            if s in src.index:
                cs[f] = src.loc[s].reindex(cs.index)
        panel[s] = cs

    # 月度相关性 (Spearman, 与bp_ep)
    corr_rows = {f: [] for f in ("id_vol", "alpha015", "alpha006", "alpha003")}
    corr_eps = {f: [] for f in ("id_vol", "alpha015", "alpha006", "alpha003")}
    for s, cs in panel.items():
        for f in corr_rows:
            sub = cs[["bp_ep", f]].dropna()
            if len(sub) >= MIN_CROSS:
                corr_rows[f].append(stats.spearmanr(sub["bp_ep"], sub[f])[0])
                sub2 = cs[["eps", f]].dropna()
                if len(sub2) >= MIN_CROSS:
                    corr_eps[f].append(stats.spearmanr(sub2["eps"], sub2[f])[0])
    log("      月度Spearman相关(vs bp_ep组合): " + ", ".join(
        "%s%+.3f" % (f, float(np.mean(v))) for f, v in corr_rows.items()))
    log("      月度Spearman相关(vs eps单因子): " + ", ".join(
        "%s%+.3f" % (f, float(np.mean(v))) for f, v in corr_eps.items()))

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

    log("[4/6] 组合回测 (5臂)...")

    def pick_top(score_s: dict):
        sig = {}
        for s in month_ends:
            if s not in score_s or score_s[s] is None:
                sig[s] = []
                continue
            score = score_s[s]
            if len(score) < MIN_CROSS:
                sig[s] = []
                continue
            score = score.sort_values(ascending=False)
            pos = cal.searchsorted(s, side="right")
            e = cal[pos] if pos < len(cal) and cal[pos] > s else None
            price_avail = set(prices.loc[e].dropna().index) if e is not None else set()
            picks, used = [], {}
            for code in score.index[:CAND_POOL]:
                if len(picks) >= TOPN:
                    break
                if code not in price_avail:
                    continue
                ind = ind_map.get(code, "UNKNOWN")
                if used.get(ind, 0) >= MAX_PER_IND:
                    continue
                used[ind] = used.get(ind, 0) + 1
                picks.append(code)
            sig[s] = picks
        return sig

    scores = {"Base": {}, "bp_ep+id_vol": {}, "bp_ep+alpha015": {},
              "bp_ep+alpha006": {}, "bp_ep+alpha003": {}, "id_vol_alone": {}}
    for s, cs in panel.items():
        scores["Base"][s] = cs["bp_ep"]
        for f in ("id_vol", "alpha015", "alpha006", "alpha003"):
            sub = cs[["bp_ep", f]].dropna()
            if len(sub) < MIN_CROSS:
                continue
            # id_vol 负向(高波动→差): 用 -rank; alpha 因子 IC 为正: 直接用 rank
            r = -sub["id_vol"].rank(pct=True) if f == "id_vol" else sub[f].rank(pct=True)
            scores[f"bp_ep+{f}"][s] = (sub["bp_ep"].rank(pct=True) + r) / 2
        sub = cs[["id_vol"]].dropna()
        if len(sub) >= MIN_CROSS:
            scores["id_vol_alone"][s] = -sub["id_vol"].rank(pct=True)

    picks_all = {k: pick_top(v) for k, v in scores.items()}
    results = {}
    for k, sig in picks_all.items():
        out = qc.run_engine(sig, prices, ret, COST, MAIN_END_TS)
        results[k] = out
        log("      %-14s CAGR%+.1f%% Sharpe%.2f MDD%.1f%% 换手%.0f%% n=%.1f" % (
            k, out["cagr"] * 100, out["sharpe"], out["mdd"] * 100,
            out["avg_month_turnover"] * 100, out["avg_n"]))

    # 分年收益
    for k, out in results.items():
        nav = out["nav"]
        y = (1 + nav.pct_change().dropna()).groupby(
            nav.pct_change().dropna().index.year).prod() - 1
        results[k]["yearly"] = y
        if k in ("Base", "bp_ep+id_vol"):
            log("      %s 分年: %s" % (k, ", ".join(
                "%d%+.0f%%" % (yy, v * 100) for yy, v in sorted(y.items()))))

    # 成本敏感性 (bp_ep+id_vol)
    log("      成本敏感性 bp_ep+id_vol:")
    for cst in (0.002, 0.003):
        out = qc.run_engine(picks_all["bp_ep+id_vol"], prices, ret, cst, MAIN_END_TS)
        log("        cost=%.1f%%: CAGR%+.1f%% Sharpe%.2f MDD%.1f%%" % (
            cst * 100, out["cagr"] * 100, out["sharpe"], out["mdd"] * 100))

    # 组合分月度IC (对持有期收益)
    log("[5/6] 组合分月度IC...")
    ic_rows = []
    for s, cs in panel.items():
        if s not in fr:
            continue
        f = fr[s]
        row = {"date": str(int(s.strftime("%Y%m%d")))}
        for k, scs in scores.items():
            if s not in scs or scs[s] is None:
                continue
            v = scs[s].reindex(f.index).dropna()
            if len(v) >= MIN_CROSS:
                ic = stats.spearmanr(v, f.reindex(v.index))[0]
                row[k] = ic if ic == ic else np.nan
        ic_rows.append(row)
    ic_df = pd.DataFrame(ic_rows).set_index("date")
    log("      组合分月度IC: " + ", ".join(
        "%s%+.4f(ICIR%.2f)" % (c, ic_df[c].mean(),
            ic_df[c].mean() / ic_df[c].std() if ic_df[c].std() > 0 else 0)
        for c in ic_df.columns))

    log("[6/6] 写报告...")
    L = []
    L.append("# 候选因子 × bp+ep 相关性与组合增量测试 — 2026-08-15\n")
    L.append("- 口径: 月末信号→次bar; 60/00/30/68 非ST; raw close≤30元; qfq±21%%; 成本0.1%%; "
             "2018-01~2026-07; Top20+行业≤4+CAND60\n")
    L.append("## 一、月度Spearman相关性(统一bp+ep合格集)\n")
    L.append("| 因子 | vs bp_ep组合 | vs eps单因子 |")
    L.append("|---|---|---|")
    for f in corr_rows:
        L.append("| %s | %+.3f | %+.3f |" % (
            f, float(np.mean(corr_rows[f])), float(np.mean(corr_eps[f]))))
    L.append("\n## 二、组合回测(5臂)\n")
    L.append("| 臂 | CAGR | Sharpe | MDD | 累计 | 月均换手 | 平均持仓 |")
    L.append("|---|---|---|---|---|---|---|")
    for k, out in results.items():
        L.append("| %s | %+.1f%% | %.2f | %.1f%% | %+.0f%% | %.0f%% | %.1f |" % (
            k, out["cagr"] * 100, out["sharpe"], out["mdd"] * 100, out["total"] * 100,
            out["avg_month_turnover"] * 100, out["avg_n"]))
    L.append("\n## 三、组合分月度IC\n")
    L.append("| 臂 | 月度IC | ICIR | 月数 |")
    L.append("|---|---|---|---|")
    for c in ic_df.columns:
        v = ic_df[c].dropna()
        L.append("| %s | %+.4f | %.2f | %d |" % (
            c, v.mean(), v.mean() / v.std() if v.std() > 0 else 0, len(v)))
    L.append("\n## 四、分年收益(Base / bp_ep+id_vol)\n")
    L.append("| 年 | Base | bp_ep+id_vol |")
    L.append("|---|---|---|")
    yrs = sorted({y for k in ("Base", "bp_ep+id_vol") for y in results[k]["yearly"].index})
    for y in yrs:
        L.append("| %d | %+.1f%% | %+.1f%% |" % (
            y, results["Base"]["yearly"].get(y, np.nan) * 100,
            results["bp_ep+id_vol"]["yearly"].get(y, np.nan) * 100))
    L.append("\n## 解读\n")
    L.append("（由主流程人工补写）")
    (REPORT_DIR / "FACTOR_INCREMENT_TEST_20260815.md").write_text(
        "\n".join(L), encoding="utf-8")
    log("完成 → reports/FACTOR_INCREMENT_TEST_20260815.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
