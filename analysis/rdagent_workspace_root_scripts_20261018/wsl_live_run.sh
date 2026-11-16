#!/usr/bin/env bash
# RD-Agent 首发实弹·续跑v3(2026-09-29 晨): 新增本地 embedding 服务修复 RuntimeError
# chat=百炼 token-plan; embedding=本地 bge (litellm hosted_vllm 通道)
# key 仅内存注入; 跑前后自动脱敏; 正式固化仍请主人执行 wire_bailian_to_rdagent.py
export PATH=/root/miniconda3/envs/rdagent4qlib/bin:/root/miniconda3/bin:$PATH
export MODEL_CoSTEER_ENV_TYPE=conda
export CONDA_DEFAULT_ENV=rdagent4qlib
export HF_ENDPOINT=https://hf-mirror.com

# ── 0. 脱敏旧日志 ──
sed -i 's/sk-sp-[A-Za-z0-9._-]*/[REDACTED_KEY]/g' /root/*.log /mnt/d/zcodesj/rdagent_workspace/*.log 2>/dev/null
find /mnt/d/zcodesj/rdagent_workspace/log -name "*.pkl" -path "*LITELLM_SETTINGS*" -delete 2>/dev/null

# ── 1. 本地 embedding 服务(自愈) ──
if ! curl -s --max-time 3 http://127.0.0.1:9101/v1/models 2>/dev/null | grep -q bge; then
  nohup python /mnt/d/zcodesj/rdagent_workspace/embed_server.py > /root/embed_server.log 2>&1 &
  sleep 5
fi
for i in $(seq 1 40); do
  if curl -s --max-time 3 http://127.0.0.1:9101/v1/models 2>/dev/null | grep -q bge; then break; fi
  sleep 5
done
if ! curl -s --max-time 3 http://127.0.0.1:9101/v1/models 2>/dev/null | grep -q bge; then
  echo "EMBED_SERVER_FAILED"; exit 9
fi
echo "EMBED_SERVER_READY"

# ── 2. 注入百炼 key(内存) ──
python - <<'PYEOF' > /root/live_run_env.sh
import json
cfg = json.load(open('/mnt/c/Users/Administrator/.bailian/config.json'))
print(f"export OPENAI_API_KEY={cfg['token-plan']['api_key']}")
PYEOF
source /root/live_run_env.sh && rm -f /root/live_run_env.sh

# ── 3. 双通道路由: chat=百炼, embedding=本地 ──
export OPENAI_API_BASE=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
export LITELLM_CHAT_MODEL=openai/qwen3.8-max
export LITELLM_EMBEDDING_MODEL=hosted_vllm/BAAI/bge-small-zh-v1.5
export HOSTED_VLLM_API_BASE=http://127.0.0.1:9101/v1
export HOSTED_VLLM_API_KEY=local
export CHAT_MODEL=openai/qwen3.8-max
export EMBEDDING_MODEL=hosted_vllm/BAAI/bge-small-zh-v1.5
export MLFLOW_ALLOW_FILE_STORE=true

# ── 4. 发跑前自检: chat + embedding 各一发(快速失败) ──
/root/miniconda3/envs/rdagent4qlib/bin/python - <<'PYEOF'
import os, litellm
r1 = litellm.completion(model='openai/qwen3.8-max', messages=[{'role':'user','content':'回复:OK'}], max_tokens=8)
r2 = litellm.embedding(model='hosted_vllm/BAAI/bge-small-zh-v1.5', input=['自检'])
emb = r2.data[0]['embedding'] if isinstance(r2.data[0], dict) else r2.data[0].embedding
print('PRECHECK_CHAT_OK', len(emb) > 0)
PYEOF
if [ $? -ne 0 ]; then echo "PRECHECK_FAILED"; exit 8; fi
echo "PRECHECK_OK"

# ── 5. 断点续跑 ──
cd /mnt/d/zcodesj/rdagent_workspace
rdagent fin_factor --loop-n "${LOOP_TARGET:-3}" --path /mnt/d/zcodesj/rdagent_workspace/log/2026-09-28_13-51-55-095648 > /root/finfactor_live3.log 2>&1
echo "FIN_FACTOR_EXIT=$?"

# ── 6. 脱敏收尾 ──
sed -i 's/sk-sp-[A-Za-z0-9._-]*/[REDACTED_KEY]/g' /root/*.log /mnt/d/zcodesj/rdagent_workspace/*.log 2>/dev/null
find /mnt/d/zcodesj/rdagent_workspace/log -name "*.pkl" -path "*LITELLM_SETTINGS*" -delete 2>/dev/null
echo LIVE_RESUME_DONE
