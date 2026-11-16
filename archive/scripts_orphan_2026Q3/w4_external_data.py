# -*- coding: utf-8 -*-
"""
W4: 外部数据注入实验 - daily_basic 估值/换手/市值因子
目的: Alpha158(量价) 已到天花板 0.0412, 注入跨数据源因子尝试突破 0.05
外部因子 (daily_basic, 覆盖 2020-01 ~ 2026-08):
  pe_ttm, pb, ps_ttm           - 估值维度 (Alpha158 完全缺失)
  turnover_rate                - 换手率
  volume_ratio                 - 量比
  total_mv, circ_mv            - 市值 (规模因子)
实验设计 (三组对比, 唯一变量=外部特征):
  A. qlib CatBoostModel + DatasetH         (官方锚点, 应=0.0412)
  B. 原生 CatBoost + Alpha158 手工特征      (验证手工管线等价性)
  C. 原生 CatBoost + Alpha158 + 7外部因子   (实验组)
环境: qlib_venv (py3.12)
"""
import os, sys, time, json, sqlite3
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np
from catboost import CatBoostRegressor

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
DB_PATH = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT_DIR = r"D:\zcodesj\w4_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)

from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset import DatasetH
from qlib.contrib.model.catboost_model import CatBoostModel

EXT_FIELDS = ["pe_ttm", "pb", "ps_ttm", "turnover_rate",
              "volume_ratio", "total_mv", "circ_mv"]
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


def to_qlib_code(ts_code):
    code, ex = ts_code.split(".")
    return ex + code


