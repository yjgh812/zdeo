#!/usr/bin/env bash
# Loop 6 新批次外部换窗验证
export PATH=/root/miniconda3/envs/rdagent4qlib/bin:/root/miniconda3/bin:$PATH
export MLFLOW_ALLOW_FILE_STORE=true
python /mnt/d/zcodesj/rdagent_workspace/walkforward_validate.py /mnt/d/zcodesj/rdagent_workspace/git_ignore_folder/RD-Agent_workspace/b6f3a6495a81496690632a6b27623270/combined_factors_df.parquet 2>&1 | grep -aE "IC=|WF_SUMMARY|Error|Traceback" | tail -8
echo L6WF_DONE
