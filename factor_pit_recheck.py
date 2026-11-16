#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_pit_recheck.py — droe+roa PIT 口径重验（修正前视污染，对齐 8/15 仲裁口径）

修正点（对照 v1）:
  v1 bug: ≤30元过滤用 qfq 历史复权价（2026基准缩放历史价）→ 域失真
  本版:  ≤30元过滤用信号日当天**原始收盘价**（stock_daily_meta，与仲裁 stock_factors_fundamental 同源）
  v1 差异: 信号日=月初 → 本版=月末最后交易日（对齐仲裁 SIG_FIRST=2018-01-31）
  v1 差异: 陈旧度=400交易日 → 本版=400**日历日**（对齐仲裁 MAX_STALE_DAYS）

口径:
  - 价格/收益: dragon_quant_qfq.db 前复权（next-bar 成交）
  - 因子: factor_pit.db.stock_factors_fundamental_pit_v2 的 droe/roa（事件日=pit_avail_date；2026-09-01 修复：8/26 PIT v1→v2 迁移改名漏改此处引用）
  - WF: train=500d icir 权重（IC 全域日度）
  - 组合: Top10%（对齐前视版口径）/ Top50 / Top50+行业≤4（对齐仲裁）

用法: python factor_pit_recheck.py [--start 20160104 --end 20260731]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import factor_combo_walkforward as fcw  # noqa: E402
import factor_qfq_recheck as fqr  # noqa: E402

PIT_DB = r"D:\zcodesj\factor_pit.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")
STOCK_PREFIX = ("00", "30", "60", "68")
SIG_FIRST = pd.Timestamp("2018-01-31")
TRAIN_D, MIN_HIST, FWD_D = 500, 120, 20
COST = 0.001
TOPN = 50
MAX_PER_IND = 4
MAX_STALE_DAYS = 400
MIN_CROSS = 50


def load_raw_close_pivot(prices_index: pd.DatetimeIndex) -> pd.DataFrame:
    """原始收盘价（不复权）→ date×code pivot，≤30元过滤用（信号日当天真实价格）"""
    d0, d1 = prices_index[0].strftime("%Y%m%d"), prices_index[-1].strftime("%Y%m%d")
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=120)
    try:
        df = pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta "
            "WHERE date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
            conn, params=[int(d0), int(d1)])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    piv.index = pd.DatetimeIndex(piv.index)
    return piv.reindex(prices_index)


def load_pit_factors() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT code, pit_avail_date, droe, roa FROM stock_factors_fundamental_pit_v2",
            conn)
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["ev_ts"] = pd.to_datetime(df["pit_avail_date"].astype(str), format="%Y%m%d")
    return df.dropna(subset=["droe"])[["code", "ev_ts", "droe", "roa"]]


def factor_matrix(events: pd.DataFrame, col: str, cal: pd.DatetimeIndex) -> pd.DataFrame:
    """(code, ev_ts, value) → days×codes 矩阵（前向填充）"""
    ev = events[["code", "ev_ts", col]].dropna()
    piv = ev.pivot_table(index="code", columns="ev_ts", values=col, aggfunc="last")
    piv = piv.sort_index(axis=1).ffill(axis=1).reindex(columns=cal, method="ffill")
    return piv.T.astype(np.float64)


def cs_rank_z(row: pd.Series) -> pd.Series:
    r = row.rank()
    n = row.notna().sum()
    return (r - (n + 1) / 2) / (n / np.sqrt(12))


def month_ends(cal: pd.DatetimeIndex, first: pd.Timestamp, last: pd.Timestamp) -> list:
    ym = cal.to_period("M")
    me = pd.Series(cal, index=ym).groupby(level=0).max()
    return sorted([d for d in me if first <= d <= last])


def run_engine(signals: dict, prices: pd.DataFrame, cost: float,
               end_ts: pd.Timestamp) -> tuple:
    """月末信号 → 次一交易日成交，qfq 收益，next-bar 严格无未来。
    返回 (nav_df, avg_turnover, avg_hold_n)"""
    ret = prices.pct_change().clip(-0.21, 0.21)
    idx = prices.index
    sigs = sorted(signals)
    execs = []
    for s in sigs:
        pos = idx.searchsorted(s, side="right")
        if pos < len(idx) and idx[pos] > s:
            execs.append(idx[pos])
    nav, navs, dts = 1.0, [], []
    prev_hold, turns, holds = None, [], []
    for i, e in enumerate(execs):
        end_e = execs[i + 1] if i + 1 < len(execs) else end_ts
        hold = [c for c in signals[sigs[i]] if c in prices.columns]
        period = idx[(idx > e) & (idx <= end_e)]
        if prev_hold is not None:
            turn = 1 - len(set(hold) & set(prev_hold)) / max(len(set(hold)), 1)
            nav *= (1 - cost * turn)
            turns.append(turn)
        prev_hold = set(hold)
        holds.append(len(hold))
        for d in period:
            if len(hold):
                r = ret.loc[d].reindex(hold).fillna(0.0)
                nav *= (1 + r.mean())
            navs.append(nav)
            dts.append(d)
    nav_s = pd.DataFrame({"nav": navs}, index=pd.DatetimeIndex(dts))
    avg_to = float(np.mean(turns)) if turns else 0.0
    avg_n = float(np.mean(holds)) if holds else 0.0
    return nav_s, avg_to, avg_n


