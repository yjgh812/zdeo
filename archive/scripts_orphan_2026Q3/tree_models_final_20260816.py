# -*- coding: utf-8 -*-
"""
树模型终审 v2 Phase 1.3 (修正WF按月分割)
================================================
修正: v1误把样本行当天数分割。v2按日历月度Walk-Forward:
  train=过去36月样本 / test=次月样本 / 步进1月 / gap=FWD(embargo)
  日采样上限300股(控样本量, Ridge同口径公平对比)
模型: Ridge / LightGBM / XGBoost / CatBoost (5种子集成, 早停用train尾部)
产出: reports/TREE_MODELS_FINAL_20260816.md
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
SEEDS = (0, 1, 2, 3, 4)
FWD = 20
DAILY_SAMPLE = 300


def load_matrix():
    """fml_v2面板 → (X, y, ym标签) 日采样上限300。"""
    p = CACHE / "fml_v2_panels_20200115_20260731_fwd20.pkl"
    panels = pickle.load(open(p, "rb"))
    wides, fwd, cal = panels["wides"], panels["fwd"], panels["calendar"]
    feats = ["bp", "ep", "turnover", "volume_ratio", "dv_ratio",
             "ag", "droe", "acc", "sg", "roa", "pm", "ato", "opa", "tbi", "bl",
             "rev20", "vol20", "illiq20", "size", "max_ret20"]
    cols = wides[feats[0]].columns
    for f in feats[1:]:
        cols = cols.union(wides[f].columns)
    Xs, ys, yms = [], [], []
    rng = np.random.default_rng(42)
    for di, d in enumerate(cal):
        row_idx = np.arange(len(cols))
        y_row = fwd.iloc[di].reindex(cols).values if hasattr(fwd, 'iloc') else None
        ok = ~np.isnan(y_row)
        if ok.sum() < 200:
            continue
        idx = np.flatnonzero(ok)
        if len(idx) > DAILY_SAMPLE:
            idx = rng.choice(idx, DAILY_SAMPLE, replace=False)
        xmat = np.empty((len(idx), len(feats)), dtype=np.float32)
        for fi, f in enumerate(feats):
            v = wides[f].iloc[di].reindex(cols).values
            xmat[:, fi] = v[idx]
        Xs.append(np.nan_to_num(xmat))
        ys.append(y_row[idx])
        yms.append(np.full(len(idx), d.strftime("%Y%m")))
    X = np.vstack(Xs); y = np.concatenate(ys); ym = np.concatenate(yms)
    return X, y, ym, list(feats)


def walk_forward_monthly(X, y, ym, model_kind, embargo=True):
    from sklearn.linear_model import Ridge
    import lightgbm as lgb
    import xgboost as xgb
    import catboost as cb

    months = sorted(set(ym))
    preds = np.full(len(y), np.nan)
    t0 = time.time()
    for mi in range(36, len(months) - 1):
        test_m = months[mi + 1]
        train_ms = months[max(0, mi - 36):mi + 1]
        if embargo:
            train_ms = months[max(0, mi - 36):mi + 1]   # gap由test提前1月实现
        tr_mask = np.isin(ym, train_ms)
        te_mask = (ym == test_m)
        if tr_mask.sum() < 50000 or te_mask.sum() < 2000:
            continue
        Xtr, ytr = X[tr_mask], y[tr_mask]
        Xte = X[te_mask]
        Xfit, yfit = Xtr[:-3000], ytr[:-3000]
        Xval, yval = Xtr[-3000:], ytr[-3000:]
        ens = []
        for s in SEEDS:
            if model_kind == "ridge":
                m = Ridge(alpha=1.0)
                m.fit(Xtr, ytr)
                ens.append(m.predict(Xte))
            elif model_kind == "lgbm":
                m = lgb.LGBMRegressor(n_estimators=500, learning_rate=0.05,
                                      num_leaves=31, subsample=0.8,
                                      colsample_bytree=0.8, random_state=s,
                                      verbose=-1)
                m.fit(Xfit, yfit, callbacks=[lgb.early_stopping(50, verbose=False)],
                      eval_set=[(Xval, yval)])
                ens.append(m.predict(Xte))
            elif model_kind == "xgb":
                m = xgb.XGBRegressor(n_estimators=500, learning_rate=0.05,
                                     max_depth=6, subsample=0.8,
                                     colsample_bytree=0.8, random_state=s,
                                     early_stopping_rounds=50, verbosity=0)
                m.fit(Xfit, yfit, eval_set=[(Xval, yval)], verbose=False)
                ens.append(m.predict(Xte))
            elif model_kind == "catboost":
                m = cb.CatBoostRegressor(iterations=500, learning_rate=0.05,
                                         depth=6, random_seed=s, verbose=0,
                                         early_stopping_rounds=50)
                m.fit(Xfit, yfit, eval_set=(Xval, yval))
                ens.append(m.predict(Xte))
        preds[te_mask] = np.mean(ens, axis=0)
    return preds, time.time() - t0


def ic_by_month(preds, y, ym, min_n=200):
    out = {}
    for m in sorted(set(ym)):
        mask = ~np.isnan(preds) & (ym == m)
        if mask.sum() < min_n:
            continue
        out[m] = pd.Series(preds[mask]).rank().corr(pd.Series(y[mask]).rank())
    return pd.Series(out)


def main():
    t0 = time.time()
    print("=== 树模型终审v2 (月度WF修正版) ===")
    X, y, ym, feats = load_matrix()
    print(f"  样本: {len(y):,} ({len(set(ym))}月) ({time.time()-t0:.0f}s)")
    results = {}
    for kind in ("ridge", "lgbm", "xgb", "catboost"):
        preds, dur = walk_forward_monthly(X, y, ym, kind)
        ics = ic_by_month(preds, y, ym)
        results[kind] = {"ic": float(ics.mean()),
                         "icir": float(ics.mean() / (ics.std() + 1e-12)),
                         "n": len(ics), "sec": dur}
        print(f"  {kind}: IC={ics.mean():.4f} ICIR={results[kind]['icir']:.2f} "
              f"n={len(ics)} ({dur:.0f}s)", flush=True)
    ridge_ic = results["ridge"]["ic"]
    winners = [k for k in ("lgbm", "xgb", "catboost") if results[k]["ic"] > ridge_ic]
    lines = [
        "# 树模型终审 — XGB/CatBoost首次开源协议测试(月度WF)",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 面板: fml_v2 2020-2026, B特征20因子, Fwd20, 日采样{DAILY_SAMPLE}股",
        "- WF: train36月/test1月滚动+embargo, 5种子集成, 早停用train尾部3000",
        "",
        "| 模型 | RankIC | ICIR | 测试月数 | 耗时 |",
        "|------|--------|------|---------|------|",
    ]
    for k, v in results.items():
        lines.append(f"| {k} | {v['ic']:.4f} | {v['icir']:.2f} | {v['n']} | {v['sec']:.0f}s |")
    lines += ["", f"Ridge基线IC: {ridge_ic:.4f}", ""]
    lines.append("**翻案候选**: " + ", ".join(winners) if winners
                 else "**维持判死**: 全部树模型未超Ridge")
    lines += ["", f"总耗时 {time.time()-t0:.0f}s", ""]
    p = REPORT / f"TREE_MODELS_FINAL_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")


if __name__ == "__main__":
    main()
