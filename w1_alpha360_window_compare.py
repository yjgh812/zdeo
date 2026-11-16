# -*- coding: utf-8 -*-
r"""
W1: Alpha360 对比 + 窗口优化
目的: 把模型 IC 从 +0.0386 (单日) 推升到 >0.05 可用线
实验设计:
  Phase 1: Alpha158 vs Alpha360 (固定 5年训练 + 6月 valid + 3月 test)
  Phase 2: 窗口网格扫描 (基于 Phase1 胜出特征集)
环境: qlib_venv (py3.12)
用法: D:\workbuddyspace\tulong_system\qlib_venv\Scripts\python.exe w1_alpha360_window_compare.py
"""
import os, sys, time, json
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\w1_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)
from qlib.utils import init_instance_by_config
from qlib.contrib.model.catboost_model import CatBoostModel
from qlib.contrib.data.handler import Alpha158, Alpha360
from qlib.data import D

t_global = time.time()


def build_dataset(handler_cls, tr_start, train_end, vp_start, test_start, test_end,
                  instruments="csi300"):
    """构建带 train/valid/test 三段的 DatasetH"""
    ds = init_instance_by_config({
        "class": "DatasetH", "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": handler_cls.__name__,
                "module_path": handler_cls.__module__,
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


def calc_ic(pred_df, label_df):
    """计算 rank IC (Spearman) 和 Pearson IC"""
    df = pred_df.merge(label_df, on=["datetime", "instrument"])
    df = df.dropna()
    if len(df) < 10:
        return np.nan, np.nan, 0
    # 按日分组计算 IC 再平均
    daily_ic = df.groupby("datetime").apply(
        lambda g: g["score"].corr(g["label"], method="spearman") if len(g) > 5 else np.nan,
        include_groups=False
    ).dropna()
    daily_pic = df.groupby("datetime").apply(
        lambda g: g["score"].corr(g["label"]) if len(g) > 5 else np.nan,
        include_groups=False
    ).dropna()
    return daily_ic.mean(), daily_pic.mean(), len(daily_ic)


def run_one(label, handler_cls, tr_start, train_end, vp_start, test_start, test_end,
            iterations=400):
    """跑一次完整 train + test，返回 IC 指标"""
    t0 = time.time()
    print(f"\n>>> [{label}] train={tr_start}~{train_end} valid={vp_start}~{train_end} test={test_start}~{test_end}")
    print(f"    handler={handler_cls.__name__} iters={iterations}")

    try:
        ds = build_dataset(handler_cls, tr_start, train_end, vp_start, test_start, test_end)
    except Exception as e:
        print(f"    数据集构建失败: {e}")
        return {"label": label, "error": str(e)}

    print(f"    [{time.time()-t0:.0f}s] 训练 CatBoost...")
    model = CatBoostModel(loss="RMSE", learning_rate=0.05, depth=6,
                          iterations=iterations, early_stopping_rounds=50)
    model.fit(ds)

    print(f"    [{time.time()-t0:.0f}s] 预测 test 段...")
    pred = model.predict(ds, segment="test")
    if pred is None or len(pred) == 0:
        print(f"    预测为空")
        return {"label": label, "error": "empty prediction"}

    # 获取 test 段的真实 label
    label_df = ds.prepare("test", col_set="label")
    if isinstance(label_df, pd.DataFrame):
        label_col = label_df.iloc[:, 0]
    else:
        label_col = label_df
    label_df2 = label_col.reset_index()
    label_df2.columns = ["datetime", "instrument", "label"]

    pred_df = pred.reset_index()
    pred_df.columns = ["datetime", "instrument", "score"]

    rank_ic, pic, n_days = calc_ic(pred_df, label_df2)

    # ICIR = mean/std
    df = pred_df.merge(label_df2, on=["datetime", "instrument"]).dropna()
    daily_ic_series = df.groupby("datetime").apply(
        lambda g: g["score"].corr(g["label"], method="spearman") if len(g) > 5 else np.nan,
        include_groups=False
    ).dropna()
    ic_std = daily_ic_series.std() if len(daily_ic_series) > 1 else np.nan
    icir = (rank_ic / ic_std) if (ic_std and ic_std > 0) else np.nan

    # IC > 0 的占比 (hit rate)
    ic_hit = (daily_ic_series > 0).mean() if len(daily_ic_series) > 0 else np.nan

    elapsed = time.time() - t0
    result = {
        "label": label,
        "handler": handler_cls.__name__,
        "train_start": tr_start, "train_end": train_end,
        "valid_start": vp_start,
        "test_start": test_start, "test_end": test_end,
        "iterations": iterations,
        "rank_ic": round(float(rank_ic), 4) if not np.isnan(rank_ic) else None,
        "pearson_ic": round(float(pic), 4) if not np.isnan(pic) else None,
        "icir": round(float(icir), 4) if not np.isnan(icir) else None,
        "ic_hit_rate": round(float(ic_hit), 4) if not np.isnan(ic_hit) else None,
        "n_test_days": int(n_days),
        "n_pred": len(pred_df),
        "elapsed_sec": round(elapsed, 1),
    }
    print(f"    [{elapsed:.0f}s] RankIC={result['rank_ic']} PearsonIC={result['pearson_ic']} "
          f"ICIR={result['icir']} HitRate={result['ic_hit_rate']} ({n_days}天)")
    return result


def main():
    # ===== 固定 test 段: 2025-04-01 ~ 2025-06-30 (3个月, Q2) =====
    test_start = "2025-04-01"
    test_end = "2025-06-30"
    # train_end 取 test_start 前一天
    train_end = "2025-03-31"

    results = []

    # ===== Phase 1: Alpha158 vs Alpha360 =====
    print("=" * 70)
    print("Phase 1: Alpha158 vs Alpha360 (5年训练 + 6月 valid)")
    print("=" * 70)

    tr5_start = "2020-04-01"
    vp6_start = "2024-10-01"

    for handler_cls in [Alpha158, Alpha360]:
        label = f"P1_{handler_cls.__name__}_5y6m"
        r = run_one(label, handler_cls, tr5_start, train_end, vp6_start,
                    test_start, test_end, iterations=400)
        results.append(r)

    # 判断 Phase 1 胜者
    valid_results = [r for r in results if r.get("rank_ic") is not None]
    if not valid_results:
        print("\n!! Phase 1 全部失败，无法继续 Phase 2")
        _save(results)
        return

    p1_best = max(valid_results, key=lambda r: r["rank_ic"])
    winner_handler = Alpha158 if "Alpha158" in p1_best["handler"] else Alpha360
    print(f"\n*** Phase 1 胜出: {winner_handler.__name__} RankIC={p1_best['rank_ic']} ***")

    # ===== Phase 2: 窗口网格扫描 (基于胜出 handler) =====
    print("\n" + "=" * 70)
    print(f"Phase 2: 窗口优化 (handler={winner_handler.__name__})")
    print("=" * 70)

    # 训练窗口: 3年/5年/7年 ; valid 窗口: 3月/6月/12月
    tr_windows = {
        "3y": ("2022-04-01"),
        "5y": ("2020-04-01"),
        "7y": ("2018-04-01"),
    }
    vp_windows = {
        "3m": ("2025-01-01"),
        "6m": ("2024-10-01"),
        "12m": ("2024-04-01"),
    }

    for tr_label, tr_start_val in tr_windows.items():
        for vp_label, vp_start_val in vp_windows.items():
            # valid 段不能超过 train 段
            if vp_start_val < tr_start_val:
                continue
            # 跳过已跑过的 5y6m (Phase1 已跑)
            if tr_label == "5y" and vp_label == "6m":
                continue
            label = f"P2_{winner_handler.__name__}_{tr_label}{vp_label}"
            r = run_one(label, winner_handler, tr_start_val, train_end, vp_start_val,
                        test_start, test_end, iterations=400)
            results.append(r)

    _save(results)

    # ===== 汇总 =====
    print("\n" + "=" * 70)
    print("W1 实验汇总 (按 RankIC 降序)")
    print("=" * 70)
    valid = [r for r in results if r.get("rank_ic") is not None]
    valid.sort(key=lambda r: r["rank_ic"], reverse=True)
    print(f"{'Label':<32} {'Handler':<10} {'RankIC':>8} {'PIC':>8} {'ICIR':>8} {'Hit':>8} {'Days':>6}")
    for r in valid:
        print(f"{r['label']:<32} {r['handler']:<10} {str(r['rank_ic']):>8} "
              f"{str(r['pearson_ic']):>8} {str(r['icir']):>8} "
              f"{str(r['ic_hit_rate']):>8} {r['n_test_days']:>6}")

    best = valid[0] if valid else None
    if best:
        print(f"\n*** 最佳: {best['label']} RankIC={best['rank_ic']} ICIR={best['icir']} ***")
        # 对比当前基线
        baseline = 0.0386
        delta = best["rank_ic"] - baseline
        print(f"    对比基线(单日 +0.0386): {delta:+.4f} ({'提升' if delta>0 else '下降'})")
        if best["rank_ic"] > 0.05:
            print(f"    ✓ 突破 0.05 可用线!")
        else:
            print(f"    ✗ 未达 0.05 可用线，差距: {0.05 - best['rank_ic']:.4f}")


def _save(results):
    out = os.path.join(OUT_DIR, "w1_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")


if __name__ == "__main__":
    main()
    print(f"\n总耗时: {time.time()-t_global:.0f}s")
