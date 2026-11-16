# -*- coding: utf-8 -*-
"""解析DeepSeek方案ARIA快照 → 清洗为markdown方案文档 · 2026-10-19
输入: tmp/deepseek_plan_raw_20261019.txt (domSnapshot 原样)
定位: 以第二轮指令回显「很好。现在请基于以上全部通报」之后的内容为新方案正文
输出: analysis/deepseek_improve_plan_20261019.md
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
SRC = r"D:\zcodesj\tmp\deepseek_plan_raw_20261019.txt"
DST = r"D:\zcodesj\analysis\deepseek_improve_plan_20261019.md"

with open(SRC, "r", encoding="utf-8") as f:
    raw = f.read()

marker = "- heading \"A股量化系统低胜率诊断·改进方案书\""
lines_all = raw.splitlines()
start = next(i for i, ln in enumerate(lines_all) if ln.startswith(marker))
body = "\n".join(lines_all[start:])

out = []
row_buf = []


def flush_row():
    if row_buf:
        out.append("| " + " | ".join(row_buf) + " |")
        row_buf.clear()


for line in body.splitlines():
    if re.match(r"^\s*- (paragraph|list|table|rowgroup|row)\s*$", line):
        flush_row()
        continue
    m = re.match(r'^\s*- paragraph: (.+)$', line)
    if m:
        flush_row()
        out.append(m.group(1))
        continue
    m = re.match(r'^\s*- text: (.*)$', line)
    if m:
        out.append(m.group(1))
        continue
    m = re.match(r'^\s*- strong: (.*)$', line)
    if m:
        out.append("**%s**" % m.group(1))
        continue
    m = re.match(r'^\s*- heading "(.*)" \[level=(\d)\]', line)
    if m:
        flush_row()
        out.append("#" * int(m.group(2)) + " " + m.group(1))
        continue
    m = re.match(r'^\s*- (?:cell|columnheader) "(.*)"\s*:?\s*$', line)
    if m:
        row_buf.append(m.group(1).replace("\n", " "))
        continue
    m = re.match(r'^\s*- (?:cell|columnheader) "(.*)"', line)
    if m:
        row_buf.append(m.group(1).replace("\n", " "))
        continue
    m = re.match(r'^\s*- row "(.*)"', line)
    if m:
        flush_row()
        continue
    if re.match(r'^\s*- separator', line):
        flush_row()
        out.append("---")
        continue
    # 其余结构行(button/generic/textbox等UI chrome)跳过
flush_row()

# 相邻 text/strong 行合并为段落(空行分隔逻辑: heading/表格/分隔线前后留空行)
clean = []
for ln in out:
    if ln.startswith("#") or ln == "---" or ln.startswith("|"):
        clean.append("")
        clean.append(ln)
        clean.append("")
    else:
        clean.append(ln)
text = "\n".join(clean)
text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

with open(DST, "w", encoding="utf-8") as f:
    f.write(text)

print("清洗后长度: %d 字符, %d 行" % (len(text), text.count("\n")))
checks = ["阶段A", "阶段B", "阶段C", "周度检查", "验收", "时间表", "决策权", "右尾"]
print("完整性检查: " + ", ".join(("%s=%s" % (k, "✓" if k in text else "✗MISS")) for k in checks))
print("--- 头部600字 ---")
print(text[:600])
print("--- 尾部400字 ---")
print(text[-400:])
