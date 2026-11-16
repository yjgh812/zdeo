#!/bin/bash
# P1判别树三层串行回放 runner（L0b/L1/L2），零生产改动，输出独立目录
cd /d/zcodesj || exit 1
export TULONG_ARMS="bp+ep_core"

echo "=== L0b start $(date +%H:%M:%S) ==="
TULONG_LADDER_SIGNAL=raw TULONG_BPEP_STOP=1.0 TULONG_BPEP_DDLIMIT=100 TULONG_BPEP_MAX_HOLDING=20 \
  TULONG_BT_OUT="D:/zcodesj/reports/backtest_full_20261019_ladder_L0b" \
  python analysis/bt_full_report_ladder_20261019.py > /tmp/ladder_L0b.log 2>&1
echo "=== L0b exit=$? $(date +%H:%M:%S) ==="

echo "=== L1 start $(date +%H:%M:%S) ==="
TULONG_BPEP_STOP=1.0 TULONG_BPEP_DDLIMIT=100 TULONG_BPEP_MAX_HOLDING=20 \
  TULONG_BT_OUT="D:/zcodesj/reports/backtest_full_20261019_ladder_L1" \
  python analysis/bt_full_report_ladder_20261019.py > /tmp/ladder_L1.log 2>&1
echo "=== L1 exit=$? $(date +%H:%M:%S) ==="

echo "=== L2 start $(date +%H:%M:%S) ==="
TULONG_BPEP_STOP=0.08 TULONG_BPEP_DDLIMIT=100 TULONG_BPEP_MAX_HOLDING=20 \
  TULONG_BT_OUT="D:/zcodesj/reports/backtest_full_20261019_ladder_L2" \
  python analysis/bt_full_report_ladder_20261019.py > /tmp/ladder_L2.log 2>&1
echo "=== L2 exit=$? $(date +%H:%M:%S) ==="
echo "=== ALL DONE $(date +%H:%M:%S) ==="
