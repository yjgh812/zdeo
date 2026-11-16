# -*- coding: utf-8 -*-
"""9/21 下午 schtasks 一次性残骸清理: 备份 XML 定义 -> 删除 -> 复核。
残骸判定标准(与 9/21 上午轮一致): 计划类型=一次性 且 下次运行时间=N/A 且 上次运行时间已过(任务已执行完毕,注册表惰性残留)。
"""
import subprocess, os, json, datetime

REMNANTS = [
    ('Sell_002811_20260908', '9/8 09:32 已执行,下次=N/A,一次性卖出残骸'),
    ('Sell_002811_20260908_retry', '9/8 09:50 已执行,下次=N/A,一次性卖出重试残骸'),
    ('ChainStep4Fix_0830', '9/15 09:23 已执行,下次=N/A,数据链step4修复残骸'),
    ('S5_300025_Exec_0931', '9/16 09:31 已执行(300025华星股份S-5止损@5.56),下次=N/A,残骸'),
]

bdir = r'D:\zcodesj\data\schtasks_cleanup_backup_20260921'
os.makedirs(bdir, exist_ok=True)
results = []

for name, reason in REMNANTS:
    # 1) 删前复核: 必须满足 一次性+下次N/A
    r = subprocess.run(['schtasks', '/query', '/tn', name, '/fo', 'csv', '/v'], capture_output=True)
    out = r.stdout.decode('gbk', errors='replace')
    if r.returncode != 0:
        results.append({'name': name, 'action': 'SKIP_QUERY_FAIL'})
        continue
    lines = out.splitlines()
    hdr = [h.strip('"') for h in lines[0].split('","')]
    vals = [v.strip('"') for v in lines[1].split('","')]
    d = dict(zip(hdr, vals))
    if d.get('计划类型', '').strip() != '一次性' or d.get('下次运行时间', '').strip() != 'N/A':
        results.append({'name': name, 'action': 'ABORT_NOT_REMNANT',
                        'plan': d.get('计划类型'), 'next': d.get('下次运行时间')})
        continue
    # 2) 备份 XML 定义
    rx = subprocess.run(['schtasks', '/query', '/tn', name, '/xml'], capture_output=True)
    xml = rx.stdout.decode('gbk', errors='replace')
    with open(os.path.join(bdir, name + '.xml'), 'w', encoding='utf-8') as f:
        f.write(xml)
    # 3) 删除
    rd = subprocess.run(['schtasks', '/delete', '/tn', name, '/f'], capture_output=True)
    delout = rd.stdout.decode('gbk', errors='replace').strip()
    # 4) 复核: 再查应失败
    rv = subprocess.run(['schtasks', '/query', '/tn', name], capture_output=True)
    gone = rv.returncode != 0
    results.append({'name': name, 'reason': reason, 'action': 'DELETED' if gone else 'DELETE_VERIFY_FAIL',
                    'last_run': d.get('上次运行时间'), 'delete_output': delout[:60]})

meta = {
    'cleanup_date': '2026-09-21-pm',
    'note': 'schtasks一次性残骸清理备份(XML定义已存同目录,可 schtasks /create /xml 恢复); cmd脚本文件原地保留未删',
    'items': results,
}
with open(os.path.join(bdir, '_index.json'), 'w', encoding='utf-8') as f:
    json.dump(meta, f, ensure_ascii=False, indent=1)
for x in results:
    print(x.get('name'), '->', x.get('action'), x.get('last_run', ''))
