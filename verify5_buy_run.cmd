@echo off
rem 2026-10-21: Verify5 buy-batch one-shot for 2026-10-22 09:35 (owner order: buy 5 stocks).
rem Pure ASCII only. Log to data dir. Script has its own guards: session/bid-gap/A1.
cd /d D:\zcodesj\.zcode\workspace\draft-a5f60cba-e252-4268-ab4a-058cbc4f3fe0
call D:\zcodesj\py_run.cmd morning_batch_20261022.py --execute >> D:\zcodesj\data\verify5_buy_20261022.log 2>&1
