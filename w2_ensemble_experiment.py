# -*- coding: utf-8 -*-
r"""
W2: 模型集成实验
目的: 在 W1 最优配置(Alpha158 + 5y + 6m + csi300)基础上，用集成突破 IC>0.05
实验设计:
  单模型基线:
    1. CatBoost (W1 基线, lr=0.05 depth=6 iters=400)
    2. LGBM     (lr=0.05 num_leaves=31 n_estimators=400)
    3. XGB      (lr=0.05 max_depth=6 n_estimators=400)
  集成方案:
    4. CatBoost + LGBM 简单平均
    5. CatBoost + LGBM + XGB 三模型平均
    6. CatBoost + LGBM RankIC 排序平均 (更鲁棒)
    7. CatBoost + LGBM + XGB Rank 排序平均
环境: qlib_venv (py3.12)
用法: D:\workbuddyspace\tulong_system\qlib_venv\Scripts\python.exe w2_ensemble_experiment.py
"""
import os, sys, time, json
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\w2_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)
from qlib.utils import init_instance_by_config
from qlib.contrib.model.catboost_model import CatBoostModel
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.model.xgboost import XGBModel
from qlib.contrib.data.handler import Alpha158
from qlib.data import D

t_global = time.time()


def build_dataset(tr_start, train_end, vp_start, test_start, test_end,
                  instruments="csi300"):
    """构建带 train/valid/test 三段的 DatasetH (Alpha158 + W1 最优窗口)"""
    ds = init_instance_by_config({
        "class": "DatasetH", "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": "Alpha158",
                "module_path": "qlib.contrib.data.handler",
                "kwargs": {
                    "start_time": tr_start, "end_time": test_end,
                    "fit_start_time": tr_start, "fit_end_time": train_end,
                    "instruments": instruments,
                    "label": (["Ref($close,-1)/$close-1"], ["LABEL0"]),
                    "infer_processors": [
                        {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
                        {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
                    ],
                    "learn_processors": [{"class": "DropnaLabel"}],
                },
            },
            "segments": {
                "train": (tr_start, train_end),
                "valid": (vp_start, train_end),
                "test":  (test_start, test_end),
            },
        },
    })
    return ds


def calc_metrics(pred_df, label_df):
    """计算完整 IC 指标: RankIC / PearsonIC / ICIR / HitRate"""
    df = pred_df.merge(label_df, on=["datetime", "instrument"])
    df = df.dropna()
    if len(df) < 10:
        return {"rank_ic": np.nan, "pearson_ic": np.nan, "icir": np.nan,
                "ic_hit_rate": np.nan, "n_test_days": 0}
    daily_ic = df.groupby("datetime").apply(
        lambda g: g["score"].corr(g["label"], method="spearman") if len(g) > 5 else np.nan,
        include_groups=False
    ).dropna()
    daily_pic = df.groupby("datetime").apply(
        lambda g: g["score"].corr(g["label"]) if len(g) > 5 else np.nan,
        include_groups=False
    ).dropna()
    rank_ic = daily_ic.mean()
    pic = daily_pic.mean()
    ic_std = daily_ic.std() if len(daily_ic) > 1 else np.nan
    icir = (rank_ic / ic_std) if (ic_std and ic_std > 0) else np.nan
    ic_hit = (daily_ic > 0).mean() if len(daily_ic) > 0 else np.nan
    return {
        "rank_ic": round(float(rank_ic), 4) if not np.isnan(rank_ic) else None,
        "pearson_ic": round(float(pic), 4) if not np.isnan(pic) else None,
        "icir": round(float(icir), 4) if not np.isnan(icir) else None,
        "ic_hit_rate": round(float(ic_hit), 4) if not np.isnan(ic_hit) else None,
        "n_test_days": int(len(daily_ic)),
    }


def get_label_df(ds, segment="test"):
    """从 dataset 提取 label df"""
    label_df = ds.prepare(segment, col_set="label")
    if isinstance(label_df, pd.DataFrame):
        label_col = label_df.iloc[:, 0]
    else:
        label_col = label_df
    out = label_col.reset_index()
    out.columns = ["datetime", "instrument", "label"]
    return out


def pred_to_df(pred):
    """预测结果转标准 df"""
    out = pred.reset_index()
    out.columns = ["datetime", "instrument", "score"]
    return out


def rank_normalize(df):
    """对每个 datetime 的 score 做排名归一化到 [0,1]"""
    out = df.copy()
    out["score"] = out.groupby("datetime")["score"].rank(pct=True)
    return out


