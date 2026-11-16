# -*- coding: utf-8 -*-
"""tests/test_profitable_methods_registry.py — 盈利选股方式登记表守卫

主人令 2026-11-01「记住这些选股方式」：登记表必须永远在册、可加载、结构完整。
T1 文件存在且 JSON 合法（经 _common.get_profitable_methods 加载器，fail-loud）
T2 方法族总数 >= 20（V5=23，四大类）
T3 关键方法在册（防止未来误删：价值引擎/量化第一/突破共振/中枢对照/MASTER-HIST/跟踪池）
T4 版本演进记录在案（V1→V5 五轮纠错史）
T5 九组框架对照在册（主人 10/27 令官方框架）
运行: python tests/test_profitable_methods_registry.py   (只读+断言, 零交易权, 无日志写入)
"""
import sys

sys.path.insert(0, r"D:\zcodesj")
from _common import get_profitable_methods, PROFITABLE_METHODS_REGISTRY_PATH

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


# T1 加载器 fail-loud 读取
try:
    reg = get_profitable_methods()
    check("T1_登记表存在且可加载", True, PROFITABLE_METHODS_REGISTRY_PATH)
except Exception as e:
    check("T1_登记表存在且可加载", False, f"{type(e).__name__}: {e}")
    print(f"结果: FAIL -> {FAILS}")
    sys.exit(1)

all_methods = [m for v in reg.get("methods", {}).values() if isinstance(v, list) for m in v]
named = [m for m in all_methods if isinstance(m, dict)]
check("T2_方法族数量>=20", len(all_methods) >= 20, f"共{len(all_methods)}族")

joined = " ".join(str(m.get("name", "")) for m in named)
for key in ("bp_ep价值引擎", "量化综合排名第一", "突破共振", "探底缩量企稳",
            "中枢对照", "HIGHWIN", "MASTER/HIST双模型", "多源白名单调仓批",
            "挂账核销批", "跟踪池体系"):
    check(f"T3_关键方法在册[{key}]", key in joined)

import re as _re
check("T4_版本演进在案", bool(_re.match(r"V([5-9]|[1-9]\d{2})", str(reg.get("version", "")))),
      f"version={reg.get('version')}（V5起为多轮纠错后定稿, V7=盘前筛选破案+复现批次）")

ngf = reg.get("nine_group_frame", {})
check("T5_九组框架对照在册", isinstance(ngf, dict) and len(ngf.get("收录对照", {})) >= 9,
      f"九组对照{len(ngf.get('收录对照', {}))}组")

print()
if FAILS:
    print(f"结果: FAIL ({len(FAILS)}项) -> {FAILS}")
    sys.exit(1)
print(f"结果: 全部 PASS | 登记表 {reg.get('version')} 共{len(all_methods)}方法族 | {PROFITABLE_METHODS_REGISTRY_PATH}")
sys.exit(0)
