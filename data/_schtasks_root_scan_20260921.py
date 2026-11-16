# -*- coding: utf-8 -*-
"""只读: 列出根级 schtasks(排除 \Microsoft\ 系统任务), 标注名称。"""
import subprocess, sys

out = subprocess.run(['schtasks', '/query', '/fo', 'csv'], capture_output=True)
text = out.stdout.decode('gbk', errors='replace')
rows = []
for line in text.splitlines():
    parts = [p.strip('"') for p in line.split('","')]
    if not parts or not parts[0]:
        continue
    name = parts[0]
    # 根级任务形如 \TaskName(仅一个反斜杠), \Microsoft\... 属系统任务排除
    if name.startswith('\\') and name.count('\\') == 1:
        rows.append(name)
for r in rows:
    print(r)
print('---TOTAL_ROOT:', len(rows))
