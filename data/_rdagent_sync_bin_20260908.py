# -*- coding: utf-8 -*-
"""rdagent qlib_bin 副本同步（生产 bin → .rdagent_ws 副本, 幂等）

三步:
  1. robocopy 增量复制生产 qlib_bin → 副本 (不带 /MIR, 保留副本自有文件如 SH000300)
  2. 重跑 make_bench_bin.py (幂等: 已对齐则零写, instruments 已注册则跳过)
  3. 重跑 make_h5_direct.py (因子源数据 h5 刷新)
用法: python .rdagent_ws/sync_rdagent_bin.py
"""
import subprocess
from pathlib import Path

SRC = r"D:\workbuddyspace\qlib_data\qlib_bin"
DST = r"D:\zcodesj\.rdagent_ws\qlib_data\qlib_bin"
WS = r"D:\zcodesj\.rdagent_ws"
PY = r"D:\anzhuang\miniconda3\envs\rdagent4qlib\python.exe"


def main():
    print("[1/3] robocopy 增量同步 ...")
    r = subprocess.run(
        ["robocopy", SRC, DST, "/E", "/NFL", "/NDL", "/NJH", "/NP", "/XX"],
        capture_output=True, text=True, errors="replace",
    )
    # robocopy 退出码: 0-7 均为成功(1=有新文件, 3=有新文件+extra)
    print(f"  robocopy exit={r.returncode} ({'OK' if r.returncode <= 7 else 'FAIL'})")
    if r.returncode > 7:
        print(r.stdout[-500:])
        return 1

    print("[2/3] 重注入 SH000300 基准 (幂等) ...")
    r2 = subprocess.run([PY, str(Path(WS) / "make_bench_bin.py")], capture_output=True, text=True)
    ok2 = "BENCH BIN DONE" in r2.stdout
    print("  ", (r2.stdout.strip().splitlines() or [""])[-1] if r2.stdout else "no output", "|", "OK" if ok2 else "CHECK")

    print("[3/3] 刷新因子源数据 h5 (幂等) ...")
    tpl = Path(r"D:\zcodesj\.rdagent_ws\.venv\Lib\site-packages\rdagent\scenarios\qlib\experiment\factor_data_template")
    # h5 生成脚本在 data 副本/工作区: 用 make_h5_direct.py
    r3 = subprocess.run([PY, str(Path(WS) / "make_h5_direct.py")], capture_output=True, text=True)
    ok3 = "DIRECT H5 DONE" in r3.stdout
    # 同步 h5 到 rdagent 因子源数据目录
    if ok3:
        import shutil
        fdst = Path(r"D:\zcodesj\.rdagent_ws\git_ignore_folder\factor_implementation_source_data")
        fdst.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(WS) / "daily_pv_all.h5", fdst / "daily_pv.h5")
        fdst_dbg = Path(r"D:\zcodesj\.rdagent_ws\git_ignore_folder\factor_implementation_source_data_debug")
        fdst_dbg.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(WS) / "daily_pv_debug.h5", fdst_dbg / "daily_pv.h5")
    print("  ", "OK" if ok3 else "CHECK")

    print("=== SYNC DONE ===")
    return 0


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
