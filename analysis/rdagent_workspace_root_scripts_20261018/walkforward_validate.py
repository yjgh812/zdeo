# -*- coding: utf-8 -*-
"""Loop 2 SOTA 忠实版换窗验证 v3。

特征集双臂:
  FULL(忠实) = Alpha158 式 20 特征(原跑渲染表达式, build_alpha20.py 缓存) + 5 AI 因子 = 25 特征
  BASE(基线) = 仅 Alpha20 = 20 特征 → FULL - BASE = AI 因子边际贡献
标签 = Ref($close,-2)/Ref($close,-1)-1 (csi300); 训练/验证标签逐日 zscore(等价 CSZScoreNorm), 测试评估用原始标签
模型 = lightgbm 同参; 窗口 A(原窗对照)/B/C/D。
"""
import json
import sys
import warnings

warnings.filterwarnings("ignore")

import lightgbm as lgb
import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.data import D

ALPHA20 = "/mnt/d/zcodesj/rdagent_workspace/git_ignore_folder/alpha20_features.parquet"
FACTORS = sys.argv[1] if len(sys.argv) > 1 else (
    "/mnt/d/zcodesj/rdagent_workspace/git_ignore_folder/RD-Agent_workspace/290b2833fec64768a4b39a9ee4778f79/combined_factors_df.parquet")
LGB_KW = dict(objective="regression", metric="l2", colsample_bytree=0.8879, learning_rate=0.2,
              subsample=0.8789, lambda_l1=205.6999, lambda_l2=580.9768,
              max_depth=8, num_leaves=210, num_threads=8, verbose=-1)

WINDOWS = {
    "A_ORIG": dict(train=("2019-01-01", "2023-12-31"), valid=("2024-01-01", "2025-06-30"), test=("2025-07-01", "2026-06-30")),
    "B": dict(train=("2017-01-01", "2020-06-30"), valid=("2020-07-01", "2020-12-31"), test=("2021-01-01", "2022-06-30")),
    "C": dict(train=("2018-01-01", "2021-06-30"), valid=("2021-07-01", "2021-12-31"), test=("2022-01-01", "2023-06-30")),
    "D": dict(train=("2019-01-01", "2022-06-30"), valid=("2022-07-01", "2022-12-31"), test=("2023-01-01", "2024-06-30")),
}


def main():
    qlib.init(provider_uri="/root/.qlib/qlib_data/cn_data", region=REG_CN)
    label_df = D.features(D.instruments("csi300"), ["Ref($close, -2)/Ref($close, -1) - 1"],
                          start_time="2016-01-01", end_time="2026-09-24")
    label_df.columns = ["LABEL0"]
    label_df = label_df.reset_index()

    a20 = pd.read_parquet(ALPHA20).reset_index()
    fac = pd.read_parquet(FACTORS)
    fac.columns = fac.columns.droplevel(0)
    fac = fac.reset_index()
    fac_cols = [c for c in fac.columns if c not in ("datetime", "instrument")]
    a20_cols = [c for c in a20.columns if c not in ("datetime", "instrument")]

    base = a20.merge(label_df, on=["datetime", "instrument"], how="inner")
    full = base.merge(fac, on=["datetime", "instrument"], how="inner")
    full["datetime"] = pd.to_datetime(full["datetime"])
    base["datetime"] = pd.to_datetime(base["datetime"])
    full = full.dropna(subset=a20_cols + fac_cols + ["LABEL0"])
    base = base.dropna(subset=a20_cols + ["LABEL0"])
    full["LABEL0_Z"] = full.groupby("datetime")["LABEL0"].transform(lambda s: (s - s.mean()) / s.std())
    base["LABEL0_Z"] = base.groupby("datetime")["LABEL0"].transform(lambda s: (s - s.mean()) / s.std())

    results = {}
    for wname, w in WINDOWS.items():
        for arm, d in (("FULL", full), ("BASE", base)):
            tr = d[(d.datetime >= w["train"][0]) & (d.datetime <= w["train"][1])]
            va = d[(d.datetime >= w["valid"][0]) & (d.datetime <= w["valid"][1])]
            te = d[(d.datetime >= w["test"][0]) & (d.datetime <= w["test"][1])]
            feats = a20_cols + (fac_cols if arm == "FULL" else [])
            booster = lgb.train(LGB_KW, lgb.Dataset(tr[feats], tr["LABEL0_Z"]),
                                num_boost_round=1000,
                                valid_sets=[lgb.Dataset(va[feats], va["LABEL0_Z"])],
                                callbacks=[lgb.early_stopping(50, verbose=False)])
            pred = booster.predict(te[feats], num_iteration=booster.best_iteration)
            y = te["LABEL0"].reset_index(drop=True)
            p = pd.Series(pred)
            ic = p.corr(y)
            ric = p.corr(y, method="spearman")
            key = f"{wname}_{arm}"
            results[key] = {"IC": round(float(ic), 4), "RankIC": round(float(ric), 4),
                            "iters": booster.best_iteration}
            print(f"{key}: IC={ic:.4f} RankIC={ric:.4f} iters={booster.best_iteration}")

    print("WF_SUMMARY " + json.dumps(results, ensure_ascii=False))
    print("WALKFORWARD_DONE")


if __name__ == "__main__":
    main()
