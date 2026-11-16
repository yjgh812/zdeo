# -*- coding: utf-8 -*-
"""⛔ 已弃用 (2026-08-22 qfq 根治) — 本脚本【禁止运行】, 运行即退出。
被 hist_model_build.py(重训) + hist_daily_update.py(日推) 取代。
原因: 本脚本 qrun workflow_hist_alpha360_tulong.yaml(旧窗 train≤2021 + 旧 raw 基准),
  并把 mlruns pred.pkl 覆盖写 hist_pred_seed1-5.pkl —— 会把已刷到 8/21 的 qfq 新预测回退成旧产物。
"""
import sys
print("⛔ hist_seeds_runner_20260819.py 已弃用(2026-08-22 qfq 根治), 禁止运行。"
      "请用 hist_model_build.py(一次性重训) + hist_daily_update.py(日推)。", file=sys.stderr)
sys.exit(1)

# ========== 以下为被弃用的原逻辑(仅供回溯参考, 不可运行) ==========
# HIST 5-seed集成跑批 — 2026-08-19晨
# seed1已在先前训练完成(pred.pkl可辨); 本批跑seed2-5(新进程+PYTHONHASHSEED)
# 完成后: ①ensemble预测均值 ②可投资回测(5日Top20) ③袖筒/相关性(含MASTER×HIST)
import glob
import os
import shutil
import subprocess
import time

EXP = r"D:\zcodesj\qlib_exp"
STATUS = r"D:\zcodesj\reports\_hist_seeds_status.txt"


def status(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(STATUS, "a", encoding="utf-8") as f:
        f.write(line + "\n")


try:
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
except Exception:
    pass


def run_qrun(tag):
    env = dict(os.environ)
    env.update({"MLFLOW_ALLOW_FILE_STORE": "true", "PYTHONHASHSEED": str(abs(hash(tag)) % 2**31)})
    r = subprocess.run(
        ["python", "-c",
         "from qlib.cli.run import run; import sys; "
         "sys.argv=['qrun','workflow_hist_alpha360_tulong.yaml']; run()"],
        cwd=EXP, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="ignore")
    with open(rf"D:\zcodesj\reports\_hist_seed_{tag}.log", "w", encoding="utf-8") as f:
        f.write((r.stdout or "")[-120000:] + "\n--stderr--\n" + (r.stderr or "")[-30000:])
    return r.returncode


def collect_preds():
    pats = sorted(glob.glob(rf"{EXP}\mlruns\*\*\*\artifacts\pred.pkl"), key=os.path.getmtime)
    return pats


def main():
    status("=== HIST seeds跑批启动 ===")
    ran = 0
    for i in (2, 3, 4, 5):
        rc = run_qrun(f"s{i}")
        status(f"seed{i} rc={rc}")
        if rc != 0 and ran == 0 and i == 2:
            status("首轮即败,停批")
            return
    # 收集5个pred(最新5个)
    pats = collect_preds()[-5:]
    saved = []
    for j, p in enumerate(pats):
        dst = rf"{EXP}\hist_data\hist_pred_seed{j+1}.pkl"
        shutil.copy(p, dst)
        saved.append(dst)
    status(f"收集{len(saved)}个seed预测: {saved}")
    # 集成+分析(交给analysis脚本)
    with open(r"D:\zcodesj\reports\_hist_seeds_list.txt", "w") as f:
        f.write("\n".join(saved))
    status("=== 跑批结束, 集成/回测/袖筒分析待执行 ===")


if __name__ == "__main__":
    main()