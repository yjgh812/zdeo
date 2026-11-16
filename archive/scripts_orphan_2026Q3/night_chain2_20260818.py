# -*- coding: utf-8 -*-
"""夜间GPU接力链v5 — 2026-08-18
链: 等Kronos v3-fix完成 → 官方30月sweep → Fwd10评估
    → LSTM Alpha360底座(qrun) → 提取模型pkl → HIST Alpha360(qrun)
保护: 每棒GPU车道检查+3次重试+分文件日志; 状态实时写_status文件
"""
import glob
import os
import shutil
import subprocess
import time

STATUS = r"D:\zcodesj\reports\_night_chain2_status.txt"
LOG_FT = r"D:\zcodesj\reports\_kronos_ft_v3fix.log"
DONE_STR = "Basemodel training completed"
TIMEOUT = time.time() + 14 * 3600
EXP = r"D:\zcodesj\qlib_exp"
LSTM_PKL_TARGET = rf"{EXP}\hist_data\model_lstm_csi300_tulong.pkl"


def status(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(STATUS, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def gpu_free():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15).stdout.strip().splitlines()[0]
        return int(out) < 1000
    except Exception:
        return False


def run(name, cmd, cwd=None, env_extra=None, tag=None):
    status(f"{name} 发车")
    t0 = time.time()
    log = rf"D:\zcodesj\reports\_chain2_{tag or 'stage'}.log"
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    for attempt in range(3):
        while not gpu_free():
            status(f"{name}: GPU未释放,60s后重查")
            time.sleep(60)
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", env=env)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%F %T')} attempt{attempt+1} rc={r.returncode} =====\n")
            f.write((r.stdout or "")[-200000:])
            f.write("\n--stderr--\n")
            f.write((r.stderr or "")[-50000:])
        if r.returncode == 0:
            status(f"{name} 完成✓ {(time.time()-t0)/60:.0f}min 日志{log}")
            return True
        status(f"{name} 第{attempt+1}次失败 rc={r.returncode}: {(r.stderr or '')[-300:]}")
        time.sleep(60)
    return False


def wait_v3fix():
    status("等Kronos v3-fix完成...")
    mtime_last, t_last = None, time.time()
    while time.time() < TIMEOUT:
        time.sleep(120)
        try:
            body = open(LOG_FT, encoding="utf-8", errors="ignore").read()
        except Exception:
            body = ""
        if DONE_STR in body:
            status("v3-fix训练完成✓")
            return True
        mtime = os.path.getmtime(LOG_FT) if os.path.exists(LOG_FT) else 0
        if mtime != mtime_last:
            mtime_last, t_last = mtime, time.time()
        elif time.time() - t_last > 1200 and gpu_free():
            status("⚠日志停更20min且GPU空闲→放行")
            return False
    return False


def main():
    status("=== 夜间链v5启动 ===")
    ok = wait_v3fix()
    if ok:
        if run("Kronos官方30月sweep",
               ["python", "-u", r"D:\zcodesj\kronos_sweep_v2_20260818.py",
                "--ckpt", r"D:\zcodesj\Kronos\finetune_csv\ckpt_ashare_v3_fix",
                "--out", r"D:\zcodesj\Kronos\finetune_csv\ckpt_ashare_v3_fix_sweep_full.csv",
                "--start-month", "202401", "--end-month", "202606"], tag="ksweep"):
            run("Kronos Fwd10评估",
                ["python", "-u", r"D:\zcodesj\kronos_eval_v2_20260818.py",
                 r"D:\zcodesj\Kronos\finetune_csv\ckpt_ashare_v3_fix_sweep_full.csv", "10"],
                tag="keval")
    else:
        status("跳过Kronos sweep(训练未确认完成)")
    # LSTM底座
    if run("LSTM Alpha360底座",
           ["python", "-c",
            "from qlib.cli.run import run; import sys; "
            "sys.argv=['qrun','workflow_lstm_alpha360_tulong.yaml']; run()"],
           cwd=EXP, env_extra={"MLFLOW_ALLOW_FILE_STORE": "true"}, tag="lstm"):
        # 提取模型pkl(recorder artifacts里最新的models/*.pkl)
        cands = sorted(glob.glob(rf"{EXP}\mlruns\*\*\*\models\*.pkl"),
                       key=os.path.getmtime)
        if cands:
            shutil.copy(cands[-1], LSTM_PKL_TARGET)
            status(f"LSTM底座模型: {cands[-1]} -> {LSTM_PKL_TARGET}")
            run("HIST Alpha360",
                ["python", "-c",
                 "from qlib.cli.run import run; import sys; "
                 "sys.argv=['qrun','workflow_hist_alpha360_tulong.yaml']; run()"],
                cwd=EXP, env_extra={"MLFLOW_ALLOW_FILE_STORE": "true"}, tag="hist")
        else:
            status("⚠未找到LSTM模型pkl, HIST跳过")
    else:
        status("LSTM底座失败,HIST跳过")
    status("=== 夜间链v5结束 ===")


if __name__ == "__main__":
    main()