def load_ext_features(start, end):
    """读 daily_basic, 返回 (datetime, instrument) × EXT_FIELDS"""
    conn = sqlite3.connect(DB_PATH)
    cols = ", ".join(["ts_code", "trade_date"] + EXT_FIELDS)
    sql = f"SELECT {cols} FROM daily_basic WHERE trade_date BETWEEN {start} AND {end}"
    df = pd.read_sql(sql, conn)
    conn.close()
    df["datetime"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
    df["instrument"] = df["ts_code"].apply(to_qlib_code)
    df = df.drop(columns=["ts_code", "trade_date"])
    df = df.set_index(["datetime", "instrument"])
    return df


def prepare_features(ds, ext_df=None):
    """从 DatasetH 取三段特征+标签 (对齐索引 + DropnaLabel), 可选 join 外部特征"""
    segs = {}
    for seg in ["train", "valid", "test"]:
        feat = ds.prepare(seg, col_set="feature")
        label = ds.prepare(seg, col_set="label")
        if isinstance(label, pd.DataFrame):
            label = label.iloc[:, 0]
        label = label.rename("label")
        feat_df = feat.copy()
        if ext_df is not None:
            feat_df = feat_df.join(ext_df, how="left")
        # 对齐 feature 与 label 索引 (同 qlib DropnaLabel 语义)
        aligned = pd.concat([feat_df, label], axis=1, join="inner")
        aligned = aligned.dropna(subset=["label"])
        feat_out = aligned.drop(columns=["label"])
        label_out = aligned["label"]
        segs[seg] = (feat_out, label_out)
    return segs


def train_catboost(X_train, y_train, X_valid, y_valid):
    model = CatBoostRegressor(
        loss_function="RMSE", learning_rate=0.05, depth=6,
        iterations=400, early_stopping_rounds=50,
        verbose=False, random_seed=42,
    )
    model.fit(X_train, y_train, eval_set=(X_valid, y_valid))
    return model


def calc_metrics(pred_s, label_s):
    """pred_s/label_s: pd.Series with (datetime, instrument) MultiIndex"""
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


def run_experiment(label, ds, ext_df=None):
    t0 = time.time()
    segs = prepare_features(ds, ext_df)
    (X_train, y_train), (X_valid, y_valid), (X_test, y_test) = \
        segs["train"], segs["valid"], segs["test"]

    print(f"    train={X_train.shape} valid={X_valid.shape} test={X_test.shape}")
    if ext_df is not None:
        nan_ratio = X_test.isna().mean().mean()
        print(f"    test 外部特征 NaN 比例: {nan_ratio:.4f}")

    model = train_catboost(X_train.values, y_train.values,
                           X_valid.values, y_valid.values)
    n_iters = model.tree_count_

    pred = model.predict(X_test.values)
    pred_s = pd.Series(pred, index=X_test.index)

    m = calc_metrics(pred_s, y_test)
    m["label"] = label
    m["n_iters"] = n_iters
    m["elapsed_sec"] = round(time.time() - t0, 1)
    m["n_features"] = int(X_train.shape[1])
    print(f"    {label}: RankIC={m['rank_ic']} ICIR={m['icir']} "
          f"Hit={m['ic_hit_rate']} iters={n_iters} [{m['elapsed_sec']:.0f}s]")
    return m


def main():
    tr_start = "2020-04-01"
    train_end = "2025-03-31"
    vp_start = "2024-10-01"
    test_start = "2025-04-01"
    test_end = "2025-06-30"

    print("=" * 70)
    print("W4: 外部数据注入 (daily_basic 估值/换手/市值)")
    print(f"    train={tr_start}~{train_end} valid={vp_start}~{train_end}")
    print(f"    test={test_start}~{test_end}")
    print("=" * 70)

    # 加载外部特征 (2020-01-15 起, 覆盖训练起点之前的窗口)
    t0 = time.time()
    print(f"\n[0] 加载 daily_basic 外部特征...")
    ext_df = load_ext_features("20200115", test_end.replace("-", ""))
    print(f"    外部特征: {ext_df.shape} ({(time.time()-t0):.0f}s)")

    # 构建 DatasetH (一次, 供 A/B/C 共用)
    t0 = time.time()
    print(f"\n[1] 构建 Alpha158 DatasetH...")
    ds = build_ds(tr_start, train_end, vp_start, test_start, test_end)
    print(f"    完成 ({(time.time()-t0):.0f}s)")

    results = []

    # A. qlib 官方锚点
    print(f"\n[2] A. qlib CatBoostModel 锚点...")
    t0 = time.time()
    model_a = CatBoostModel(loss="RMSE", learning_rate=0.05, depth=6,
                            iterations=400, early_stopping_rounds=50)
    model_a.fit(ds)
    pred_a = model_a.predict(ds, segment="test")
    pred_a_s = pd.Series(np.asarray(pred_a).ravel(),
                         index=ds.prepare("test", col_set="feature").index)
    label_a = ds.prepare("test", col_set="label")
    if isinstance(label_a, pd.DataFrame):
        label_a = label_a.iloc[:, 0]
    m_a = calc_metrics(pred_a_s, label_a)
    m_a["label"] = "A_qlib_catboost"
    m_a["elapsed_sec"] = round(time.time() - t0, 1)
    results.append(m_a)
    print(f"    A: RankIC={m_a['rank_ic']} ICIR={m_a['icir']} Hit={m_a['ic_hit_rate']}")

    # B. 原生 CatBoost + Alpha158 (手工管线等价性)
    print(f"\n[3] B. 原生 CatBoost + Alpha158...")
    m_b = run_experiment("B_native_alpha158", ds, ext_df=None)
    results.append(m_b)

    # C. 原生 CatBoost + Alpha158 + 外部因子
    print(f"\n[4] C. 原生 CatBoost + Alpha158 + 7外部因子...")
    m_c = run_experiment("C_native_ext", ds, ext_df=ext_df)
    results.append(m_c)

    # 保存
    out = os.path.join(OUT_DIR, "w4_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")

    # 汇总
    print("\n" + "=" * 70)
    print("W4 汇总")
    print("=" * 70)
    valid = [r for r in results if r.get("rank_ic") is not None]
    valid.sort(key=lambda r: r["rank_ic"], reverse=True)
    print(f"{'Label':<28} {'RankIC':>8} {'ICIR':>8} {'Hit':>8} {'N_feat':>7} {'Iters':>6}")
    for r in valid:
        print(f"{r['label']:<28} {str(r['rank_ic']):>8} {str(r['icir']):>8} "
              f"{str(r['ic_hit_rate']):>8} {r.get('n_features','?'):>7} {r.get('n_iters','?'):>6}")

    best = valid[0]
    print(f"\n*** W4 最佳: {best['label']} RankIC={best['rank_ic']} ***")
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
