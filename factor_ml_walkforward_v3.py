#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_ml_walkforward_v3.py — ML 因子管线 v3：财务特征 PIT 化
================================================================================
背景（8/15 前视危机铁律）: v2 的 A/B 特征集含 10 个财务因子，复用 afd loader
（report_date 期末日前向填充，最多约4个月 as-of 前视）——该 loader 已禁用，
回测必走 factor_pit.db。v3 把财务块整体替换为 PIT 通道，得到全无前视的 ML 终版。

特征集:
    D = PIT9 [droe, roa, roe, lev, ato, pm, ep=eps, bp=close/bps, sp=close/sps]
        + daily_basic4 [turnover, volume_ratio, dv_ratio, size]（当日快照，无前视）
        + 量价4 [rev20, vol20, illiq20, max_ret20]（qfq滚动，无前视）  = 17 特征
    E = daily_basic4 + 量价4（零财务对照组）                         =  8 特征

PIT 语义（与 factor_combo_walkforward.load_pit_panels 一致，自测无前视）:
    - 每股一期: pit_avail_date ≤ T 的最新 report_date；同日双报取最新 report_date
    - 报告期距 T 超过 400 日历日 → 过旧剔除（max_stale_days）
    - bp/sp 用当日原始 close（与 ≤30 过滤同源）/ bps/sps；ep=eps（生产口径，rank-z 下无标度问题）

其余全部复用 v2 基础设施（build_panels / AlignedFeatures / walk_forward_ml_v2 /
simulate_ml_portfolio / factor_ic_series），折结构/清洗/回测口径与 v2 完全一致，
结果可与 v2 报告表直接对照（B=污染上界 → D=PIT 真实成色）。

用法:
    python factor_ml_walkforward_v3.py --featureset D E --model ridge            # EP 默认开
    python factor_ml_walkforward_v3.py --featureset D --model ridge --no-embargo --no-purge
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import factor_ml_walkforward_v2 as f2  # noqa: E402
import factor_combo_walkforward as fcw  # noqa: E402

PIT_DB = r"D:\zcodesj\factor_pit.db"
RESULTS_DIR = f2.REPORT_DIR / "fml_v3"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CLEAN_DB = ["turnover", "volume_ratio", "dv_ratio", "size"]          # daily_basic 当日快照
CLEAN_VOL = ["rev20", "vol20", "illiq20", "max_ret20"]               # qfq 滚动量价
PIT_DIRECT = ["droe", "roa", "roe", "lev", "ato", "pm"]              # 纯比率，直接承载
FEATURESETS = {
    "D": PIT_DIRECT + ["ep", "bp", "sp"] + CLEAN_DB + CLEAN_VOL,     # 17，PIT 全清洁
    "E": CLEAN_DB + CLEAN_VOL,                                       # 8，零财务对照
}


def load_pit_wides(dates_index: pd.DatetimeIndex, raw_close: pd.DataFrame,
                   max_stale_days: int = 400, verbose: bool = True) -> dict[str, pd.DataFrame]:
    """factor_pit.db → 逐日 as-of PIT 因子截面z宽表（date × code）。

    实现 = fcw.load_pit_panels 的宽表化：pivot(pit_avail_date) ∪ 交易日历 → ffill
    → reindex 日历；stale 掩码按 report_date 距 T 的日历日数 > max_stale_days 剔除。
    """
    t0 = time.time()
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=60)
    # [2026-08-25] PIT v1→v2 迁移对齐：v1 表已改名 _v1_deprecated，生产口径切 v2
    df = pd.read_sql_query(
        "SELECT code, report_date, pit_avail_date, droe, roa, roe, lev, ato, pm, "
        "eps, bps, sps FROM stock_factors_fundamental_pit_v2", con)
    con.close()
    df["code"] = df["code"].astype(str).str[-6:]
    for c in ("report_date", "pit_avail_date"):
        df[c] = pd.to_datetime(df[c].astype(str), format="%Y%m%d")
    df = (df.sort_values(["code", "pit_avail_date", "report_date"])
            .drop_duplicates(["code", "pit_avail_date"], keep="last"))

    rd = df.pivot_table(index="pit_avail_date", columns="code",
                        values="report_date", aggfunc="last", dropna=False)
    idx = rd.index.union(dates_index).sort_values()
    rd_c = rd.reindex(idx).ffill().reindex(dates_index)
    stale = (dates_index.to_numpy()[:, None] - rd_c.to_numpy())
    stale = (stale / np.timedelta64(1, "D")).astype(float)
    fresh = stale <= max_stale_days

    def _carry(col: str) -> pd.DataFrame:
        ev = df.pivot_table(index="pit_avail_date", columns="code", values=col,
                            aggfunc="last", dropna=False)
        # dropna=False 下全NaN列仍可能与 rd 列集有差 → 显式对齐列再套 stale 掩码
        ev = ev.reindex(columns=rd.columns)
        return ev.reindex(idx).ffill().reindex(dates_index).where(fresh)

    wides: dict[str, pd.DataFrame] = {}
    for c in PIT_DIRECT:
        wides[c] = f2._z_wide(_carry(c))
    eps_p, bps_p, sps_p = _carry("eps"), _carry("bps"), _carry("sps")
    wides["ep"] = f2._z_wide(eps_p)                       # 生产口径 ep = EPS
    wides["bp"] = f2._z_wide(bps_p / raw_close)           # 当日BP = 每股净资产/价 [2026-08-22修复: 原 raw_close/bps 反向]
    wides["sp"] = f2._z_wide(sps_p / raw_close)           # 当日PS倒数 = 每股收入/价 [2026-08-22修复: 原 raw_close/sps 反向]
    if verbose:
        cov = int(fresh.sum())
        print(f"      PIT 9 因子宽表完成: {len(wides)} 个, fresh格={cov:,}, "
              f"pit行={len(df):,} ({time.time()-t0:.0f}s)", flush=True)
    return wides


