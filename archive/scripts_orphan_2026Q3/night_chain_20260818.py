# -*- coding: utf-8 -*-
"""夜间GPU接力链 — 2026-08-18凌晨无人值守
链: 等Kronos v2训完 → MASTER训练(GPU) → Kronos官方sweep(GPU) → Fwd10评估
保护: 每棒发车前查GPU车道(显存<1GB);任一棒失败则停链留现场
状态: D:/zcodesj/reports/_night_chain_status.txt (实时更新)
"""
import glob
import os
import subprocess
import time

LOG_FT = r"D:\zcodesj\reports\_kronos_ft_v2.log"
STATUS = r"D:\zcodesj\reports\_night_chain_status.txt"
DONE_STR = "Basemodel training completed"
TIMEOUT = time.time() + 7 * 3600      # 最晚06:30左右强制放行


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


def wait_kronos():
    status("等Kronos v2完成...")
    mtime_last, t_last = None, time.time()
    while time.time() < TIMEOUT:
        time.sleep(60)
        try:
            body = open(LOG_FT, encoding="utf-8", errors="ignore").read()
        except Exception:
            body = ""
        if DONE_STR in body:
            status("Kronos v2训练完成✓")
            return True
        mtime = os.path.getmtime(LOG_FT) if os.path.exists(LOG_FT) else 0
        if mtime != mtime_last:
            mtime_last, t_last = mtime, time.time()
        elif time.time() - t_last > 900 and gpu_free():
            status("⚠日志停更15min且GPU空闲→判定训练已死,放行队列")
            return False
    status("⚠超时放行")
    return False


def run(name, cmd, cwd=None):
    status(f"{name} 发车: {' '.join(cmd)}")
    t0 = time.time()
    log_path = rf"D:\zcodesj\reports\_chain_{'master' if 'MASTER' in name else 'sweep' if 'sweep' in name else 'eval'}.log"
    for attempt in range(3):
        while not gpu_free():
            status(f"{name}: GPU未释放,60s后重查")
            time.sleep(60)
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%F %T')} attempt{attempt+1} rc={r.returncode} =====\n")
            f.write(r.stdout[-200000:] if r.stdout else "")
            f.write("\n--stderr--\n")
            f.write(r.stderr[-50000:] if r.stderr else "")
        if r.returncode == 0:
            status(f"{name} 完成✓ 耗时{(time.time()-t0)/60:.0f}min 日志{log_path}")
            return True
        status(f"{name} 第{attempt+1}次失败 rc={r.returncode}: {r.stderr[-400:]}")
        time.sleep(60)
    return False


def main():
    status("=== 夜间接力链启动 ===")
    ok_ft = wait_kronos()
    # 棒1: MASTER训练 + 屠龙三关评估 (若CPU先行版已完成则跳过)
    master_done = (os.path.exists(r"D:\zcodesj\strategy_libs\MASTER\model\tulong_csi300_0.pkl")
                   and len(glob.glob(r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_*.pkl")) > 0)
    if master_done:
        status("MASTER已有结果(CPU先行版),跳过训练棒")
    elif run("MASTER训练", ["python", "-u", "tulong_train.py"],
             cwd=r"D:\zcodesj\strategy_libs\MASTER"):
        with open(r"D:\zcodesj\reports\MASTER_TULONG_RESULT_20260818.txt", "a",
                  encoding="utf-8") as f:
            f.write(f"\n[chain] 完成于{time.strftime('%F %T')}\n")
    else:
        status("MASTER失败,停链")
        return
    if len(glob.glob(r"D:\zcodesj\reports\MASTER_GATE_EVAL_20260818.md")) == 0:
        run("MASTER三关评估", ["python", "-u", r"D:\zcodesj\master_gate_eval_20260818.py"])
    # 棒2: Kronos官方sweep(仅当v2训练确实完成)
    if ok_ft:
        if run("Kronos官方sweep",
               ["python", "-u", r"D:\zcodesj\kronos_sweep_v2_20260818.py",
                "--ckpt", r"D:\zcodesj\Kronos\finetune_csv\ckpt_ashare_v2"]):
            run("Fwd10评估",
                ["python", "-u", r"D:\zcodesj\kronos_eval_v2_20260818.py",
                 r"D:\zcodesj\Kronos\finetune_csv\ckpt_ashare_v2_sweep_v2.csv", "10"])
        else:
            status("sweep失败(留现场)")
    else:
        status("跳过sweep(v2训练未正常完成)")
    status("=== 夜间接力链结束 ===")


if __name__ == "__main__":
    main()