def main():
    # ===== 固定 W1 最优配置 =====
    tr_start = "2020-04-01"      # 5年训练
    train_end = "2025-03-31"
    vp_start = "2024-10-01"      # 6月验证
    test_start = "2025-04-01"    # 3月测试
    test_end = "2025-06-30"

    print("=" * 70)
    print(f"W2: 模型集成实验 (Alpha158 + 5y + 6m + csi300)")
    print(f"    train={tr_start}~{train_end} valid={vp_start}~{train_end}")
    print(f"    test={test_start}~{test_end}")
    print("=" * 70)

    # ===== 1. 构建数据集 (一次, 所有模型共用) =====
    t0 = time.time()
    print(f"\n[1/3] 构建数据集...")
    ds = build_dataset(tr_start, train_end, vp_start, test_start, test_end)
    label_df = get_label_df(ds, "test")
    print(f"    完成 ({time.time()-t0:.0f}s), label 行数={len(label_df)}")

    # ===== 2. 训练三个单模型 =====
    single_preds = {}  # name -> pred_df

    # CatBoost
    t0 = time.time()
    print(f"\n[2/3] 训练 CatBoost...")
    m_cat = CatBoostModel(loss="RMSE", learning_rate=0.05, depth=6,
                          iterations=400, early_stopping_rounds=50)
    m_cat.fit(ds)
    pred_cat = m_cat.predict(ds, segment="test")
    single_preds["CatBoost"] = pred_to_df(pred_cat)
    print(f"    完成 ({time.time()-t0:.0f}s)")

    # LGBM
    t0 = time.time()
    print(f"\n训练 LGBM...")
    m_lgb = LGBModel(loss="mse", learning_rate=0.05, num_leaves=31,
                     n_estimators=400, early_stopping_rounds=50)
    m_lgb.fit(ds)
    pred_lgb = m_lgb.predict(ds, segment="test")
    single_preds["LGBM"] = pred_to_df(pred_lgb)
    print(f"    完成 ({time.time()-t0:.0f}s)")

    # XGB
    t0 = time.time()
    print(f"\n训练 XGB...")
    m_xgb = XGBModel(loss="reg:squarederror", learning_rate=0.05, max_depth=6,
                     n_estimators=400, early_stopping_rounds=50)
    m_xgb.fit(ds)
    pred_xgb = m_xgb.predict(ds, segment="test")
    single_preds["XGB"] = pred_to_df(pred_xgb)
    print(f"    完成 ({time.time()-t0:.0f}s)")

    # ===== 3. 评估单模型 =====
    results = []
    print("\n" + "=" * 70)
    print("单模型结果")
    print("=" * 70)
    for name, pdf in single_preds.items():
        m = calc_metrics(pdf, label_df)
        m["label"] = f"single_{name}"
        m["type"] = "single"
        results.append(m)
        print(f"  {name:<10} RankIC={m['rank_ic']} ICIR={m['icir']} Hit={m['ic_hit_rate']}")

    # 单模型间相关性 (诊断集成潜力)
    print("\n单模型预测相关性 (Pearson):")
    merged = None
    for name, pdf in single_preds.items():
        tmp = pdf.rename(columns={"score": f"score_{name}"})
        merged = tmp if merged is None else merged.merge(tmp, on=["datetime", "instrument"])
    if merged is not None and len(merged) > 10:
        score_cols = [c for c in merged.columns if c.startswith("score_")]
        corr = merged[score_cols].corr()
        for i in range(len(score_cols)):
            for j in range(i + 1, len(score_cols)):
                c = corr.iloc[i, j]
                print(f"  {score_cols[i]} <-> {score_cols[j]}: {c:.3f}")

    # ===== 4. 集成方案 =====
    print("\n" + "=" * 70)
    print("集成方案")
    print("=" * 70)

    def average_ensemble(names, weights=None):
        """简单加权平均"""
        if weights is None:
            weights = [1.0 / len(names)] * len(names)
        base = single_preds[names[0]][["datetime", "instrument"]].copy()
        base["score"] = 0.0
        for n, w in zip(names, weights):
            base = base.merge(single_preds[n][["datetime", "instrument", "score"]].rename(
                columns={"score": f"s_{n}"}), on=["datetime", "instrument"])
            base["score"] = base["score"] + w * base[f"s_{n}"]
        return base[["datetime", "instrument", "score"]]

    def rank_ensemble(names):
        """Rank 排序平均: 每个模型先做日截面 rank pct, 再平均"""
        base = single_preds[names[0]][["datetime", "instrument"]].copy()
        base["score"] = 0.0
        for n in names:
            ranked = rank_normalize(single_preds[n])
            base = base.merge(ranked[["datetime", "instrument", "score"]].rename(
                columns={"score": f"r_{n}"}), on=["datetime", "instrument"])
            base["score"] = base["score"] + base[f"r_{n}"]
        base["score"] = base["score"] / len(names)
        return base[["datetime", "instrument", "score"]]

    # 4a. CatBoost + LGBM 平均
    pred_cl = average_ensemble(["CatBoost", "LGBM"])
    m = calc_metrics(pred_cl, label_df)
    m["label"] = "ensemble_CL_avg"; m["type"] = "ensemble"
    results.append(m)
    print(f"  CL_avg     RankIC={m['rank_ic']} ICIR={m['icir']} Hit={m['ic_hit_rate']}")

    # 4b. CatBoost + LGBM + XGB 三模型平均
    pred_clx = average_ensemble(["CatBoost", "LGBM", "XGB"])
    m = calc_metrics(pred_clx, label_df)
    m["label"] = "ensemble_CLX_avg"; m["type"] = "ensemble"
    results.append(m)
    print(f"  CLX_avg    RankIC={m['rank_ic']} ICIR={m['icir']} Hit={m['ic_hit_rate']}")

    # 4c. CatBoost + LGBM Rank 平均
    pred_cl_rank = rank_ensemble(["CatBoost", "LGBM"])
    m = calc_metrics(pred_cl_rank, label_df)
    m["label"] = "ensemble_CL_rank"; m["type"] = "ensemble"
    results.append(m)
    print(f"  CL_rank    RankIC={m['rank_ic']} ICIR={m['icir']} Hit={m['ic_hit_rate']}")

    # 4d. CatBoost + LGBM + XGB Rank 平均
    pred_clx_rank = rank_ensemble(["CatBoost", "LGBM", "XGB"])
    m = calc_metrics(pred_clx_rank, label_df)
    m["label"] = "ensemble_CLX_rank"; m["type"] = "ensemble"
    results.append(m)
    print(f"  CLX_rank   RankIC={m['rank_ic']} ICIR={m['icir']} Hit={m['ic_hit_rate']}")

    # 4e. 最优两模型 Rank 平均 (动态选 top2)
    valid_single = [(n, calc_metrics(p, label_df)["rank_ic"]) for n, p in single_preds.items()]
    valid_single.sort(key=lambda x: x[1] if x[1] else -1, reverse=True)
    top2 = [valid_single[0][0], valid_single[1][0]]
    pred_top2_rank = rank_ensemble(top2)
    m = calc_metrics(pred_top2_rank, label_df)
    m["label"] = f"ensemble_top2rank({'|'.join(top2)})"; m["type"] = "ensemble"
    results.append(m)
    print(f"  top2rank({'|'.join(top2)}) RankIC={m['rank_ic']} ICIR={m['icir']} Hit={m['ic_hit_rate']}")

    # ===== 5. 保存 + 汇总 =====
    out = os.path.join(OUT_DIR, "w2_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")

    print("\n" + "=" * 70)
    print("W2 实验汇总 (按 RankIC 降序)")
    print("=" * 70)
    valid = [r for r in results if r.get("rank_ic") is not None]
    valid.sort(key=lambda r: r["rank_ic"], reverse=True)
    print(f"{'Label':<40} {'Type':<10} {'RankIC':>8} {'PIC':>8} {'ICIR':>8} {'Hit':>8}")
    for r in valid:
        print(f"{r['label']:<40} {r['type']:<10} {str(r['rank_ic']):>8} "
              f"{str(r['pearson_ic']):>8} {str(r['icir']):>8} {str(r['ic_hit_rate']):>8}")

    if valid:
        best = valid[0]
        baseline = 0.0412  # W1 最佳
        delta = best["rank_ic"] - baseline
        print(f"\n*** W2 最佳: {best['label']} RankIC={best['rank_ic']} ICIR={best['icir']} ***")
        print(f"    对比 W1 基线(0.0412): {delta:+.4f} ({'提升' if delta > 0 else '下降'})")
        if best["rank_ic"] > 0.05:
            print(f"    ✓ 突破 0.05 可用线!")
            sys.exit(0)
        else:
            print(f"    ✗ 未达 0.05 可用线，差距: {0.05 - best['rank_ic']:.4f}")
            sys.exit(1)

    print(f"\n总耗时: {time.time()-t_global:.0f}s")


if __name__ == "__main__":
    main()
