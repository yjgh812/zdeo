# -*- coding: utf-8 -*-
"""链v8 — LSTM底座(官方配置修正版)+HIST+三关 (2026-08-19晨)"""
import glob
import os
import shutil
import subprocess
import time

STATUS = r"D:\zcodesj\reports\_chain5_status.txt"
EXP = r"D:\zcodesj\qlib_exp"
LSTM_PKL = rf"{EXP}\hist_data\model_lstm_csi300_tulong.pkl"

try:
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
except Exception:
    pass


def status(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(STATUS, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def gpu_free():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15).stdout.strip().splitlines()[0]
        return int(out) < 2500
    except Exception:
        return False


def run(name, cmd, tag, cwd=None, env_extra=None):
    status(f"{name} 发车")
    t0 = time.time()
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    for att in range(3):
        n = 0
        while not gpu_free() and n < 120:
            n += 1
            time.sleep(60)
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", env=env)
        with open(rf"D:\zcodesj\reports\_chain5_{tag}.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%F %T')} att{att+1} rc={r.returncode} =====\n"
                    + (r.stdout or "")[-150000:] + "\n--stderr--\n" + (r.stderr or "")[-30000:])
        if r.returncode == 0:
            status(f"{name} 完成✓ {(time.time()-t0)/60:.0f}min")
            return True
        status(f"{name} att{att+1}失败 rc={r.returncode}: {(r.stderr or '')[-250:]}")
        time.sleep(60)
    return False


def main():
    status("=== 链v8: LSTM(官方配置) → HIST → 三关 ===")
    if run("LSTM Alpha360底座",
           ["python", "-c", "from qlib.cli.run import run; import sys; "
            "sys.argv=['qrun','workflow_lstm_alpha360_tulong.yaml']; run()"],
           "lstm", cwd=EXP, env_extra={"MLFLOW_ALLOW_FILE_STORE": "true"}):
        cands = sorted(glob.glob(rf"{EXP}\mlruns\*\*\*\models\*.pkl"), key=os.path.getmtime)
        if cands:
            shutil.copy(cands[-1], LSTM_PKL)
            status(f"LSTM模型: {cands[-1]}")
            run("HIST Alpha360",
                ["python", "-c", "from qlib.cli.run import run; import sys; "
                 "sys.argv=['qrun','workflow_hist_alpha360_tulong.yaml']; run()"],
                "hist", cwd=EXP, env_extra={"MLFLOW_ALLOW_FILE_STORE": "true"})
        else:
            status("⚠无LSTM pkl")
    status("=== 链v8结束 ===")


if __name__ == "__main__":
    main()