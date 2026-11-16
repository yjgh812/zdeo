#!/bin/bash
# 产物停滞检测: 每240s跑一次探针, 连续4轮完全相同 → cron流水线停滞/结束, 退出报告
LOG=/d/zcodesj/_chain_stall_watch_0827.log
: > "$LOG"
prev=""
same=0
for i in $(seq 1 10); do
  out=$(python /d/zcodesj/chain_progress_probe_0827.py 2>&1)
  echo "===== 轮次#$i =====" >> "$LOG"
  echo "$out" >> "$LOG"
  body=$(echo "$out" | tail -n +2)
  if [ "$body" == "$prev" ]; then same=$((same+1)); else same=0; prev="$body"; fi
  if [ $same -ge 4 ]; then
    echo "[$(date '+%H:%M:%S')] STALL: 连续4轮产物无变化, 判定流水线停滞/结束" >> "$LOG"
    exit 0
  fi
  sleep 240
done
echo "[$(date '+%H:%M:%S')] 监控窗口(40分钟)用尽退出" >> "$LOG"
