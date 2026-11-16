#!/usr/bin/env bash
# RD-Agent 正式运行脚本(WSL 内, 2026-09-28)
# 前置: 主人已在 D:\zcodesj\rdagent_workspace\.env 填好真实 LLM key
#       (国内网络直连 api.openai.com 不通, 推荐 DeepSeek 段: DEEPSEEK_API_KEY=sk-...,
#        或 OPENAI_API_KEY+OPENAI_API_BASE 指向可达端点)
# 运行: wsl -d Ubuntu-24.04 -u root -- bash /mnt/d/zcodesj/rdagent_workspace/wsl_run_fin_factor.sh
export PATH=/root/miniconda3/bin:/root/miniconda3/envs/rdagent4qlib/bin:$PATH
export MODEL_CoSTEER_ENV_TYPE=conda
export CONDA_DEFAULT_ENV=rdagent4qlib
cd /mnt/d/zcodesj/rdagent_workspace
rdagent fin_factor --loop-n "${1:-5}"