def oos_last_pick(signals: dict, prices: pd.DataFrame, last_sig: pd.Timestamp,
                  end_ts: pd.Timestamp) -> dict:
    """最近一期信号在 (执行日, end] 的样本外收益（next-bar）"""
    ret = prices.pct_change().clip(-0.21, 0.21)
    idx = prices.index
    pos = idx.searchsorted(last_sig, side="right")
    e = idx[pos] if pos < len(idx) and idx[pos] > last_sig else None
    if e is None:
        return {}
    hold = [c for c in signals[last_sig] if c in prices.columns]
    period = idx[(idx > e) & (idx <= end_ts)]
    if len(period) == 0:
        return {}
    r_days = []
    for d in period:
        r = ret.loc[d].reindex(hold).fillna(0.0)
        r_days.append((d, float(r.mean())))
    r_s = pd.Series(dict(r_days))
    return {"exec": e, "n": len(hold), "days": len(r_s),
            "cum_ret": float((1 + r_s).prod() - 1), "daily_mean": float(r_s.mean())}


def main() -> int:
    ap = argparse.ArgumentParser(description="droe+roa PIT 口径重验（对齐仲裁）")
    ap.add_argument("--start", default="20160104")
    ap.add_argument("--end", default="20260731")
    ap.add_argument("--cost-sweep", action="store_true", help="成本敏感性扫描（Top10%%）")
    ap.add_argument("--oos", action="store_true", help="最近一期清单样本外验证（至 8/14）")
    a = ap.parse_args()
    t0 = time.time()

    print("[1/6] 加载 qfq 价格 + 原始 close + ST/行业...")
    price_end = "20260814" if a.oos else a.end   # OOS 验证需扩展价格到数据末日
    prices = fqr.load_price_pivot_qfq(a.start, price_end)
    prices = prices[[c for c in prices.columns if c.startswith(STOCK_PREFIX)]]
    raw_close = load_raw_close_pivot(prices.index)
    print(f"      qfq {prices.shape[0]}日 × {prices.shape[1]}股 | 原始价 {raw_close.shape[0]}日")
    st_codes = fcw.load_st_codes()
    ind_map = fqr.load_industry_map()

    print("[2/6] 构建 PIT 因子矩阵 + 日度 IC...")
    pit = load_pit_factors()
    print(f"      PIT 事件 {len(pit)} 行")
    fwd_mat = (prices.shift(-FWD_D) / prices - 1).astype(np.float64)
    fwd_rank = fwd_mat.rank(axis=1)
    mats, ics = {}, {}
    for f in ("droe", "roa"):
        m = factor_matrix(pit, f, prices.index)
        mats[f] = m
        ic_series = []
        for d in m.index:
            col = m.loc[d].dropna()
            fr = fwd_rank.loc[d].reindex(col.index).dropna()
            col = col.reindex(fr.index)
            if len(col) >= 30:
                ic, _ = __import__("scipy.stats", fromlist=["spearmanr"]).spearmanr(col, fr)
                if not np.isnan(ic):
                    ic_series.append((d, ic))
        s = pd.Series(dict(ic_series)).sort_index()
        ics[f] = s
        print(f"      {f:<5} IC={s.mean():+.4f} ICIR={s.mean()/s.std() if s.std() else 0:+.3f}")
    daily_ic = pd.DataFrame(ics).dropna()
    print(f"      IC 对齐 {len(daily_ic)} 天")

    print("[3/6] 月末信号 + 打分（wf-icir, PIT）...")
    cal = prices.index
    sigs = month_ends(cal, SIG_FIRST, pd.Timestamp(a.end))
    print(f"      {len(sigs)} 个月末信号日（{sigs[0].date()} ~ {sigs[-1].date()}）")

    def wf_icir_weights(s):
        hist = daily_ic[daily_ic.index < s].tail(TRAIN_D)
        if len(hist) < MIN_HIST:
            return None
        return fcw.weights_from_train(hist, "icir")

    def eligible_at(s):
        rc = raw_close.loc[s] if s in raw_close.index else pd.Series(dtype=float)
        codes = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                         & rc.index.str[:2].isin(STOCK_PREFIX)]
        return codes.intersection(prices.columns)

    def snap_at(s):
        sub = pit[pit["ev_ts"] <= s]
        if sub.empty:
            return pd.DataFrame()
        sub = sub.sort_values(["code", "ev_ts"]).groupby("code", as_index=False).tail(1)
        sub = sub[(s - sub["ev_ts"]).dt.days <= MAX_STALE_DAYS]
        return sub.set_index("code")

    def signals(topn=None, top_pct=None, max_per_ind=0):
        sig = {}
        for s in sigs:
            elig = eligible_at(s)
            if len(elig) < MIN_CROSS:
                sig[s] = []
                continue
            w = wf_icir_weights(s)
            if w is None:
                sig[s] = []
                continue
            snap = snap_at(s)
            parts = []
            for k, f in enumerate(("droe", "roa")):
                vals = mats[f].loc[s].reindex(snap.index)
                parts.append(cs_rank_z(vals) * w[k])
            score = sum(parts).dropna().reindex(elig).dropna()
            if len(score) < MIN_CROSS:
                sig[s] = []
                continue
            if top_pct is not None:
                n_top = max(5, int(len(score) * top_pct))
            else:
                n_top = topn
            if max_per_ind > 0:
                inds = pd.Series([ind_map.get(c, "未知") for c in score.index], index=score.index)
                picked = []
                for _, sub in score.groupby(inds):
                    picked.append(sub.nlargest(max_per_ind))
                cand = pd.concat(picked).nlargest(n_top)
                sig[s] = list(cand.index)
            else:
                sig[s] = list(score.nlargest(n_top).index)
        return sig

    def ew_signals():
        sig = {}
        for s in sigs:
            elig = eligible_at(s)
            sig[s] = list(elig) if len(elig) >= MIN_CROSS else []
        return sig

    def quota_signals(top_pct=0.10):
        """行业中性化：每行业按其股票数比例配额选股（行业暴露≈市场）"""
        sig = {}
        for s in sigs:
            elig = eligible_at(s)
            if len(elig) < MIN_CROSS:
                sig[s] = []
                continue
            w = wf_icir_weights(s)
            if w is None:
                sig[s] = []
                continue
            snap = snap_at(s)
            parts = []
            for k, f in enumerate(("droe", "roa")):
                vals = mats[f].loc[s].reindex(snap.index)
                parts.append(cs_rank_z(vals) * w[k])
            score = sum(parts).dropna().reindex(elig).dropna()
            if len(score) < MIN_CROSS:
                sig[s] = []
                continue
            inds = pd.Series([ind_map.get(c, "未知") for c in score.index], index=score.index)
            picks = []
            for _, sub in score.groupby(inds):
                n = max(1, int(round(len(sub) * top_pct)))
                picks.append(sub.nlargest(n))
            sig[s] = list(pd.concat(picks).index)
        return sig

    print("[4/6] 组合模拟（next-bar, qfq, 0.1% 成本）...")
    end_ts = prices.index[-1] if a.oos else pd.Timestamp(a.end)
    sig_t10 = signals(top_pct=0.10)
    sig_t50 = signals(topn=TOPN)
    sig_t50i = signals(topn=TOPN, max_per_ind=MAX_PER_IND)
    sig_quota = quota_signals(0.10)
    sig_ew = ew_signals()
    portfolios = {
        "等权≤30(原始价域)": run_engine(sig_ew, prices, 0.0, end_ts),
        "PIT_droe+roa_Top10%": run_engine(sig_t10, prices, COST, end_ts),
        "PIT_droe+roa_Top10%_行业中性": run_engine(sig_quota, prices, COST, end_ts),
        "PIT_droe+roa_Top50": run_engine(sig_t50, prices, COST, end_ts),
        "PIT_droe+roa_Top50_行业≤4": run_engine(sig_t50i, prices, COST, end_ts),
    }
    # 换手率
    for k, (nav_df, avg_to, avg_n) in portfolios.items():
        print(f"      {k:<26} 平均换手 {avg_to:.1%}/月 | 平均持仓 {avg_n:.0f} 只")
    stats = [fcw.perf_stats(v[0].nav, k) for k, v in portfolios.items()]
    print(f"\n      {'组合':<26} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'总收益':>9}")
    print(f"      {'─' * 62}")
    for st_ in stats:
        print(f"      {st_['label']:<26} {st_['cagr']:>+7.1%} {st_['sharpe']:>+8.3f} "
              f"{st_['mdd']:>+7.1%} {st_['total']:>+8.1%}")
    yr = pd.DataFrame({k: fcw.yearly_returns(v[0].nav) for k, v in portfolios.items()})
    print(f"\n      年度收益:")
    print(yr.round(4).to_string())

    # ── 4b. 成本敏感性（Top10%%）──
    sweep_rows = []
    if a.cost_sweep:
        print(f"\n[4b/6] 成本敏感性（PIT Top10%%）...")
        for c in (0.0, 0.0005, 0.001, 0.002, 0.003, 0.005):
            nav_df, avg_to, avg_n = run_engine(sig_t10, prices, c, end_ts)
            st_ = fcw.perf_stats(nav_df.nav, f"cost={c:.3%}")
            sweep_rows.append({**st_, "ann_turnover": round(avg_to * 12, 2)})
            print(f"      cost={c:.3%} CAGR={st_['cagr']:+.1%} Sharpe={st_['sharpe']:+.3f} "
                  f"总收益={st_['total']:+.1%} 年换手={avg_to*12:.1f}x")

    # ── 4c. 最近一期清单样本外验证 ──
    oos_row = {}
    if a.oos and len(sigs) >= 1:
        last_sig = sigs[-1]
        print(f"\n[4c/6] 最近一期清单样本外（信号 {last_sig.date()} → 8/14）...")
        for label, sig in (("Top10%", sig_t10), ("Top50", sig_t50), ("Top50_行业≤4", sig_t50i)):
            r = oos_last_pick(sig, prices, last_sig, end_ts)
            if r:
                oos_row[label] = r
                print(f"      {label:<14} 执行 {r['exec'].date()} | {r['n']} 只 | "
                      f"{r['days']} 日 | 累计 {r['cum_ret']:+.2%} | 日均 {r['daily_mean']:+.3%}")

    print("[5/6] 保存报告...")
    rpt = REPORT_DIR / f"FACTOR_PIT_RECHECK_{datetime.now().strftime('%Y%m%d')}.md"
    lines = [
        "# droe+roa PIT 口径重验（对齐仲裁，修正前视+价格域）",
        "",
        f"- 生成: {datetime.now().isoformat(timespec='seconds')}",
        f"- 区间: {a.start} ~ {a.end} | 信号=月末 | 成交=次bar | 成本 {COST:.1%}",
        f"- 因子: factor_pit.db（pit_avail_date）| 价格: qfq（收益）/ 原始close（≤30域）",
        "",
        "## 一、日度 IC（PIT，全域）",
        "",
        "| 因子 | IC | ICIR |",
        "|:--:|:--:|:--:|",
    ]
    for f in ("droe", "roa"):
        s = ics[f]
        lines.append(f"| {f} | {s.mean():+.4f} | {s.mean()/s.std():+.3f} |")
    lines += ["", "## 二、组合（next-bar, qfq）", "",
              "| 组合 | CAGR | 年化波动 | Sharpe | MDD | 总收益 |",
              "|:--|:--:|:--:|:--:|:--:|:--:|"]
    for st_ in stats:
        lines.append(f"| {st_['label']} | {st_['cagr']:+.1%} | {st_['ann_vol']:.1%} | "
                     f"{st_['sharpe']:+.3f} | {st_['mdd']:+.1%} | {st_['total']:+.1%} |")
    lines += ["", "## 三、年度收益", "", yr.round(4).to_markdown(), "",
              "## 对照与结论", "",
              "- 前视口径（report_date as-of + qfq≤30域, Top10%%）: 十年 +21.7%/Sharpe 0.922（作废）",
              "- 8/15 仲裁 PIT（Top50+行业≤4, 原始价域）: 2018-2026 +14.89%/Sharpe 0.577",
              ]
    if sweep_rows:
        lines += ["", "## 成本敏感性（PIT Top10%%）", "",
                  "| 单边成本 | CAGR | Sharpe | MDD | 总收益 | 年换手 |",
                  "|:--:|:--:|:--:|:--:|:--:|:--:|"]
        for r in sweep_rows:
            lines.append(f"| {r['label']} | {r['cagr']:+.1%} | {r['sharpe']:+.3f} | "
                         f"{r['mdd']:+.1%} | {r['total']:+.1%} | {r['ann_turnover']:.1f}x |")
    if oos_row:
        lines += ["", "## 最近一期清单样本外（至 8/14）", "",
                  "| 口径 | 执行日 | 持仓数 | 持有日数 | 累计收益 | 日均收益 |",
                  "|:--|:--:|:--:|:--:|:--:|:--:|"]
        for label, r in oos_row.items():
            lines.append(f"| {label} | {r['exec'].date()} | {r['n']} | {r['days']} | "
                         f"{r['cum_ret']:+.2%} | {r['daily_mean']:+.3%} |")
    lines += [f"- 耗时: {time.time()-t0:.0f}s"]
    rpt.write_text("\n".join(lines), encoding="utf-8")
    print(f"      ✅ {rpt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
