#!/usr/bin/env bash
# WSL 环境搭建第二步: conda env + pyqlib (2026-09-28)
# 运行: wsl -d Ubuntu-24.04 -u root -- bash -c "bash /mnt/d/zcodesj/rdagent_workspace/wsl_setup2.sh"
set -e
C=/root/miniconda3/bin/conda
if [ ! -x /root/miniconda3/envs/rdagent4qlib/bin/python ]; then
  echo "[1/3] conda create rdagent4qlib..."
  $C create -y -n rdagent4qlib python=3.10 2>&1 | tail -3
fi
P=/root/miniconda3/envs/rdagent4qlib/bin
echo "[2/3] pip upgrade + install pyqlib cython lightgbm (official PyPI)..."
$P/pip install -q --upgrade pip
$P/pip install -q pyqlib==0.9.7 cython lightgbm 2>&1 | tail -3
echo "[3/3] verify..."
$P/python -c "import qlib, lightgbm, sys; print('WSL env OK: py', sys.version.split()[0], '+ pyqlib + lightgbm')"
echo WSL_SETUP2_DONE