def main() -> int:
    ap = argparse.ArgumentParser(description="ML 因子管线 v3: 财务特征 PIT 化（全无前视）")
    ap.add_argument("--start", default="20200115")
    ap.add_argument("--end", default="20260731")
    ap.add_argument("--featureset", nargs="+", default=["D"], choices=["D", "E"])
    ap.add_argument("--model", nargs="+", default=["ridge"], choices=["ridge"])
    ap.add_argument("--embargo", dest="embargo", action="store_true", default=True)
    ap.add_argument("--no-embargo", dest="embargo", action="store_false")
    ap.add_argument("--purge", dest="purge", action="store_true", default=True)
    ap.add_argument("--no-purge", dest="purge", action="store_false")
    ap.add_argument("--embargo-days", type=int, default=21)
    ap.add_argument("--train", type=int, default=500)
    ap.add_argument("--test", type=int, default=60)
    ap.add_argument("--fwd", type=int, default=20)
    ap.add_argument("--cost", type=float, default=0.001)
    ap.add_argument("--max-train-samples", type=int, default=600_000)
    ap.add_argument("--warmup", type=int, default=90)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    t0 = time.time()
    print(f"\n{'#' * 78}")
    print(f"  ML 因子管线 v3（PIT化）| 特征集 {a.featureset} | 模型 {a.model}")
    print(f"  区间 {a.start}~{a.end} | train={a.train}d test={a.test}d fwd={a.fwd}d | "
          f"purge={'ON' if a.purge else 'OFF'} embargo={'ON' if a.embargo else 'OFF'}({a.embargo_days}d)")
    print(f"  财务通道: factor_pit.db（pit_avail_date as-of, max_stale=400d）——afd loader 禁用")
    print(f"{'#' * 78}", flush=True)

    # ── 1. 面板（复用 v2 缓存；只用其中干净特征）──
    print("\n[1/5] 构建面板（v2 缓存复用 + PIT 9 因子）...")
    panels = f2.build_panels(a.start, a.end, a.fwd, warmup=a.warmup)
    cal = panels["calendar"]
    fwd_w, ret_bt, raw_close = panels["fwd"], panels["ret_bt"], panels["raw_close"]
    wides = {k: panels["wides"][k] for k in CLEAN_DB + CLEAN_VOL}   # 剔除财务×10与daily_basic bp/ep
    wides.update(load_pit_wides(cal, raw_close))
    print(f"      清洁日历 {len(cal)} 天（{cal[0].date()} ~ {cal[-1].date()}），污染日已剔除 "
          f"{len(panels['polluted'])} 天 | 特征 {sum(len(v) for v in [CLEAN_DB, CLEAN_VOL]) + 9} 个")
    st_codes = fcw.load_st_codes()
    print(f"      ST {len(st_codes)} 只（剔除）")

    # ── 2. 基准 ──
    print("\n[2/5] 基准组合（等权≤30元，next-bar）...")
    reb_pos = [cal.searchsorted(d) for d in
               fcw.month_starts(cal, from_date=cal[min(a.train, len(cal) - 1)])]
    reb_pos = [p for p in reb_pos if p < len(cal)]
    ew_nav, ew_month = f2.simulate_ew(ret_bt, raw_close, st_codes, reb_pos)
    ew_stats = fcw.perf_stats(ew_nav.nav, "等权基准≤30元")
    print(f"      {len(reb_pos)} 个调仓月 | 等权: CAGR={ew_stats['cagr']:+.1%} "
          f"Sharpe={ew_stats['sharpe']:+.3f} MDD={ew_stats['mdd']:+.1%}")

    # ── 3~4. WF + 回测 ──
    all_results = {}
    for fs in a.featureset:
        features = FEATURESETS[fs]
        aligned = f2.AlignedFeatures(wides, features, cal)
        for model_kind in a.model:
            tag = f"{fs}_{model_kind}_{'E' if a.embargo else 'nE'}{'P' if a.purge else 'nP'}_{a.start}_{a.end}"
            print(f"\n[3/5] === 特征集 {fs}（{len(features)}特征, PIT清洁）× {model_kind} ===")
            t1 = time.time()
            wf = f2.walk_forward_ml_v2(aligned, fwd_w, model_kind, train_d=a.train,
                                       test_d=a.test, fwd_days=a.fwd, use_purge=a.purge,
                                       use_embargo=a.embargo, embargo_days=a.embargo_days,
                                       max_train_samples=a.max_train_samples, seed=a.seed)
            wf_runtime = time.time() - t1

            print(f"[4/5] 组合回测 [{tag}] ...", flush=True)
            nav, month_rets = f2.simulate_ml_portfolio(wf["folds"], aligned, ret_bt, raw_close,
                                                       st_codes, reb_pos, cost=a.cost)
            ml_stats = fcw.perf_stats(nav.nav, f"ML_{model_kind}_{fs}")
            k = min(len(month_rets), len(ew_month))
            excess_ann = float((month_rets[:k] - ew_month[:k]).mean() * 12) if k > 6 else None
            top10_monthly = {
                "mean": round(float(np.mean(month_rets)), 4),
                "median": round(float(np.median(month_rets)), 4),
                "pos_rate": round(float((month_rets > 0).mean()), 4),
                "n_month": int(len(month_rets)),
                "excess_ann": round(excess_ann, 4) if excess_ann is not None else None,
            }

            oos_dates = set(pd.DatetimeIndex(wf["oos_dates"]))
            single_ic = {}
            for name in features:
                ic_series = f2.factor_ic_series(name, wides, fwd_w)
                arr = ic_series[ic_series.index.isin(oos_dates)].dropna().values
                if len(arr):
                    single_ic[name] = {"ic": round(float(arr.mean()), 4),
                                       "icir": round(float(arr.mean() / arr.std()), 3),
                                       "pos_rate": round(float((arr > 0).mean()), 4),
                                       "n": int(len(arr))}

            yr = fcw.yearly_returns(nav.nav)
            res = {
                "tag": tag, "featureset": fs, "model": model_kind, "pipeline": "v3-pit",
                "embargo": bool(a.embargo), "purge": bool(a.purge), "embargo_days": a.embargo_days,
                "start": a.start, "end": a.end, "n_features": len(features), "features": features,
                "wf": wf["summary"], "wf_runtime_s": round(wf_runtime, 1),
                "portfolio": {kk: ml_stats[kk] for kk in ("cagr", "ann_vol", "sharpe", "mdd", "total")},
                "top10_monthly": top10_monthly,
                "benchmarks": {"等权基准≤30元": {kk: vv for kk, vv in ew_stats.items() if kk != "label"}},
                "single_factor_oos": single_ic,
                "feature_importance": wf["importance"],
                "fold_meta": wf["fold_meta"],
                "yearly_returns": {str(kk): round(float(vv), 4) for kk, vv in yr.items()},
                "total_runtime_s": round(time.time() - t1, 1),
                "cleaning": {"polluted_days_dropped": panels["polluted"], "ret_clip": 0.21,
                             "price_source": "dragon_quant_qfq.stock_daily_meta_qfq",
                             "finance_channel": "factor_pit.db(pit_avail_date as-of, max_stale=400d)"},
                "generated": datetime.now().isoformat(timespec="seconds"),
            }
            all_results[tag] = res
            out_json = RESULTS_DIR / f"{tag}.json"
            out_json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            pd.to_pickle(nav.nav, RESULTS_DIR / f"{tag}_nav.pkl")

            s_ = wf["summary"]
            print(f"      OOS IC={s_['mean_ic']:+.4f} ICIR={s_['icir']:+.3f} 正率={s_['pos_rate']:.0%} "
                  f"天数={s_['n_days']} 折={s_['n_folds']}")
            print(f"      组合 CAGR={ml_stats['cagr']:+.1%} Sharpe={ml_stats['sharpe']:+.3f} "
                  f"MDD={ml_stats['mdd']:+.1%} 总={ml_stats['total']:+.1%} "
                  f"多头月均={top10_monthly['mean']:+.2%} 年化超额={top10_monthly['excess_ann']:+.1%}"
                  if top10_monthly["excess_ann"] is not None else "")
            print(f"      JSON: {out_json.name} | 本run耗时 {res['total_runtime_s']}s", flush=True)

    print(f"\n[5/5] 汇总（本次进程 {len(all_results)} 个 run）")
    print(f"      {'run':<46} {'OOS IC':>8} {'ICIR':>7} {'正率':>5} {'CAGR':>7} {'Sharpe':>7} {'MDD':>7}")
    print(f"      {'-' * 92}")
    for tag, r in all_results.items():
        w, p = r["wf"], r["portfolio"]
        if w["mean_ic"] is None:
            continue
        print(f"      {tag:<46} {w['mean_ic']:>+8.4f} {w['icir']:>+7.3f} "
              f"{w['pos_rate']:>4.0%} {p['cagr']:>+7.1%} {p['sharpe']:>+7.3f} {p['mdd']:>+7.1%}")
    print(f"\n  总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
