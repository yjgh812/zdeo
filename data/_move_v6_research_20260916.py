# -*- coding: utf-8 -*-
"""画像目录物理分层: 移动39个线B研究文件到 v6_research/ 子目录(一次性执行)."""
import shutil, os
SRC = r'D:\zcodesj\portrait_agent'
DST = os.path.join(SRC, 'v6_research')
os.makedirs(DST, exist_ok=True)
FILES = [
    'backtest_3yr.py','backtest_3yr_honest.py','chan_fix_eventstudy_20260906.py',
    'chip_build_v6.py','combo_analysis_20260817.py','combo_b_list_generator.py',
    'combo_clean_20260817.py','combo_c_validation.py','combo_v3_optimize.py',
    'combo_c_v2_recompute_20260906.py','combo_c_2016_backtest_20260906.py',
    'combo_c_2016_nextbar_20260906.py','deep_bug_scan.py','downside_profile.py',
    'doubled_batch_profile_20260906.py','doubled_batch_profile_v2_20260906.py',
    'doubled_industry_role_20260906.py','doubled_valuation_profile_20260906.py',
    'dr_rebound_design_20260906.py','engine_a_shadow_scan.py','engineA_shadow_eval.py',
    'fast_backtest_v4.py','feature_census_2016_20260906.py','industry_synergy_validate.py',
    'jjmy_template_scan_bt.py','morph_profile.py','profile_startup_cond_backtest_20260907.py',
    'reverse_backtest.py','retrain_v2_bigdata.py','retrain_v3_morph.py','retrain_v4_full.py',
    'sector_filter_chan12_20260909.py','sector_laggard_scan.py','sector_laggard_track.py',
    'tune_v1_1.py','tune_y_mid.py','upgrade_v6.py','weekly_filter_chan12_20260909.py',
    'coldstart.py',
]
moved = 0
for f in FILES:
    src = os.path.join(SRC, f)
    dst = os.path.join(DST, f)
    if os.path.exists(src):
        shutil.move(src, dst)
        moved += 1
    else:
        print(f'  缺: {f}')
print(f'移入 {moved}/{len(FILES)} 文件到 v6_research/')
