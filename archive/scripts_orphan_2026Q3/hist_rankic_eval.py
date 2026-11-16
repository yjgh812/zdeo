# -*- coding: utf-8 -*-
"""HIST 2026 测试窗 RankIC 回测 — 对比旧闸门(0.0307@2023-2026)
用法: python hist_rankic_eval.py [--date 2026-08-21]
读 hist_ensemble_seed12345.pkl(2026-01-01..end) 的预测 + qfq 2日前瞻收益 → 日RankIC均值
2026-08-22 完全根治后核对用。
"""
import sys

import numpy as np
import pandas as pd

ENS = r"D:\zcodesj\qlib_exp\hist_data\hist_ensemble_seed12345.pkl"
QLIB = r"D:\workbuddyspace\qlib_data\qlib_bin"


def main():
    end = None
    for a in sys.argv[1:]:
        if a.startswith("--date"):
            end = a.split("=", 1)[1]
    pred = pd.read_pickle(ENS)
    pred = pred["score"]
    dates = pred.index.get_level_values(0)
    if end is None:
        end = str(dates.max())[:10]

    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=QLIB, region=REG_CN, kernels=2)
    from qlib.data import D
    # 2日前瞻收益作为真实标签(与训练label同口径)
    lab = D.features(D.instruments("csi300"), ["Ref($close,-2)/Ref($close,-1)-1"],
                     start_time="2026-01-01", end_time=end)
    lab.columns = ["label"]
    df = pd.DataFrame({"pred": pred}).join(lab, how="inner")
    df = df.dropna()

    ics = df.groupby(level=0).apply(lambda g: g["pred"].corr(g["label"], method="spearman") if len(g) > 10 else np.nan)
    ics = ics.dropna()
    n_days = len(ics)
    mean_ic = ics.mean()
    icir = ics.mean() / ics.std() * np.sqrt(252) if ics.std() > 0 else np.nan
    pos = (ics > 0).mean()
    # t 检验
    from scipy import stats
    t, p = stats.ttest_1samp(ics, 0)
    print(f"=== HIST RankIC @2026 (2026-01-01..{end}) ===")
    print(f"  样本日 {n_days} | 平均日RankIC {mean_ic:.4f} | ICIR {icir:.2f} | 正IC占比 {pos:.1%} | t={t:.2f} p={p:.3f}")
    return mean_ic


if __name__ == "__main__":
    main()
