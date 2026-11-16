# -*- coding: utf-8 -*-
"""
W5: 特征选择实验 (减法方向)
推理: W3/W4 加法全部有害 -> 158个高度相关冗余特征可能过拟合
设计: 全特征训练 -> 特征重要性 -> top-K 子集重训
  K = 158(全) / 80 / 50 / 30
对比: 是否 K < 158 时 IC 更高
"""
import os, sys, time, json
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np
from catboost import CatBoostRegressor

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\w5_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)

from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset import DatasetH

t_global = time.time()


def build_ds(tr_start, train_end, vp_start, test_start, test_end,
             instruments="csi300"):
    handler = Alpha158(
        instruments=instruments,
        start_time=tr_start, end_time=test_end,
        fit_start_time=tr_start, fit_end_time=train_end,
        label=(["Ref($close,-1)/$close-1"], ["LABEL0"]),
        infer_processors=[
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        learn_processors=[{"class": "DropnaLabel"}],
    )
    return DatasetH(handler=handler, segments={
        "train": (tr_start, train_end),
        "valid": (vp_start, train_end),
        "test":  (test_start, test_end),
    })


def prepare_features(ds):
    segs = {}
    for seg in ["train", "valid", "test"]:
        feat = ds.prepare(seg, col_set="feature")
        label = ds.prepare(seg, col_set="label")
        if isinstance(label, pd.DataFrame):
            label = label.iloc[:, 0]
        label = label.rename("label")
        aligned = pd.concat([feat, label], axis=1, join="inner")
        aligned = aligned.dropna(subset=["label"])
        segs[seg] = (aligned.drop(columns=["label"]), aligned["label"])
    return segs


def train_catboost(X_train, y_train, X_valid, y_valid, iterations=999):
    model = CatBoostRegressor(
        loss_function="RMSE", learning_rate=0.05, depth=6,
        iterations=iterations, early_stopping_rounds=50,
        verbose=False, random_seed=42,
    )
    model.fit(X_train, y_train, eval_set=(X_valid, y_valid))
    return model


def calc_metrics(pred_s, label_s):
    df = pd.DataFrame({"score": pred_s, "label": label_s}).dropna()
    if len(df) < 10:
        return {"rank_ic": None, "pearson_ic": None, "icir": None,
                "ic_hit_rate": None, "n_test_days": 0}
    daily_ic = df.groupby(level="datetime").apply(
        lambda g: g["score"].corr(g["label"], method="spearman") if len(g) > 5 else np.nan
    ).dropna()
    daily_pic = df.groupby(level="datetime").apply(
        lambda g: g["score"].corr(g["label"]) if len(g) > 5 else np.nan
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


def main():
    tr_start = "2020-04-01"
    train_end = "2025-03-31"
    vp_start = "2024-10-01"
    test_start = "2025-04-01"
    test_end = "2025-06-30"

    print("=" * 70)
    print("W5: 特征选择实验 (减法方向)")
    print(f"    train={tr_start}~{train_end} valid={vp_start}~{train_end}")
    print(f"    test={test_start}~{test_end}")
    print("=" * 70)

    t0 = time.time()
    print(f"\n[1] 构建 Alpha158 DatasetH...")
    ds = build_ds(tr_start, train_end, vp_start, test_start, test_end)
    segs = prepare_features(ds)
    (X_train, y_train), (X_valid, y_valid), (X_test, y_test) = \
        segs["train"], segs["valid"], segs["test"]
    print(f"    train={X_train.shape} valid={X_valid.shape} test={X_test.shape} [{(time.time()-t0):.0f}s]")

    results = []

    # [2] 全特征训练 + 特征重要性
    print(f"\n[2] 全特征训练 (158, iters=999)...")
    t0 = time.time()
    model_full = train_catboost(X_train.values, y_train.values,
                                X_valid.values, y_valid.values)
    print(f"    完成 iters={model_full.tree_count_} [{(time.time()-t0):.0f}s]")

    # 特征重要性
    imp = model_full.get_feature_importance()
    feat_names = list(X_train.columns)
    imp_df = pd.DataFrame({"feature": feat_names, "importance": imp})
    imp_df = imp_df.sort_values("importance", ascending=False).reset_index(drop=True)
    print("\nTop 15 特征:")
    for i, row in imp_df.head(15).iterrows():
        print(f"    {row['feature']:<16} {row['importance']:.4f}")

    # 全特征指标
    pred = model_full.predict(X_test.values)
    m = calc_metrics(pd.Series(pred, index=X_test.index), y_test)
    m["label"] = "K158_full"; m["n_features"] = 158
    m["n_iters"] = model_full.tree_count_
    results.append(m)
    print(f"    K158_full: RankIC={m['rank_ic']} ICIR={m['icir']} Hit={m['ic_hit_rate']}")

    # [3] top-K 子集重训
    for k in [100, 60, 30]:
        topk = imp_df["feature"].head(k).tolist()
        print(f"\n[3] top-{k} 子集重训...")
        t0 = time.time()
        model_k = train_catboost(X_train[topk].values, y_train.values,
                                 X_valid[topk].values, y_valid.values)
        pred_k = model_k.predict(X_test[topk].values)
        m_k = calc_metrics(pd.Series(pred_k, index=X_test.index), y_test)
        m_k["label"] = f"K{k}_top"; m_k["n_features"] = k
        m_k["n_iters"] = model_k.tree_count_
        results.append(m_k)
        print(f"    K{k}_top: RankIC={m_k['rank_ic']} ICIR={m_k['icir']} "
              f"Hit={m_k['ic_hit_rate']} [{(time.time()-t0):.0f}s]")

    # 保存
    out = os.path.join(OUT_DIR, "w5_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")

    # 汇总
    print("\n" + "=" * 70)
    print("W5 汇总")
    print("=" * 70)
    valid = [r for r in results if r.get("rank_ic") is not None]
    valid.sort(key=lambda r: r["rank_ic"], reverse=True)
    print(f"{'Label':<15} {'RankIC':>8} {'ICIR':>8} {'Hit':>8} {'N_feat':>7} {'Iters':>6}")
    for r in valid:
        print(f"{r['label']:<15} {str(r['rank_ic']):>8} {str(r['icir']):>8} "
              f"{str(r['ic_hit_rate']):>8} {r['n_features']:>7} {r['n_iters']:>6}")

    best = valid[0]
    print(f"\n*** W5 最佳: {best['label']} RankIC={best['rank_ic']} ***")
    print(f"    对比 W1 基线(0.0412): {best['rank_ic'] - 0.0412:+.4f}")
    if best["rank_ic"] > 0.05:
        print(f"    V 突破 0.05!")
        sys.exit(0)
    else:
        print(f"    X 未达 0.05, 差距: {0.05 - best['rank_ic']:.4f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
    print(f"\n总耗时: {time.time()-t_global:.0f}s")
