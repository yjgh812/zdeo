# -*- coding: utf-8 -*-
"""
hist_model_build.py — HIST 5-seed 模型固化（一次性，GPU 训练，约 2-4 小时）
================================================================================
背景: 8/22 查明 HIST 每日推理无法增量化的根因 — 原 5-seed 训练权重从未持久化
  (qlib workflow 未配 ModelRecorder, HIST.fit 的 save_path=None → 落 ~/tmp/mkstemp 临时文件, 已被清)。
  只剩 hist_pred_seed*.parquet 预测序列(停 8/13)。要"日常化"必须先固化 5 个 seed 权重。

本脚本: 按 workflow_hist_alpha360_tulong.yaml 完全相同的模型/数据/训练配置, 训练 5 个 seed,
  每个 seed 权重 torch.save 到 qlib_exp/hist_data/hist_model_seed{s}.pkl(显式 seed 保证可复现)。
  训练段(train 2017-2021 / valid 2022-2023.06)与端到端无关; end_time 取当前 qlib_bin 日历末位,
  使测试段预测一步到位到最新交易日。

用法:  python hist_model_build.py [--date 2026-08-21] [--seeds 1,2,3,4,5] [--epochs 200]
注意: 长时间 GPU 训练, 发车前确认 nvidia-smi 空闲; 脚本写完权重即 done, 勿以进程退出误判。
"""
import argparse
import os
import sys
import time

import numpy as np
import torch

QLIB_URI = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\qlib_exp\hist_data"
BASE_MODEL = r"D:\zcodesj\qlib_exp\hist_data\model_lstm_csi300_tulong.pkl"   # 预训练 LSTM base
STOCK2CONCEPT = r"D:\zcodesj\qlib_exp\hist_data\stock2concept.npy"
STOCK_INDEX = r"D:\zcodesj\qlib_exp\hist_data\stock_index.npy"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def latest_cal() -> str:
    with open(rf"{QLIB_URI}\calendars\day.txt", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()][-1]


# 2026-08-22 完全根治: 训练窗改生产窗(原 train≤2021 已 5 年陈旧) + qfq 基准(qlib_bin 重建后自动生效)
TRAIN = ["2017-01-01", "2024-12-31"]
VALID = ["2025-01-01", "2025-12-31"]
TEST_START = "2026-01-01"
# 2026-08-29 前视防重叠闸门(round11 ml腿教训): 训练窗与生产 test 段重叠=in-sample 预测静默入产。
# hist_daily_update.py 亦 import 本模块, 闸门对日推同样生效; 改 TRAIN/TEST_START 必须保持 TRAIN 尾日 < TEST_START。
from datetime import date as _date
if _date.fromisoformat(TRAIN[1]) >= _date.fromisoformat(TEST_START):
    raise SystemExit("[FATAL] TRAIN 尾日 >= TEST_START → 训练/生产窗重叠, 拒绝构建(import exit)")


def build_dataset(end_time: str):
    from qlib.contrib.data.handler import Alpha360
    from qlib.data.dataset import DatasetH

    handler = Alpha360(
        instruments="csi300",
        start_time="2016-06-01",
        end_time=end_time,
        fit_start_time=TRAIN[0],
        fit_end_time=TRAIN[1],
        infer_processors=[
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        learn_processors=[
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        label=["Ref($close, -2) / Ref($close, -1) - 1"],
    )
    return DatasetH(
        handler,
        segments={
            "train": TRAIN,
            "valid": VALID,
            "test": [TEST_START, end_time],
        },
    )


def train_seed(dataset, seed: int, n_epochs: int) -> str:
    from qlib.contrib.model.pytorch_hist import HIST

    model = HIST(
        d_feat=6, hidden_size=64, num_layers=2, dropout=0,
        n_epochs=n_epochs, lr=1e-4, metric="ic", loss="mse",
        base_model="LSTM", model_path=BASE_MODEL,
        stock2concept=STOCK2CONCEPT, stock_index=STOCK_INDEX,
        GPU=0, seed=seed,
    )
    save_path = os.path.join(OUT_DIR, f"hist_model_seed{seed}.pkl")
    evals = {}
    t0 = time.time()
    model.fit(dataset, evals_result=evals, save_path=save_path)
    best = max(evals.get("valid", [0.0])) if evals.get("valid") else None
    with open(os.path.join(OUT_DIR, f"hist_model_seed{seed}.ic.txt"), "w") as f:
        f.write(f"best_valid_ic={best}\nepochs={n_epochs}\nseed={seed}\n")
    log(f"seed{seed} 完成 ({time.time()-t0:.0f}s) 最优valid_IC={best} -> {save_path}")
    return save_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="end_time, 默认 qlib_bin 日历末位")
    ap.add_argument("--seeds", default="1,2,3,4,5")
    ap.add_argument("--epochs", type=int, default=200)
    args = ap.parse_args()
    end_time = args.date or latest_cal()
    seeds = [int(s) for s in args.seeds.split(",")]
    log(f"HIST 5-seed 固化启动: seeds={seeds} epochs={args.epochs} end={end_time}")

    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=QLIB_URI, region=REG_CN, kernels=4)
    os.makedirs(OUT_DIR, exist_ok=True)
    log("构建数据集(Alpha360 csi300)...")
    t0 = time.time()
    dataset = build_dataset(end_time)
    log(f"数据集就绪 ({time.time()-t0:.0f}s)")
    for s in seeds:
        train_seed(dataset, s, args.epochs)
    missing = [s for s in seeds if not os.path.exists(os.path.join(OUT_DIR, f"hist_model_seed{s}.pkl"))]
    if missing:
        log(f"⚠ 缺失: {missing}")
        sys.exit(2)
    log("=== 全部 seed 固化完成, 权重齐全 ✅ ===")


if __name__ == "__main__":
    main()
