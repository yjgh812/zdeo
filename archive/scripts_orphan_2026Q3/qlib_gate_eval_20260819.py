# -*- coding: utf-8 -*-
"""HIST/LSTM三关评估 — 从qlib recorder预测pkl加载 (2026-08-19晨备用)
用法: python qlib_gate_eval_20260819.py <pred.pkl路径或mlruns搜索LSTM/HIST> [--tag hist]
pred.pkl格式:qlib SignalRecord保存的预测DataFrame(index=datetime,instrument,列score)
"""
import glob
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

TAG = "hist"


def find_pred(tag):
    if len(sys.argv) > 1 and sys.argv[1].endswith(".pkl"):
        return sys.argv[1]
    cands = []
    for pat in (rf"D:\zcodesj\qlib_exp\mlruns\*\*\*\predictions.pkl",
                rf"D:\zcodesj\qlib_exp\mlruns\*\*\*\pred.pkl"):
        cands += glob.glob(pat)
    cands = sorted(set(cands), key=lambda p: (glob.getmtime(p)))
    return cands[-1] if cands else None


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
    g3 = (yr > 0).all()
    print(f"\n[{name}] n={len(df)} 日数={len(t)}")
    print(f"  关1 日RankIC {m:+.4f} (t={tv:+.2f}, 正{(t['ic']>0).mean():.0%}) -> {'PASS' if g1 else 'FAIL'}")
    print(f"  关2 单调r={mono:+.2f} 价差{sp.mean():+.5f} 胜率{(sp>0).mean():.0%} -> {'PASS' if g2 else 'FAIL'}")
    print(f"  关3 分年{yr.round(4).to_dict()} -> {'PASS' if g3 else 'FAIL'}")
    v = all([g1, g2, g3])
    print(f"  [VERDICT] {'PASS ✅' if v else 'FAIL ❌'}")
    return v


def main():
    tag = TAG
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
    p = find_pred(tag)
    if not p:
        sys.exit("无predictions.pkl")
    pred = pd.read_pickle(p)
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    print(f"[pred] {p}: {len(pred)} 样本")
    ok = gates(pred, f"{tag}三关")
    with open(rf"D:\zcodesj\reports\QLIB_{tag.upper()}_GATE_20260819.txt", "w",
              encoding="utf-8") as f:
        f.write(f"{tag} gate eval from {p}\n")


if __name__ == "__main__":
    main()