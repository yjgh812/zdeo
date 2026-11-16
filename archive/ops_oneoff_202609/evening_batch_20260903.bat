@echo off
cd /d D:\zcodesj
python attribution_patterns_backfill.py --dates 20260903
python attribution_patterns_backfill.py --dates 20260903 --event big_drop
python feed_feedback_tables.py all --date 20260903
python signal_performance_report.py
python sentiment_composite.py --today
python signal_forward_tracker.py --record --date 20260903
python signal_forward_tracker.py --backfill
python signal_forward_tracker.py --report
python prem_seal_daily.py --json
python vibe_card.py --append
python combo_experiment_tracker.py
python reconcile_pending_fills.py --date 20260903
python fill_confirm_monitor.py --reclassify 20260903
python fill_confirm_monitor.py --expire 20260903
python tulong_sector_review.py
