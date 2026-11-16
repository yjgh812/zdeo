# -*- coding: utf-8 -*-
"""
W3: 特征工程实验 - 自定义量价合成因子扩展
目的: 在 Alpha158 基础上加 8 个差异化因子，突破 IC>0.05
设计:
  Phase 1: Alpha158 基线 (复现 W1/W2 的 0.0412)
  Phase 2: Alpha158Plus (+8 自定义因子 = 166 特征)
自定义因子 (确保与 Alpha158 差异化):
  1. VOL_RATIO_5_60  - 短/长期波动率比 (波动结构二阶信息)
  2. AMIHUD_20       - Amihud非流动性 (流动性经典度量)
  3. TURNNMOM_5_20   - 成交额动量
  4. RANGE_20        - 日内振幅均值 (不同于 STD)
  5. VRATIO_20       - 量比 (相对放量)
  6. RSV_20          - 随机指标位置
  7. SKEW_RET_20     - 收益率偏度 (分布三阶矩)
  8. KURT_RET_20     - 收益率峰度 (分布四阶矩)
环境: qlib_venv (py3.12)
"""
import os, sys, time, json
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\w3_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)

from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset import DatasetH
from qlib.contrib.model.catboost_model import CatBoostModel

t_global = time.time()


class Alpha158Plus(Alpha158):
    """Alpha158 + 8 个差异化合成因子"""

    def get_feature_config(self):
        fields, names = super().get_feature_config()

        extra_fields = [
            # 1. 波动率结构比 (短期/长期波动率比 - 二阶信息)
            "Std($close/Ref($close,1)-1, 5) / (Std($close/Ref($close,1)-1, 60)+1e-12)",
            # 2. Amihud 非流动性 (|收益|/成交额)
            "Mean(Abs($close/Ref($close,1)-1)/($volume*$close*1e-8+1e-12), 20)",
            # 3. 成交额动量 (短长期成交额比)
            "Mean($volume*$close, 5) / (Mean($volume*$close, 20)+1)",
            # 4. 日内振幅均值
            "Mean(($high-$low)/Ref($close,1), 20)",
            # 5. 量比 (当日成交量/20日均量)
            "$volume / (Mean($volume, 20)+1)",
            # 6. 随机指标位置 (收盘在20日区间的相对位置)
            "($close - Min($low, 20)) / (Max($high, 20) - Min($low, 20) + 1e-12)",
            # 7. 收益率偏度 (分布三阶矩)
            "Skew($close/Ref($close,1)-1, 20)",
            # 8. 收益率峰度 (分布四阶矩, 尾部风险)
            "Kurt($close/Ref($close,1)-1, 20)",
        ]
        extra_names = [
            "VOL_RATIO_5_60", "AMIHUD_20", "TURNNMOM_5_20", "RANGE_20",
            "VRATIO_20", "RSV_20", "SKEW_RET_20", "KURT_RET_20",
        ]

        return fields + extra_fields, names + extra_names


