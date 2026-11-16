#!/bin/bash
# 分钟线回补自动收尾: 等下载完成→合并→重跑微观因子 (8/16挂机)
cd /d/zcodesj
echo "$(date +%H:%M) autopilot start" >> /d/zcodesj/_m5_autopilot.log
for i in $(seq 1 120); do  # 最多20小时
  n=$(cat /d/zcodesj/_cache/m5_done_*.txt 2>/dev/null | wc -l)
  echo "$(date +%H:%M) done=$n" >> /d/zcodesj/_m5_autopilot.log
  if [ "$n" -ge 790 ]; then
    sleep 60
    echo "$(date +%H:%M) 下载完成, 开始合并" >> /d/zcodesj/_m5_autopilot.log
    python merge_minute_shards.py >> /d/zcodesj/_m5_autopilot.log 2>&1
    echo "$(date +%H:%M) 合并完成, 重跑微观因子" >> /d/zcodesj/_m5_autopilot.log
    python microstructure_factors_20260815.py --start 20200601 --end 20260731 > /d/zcodesj/_microstructure_rerun.log 2>&1
    echo "$(date +%H:%M) 因子重跑完成" >> /d/zcodesj/_m5_autopilot.log
    break
  fi
  sleep 600
done
echo "$(date +%H:%M) autopilot exit" >> /d/zcodesj/_m5_autopilot.log
