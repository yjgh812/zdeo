#!/usr/bin/env bash
# 跑 Loop 2 SOTA 五因子组的换窗验证(零LLM)
export PATH=/root/miniconda3/envs/rdagent4qlib/bin:/root/miniconda3/bin:$PATH
export MLFLOW_ALLOW_FILE_STORE=true
python /mnt/d/zcodesj/rdagent_workspace/walkforward_validate.py 2>&1 | grep -aE "IC=|WF_SUMMARY|Error|Traceback" | tail -8
echo WF_SCRIPT_DONE
