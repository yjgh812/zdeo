#!/usr/bin/env bash
# 启动本地 embedding 服务并实测一发(自愈: 已活则跳过)
export PATH=/root/miniconda3/envs/rdagent4qlib/bin:$PATH
export HF_ENDPOINT=https://hf-mirror.com
if ! curl -s --max-time 3 http://127.0.0.1:9101/v1/models > /dev/null 2>&1; then
  nohup python /mnt/d/zcodesj/rdagent_workspace/embed_server.py > /root/embed_server.log 2>&1 &
  echo "EMBED_SERVER_STARTED pid=$!"
  sleep 5
fi
# 等模型就绪(首启要下载)
for i in $(seq 1 60); do
  if curl -s --max-time 3 http://127.0.0.1:9101/v1/models | grep -q bge; then break; fi
  sleep 5
done
curl -s --max-time 30 -X POST http://127.0.0.1:9101/v1/embeddings -H "Content-Type: application/json" \
  -d '{"model":"BAAI/bge-small-zh-v1.5","input":"动量因子测试"}' | head -c 120
echo
echo EMBED_TEST_DONE
