#!/bin/bash
# 补跑shard 1/2/6: 每8分钟检查活跃baostock分片数,<6时空一个slot启动一个
cd /d/zcodesj
for target in 1 2 6; do
  launched=0
  while [ $launched -eq 0 ]; do
    sleep 480
    active=$(tasklist //FI "IMAGENAME eq python.exe" 2>/dev/null | grep -c python)
    # 检查该shard是否已完成
    if grep -q "DONE" /d/zcodesj/_m5_shard$target.log 2>/dev/null; then break; fi
    n_done=$(wc -l < /d/zcodesj/_cache/m5_done_$target.txt 2>/dev/null || echo 0)
    # 分片i应做100只; 若done>=100视为完成
    if [ "$n_done" -ge 100 ]; then launched=1; echo "$(date +%H:%M) shard$target already $n_done" >> /d/zcodesj/_m5_watchdog.log; break; fi
    n_run=$(ps aux 2>/dev/null | grep -c "[b]ackfill_minute_baostock")
    echo "$(date +%H:%M) target=$target running=$n_run done_$target=$n_done" >> /d/zcodesj/_m5_watchdog.log
    if [ "$n_run" -lt 6 ]; then
      nohup python backfill_minute_baostock.py --shard $target/8 >> /d/zcodesj/_m5_shard$target.log 2>&1 &
      launched=1
      echo "$(date +%H:%M) launched shard$target" >> /d/zcodesj/_m5_watchdog.log
    fi
  done
done
echo "$(date +%H:%M) watchdog exit" >> /d/zcodesj/_m5_watchdog.log
