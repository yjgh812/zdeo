# -*- coding: utf-8 -*-
"""MASTER 5-seed集成三关评估 — 2026-08-18
用法: python master_ensemble_eval_20260818.py [--gates-only|--seed-only 0]
集成: 同(datetime,instrument)预测均值; 缺失种子自动跳过并提示
"""
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, r"D:\zcodesj\strategy_libs\MASTER")


def load_seeds(seed_only=None):
    import glob
    pats = glob.glob(r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_*_seed*.pkl")
    frames = {}
    for p in pats:
        s = int(p.rsplit("_seed", 1)[1].split(".")[0])
        if seed_only is not None and s != seed_only:
            continue
        frames[s] = pd.read_pickle(p)
    if seed_only is not None:
        return frames.get(seed_only, None)
    all_seeds = sorted(frames)
    print(f"[seeds] {all_seeds} 共{len(all_seeds)}个")
    ens = pd.concat(frames, axis=1).mean(axis=1, skipna=True)
    return ens


def gates(pred, name):
    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=r"D:\workbuddyspace\qlib_data\qlib_bin", region=REG_CN, kernels=2)
    from qlib.data import D
    lab = D.features(D.instruments("csi300"), ["Ref($close,-2)/Ref($close,-1)-1"],
                     start_time=str(pred.index.get_level_values(0).min().date()),
                     end_time=str(pred.index.get_level_values(0).max().date()))
    lab.columns = ["label"]
    lab = lab.swaplevel(0, 1).sort_index()
    df = pd.concat([pred.rename("score"), lab], axis=1, join="inner").dropna()
    rows = []
    for d, g in df.groupby(level=0):
        if len(g) < 50:
            continue
        ic, _ = spearmanr(g["score"], g["label"])
        layout = None
        if len(g) >= 100:
            q = pd.qcut(g["score"].rank(method="first"), 5, labels=False)
            mm = g.groupby(q)["label"].mean()
            layout = float(mm.iloc[-1] - mm.iloc[0])
        rows.append((d, ic, layout))
    t = pd.DataFrame(rows, columns=["date", "ic", "spread"]).set_index("date")
    m, sd = t["ic"].mean(), t["ic"].std()
    tv = m / (sd / np.sqrt(len(t))) if sd > 0 else 0
    sp = t["spread"].dropna()
    qs = []
    for d, g in df.groupby(level=0):
        if len(g) < 100:
            continue
        q = pd.qcut(g["score"].rank(method="first"), 5, labels=False)
        qs.append(g.groupby(q)["label"].mean())
    qm = pd.DataFrame(qs).mean()
    mono = np.corrcoef(range(5), qm)[0, 1]
    yr = t["ic"].groupby(t.index.year).mean()
    g1 = m >= 0.02
    g2 = mono > 0.5 and sp.mean() > 0 and (sp > 0).mean() > 0.5
    g3 = (yr > 0).all() and (yr > 0).mean() == 1
    print(f"\n[{name}] n={len(df)} 日数={len(t)}")
    print(f"  关1 日RankIC {m:+.4f} (t={tv:+.2f}, 正{(t['ic']>0).mean():.0%}) -> {'PASS' if g1 else 'FAIL'}"
          f" (门槛0.02论文级)")
    print(f"  关2 分层{qm.round(5).to_dict()} r={mono:+.2f} 价差{sp.mean():+.5f} "
          f"胜率{(sp>0).mean():.0%} -> {'PASS' if g2 else 'FAIL'}")
    print(f"  关3 分年{yr.round(4).to_dict()} -> {'PASS' if g3 else 'FAIL'}")
    print(f"  [VERDICT] {'PASS ✅' if all([g1, g2, g3]) else 'FAIL ❌'}")
    return m, g1, g2, g3


def main():
    if "--seed-only" in sys.argv:
        s = int(sys.argv[sys.argv.index("--seed-only") + 1])
        p = load_seeds(seed_only=s)
        gates(p, f"单seed{s}")
        return
    ens = load_seeds()
    if ens is None:
        sys.exit("无任何seed预测")
    gates(ens, "5-seed集成")
    if "--gates-only" not in sys.argv:
        # 逐个seed对照
        pass


if __name__ == "__main__":
    main()