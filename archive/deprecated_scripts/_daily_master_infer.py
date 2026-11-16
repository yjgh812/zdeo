# -*- coding: utf-8 -*-
"""⚠️ 已弃用 (2026-08-22) — 请勿再调用本脚本！
被 daily_signal_production_v4.py 取代(16:00 cron 已改指 v4)。
本脚本缺陷: 实时算完 MASTER 预测但【不落盘】(无 to_pickle), 结果被丢弃;
  而旧合并脚本读 8/18 冻结 pred pkl(停 8/11) → 生产 MASTER 静默过期。
  根治见 reports/QLIB_BIN_REBUILD_PLAN_20260822.md 第九节。
2026-08-25 归档: 从 D:\zcodesj 项目根移至 archive/deprecated_scripts/（R3 执行完毕 O29）。
"""
"""MASTER日推 — qlib计算特征+推理 (2026-08-19晚)"""
import glob
import sys
import time

import numpy as np
import pandas as pd
import torch

TARGET = "2026-08-13"  # 默认: qlib_bin实际数据最新日(日历到8/19但股票数据到8/13)
BASE = r"D:\zcodesj"


def main():
    global TARGET
    for arg in sys.argv[1:]:
        if arg.startswith("--date"):
            TARGET = arg.split("=")[1]
    sys.path.insert(0, rf"{BASE}\strategy_libs\MASTER")
    t0 = time.time()

    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=r"D:\workbuddyspace\qlib_data\qlib_bin", region=REG_CN, kernels=1)
    from qlib.contrib.data.handler import Alpha158
    from qlib.data import D

    h = Alpha158(instruments="csi300", start_time="2026-01-01", end_time=TARGET)
    cfg = h.get_feature_config()
    fields, names = cfg[0], cfg[1]
    feat = D.features(D.instruments("csi300"), fields, start_time="2026-01-01", end_time=TARGET)
    feat.columns = names
    lab = D.features(D.instruments("csi300"), ["Ref($close,-2)/Ref($close,-1)-1"],
                     start_time="2026-01-01", end_time=TARGET)
    lab.columns = ["LABEL0"]
    df = feat.join(lab, how="left")
    print(f"feat: {df.shape} ({time.time()-t0:.0f}s)")

    mkt = pd.read_parquet(rf"{BASE}\strategy_libs\MASTER\data\tulong\_market_cache.parquet")
    mkt = mkt.reindex(df.index.get_level_values("datetime").unique())
    df = df.reset_index().merge(mkt, left_on="datetime", right_index=True, how="left")
    df = df.set_index(["datetime", "instrument"]).sort_index()

    ns = pd.read_parquet(rf"{BASE}\strategy_libs\MASTER\data\tulong\_norm_stats.parquet")
    feat_cols = [c for c in df.columns if c != "LABEL0"]
    z = (df[feat_cols] - ns["med"].reindex(feat_cols).values) / ns["mad"].reindex(feat_cols).values
    df[feat_cols] = z.clip(-3, 3).fillna(0.0)
    df["LABEL0"] = 0.0

    dates = sorted(df.index.get_level_values("datetime").unique())
    dt = pd.Timestamp(TARGET)
    pos = dates.index(dt)
    need = dates[pos - 8:pos + 1]
    sub = df[df.index.get_level_values("datetime").isin(need)]
    arrs, idx = [], []
    for ins, g in sub.groupby(level="instrument"):
        if len(g) < 9:
            continue
        a = g.values.astype(np.float32)
        w = np.lib.stride_tricks.sliding_window_view(a, 8, axis=0).transpose(0, 2, 1)
        arrs.append(w[-1:])
        idx.append(g.index[-1])
    data = np.concatenate(arrs, axis=0)
    index = pd.Index([t[1] for t in idx], name="instrument")
    print(f"TARGET {TARGET}: {len(data)} 样本, shape={data.shape} ({time.time()-t0:.0f}s)")

    from master import MASTERModel
    ens = []
    for s in range(5):
        pkl = glob.glob(rf"{BASE}\strategy_libs\MASTER\model\tulong_csi300_{s}.pkl")
        if not pkl:
            continue
        m = MASTERModel(d_feat=158, d_model=256, t_nhead=4, s_nhead=2,
                        T_dropout_rate=0.5, S_dropout_rate=0.5, beta=5,
                        gate_input_start_index=158, gate_input_end_index=221,
                        n_epochs=1, lr=1e-5, GPU=0, seed=s,
                        train_stop_loss_thred=0.95,
                        save_path=rf"{BASE}\strategy_libs\MASTER\model", save_prefix="tulong_csi300")
        m.load_param(pkl[-1])
        m.init_model()
        m.model.to(m.device).eval()
        with torch.no_grad():
            x = torch.from_numpy(data).to(m.device).float()
            pred = m.model(x).detach().cpu().numpy().ravel()
        ens.append(pd.Series(pred, index=index))
    master = pd.concat(ens, axis=1).mean(axis=1)
    print(f"master: {master.shape}")
    print(master.nlargest(10).to_string())


if __name__ == "__main__":
    main()
