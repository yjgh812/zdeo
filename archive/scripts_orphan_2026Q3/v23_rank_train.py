# -*- coding: utf-8 -*-
"""v23_rank_train.py — LGBMRanker 排序学习冒烟训练（R-20260828-03 协议 step1）

目的: 验证「排序学习目标相对回归目标在屠龙 Alpha158 因子库上的 IC 增量」假设方向。
干净对照: 同一 Alpha158 特征 + 同一 20 日前瞻收益标签 + 同一训练/验证/测试段，
仅目标函数不同（lambdarank vs mse 回归），1 seed 冒烟。

口径(对齐 workflow_lgbm_alpha158_tulong.yaml 基线):
  - 样本: csi300；特征 Alpha158；label=未来20交易日收益
  - train 2017-01-01~2021-12-31 / valid 2022-01-01~2023-06-30 / test 2023-07-01~2026-08-14
  - group = 按交易日分组(截面样本即 query)；lambdarank 组内按 label 排序

输出:
  qlib_exp/v23_rank/v23_rank_model_seed1.joblib
  qlib_exp/v23_rank/v23_rank_scores_test.csv
  reports/v23_rank_smoke.json + .md

只读研究件; 不写生产库, 不改生产信号。用法: python v23_rank_train.py [--seed 1]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

QLIB_URI = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\qlib_exp\v23_rank"
REPORT_DIR = r"D:\zcodesj\reports"

TRAIN = ("2017-01-01", "2021-12-31")
VALID = ("2022-01-01", "2023-06-30")
TEST_START = "2023-07-01"
END_TIME = "2026-08-14"
INSTRUMENTS = "csi300"
LABEL = ["Ref($close, -21) / Ref($close, -1) - 1"]  # 未来20交易日收益


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def build_dataset():
    import qlib
    import qlib.config as _C
    # 本机常驻进程多, joblib 多进程 IPC 返回大 DF 偶发 OSError(22); 单进程加载特征(慢但稳)
    _C.kernels = 1
    qlib.init(provider_uri=QLIB_URI, region="cn")
    from qlib.contrib.data.handler import Alpha158
    from qlib.data.dataset import DatasetH, DataHandlerLP

    handler = Alpha158(
        instruments=INSTRUMENTS,
        start_time="2016-06-01",
        end_time=END_TIME,
        fit_start_time=TRAIN[0],
        fit_end_time=TRAIN[1],
        infer_processors=[
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        learn_processors=[
            {"class": "DropnaLabel"},
        ],
        label=LABEL,
    )
    return DatasetH(handler, segments={
        "train": TRAIN, "valid": VALID, "test": [TEST_START, END_TIME]})


def _to_np(df: pd.DataFrame):
    """df: MultiIndex(datetime, instrument) + feature/label → X,y,group,order(行→日期idx)"""
    feat = df["feature"]
    lab = df["label"]
    x = feat.values.astype(np.float32)
    y = lab.values.ravel().astype(np.float32)
    dates = df.index.get_level_values(0)
    uniq = dates.unique().sort_values()  # 日期升序唯一
    order = dates.map({d: i for i, d in enumerate(uniq)}).values.astype(np.int32)
    group = np.array([int((dates == d).sum()) for d in uniq], dtype=np.int32)
    return x, y, group, order, uniq.tolist()


def _grade_rank(y: np.ndarray, order: np.ndarray, n_groups: int) -> np.ndarray:
    """组内(按交易日)截面 rank → 整数等级 0~9（lambdarank 需整数 relevance 标签）"""
    from scipy.stats import rankdata
    g = np.zeros(len(y), dtype=np.int32)
    for k in range(n_groups):
        idx = np.where(order == k)[0]
        if len(idx) >= 2:
            pct = rankdata(y[idx]) / len(idx)
            g[idx] = np.clip((pct * 10).astype(np.int32), 0, 9)
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--n-est", type=int, default=400, help="冒烟: 基学习器数(全量可提) ")
    ap.add_argument("--lr", type=float, default=0.05)
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    t0 = time.time()
    log("构建 Alpha158 dataset (csi300, label=20日前瞻)...")
    ds = build_dataset()
    log(f"  dataset 就绪 ({time.time()-t0:.0f}s)")

    t0 = time.time()
    log("prepare train/valid/test...")
    tr = ds.prepare("train", col_set=["feature", "label"], data_key="learn")
    va = ds.prepare("valid", col_set=["feature", "label"], data_key="learn")
    te = ds.prepare("test", col_set=["feature", "label"], data_key="learn")
    Xt, yt, gt, ot, _ = _to_np(tr)
    Xv, yv, gv, ov, _ = _to_np(va)
    Xe, ye, ge, _, _ = _to_np(te)
    log(f"  train={Xt.shape} valid={Xv.shape} test={Xe.shape} ({time.time()-t0:.0f}s)")

    import lightgbm as lgb

    # ---- Rank 模型 (lambdarank; 标签=组内截面rank整数等级0-9) ----
    log("训练 LGBMRanker (lambdarank, 标签=组内截面rank等级)...")
    yt_g = _grade_rank(yt, ot, len(gt))
    yv_g = _grade_rank(yv, ov, len(gv))
    rk = lgb.LGBMRanker(
        objective="lambdarank", metric="ndcg", n_estimators=a.n_est,
        learning_rate=a.lr, num_leaves=210, max_depth=8,
        colsample_bytree=0.88, subsample=0.88, reg_alpha=205.7, reg_lambda=580.98,
        num_threads=12, random_state=a.seed, verbosity=-1)
    rk.fit(Xt, yt_g, group=gt,
           eval_set=[(Xv, yv_g)], eval_group=[gv],
           callbacks=[lgb.early_stopping(50, verbose=False)])
    log(f"  rank best_iter={rk.best_iteration_}")

    # ---- Reg 模型 (mse, 同数据同标签 = 干净对照) ----
    log("训练 LGBMRegressor (mse, 同数据同标签对照)...")
    rg = lgb.LGBMRegressor(
        objective="mse", n_estimators=a.n_est, learning_rate=a.lr,
        num_leaves=210, max_depth=8, colsample_bytree=0.88, subsample=0.88,
        reg_alpha=205.7, reg_lambda=580.98, num_threads=12,
        random_state=a.seed, verbosity=-1)
    rg.fit(Xt, yt, eval_set=[(Xv, yv)],
           callbacks=[lgb.early_stopping(50, verbose=False)])
    log(f"  reg best_iter={rg.best_iteration_}")

    # ---- Test 段 IC / Rank IC（按日截面 Spearman vs 20日前瞻收益）----
    log("test 段 IC 对拍...")
    s_rk = rk.predict(Xe)
    s_rg = rg.predict(Xe)
    out = pd.DataFrame({"date": te.index.get_level_values(0), "instrument": te.index.get_level_values(1),
                        "label": ye, "s_rank": s_rk, "s_reg": s_rg})
    res = {}
    for tag, col in (("rank", "s_rank"), ("reg", "s_reg")):
        ics = []
        for d, g in out.groupby("date"):
            if len(g) >= 5 and g["label"].std() > 0 and g[col].std() > 0:
                ics.append(g[col].corr(g["label"], method="spearman"))
        ics = np.array(ics)
        res[tag] = {"n_days": len(ics), "mean_rank_ic": round(float(ics.mean()), 4),
                    "pos_days_pct": round(100.0 * (ics > 0).mean(), 1)}
        log(f"  [{tag}] 截面Spearman(vs 20日前瞻): 日均={res[tag]['mean_rank_ic']:.4f} "
            f"正日占比={res[tag]['pos_days_pct']}%  n={len(ics)}")
    # rank vs reg 直接相关系数
    res["rank_vs_reg_corr"] = round(float(pd.Series(s_rk).corr(pd.Series(s_rg))), 4)
    res["meta"] = {"seed": a.seed, "n_estimators": a.n_est, "universe": INSTRUMENTS,
                   "label": "20日前瞻收益", "test": f"{TEST_START}~{END_TIME}",
                   "train_days": int(tr.index.get_level_values(0).nunique()),
                   "runtime_s": round(time.time() - t0, 0)}

    if not a.no_save:
        import joblib
        joblib.dump(rk, os.path.join(OUT_DIR, f"v23_rank_model_seed{a.seed}.joblib"))
        out.to_csv(os.path.join(OUT_DIR, f"v23_rank_scores_test.csv"), index=False)
        log(f"  saved -> {OUT_DIR}")

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "v23_rank_smoke.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    log(f"saved -> reports/v23_rank_smoke.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
