# -*- coding: utf-8 -*-
"""tests/test_group_names_decree.py — 命名法令守卫(主人令2026-11-02定名令)。
T1 法定名总表在册可加载(92组)
T2 法令字段在位(颁布/永久/禁止截断)
T3 SOURCE_CN_NAMES 与法定名一一对应(防漂移)
T4 近似家族标注在位(同源多组必须可区分)
运行: python tests/test_group_names_decree.py   (只读断言, 零交易权)"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


try:
    d = json.load(open(r"D:\zcodesj\data\group_names_standard.json", encoding="utf-8"))
    check("T1_法定名总表在册", len(d.get("names", [])) >= 90, "%d组" % len(d.get("names", [])))
except Exception as e:
    check("T1_法定名总表在册", False, str(e))
    print(f"结果: FAIL -> {FAILS}")
    sys.exit(1)

dc = d.get("decree", {})
check("T2_法令字段在位", dc.get("decree_no") == "命名法令2026-01" and dc.get("effective") == "永久",
      dc.get("decree_no", "缺"))

from _common import SOURCE_CN_NAMES
mismatch = [(s["source"], s["standard_name"]) for s in d["names"] if SOURCE_CN_NAMES.get(s["source"]) != s["standard_name"]]
check("T3_与SOURCE_CN_NAMES零漂移", not mismatch, str(mismatch[:3]))

fam = d.get("similar_families", {})
sem = [k for k, v in fam.items() if isinstance(v, list) and any(isinstance(x, str) and x.startswith("【区分】") for x in v)]
check("T4_语义家族标注在位", len(sem) >= 5, "%d个语义家族" % len(sem))

print()
if FAILS:
    print(f"结果: FAIL ({len(FAILS)}项) -> {FAILS}")
    sys.exit(1)
print("结果: 全部 PASS | 命名法令2026-01 守卫通过 | %d组法定名冻结" % len(d["names"]))
sys.exit(0)
