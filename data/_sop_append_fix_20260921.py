# -*- coding: utf-8 -*-
"""9/21 下午: SOP 段六十行内追加裸python修复记录(按行定位,不假设顶部;防并行会话插入)。"""
import io

sop = r'D:\zcodesj\DAILY_WORKFLOW_SOP.md'
addition = (
    '**同轮修复**=health_check实跑揪出ExternalBenchReconcile_1016裸python哑火风险'
    '（直接WindowsApps python.exe启动无cmd启动器,10/16对账将静默落空,9/7哑火同款),'
    '新建external_bench_reconcile_run.cmd（纯ASCII+py_run.cmd模式）+schtasks /change重指向'
    '+/run实弹复测过（12:42日志新行,对账外部组+10.84%超额+9.67pp与段五十九口径一致）,'
    '复跑health_check待bare_python行归零确认；'
)

with io.open(sop, 'r', encoding='utf-8') as f:
    lines = f.readlines()

hits = [i for i, ln in enumerate(lines) if '(六十)' in ln and 'schtasks一次性残骸二次清理' in ln]
assert len(hits) == 1, f'段六十定位异常: {hits}'
i = hits[0]
assert '同轮修复' not in lines[i], '修复记录已存在, 防重复中止'

idx = lines[i].find('🔴名称勘误')
assert idx > 0, '段六十行内未找到名称勘误锚点'
lines[i] = lines[i][:idx] + addition + lines[i][idx:]

with io.open(sop, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print(f'OK 段六十位于第{i+1}行, 已追加修复记录')
print('当前顶部3行段号:')
for ln in lines[:3]:
    print('  ', ln[:80])
