# -*- coding: utf-8 -*-
"""热修 pre_trade_check_v2.py 中 R22 函数被污染的return行"""
import re

path = r"D:\zcodesj\pre_trade_check_v2.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

# 被染片段特征: 'w5, "ld5": ld5}\n    \n    # 双口径同正:' 之后一直到下一个 def check
pat = r'return \{"status": "pass" if not ld5 else "w5, "ld5": ld5}\s*\n\s+# 双口径同正.+\n.+return \{"status": "pass" if not ld5 else "warn"[^}]+}'
clean = '''return {
        "status": "pass" if not ld5 else "warn",
        "note": note,
        "em5": em5, "ths5": ths5, "ld5": ld5,
    }


def check(args) -> dict:'''

if re.search(pat, content, re.DOTALL):
    content = re.sub(pat, clean, content, count=1, flags=re.DOTALL)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("R22_RETURN_REPAIRED")
else:
    print("PATTERN_NOMATCH — 手动检查")
    # fallback: 行号范围暴力替换
    lines = content.split("\n")
    for i in range(182, min(198, len(lines))):
        print(f"L{i+1}: {lines[i][:120]}")