#!/bin/bash
# m5分片错峰补跑 v3（8/16知微）
# 实况: 7路(0/1/3/4/5/6/7)自01:45存活推进正常; 唯独shard2真死(无进程无done文件)
# 坑1: `ps aux`在Git Bash只见本会话进程树→全局瞎(watchdog误判根因)
# 坑2: WindowsApps python.exe别名实际进程名=python3.13.exe
# v3: PowerShell全局计数(Name=python3.13.exe+CommandLine含脚本名); 等7路结束后单跑shard2(幂等)
cd /d/zcodesj
LOG=/d/zcodesj/_m5_recover.log
echo "$(date +%H:%M) recover-v3启动: 等待7路存活分片结束" >> $LOG
for i in $(seq 1 90); do  # 最多15小时
  n_run=$(powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='python3.13.exe'\" | Where-Object { \$_.CommandLine -match 'backfill_minute_baostock' }).Count" 2>/dev/null | tr -d '\r')
  echo "$(date +%H:%M) running=$n_run total_done=$(cat /d/zcodesj/_cache/m5_done_*.txt 2>/dev/null | wc -l)" >> $LOG
  if grep -q "DONE" /d/zcodesj/_m5_shard2.log 2>/dev/null; then
    echo "$(date +%H:%M) shard2已有DONE标记, 无需补跑" >> $LOG; exit 0
  fi
  if [ "$n_run" = "0" ]; then break; fi
  sleep 600
done
echo "$(date +%H:%M) 存活分片已结束, 串行单跑shard2" >> $LOG
python backfill_minute_baostock.py --shard 2/8 >> /d/zcodesj/_m5_shard2.log 2>&1
echo "$(date +%H:%M) shard2退出码$? done=$(wc -l < /d/zcodesj/_cache/m5_done_2.txt 2>/dev/null || echo 0)" >> $LOG
echo "$(date +%H:%M) recover-v3结束 total_done=$(cat /d/zcodesj/_cache/m5_done_*.txt 2>/dev/null | wc -l)" >> $LOG
