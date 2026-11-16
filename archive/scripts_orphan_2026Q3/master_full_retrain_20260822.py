# -*- coding: utf-8 -*-
"""MASTER 5-seed 全量重训（qfq 基准 + 生产窗, GPU）— 2026-08-22 完全根治
跑 5 seed(0-4), 每 seed epochs=30, 输出 TEST 指标(RankIC) + 模型 tulong_csi300_{s}.pkl。
用法:  python master_full_retrain_20260822.py     (GPU; 发车前确认 nvidia-smi 空闲)
原 runner(tulong_seeds_runner.py) 是 --cpu 旧版, 且 epochs 25/30 不一, 本脚本统一 30。
"""
import os
import subprocess
import sys
import time

D = r"D:\zcodesj\strategy_libs\MASTER"
SEEDS = list(range(5))
EPOCHS = 30
RESULT = r"D:\zcodesj\reports\MASTER_RETRAIN_QFQ_20260822.txt"


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(RESULT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    log(f"=== MASTER 5-seed 全量重训(qfq+生产窗) 启动: seeds={SEEDS} epochs={EPOCHS} ===")
    for s in SEEDS:
        t0 = time.time()
        log(f"seed{s} 发车")
        r = subprocess.run(["python", "-u", "tulong_train.py", "--seed", str(s), "--epochs", str(EPOCHS)],
                           cwd=D, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        # 提取 TEST 指标行
        tail = [l for l in (r.stdout or "").splitlines() if "TEST" in l or "train=" in l or "seed" in l.lower()]
        log(f"seed{s} rc={r.returncode} 耗时{(time.time()-t0)/60:.0f}min")
        for l in tail[-4:]:
            log(f"  {l}")
        if r.returncode != 0:
            log(f"  STDERR: {(r.stderr or '')[-300:]}")
    # 校验 5 个模型文件
    missing = [s for s in SEEDS if not os.path.exists(os.path.join(D, "model", f"tulong_csi300_{s}.pkl"))]
    if missing:
        log(f"⚠ 缺失模型: {missing}")
        sys.exit(2)
    log("=== MASTER 5-seed 重训完成, 模型齐全 ✅ ===")


if __name__ == "__main__":
    main()
