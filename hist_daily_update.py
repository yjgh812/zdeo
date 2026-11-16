# -*- coding: utf-8 -*-
"""
hist_daily_update.py — HIST 日频推理（轻量，不重训）
================================================================================
依赖 hist_model_build.py 固化的 5 个 seed 权重(hist_model_seed{s}.pkl)。
每日: ①Alpha360 特征(与训练同配置)算到 target ②加载 5 个 seed 权重做 ensemble 推理
      ③重写 hist_pred_seed{s}.parquet(测试段 2026-01-01..target, 生产窗), 使 daily_signal_production_v4
        的 HIST 列不再停 8/13。
重训由 hist_model_build.py 一次性完成; 本脚本只做特征+前向, csi300 约 3-8 分钟。

用法:  python hist_daily_update.py [--date 2026-08-21] [--force]
幂等: 若已有预测已到 target 且非 --force, 直接跳过。
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, r"D:\zcodesj")
import hist_model_build as HMB   # 复用 QLIB_URI/OUT_DIR/常量/build_dataset/latest_cal

SEEDS = [1, 2, 3, 4, 5]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def patch_hist_forward_device():
    """GPU 崩溃兜底(2026-09-21): NVIDIA 独显驱动崩溃(nvlddmkm STOPPED/exit31)期间 cuda
    不可用, qlib HIST.forward 首行 torch.device(torch.get_device(x)) 在 CPU 张量上
    get_device 返回 -1 → RuntimeError("Device index must not be negative"), qlib 该行
    从未走过 CPU 分支。源码级替换为 x.device —— CUDA 下语义等价、CPU 下修复;
    不改 qlib 本体, 升级安全; GPU 恢复后同样兼容, 幂等可重复调用。"""
    import inspect
    import textwrap

    from qlib.contrib.model.pytorch_hist import HISTModel

    if getattr(HISTModel.forward, "__code__", None) is not None and \
            HISTModel.forward.__code__.co_filename == "<hist_forward_cpu_patch>":
        return  # 已打过补丁, 幂等返回
    try:
        src = textwrap.dedent(inspect.getsource(HISTModel.forward))
    except OSError:
        return  # forward 源码不可得(已替换或 C 扩展), 不动
    bad = "device = torch.device(torch.get_device(x))"
    if bad not in src:
        return  # qlib 已自行修复或结构变化, 不动
    ns = {}
    exec(compile(src.replace(bad, "device = x.device"),
                 "<hist_forward_cpu_patch>", "exec"), {"torch": torch}, ns)
    HISTModel.forward = ns["forward"]


def pred_of(dataset, seed: int):
    """加载 seed 权重, 对测试段返回 pd.Series((datetime,instrument), value)"""
    from qlib.contrib.model.pytorch_hist import HIST
    patch_hist_forward_device()
    path = os.path.join(HMB.OUT_DIR, f"hist_model_seed{seed}.pkl")
    if not os.path.exists(path):
        log(f"⚠ 缺 seed{seed} 权重: {path}")
        return None
    model = HIST(
        d_feat=6, hidden_size=64, num_layers=2, dropout=0,
        n_epochs=1, lr=1e-4, metric="ic", loss="mse",   # 推理不需要这些, 仅构造
        base_model="LSTM", model_path=HMB.BASE_MODEL,
        stock2concept=HMB.STOCK2CONCEPT, stock_index=HMB.STOCK_INDEX,
        GPU=0, seed=seed,
    )
    # 20261018 Mimosa遗留加固: 显式weights_only(自产state_dict兼容, 消除反序列化执行类)
    model.HIST_model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    model.HIST_model.to(model.device)
    model.fitted = True
    return model.predict(dataset, "test")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标日, 默认 qlib_bin 日历末位")
    ap.add_argument("--force", action="store_true", help="即使已到 target 也重算")
    args = ap.parse_args()
    target = args.date or HMB.latest_cal()

    # 幂等: 5 个 seed 预测全部已到 target 且非 force 才跳过(只查 seed1 会漏刷 2-5)
    if not args.force:
        if all(os.path.exists(os.path.join(HMB.OUT_DIR, f"hist_pred_seed{s}.parquet")) for s in SEEDS):
            try:
                mxes = []
                for s in SEEDS:
                    old = pd.read_parquet(os.path.join(HMB.OUT_DIR, f"hist_pred_seed{s}.parquet"))
                    mxes.append(old.index.get_level_values(0).max())
                if all(pd.Timestamp(mx) >= pd.Timestamp(target) for mx in mxes):
                    log(f"5 个 seed 预测均已到 {str(max(mxes))[:10]} >= 目标 {target}, 跳过(--force 可强制)")
                    return
            except Exception:
                pass

    log(f"HIST 日推: target={target}")

    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=HMB.QLIB_URI, region=REG_CN, kernels=4)

    t0 = time.time()
    dataset = HMB.build_dataset(target)
    log(f"数据集(Alpha360 csi300)就绪 ({time.time()-t0:.0f}s)")

    ens, missing = [], []
    for s in SEEDS:
        ser = pred_of(dataset, s)
        if ser is None:
            missing.append(s)
            continue
        df = pd.DataFrame({"score": ser})
        # 20261011: 预测工件 pkl→parquet(消除反序列化执行类, Mimosa扫描遗留加固)
        df.to_parquet(os.path.join(HMB.OUT_DIR, f"hist_pred_seed{s}.parquet"))
        log(f"seed{s}: {len(df)} 行, 最新={str(df.index.get_level_values(0).max())[:10]}")
        ens.append(ser)

    if missing:
        log(f"⚠ 缺 seed 权重 {missing}, 用可用 {len(ens)} 个 seed 集成")

    if not ens:
        log("✗ 无任何 seed 权重, 无法生成预测")
        sys.exit(2)

    combined = pd.concat(ens, axis=1).mean(axis=1)
    combined.name = "score"
    combo = pd.DataFrame({"score": combined})
    combo.to_pickle(os.path.join(HMB.OUT_DIR, "hist_ensemble_seed12345.pkl"))
    last = str(combo.index.get_level_values(0).max())[:10]

    # 自检
    errs = []
    # 2026-08-24 P2修复: 统一 Timestamp 比较（last 恒为横线格式, 手动传紧凑 --date 时旧字符串比较必假 FAIL）
    # 注: 此处勿再加局部 import pandas as pd — 会遮蔽模块级导入, 使 main() 内更早的 pd.* 调用抛 UnboundLocalError
    try:
        if pd.Timestamp(last) != pd.Timestamp(target):
            errs.append(f"最新预测日 {last} != 目标 {target}")
    except ValueError:
        if last != target:
            errs.append(f"最新预测日 {last} != 目标 {target}")
    n_inst_last = combo.xs(combo.index.get_level_values(0).max(), level=0).shape[0]
    if n_inst_last < 250:
        errs.append(f"目标日样本数过少: {n_inst_last}")
    if combo["score"].isna().all():
        errs.append("预测全 NaN")
    if errs:
        for e in errs:
            log(f"   ✗ {e}")
        log("   → 自检 FAIL")
        sys.exit(2)
    log(f"自检 OK: {last} 共 {n_inst_last} 只 | 时间 {time.time()-t0:.0f}s")
    log("HIST 日推完成 ✅")


if __name__ == "__main__":
    main()
