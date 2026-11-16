#!/usr/bin/env bash
# WSL 内 fin_factor 干跑脚本(2026-09-28): 验证设置链能走到 LLM 鉴权失败点
# 运行: wsl -d Ubuntu-24.04 -u root -- bash /mnt/d/zcodesj/rdagent_workspace/wsl_dryrun.sh
export PATH=/root/miniconda3/bin:/root/miniconda3/envs/rdagent4qlib/bin:$PATH
export MODEL_CoSTEER_ENV_TYPE=conda
export CONDA_DEFAULT_ENV=rdagent4qlib
export MAX_API_RETRIES=1
cd /mnt/d/zcodesj/rdagent_workspace
timeout 150 rdagent fin_factor --loop-n 1 > /root/dryrun.log 2>&1
echo "rc=$?"
grep -aiE "error|exception|401|auth|api key|invalid|Traceback" /root/dryrun.log | tail -12
echo DRYRUN_SCRIPT_DONE
