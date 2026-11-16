# -*- coding: utf-8 -*-
"""只读: 查询待核 schtasks 的 verbose 详情(GBK 解码)。"""
import subprocess

targets = [
    'Sell_002811_20260908', 'Sell_002811_20260908_retry',
    'ChainStep4Fix_0830', 'S5_300025_Exec_0931', 'MondayBuy_0928',
    'EmotionPhaseV2_2010', 'LimitDownBackfill_2235', 'FACTORDECAY_0109',
    'MacroLedger_M16_0800', 'DDProtectGate_0800', 'AQDailyPicker', 'BpEpPick',
    'ArtifactFreshness_SUN2030', 'EastmoneyDataRefresh', 'PullbackAlert_0915',
]
for n in targets:
    r = subprocess.run(['schtasks', '/query', '/tn', n, '/fo', 'csv', '/v'], capture_output=True)
    out = r.stdout.decode('gbk', errors='replace')
    if r.returncode != 0:
        print(f'{n}: QUERY_FAIL {out[:100]}')
        continue
    lines = out.splitlines()
    hdr = [h.strip('"') for h in lines[0].split('","')]
    vals = [v.strip('"') for v in lines[1].split('","')]
    d = dict(zip(hdr, vals))
    print(f'== {n}')
    print(f"   状态={d.get('状态','?')} | 上次={d.get('上次运行时间','?')} | 下次={d.get('下次运行时间','?')} | 计划={d.get('计划类型','?')}")
    print(f"   命令={d.get('要运行的任务','?')[:150]}")
