#!/bin/bash
cd /d/zcodesj && python pre_trade_check_v2.py \
  --code 002437 --name 誉衡药业 --source mainline_radar \
  --price 4.25 --prev-close 4.10 \
  --holdings 10 --total-cash 1000000 --amount 50000 \
  --pct-60d 0.30 --close 4.20 --high 4.30 --low 4.10 \
  --sector-limit-up-count 5 2>&1 | head -20
echo "EXIT_CODE=$?"