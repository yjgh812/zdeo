# -*- coding: utf-8 -*-
"""链v7 — Kronos双模型对决+下游 (2026-08-19凌晨, 事故后重构)
双模型: A=best_model(E1 val最优,官方语义) B=checkpoint_latest(E7@6000,最深训练)
流程: 12月定向A → 12月定向B → 胜者30月全窗 → Fwd10终审 → LSTM底座 → HIST
"""
import glob
import os
import shutil
import subprocess
import time

STATUS = r"D:\zcodesj\reports\_chain4_status.txt"
EXP = r"D:\zcodesj\qlib_exp"
CK = r"D:\zcodesj\Kronos\finetune_csv"
LSTM_PKL_TARGET = rf"{EXP}\hist_data\model_lstm_csi300_tulong.pkl"

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


def wait_gpu(m):
    n = 0
    while not gpu_free():
        n += 1
        if n % 15 == 1:
            status(f"{m}: 等GPU({n}分钟)")
        if n > 180:
            return False
        time.sleep(60)
    return True


def run(name, cmd, tag, cwd=None, env_extra=None):
    status(f"{name} 发车")
    t0 = time.time()
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    for att in range(3):
        if not wait_gpu(name):
            return False
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", env=env)
        with open(rf"D:\zcodesj\reports\_chain4_{tag}.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%F %T')} att{att+1} rc={r.returncode} =====\n"
                    + (r.stdout or "")[-150000:] + "\n--stderr--\n" + (r.stderr or "")[-30000:])
        if r.returncode == 0:
            status(f"{name} 完成✓ {(time.time()-t0)/60:.0f}min")
            return True
        status(f"{name} att{att+1}失败 rc={r.returncode}: {(r.stderr or '')[-250:]}")
        time.sleep(60)
    return False


def parse_best_ic(eval_tag):
    """从chain4_keval日志抓各信号IC, 返回(最优信号列名, IC)"""
    best = (None, -9)
    try:
        body = open(rf"D:\zcodesj\reports\_chain4_{eval_tag}.log", encoding="utf-8", errors="ignore").read()
        for line in body.splitlines():
            if "IC均值" in line and "->" in line:
                col = line.split("]")[0].split("[")[-1].strip()
                ic = float(line.split("IC均值")[1].split()[0])
                if ic > best[1]:
                    best = (col, ic)
    except Exception as e:
        status(f"parse_best_ic异常: {e}")
    return best


def main():
    status("=== 链v7: Kronos双模型对决 → LSTM → HIST ===")
    # 12月定向 A(E1-best) B(E7-latest)
    okA = run("定向A: E1best 12月", ["python", "-u", r"D:\zcodesj\kronos_sweep_v2_20260818.py",
                                     "--ckpt", rf"{CK}\ckpt_ashare_v3_fix",
                                     "--out", rf"{CK}\v3fixA_E1_sweep12.csv",
                                     "--start-month", "202507", "--end-month", "202606"], "swA")
    okB = run("定向B: E7latest 12月", ["python", "-u", r"D:\zcodesj\kronos_sweep_v2_20260818.py",
                                       "--ckpt", rf"{CK}\ckpt_v3fix_E7",
                                       "--out", rf"{CK}\v3fixB_E7_sweep12.csv",
                                       "--start-month", "202507", "--end-month", "202606"], "swB")
    if not (okA or okB):
        status("双定向全失败,链止")
        return
    winner, wcsv, wckpt = None, None, None
    if okA:
        run("评估A", ["python", "-u", r"D:\zcodesj\kronos_eval_v2_20260818.py",
                      rf"{CK}\v3fixA_E1_sweep12.csv", "10"], "evA")
    if okB:
        run("评估B", ["python", "-u", r"D:\zcodesj\kronos_eval_v2_20260818.py",
                      rf"{CK}\v3fixB_E7_sweep12.csv", "10"], "evB")
    a = parse_best_ic("evA") if okA else (None, -9)
    b = parse_best_ic("evB") if okB else (None, -9)
    status(f"定向结果 A(E1)={a} B(E7)={b}")
    if a[1] >= b[1]:
        winner, wcsv, wckpt = "A_E1best", rf"{CK}\v3fixA_E1_sweep12.csv", rf"{CK}\ckpt_ashare_v3_fix"
    else:
        winner, wcsv, wckpt = "B_E7latest", rf"{CK}\v3fixB_E7_sweep12.csv", rf"{CK}\ckpt_v3fix_E7"
    status(f"胜者={winner} → 30月全窗终审")
    if run(f"终审sweep {winner}", ["python", "-u", r"D:\zcodesj\kronos_sweep_v2_20260818.py",
                                   "--ckpt", wckpt,
                                   "--out", wcsv.replace("_sweep12", "_sweep_full"),
                                   "--start-month", "202401", "--end-month", "202606"], "swF"):
        run("Kronos终审评估", ["python", "-u", r"D:\zcodesj\kronos_eval_v2_20260818.py",
                              wcsv.replace("_sweep12", "_sweep_full"), "10"], "evF")
    # LSTM底座 → HIST
    if run("LSTM Alpha360底座",
           ["python", "-c", "from qlib.cli.run import run; import sys; "
            "sys.argv=['qrun','workflow_lstm_alpha360_tulong.yaml']; run()"],
           "lstm", cwd=EXP, env_extra={"MLFLOW_ALLOW_FILE_STORE": "true"}):
        cands = sorted(glob.glob(rf"{EXP}\mlruns\*\*\*\models\*.pkl"), key=os.path.getmtime)
        if cands:
            shutil.copy(cands[-1], LSTM_PKL_TARGET)
            status(f"LSTM模型就位: {LSTM_PKL_TARGET}")
            run("HIST Alpha360",
                ["python", "-c", "from qlib.cli.run import run; import sys; "
                 "sys.argv=['qrun','workflow_hist_alpha360_tulong.yaml']; run()"],
                "hist", cwd=EXP, env_extra={"MLFLOW_ALLOW_FILE_STORE": "true"})
        else:
            status("⚠无LSTM pkl,HIST跳过")
    status("=== 链v7结束 ===")


if __name__ == "__main__":
    main()