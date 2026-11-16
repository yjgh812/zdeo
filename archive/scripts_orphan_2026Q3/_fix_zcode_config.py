# -*- coding: utf-8 -*-
"""修复 C:\\Users\\yujie\\.zcode\\cli\\config.json 的非法转义(\\\\\\ -> \\\\)，保留备份，替换后校验 JSON"""
import re, collections, json, shutil, os, sys

CFG = r"C:\Users\yujie\.zcode\cli\config.json"

s = open(CFG, encoding="utf-8").read()

# 1. 反斜杠连续序列分布
dist = collections.Counter(len(m.group(0)) for m in re.finditer(r"\\+", s))
print("[诊断] 反斜杠连续序列长度分布:", dict(sorted(dist.items())))

# 2. 检查被污染的 unicode 转义(\\\uXXXX)
polluted_uni = [m.group(0) for m in re.finditer(r"\\\\\\u[0-9a-fA-F]{4}", s)]
print("[诊断] 被污染的 unicode 转义数量:", len(polluted_uni))

# 3. 替换 3连->2连
t = s.replace("\\\\\\", "\\\\")
try:
    json.loads(t)
    ok = True
    print("[替换后] JSON 校验: 合法")
except Exception as e:
    ok = False
    print("[替换后] 仍非法:", str(e)[:150])

if ok:
    # 备份原损坏文件
    bak = CFG + ".broken-20260818"
    shutil.copy(CFG, bak)
    with open(CFG, "w", encoding="utf-8", newline="\n") as f:
        f.write(t)
    print("[已修复] 备份 ->", bak)
    print("[已修复] 写入 config.json，校验通过，共替换", len(re.findall(r"\\\\\\", s)), "处")
    # 抽查修复后的值
    j = json.loads(open(CFG, encoding="utf-8").read())
    ft = j.get("mcp", {}).get("servers", {}).get("ftshare", {})
    print("[抽查] ftshare.command =", ft.get("command"))
    print("[抽查] ftshare.args =", ft.get("args"))
else:
    sys.exit(1)