def build_dataset(handler_cls, tr_start, train_end, vp_start, test_start, test_end,
                  instruments="csi300"):
    """构建 DatasetH (直接构造 handler, 支持 Alpha158/Alpha158Plus)"""
    handler = handler_cls(
        instruments=instruments,
        start_time=tr_start,
        end_time=test_end,
        fit_start_time=tr_start,
        fit_end_time=train_end,
        label=(["Ref($close,-1)/$close-1"], ["LABEL0"]),
        infer_processors=[
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        learn_processors=[{"class": "DropnaLabel"}],
    )
    ds = DatasetH(handler=handler, segments={
        "train": (tr_start, train_end),
        "valid": (vp_start, train_end),
        "test":  (test_start, test_end),
    })
    return ds


def calc_metrics(pred_df, label_df):
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
    label_df = ds.prepare(segment, col_set="label")
    if isinstance(label_df, pd.DataFrame):
        label_col = label_df.iloc[:, 0]
    else:
        label_col = label_df
    out = label_col.reset_index()
    out.columns = ["datetime", "instrument", "label"]
    return out


def pred_to_df(pred):
    out = pred.reset_index()
    out.columns = ["datetime", "instrument", "score"]
    return out


def run_one(label, handler_cls, tr_start, train_end, vp_start, test_start, test_end,
            iterations=400):
    t0 = time.time()
    print(f"\n>>> [{label}] handler={handler_cls.__name__}")

    try:
        ds = build_dataset(handler_cls, tr_start, train_end, vp_start, test_start, test_end)
    except Exception as e:
        print(f"    数据集构建失败: {e}")
        import traceback; traceback.print_exc()
        return {"label": label, "error": str(e)}

    # 检查特征数 (确认 override 是否生效)
    feature_df = ds.prepare("test", col_set="feature")
    n_features = feature_df.shape[1] if isinstance(feature_df, pd.DataFrame) else 1
    print(f"    特征数: {n_features}")
    if handler_cls.__name__ == "Alpha158Plus" and n_features < 160:
        print(f"    !! 警告: Alpha158Plus 特征数={n_features}, override 可能未生效!")

    print(f"    [{time.time()-t0:.0f}s] 训练 CatBoost...")
    model = CatBoostModel(loss="RMSE", learning_rate=0.05, depth=6,
                          iterations=iterations, early_stopping_rounds=50)
    model.fit(ds)

    print(f"    [{time.time()-t0:.0f}s] 预测...")
    pred = model.predict(ds, segment="test")
    if pred is None or len(pred) == 0:
        return {"label": label, "error": "empty prediction"}

    label_df = get_label_df(ds, "test")
    pred_df = pred_to_df(pred)

    m = calc_metrics(pred_df, label_df)
    m["label"] = label
    m["handler"] = handler_cls.__name__
    m["n_features"] = int(n_features)
    m["elapsed_sec"] = round(time.time() - t0, 1)

    print(f"    [{m['elapsed_sec']:.0f}s] RankIC={m['rank_ic']} ICIR={m['icir']} Hit={m['ic_hit_rate']}")
    return m


def main():
    # 固定 W1 最优窗口
    tr_start = "2020-04-01"
    train_end = "2025-03-31"
    vp_start = "2024-10-01"
    test_start = "2025-04-01"
    test_end = "2025-06-30"

    print("=" * 70)
    print("W3: 特征工程实验 (自定义量价合成因子)")
    print(f"    train={tr_start}~{train_end} valid={vp_start}~{train_end}")
    print(f"    test={test_start}~{test_end}")
    print("=" * 70)

    # 先验证 Alpha158Plus get_feature_config override 是否生效
    print("\n[预检] Alpha158Plus 特征数验证...")
    try:
        f158, n158 = Alpha158().get_feature_config()
        f158p, n158p = Alpha158Plus().get_feature_config()
        print(f"    Alpha158:     {len(n158)} 特征")
        print(f"    Alpha158Plus: {len(n158p)} 特征 (预期 {len(n158)+8})")
        if len(n158p) == len(n158) + 8:
            print(f"    OK override 生效")
        else:
            print(f"    !! override 可能未生效, 继续观察实际构建结果")
            print(f"    Alpha158Plus 多出的: {set(n158p) - set(n158)}")
    except Exception as e:
        print(f"    预检失败: {e}")
        import traceback; traceback.print_exc()

    results = []

    # Phase 1: Alpha158 基线
    print("\n--- Phase 1: Alpha158 基线 ---")
    r1 = run_one("Alpha158_baseline", Alpha158, tr_start, train_end, vp_start,
                 test_start, test_end)
    results.append(r1)

    # Phase 2: Alpha158Plus (+8因子)
    print("\n--- Phase 2: Alpha158Plus (+8 自定义因子) ---")
    r2 = run_one("Alpha158Plus_8f", Alpha158Plus, tr_start, train_end, vp_start,
                 test_start, test_end)
    results.append(r2)

    # 保存
    out = os.path.join(OUT_DIR, "w3_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")

    # 汇总
    print("\n" + "=" * 70)
    print("W3 汇总")
    print("=" * 70)
    valid = [r for r in results if r.get("rank_ic") is not None]
    valid.sort(key=lambda r: r["rank_ic"], reverse=True)
    print(f"{'Label':<25} {'RankIC':>8} {'ICIR':>8} {'Hit':>8} {'N_feat':>8}")
    for r in valid:
        print(f"{r['label']:<25} {str(r['rank_ic']):>8} {str(r['icir']):>8} "
              f"{str(r['ic_hit_rate']):>8} {r.get('n_features','?'):>8}")

    if len(valid) >= 2:
        plus_r = [r for r in valid if "Plus" in r["label"]]
        base_r = [r for r in valid if "baseline" in r["label"]]
        if plus_r and base_r:
            delta = plus_r[0]["rank_ic"] - base_r[0]["rank_ic"]
            print(f"\nAlpha158Plus vs Alpha158: RankIC {delta:+.4f} ({'提升' if delta > 0 else '下降'})")
            print(f"  baseline={base_r[0]['rank_ic']} -> plus={plus_r[0]['rank_ic']}")
            best = max(plus_r[0], base_r[0], key=lambda x: x["rank_ic"])
        else:
            best = valid[0]

        print(f"\n*** W3 最佳: {best['label']} RankIC={best['rank_ic']} ***")
        baseline_w1 = 0.0412
        delta_w1 = best["rank_ic"] - baseline_w1
        print(f"    对比 W1 基线(0.0412): {delta_w1:+.4f}")
        if best["rank_ic"] > 0.05:
            print(f"    V 突破 0.05!")
            sys.exit(0)
        else:
            print(f"    X 未达 0.05, 差距: {0.05 - best['rank_ic']:.4f}")
            sys.exit(1)

    print(f"\n总耗时: {time.time()-t_global:.0f}s")


if __name__ == "__main__":
    main()
