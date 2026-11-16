#!/usr/bin/env bash
# WSL Ubuntu-24.04 RD-Agent 环境搭建脚本(2026-09-28, 以 root 运行)
# 用途: 在 WSL 内装 Miniconda + rdagent4qlib env(pyqlib 走 PyPI 绕开 GitHub)
# 运行: wsl -d Ubuntu-24.04 -u root -- bash /mnt/d/zcodesj/rdagent_workspace/wsl_setup.sh
set -e
cd /tmp

echo "[1/6] download Miniconda (official)..."
wget -q --timeout=120 https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh

echo "[2/6] install Miniconda to /root/miniconda3..."
bash Miniconda3-latest-Linux-x86_64.sh -b -p /root/miniconda3 > /dev/null 2>&1

echo "[3/6] create conda env rdagent4qlib (py3.10)..."
/root/miniconda3/bin/conda create -y -n rdagent4qlib python=3.10 > /dev/null 2>&1

P=/root/miniconda3/envs/rdagent4qlib/bin
echo "[4/6] upgrade pip..."
$P/pip install -q --upgrade pip

echo "[5/6] apt build-essential git..."
apt-get update -qq > /dev/null 2>&1
apt-get install -y -qq build-essential git > /dev/null 2>&1

echo "[6/6] pip install pyqlib==0.9.7 cython lightgbm (official PyPI)..."
$P/pip install -q pyqlib==0.9.7 cython lightgbm

$P/python -c "import qlib, lightgbm, sys; print('WSL env OK: py', sys.version.split()[0], '+ pyqlib + lightgbm')"
echo WSL_SETUP_DONE
