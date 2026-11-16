#!/usr/bin/env bash
# WSL 内用 litellm 实测百炼端点(与 rdagent 同一客户端栈), key 只在内存(从 /mnt/c 配置读), 不落盘不打印。
export PATH=/root/miniconda3/envs/rdagent4qlib/bin:$PATH
python - <<'PYEOF'
import json, os
from pathlib import Path
cfg = json.load(open('/mnt/c/Users/Administrator/.bailian/config.json'))
os.environ['OPENAI_API_KEY'] = cfg['token-plan']['api_key']
os.environ['OPENAI_API_BASE'] = cfg['token-plan']['base_url'].rstrip('/') + '/compatible-mode/v1'
import litellm
r = litellm.completion(model='openai/qwen3.8-max',
                       messages=[{'role': 'user', 'content': '只回复两个字: 在线'}],
                       max_tokens=8)
print('LITELLM_CHAT_OK:', r.choices[0].message.content[:20], '| model:', r.model)
PYEOF
echo WSL_LITELLM_TEST_DONE
