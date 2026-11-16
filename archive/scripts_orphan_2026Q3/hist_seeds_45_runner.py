# -*- coding: utf-8 -*-
"""⛔ 已弃用 (2026-08-22 qfq 根治) — 本脚本【禁止运行】, 运行即退出。
被 hist_model_build.py(重训) + hist_daily_update.py(日推) 取代。
原因: 本脚本 qrun 旧窗 yaml 并覆盖写 hist_pred_seed1-5.pkl, 会把 qfq 新预测回退成旧产物。
"""
import sys
print("⛔ hist_seeds_45_runner.py 已弃用(2026-08-22 qfq 根治), 禁止运行。", file=sys.stderr)
sys.exit(1)

# ========== 以下为被弃用的原逻辑(仅供回溯参考, 不可运行) ==========
# HIST seeds 4-5 runner (已去PortAna,无SH000300不崩) — 2026-08-19
import glob
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, r"D:\zcodesj")

YAML = r"D:\zcodesj\qlib_exp\workflow_hist_alpha360_tulong_noport.yaml"
EXP = r"D:\zcodesj\qlib_exp"
OUT_DIR = r"D:\zcodesj\qlib_exp\hist_data"
STATUS = r"D:\zcodesj\reports\_hist_seeds.status.txt"

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


def run_qrun(tag):
    env = dict(os.environ)
    env.update({"MLFLOW_ALLOW_FILE_STORE": "true"})
    t0 = time.time()
    r = subprocess.run(
        ["python", "-c",
         "from qlib.cli.run import run; import sys; "
         "sys.argv=['qrun',r'" + YAML + "']; run()"],
        cwd=EXP, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="ignore", timeout=7200)
    with open(rf"D:\zcodesj\reports\_hist_seed{tag}.log", "w", encoding="utf-8") as f:
        f.write((r.stdout or "")[-120000:] + "\n--stderr--\n" + (r.stderr or "")[-30000:])
    return r.returncode


def main():
    status("=== HIST seeds 4-5 (去PortAna) ===")
    # seeds 4-5
    for i in (4, 5):
        rc = run_qrun(str(i))
        status(f"seed{i} rc={rc}")
        if rc != 0:
            # even if PortAna crashes, pred.s saved earlier; just warn
            status(f"seed{i}非零但预测可能已存,继续")
    # collect ALL available pred.pkl from mlruns into hist_data/
    pats = sorted(glob.glob(rf"{EXP}\mlruns\*\*\*\artifacts\pred.pkl"), key=os.path.getmtime)
    status(f"收集到{len(pats)}个预测文件")
    for j, p in enumerate(pats):
        dst = rf"{OUT_DIR}\hist_pred_seed{j+1}.pkl"
        shutil.copy(p, dst)
        status(f"{j+1}: {dst}")
    status("=== seeds 4-5 完成 ===")


if __name__ == "__main__":
    main()