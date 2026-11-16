#!/bin/bash
# 画像体系全量实弹(2026-09-06 主人: 全量跑一遍遇到bug修复优化)
# 顺序=依赖链; 每步记录exit/耗时; 失败不阻断(收集全部再汇总); 日志落 portrait_agent/logs/run_all_20260906/
# 2026-09-25 路径修复: 线B研究件已迁 portrait_agent/v6_research/(9/16首轮+9/25二轮), data_enrich_v5 留根
cd /d/zcodesj
LOGDIR=portrait_agent/logs/run_all_20260906
mkdir -p "$LOGDIR"
SUMMARY="$LOGDIR/_summary.tsv"
echo -e "step\tscript\texit\tseconds" > "$SUMMARY"

run() {
  local name="$1"; shift
  local t0=$(date +%s)
  echo "[$(date +%H:%M:%S)] START $name" >> "$LOGDIR/_progress.log"
  python -W ignore "$@" > "$LOGDIR/${name}.log" 2>&1
  local rc=$?
  local dt=$(( $(date +%s) - t0 ))
  echo -e "$name\t$*\t$rc\t$dt" >> "$SUMMARY"
  echo "[$(date +%H:%M:%S)] END $name rc=$rc ${dt}s" >> "$LOGDIR/_progress.log"
}

V6R=portrait_agent/v6_research
run 01_chan_batch        $V6R/chan_batch.py
run 02_retrain_v2        $V6R/retrain_v2_bigdata.py
run 03_reverse_bt        $V6R/reverse_backtest.py
run 04_morph_profile     $V6R/morph_profile.py
run 05_retrain_v3        $V6R/retrain_v3_morph.py
run 06_retrain_v4        $V6R/retrain_v4_full.py
run 07_fast_bt_v4        $V6R/fast_backtest_v4.py
run 08_data_enrich_v5    portrait_agent/data_enrich_v5.py
run 09_combo_v3          $V6R/combo_v3_optimize.py
run 10_combo_c_valid     $V6R/combo_c_validation.py
run 11_downside          $V6R/downside_profile.py
run 12_upgrade_v6        $V6R/upgrade_v6.py
run 13_chip_build        $V6R/chip_build_v6.py
run 14_bt3yr_orig        $V6R/backtest_3yr.py
run 15_bt3yr_honest      $V6R/backtest_3yr_honest.py
run 16_shadow_signal     $V6R/combo_c_shadow.py --signal
run 17_shadow_eval       $V6R/combo_c_shadow.py --eval
run 18_shadow_status     $V6R/combo_c_shadow.py --status

echo "ALL DONE" >> "$LOGDIR/_progress.log"
cat "$SUMMARY"
