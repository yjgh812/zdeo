# -*- coding: utf-8 -*-
"""9/21 下午: SOP 段六十回写(顶部插入, 防撞号已核顶部3行=五十九/五十七/五十八)。"""
import io

sop = r'D:\zcodesj\DAILY_WORKFLOW_SOP.md'
seg = (
    '> **2026-09-21 下午(六十) schtasks一次性残骸二次清理（主人令「自动化任务，删除非必要任务」，续上午轮五十八）**：'
    '根级schtasks全量76条逐条verbose核对（计划类型/上次/下次运行），ZCode cron面复核15条构成与台账吻合无新增残骸。'
    '**删4条**（程序化三断言=计划类型一次性+下次N/A+上次已执行，防误删recurring）：'
    'Sell_002811_20260908（002811郑中设计 9/8 09:32已执行）+Sell_002811_20260908_retry（9/8 09:50）+'
    'ChainStep4Fix_0830（9/15 09:23数据链step4修复）+S5_300025_Exec_0931（300025华星创业 S-5 9/16 09:31@5.56）；'
    'XML定义备份=data/schtasks_cleanup_backup_20260921/（schtasks /create /xml 可恢复）+git入库副本'
    'reports/schtasks_cleanup_backup_20260921/，cmd脚本原地保留。'
    '**保留面核查澄清**：EmotionPhaseV2_2010=段五十五L4情绪相位v2影子（PREREG≥20交易日观察窗内）非PD-03已停用'
    'sentiment_cycle，保留；PortraitAgent_Retrain_20d维持禁用（主人前裁）；MondayBuy_0928实为每周一9:28 '
    'recurring（v5.2编排链）非一次性；WaveCensus_Sat_0600待9/26首跑；ExternalBenchReconcile_1016（10/16）未到期；'
    '其余recurring全部有未来排程。**边界外呈报不动**：BpEpPick（每月15:30，指向workbuddyspace域bp_ep_turnover，'
    '与R26名单制废止的关系待主人裁决）、AQDailyPicker（D:\\reasonix域非本项目）、Claw_*/OpenClaw Gateway/WorkBuddy_*'
    '（workbuddyspace owner域）、第三方系统任务（360Zip/Nahimic/WPS/NVIDIA/Adobe）均未触碰。'
    '🔴名称勘误=002811实为郑中设计、300025实为华星创业（stock_name_lookup实跑纠正，凭印象命名已拦）。\n'
)

with io.open(sop, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 防呆: 确认当前第1行确实是段五十九, 且全文无"六十)"段号占用
assert '五十九' in lines[0], '顶部行号漂移, 中止回写: ' + lines[0][:60]
for ln in lines:
    assert '(六十)' not in ln, '段号六十已被占用, 中止回写'

lines.insert(0, seg)
with io.open(sop, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('OK: 段六十已插入, 新第1行=', lines[0][:50])
print('新第2行=', lines[1][:50])
