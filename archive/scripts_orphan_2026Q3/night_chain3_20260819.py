# -*- coding: utf-8 -*-
"""夜间GPU接力链v6 — 2026-08-19凌晨 (v5超时重构版)
改进: ①SetThreadExecutionState防睡眠(v3-fix疑似睡眠后CUDA卡死) ②全阶段GPU门控
    ③首棒=断点续训v3-fix(从step6000自动恢复)
链: [等GPU空] 续训v3-fix → Kronos30月sweep → Fwd10评估 → LSTM底座 → 提pkl → HIST
"""
import glob
import os
import shutil
import subprocess
import time

STATUS = r"D:\zcodesj\reports\_night_chain3_status.txt"
EXP = r"D:\zcodesj\qlib_exp"
LSTM_PKL_TARGET = rf"{EXP}\hist_data\model_lstm_csi300_tulong.pkl"
CKPT = r"D:\zcodesj\Kronos\finetune_csv\ckpt_ashare_v3_fix"


def status(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(STATUS, "a", encoding="utf-8") as f:
        f.write(line + "\n")


try:  # 防系统睡眠(链存活期间) — v3-fix 21:54卡死疑似睡眠唤醒遗症
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
    status("[keep-awake] SYSTEM_REQUIRED|AWAKE已设置")
except Exception as e:
    status(f"[keep-awake] 失败: {e}")


def gpu_free():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15).stdout.strip().splitlines()[0]
        return int(out) < 2500   # 8GB卡: ≤2500基线(闲置UI挂CUDA上下文)时仍有6.6GB,训练5GB无溢出
    except Exception:
        return False


def wait_gpu(name, max_h=24):
    t0 = time.time()
    while not gpu_free():
        if time.time() - t0 > max_h * 3600:
            status(f"{name}: 等GPU超{max_h}h,放弃")
            return False
        time.sleep(120)
    return True


def run(name, cmd, cwd=None, env_extra=None, tag=None):
    status(f"{name} 发车")
    t0 = time.time()
    log = rf"D:\zcodesj\reports\_chain3_{tag}.log"
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    for attempt in range(3):
        if not wait_gpu(name):
            return False
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", env=env)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%F %T')} attempt{attempt+1} rc={r.returncode} =====\n")
            f.write((r.stdout or "")[-200000:])
            f.write("\n--stderr--\n")
            f.write((r.stderr or "")[-50000:])
        # 进程级卡死检测: 子进程结束后若日志尾部无变化且rc!=0才重试; rc=0即成
        if r.returncode == 0:
            status(f"{name} 完成✓ {(time.time()-t0)/60:.0f}min")
            return True
        status(f"{name} 第{attempt+1}次失败 rc={r.returncode}: {(r.stderr or '')[-300:]}")
        time.sleep(60)
    return False


def main():
    status("=== 链v6启动 (等Topaz让路→续训v3fix→sweep→eval→LSTM→HIST) ===")
    # 棒0: 续训v3-fix(checkpoint_latest@step6000自动恢复)
    if run("Kronos v3-fix续训",
           ["python", "-u", r"D:\zcodesj\Kronos\finetune_csv\train_sequential.py",
            "--config", r"D:\zcodesj\Kronos\finetune_csv\configs\config_ashare_v3_fix.yaml"],
           cwd=r"D:\zcodesj\Kronos\finetune_csv",
           env_extra={"PYTHONPATH": r"D:\zcodesj\Kronos;D:\zcodesj\Kronos\finetune_csv"},
           tag="ft"):
        if run("Kronos官方30月sweep",
               ["python", "-u", r"D:\zcodesj\kronos_sweep_v2_20260818.py",
                "--ckpt", CKPT,
                "--out", CKPT + "_sweep_full.csv",
                "--start-month", "202401", "--end-month", "202606"], tag="ksweep"):
            run("Kronos Fwd10评估",
                ["python", "-u", r"D:\zcodesj\kronos_eval_v2_20260818.py",
                 CKPT + "_sweep_full.csv", "10"], tag="keval")
    else:
        status("v3-fix续训失败,Kronos段止")
    # LSTM底座 → HIST
    if run("LSTM Alpha360底座",
           ["python", "-c",
            "from qlib.cli.run import run; import sys; "
            "sys.argv=['qrun','workflow_lstm_alpha360_tulong.yaml']; run()"],
           cwd=EXP, env_extra={"MLFLOW_ALLOW_FILE_STORE": "true"}, tag="lstm"):
        cands = sorted(glob.glob(rf"{EXP}\mlruns\*\*\*\models\*.pkl"), key=os.path.getmtime)
        if cands:
            shutil.copy(cands[-1], LSTM_PKL_TARGET)
            status(f"LSTM模型: {cands[-1]} -> {LSTM_PKL_TARGET}")
            run("HIST Alpha360",
                ["python", "-c",
                 "from qlib.cli.run import run; import sys; "
                 "sys.argv=['qrun','workflow_hist_alpha360_tulong.yaml']; run()"],
                cwd=EXP, env_extra={"MLFLOW_ALLOW_FILE_STORE": "true"}, tag="hist")
        else:
            status("⚠未找到LSTM模型pkl,HIST跳过")
    status("=== 链v6结束 ===")


if __name__ == "__main__":
    main()