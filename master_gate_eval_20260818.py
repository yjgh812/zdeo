# -*- coding: utf-8 -*-
"""MASTER三关评估(屠龙口径) — 2026-08-18
输入: data/tulong/pred_test_*.pkl (chain棒1产出, Series: (datetime,instrument)->score)
标签: Ref($close,-2)/Ref($close,-1)-1 (与训练同构, qlib原生)
门槛校准: MASTER为日频1日前瞻信号, 关1用日RankIC≥0.02(论文级: 好模型0.03-0.06);
         月度聚合IC另报供与月频因子体系对照; 关2分层/关3分年同标准
"""
import glob
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, r"D:\zcodesj\strategy_libs\MASTER")

PRED = sorted(glob.glob(r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_*.pkl"))
PRED = PRED[-1] if PRED else None
if not PRED:
    sys.exit("无pred_test_*.pkl (MASTER还没跑?)")


def main():
    pred = pd.read_pickle(PRED)
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    print(f"[pred] {PRED}: {len(pred)} 样本 "
          f"{pred.index.get_level_values(0).min().date()}~{pred.index.get_level_values(0).max().date()}")

    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=r"D:\workbuddyspace\qlib_data\qlib_bin", region=REG_CN, kernels=4)
    from qlib.data import D
    lab = D.features(D.instruments("csi300"), ["Ref($close,-2)/Ref($close,-1)-1"],
                     start_time=str(pred.index.get_level_values(0).min().date()),
                     end_time=str(pred.index.get_level_values(0).max().date()))
    lab.columns = ["label"]
    # qlib索引层级=(instrument, datetime), 与pred(datetime, instrument)对齐
    lab = lab.swaplevel(0, 1).sort_index()
    df = pd.concat([pred.rename("score"), lab], axis=1, join="inner").dropna()
    print(f"[join] {len(df)} / {df.index.get_level_values(0).nunique()} 天")

    # ---- 关1: 日RankIC ----
    rows = []
    for d, g in df.groupby(level=0):
        if len(g) < 50:
            continue
        ic, _ = spearmanr(g["score"], g["label"])
        lay = None
        if len(g) >= 100:
            q = pd.qcut(g["score"].rank(method="first"), 5, labels=False)
            m = g.groupby(q)["label"].mean()
            lay = float(m.iloc[-1] - m.iloc[0])
        rows.append({"date": d, "ic": ic, "spread": lay})
    t = pd.DataFrame(rows).set_index("date")
    m, sd = t["ic"].mean(), t["ic"].std()
    tv = m / (sd / np.sqrt(len(t)))
    gate1 = m >= 0.02
    print(f"[关1] 日RankIC均值{m:+.4f} t={tv:+.2f} 正占比{(t['ic']>0).mean():.0%} "
          f"(门槛0.02论文级) -> {'PASS' if gate1 else 'FAIL'}")
    # 月度聚合(供与月频因子对照)
    mic = t["ic"].resample("ME").mean().dropna()
    print(f"[参考] 月均日IC: 均值{mic.mean():+.4f} 最差月{mic.min():+.4f} "
          f"正月占比{(mic>0).mean():.0%}")

    # ---- 关2: 分层 ----
    sp = t["spread"].dropna()
    # 分层明细(全期)
    qs = []
    for d, g in df.groupby(level=0):
        if len(g) < 100:
            continue
        q = pd.qcut(g["score"].rank(method="first"), 5, labels=False)
        qs.append(g.groupby(q)["label"].mean())
    qm = pd.DataFrame(qs).mean()
    mono = np.corrcoef(range(5), qm)[0, 1]
    gate2 = mono > 0.5 and sp.mean() > 0 and (sp > 0).mean() > 0.5
    print(f"[关2] 分层{qm.round(5).to_dict()} 单调r={mono:+.2f} "
          f"日价差均值{sp.mean():+.5f} 胜率{(sp>0).mean():.0%} -> {'PASS' if gate2 else 'FAIL'}")

    # ---- 关3: 分年 ----
    yr = t["ic"].groupby(t.index.year).agg(["mean", "count"])
    same = len(set(np.sign(yr["mean"]))) == 1
    gate3 = same and (yr["mean"] > 0).all()
    print(f"[关3] 分年IC {yr['mean'].round(4).to_dict()} 同号为正={gate3}")

    verdict = all([gate1, gate2, gate3])
    print(f"\n[VERDICT] {'PASS ✅' if verdict else 'FAIL ❌'}")
    with open(r"D:\zcodesj\reports\MASTER_GATE_EVAL_20260818.md", "w", encoding="utf-8") as f:
        f.write(f"# MASTER三关评估(屠龙口径)\n\n- 数据: {PRED}\n"
                f"- 关1 日RankIC {m:+.4f}(t={tv:+.2f}) -> {'PASS' if gate1 else 'FAIL'}\n"
                f"- 关2 单调r={mono:+.2f} 价差{sp.mean():+.5f} -> {'PASS' if gate2 else 'FAIL'}\n"
                f"- 关3 分年{yr['mean'].round(4).to_dict()} -> {'PASS' if gate3 else 'FAIL'}\n\n"
                f"**综合: {'PASS' if verdict else 'FAIL'}** "
                f"(对照: LSTM64x2月频IC基线0.060/论文MASTER日IC~0.05+)\n")


if __name__ == "__main__":
    main()
