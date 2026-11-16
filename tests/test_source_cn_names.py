# -*- coding: utf-8 -*-
"""tests/test_source_cn_names.py — 源→中文名单一真源·覆盖断言

防复发背景: 10/31-11/1 会话三轮归因口径漂移(台账机器名/业务名/官方报告两套名字),
根修复 = _common.SOURCE_CN_NAMES 单一真源 + group_pnl_report v2 报告消费(cn字段)。
本测试守两条:
  T1 源登记全覆盖: PROVEN|EXPERIMENTAL|RETIRED 每个源都必须有中文名(漏登即FAIL);
  T2 官方报告消费一致: 最新 group_pnl_report_*.json 里每个源组在映射中可查
     (报告文件不存在时 SKIP, 不视为失败)。
运行: python tests/test_source_cn_names.py   (纯只读+断言, 零交易权, 无日志写入)
"""
import sys, os, json, glob

sys.path.insert(0, r"D:\zcodesj")
from _common import (SOURCE_CN_NAMES, PROVEN_EDGE_SOURCES,
                     EXPERIMENTAL_SOURCES, RETIRED_SOURCES)

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


# T1 登记表全覆盖
registered = PROVEN_EDGE_SOURCES | EXPERIMENTAL_SOURCES | RETIRED_SOURCES
missing = sorted(registered - set(SOURCE_CN_NAMES))
check("T1_登记源全覆盖", not missing, f"缺失: {missing}" if missing else f"覆盖{len(registered)}源")

extra = sorted(set(SOURCE_CN_NAMES) - registered - {"early_single_unregistered", "manual_master_app_backfill"})
check("T1b_无孤儿键", not extra, f"多余: {extra}" if extra else "")

# T2 官方报告消费一致(文件存在才校验)
reports = sorted(glob.glob(os.path.join(r"D:\zcodesj", "data", "group_pnl_report_*.json")))
if reports:
    d = json.load(open(reports[-1], encoding="utf-8"))
    srcs = set(d.get("groups_source", {}).keys())
    miss2 = sorted(s for s in srcs if s not in SOURCE_CN_NAMES)
    check("T2_官方报告源可查", not miss2,
          f"最新={os.path.basename(reports[-1])} 缺失: {miss2}" if miss2 else f"报告{len(srcs)}组全可查")
    v2_groups = [g for g, v in d.get("groups_source", {}).items() if isinstance(v, dict) and v.get("cn")]
    check("T2b_报告cn字段已注入", len(v2_groups) > 0,
          f"{len(v2_groups)}组带cn" if v2_groups else "报告尚无cn字段(旧产物, 属正常, 今晚22:00后刷新)")
else:
    print("SKIP T2: 暂无 group_pnl_report_*.json 产物")

print()
if FAILS:
    print(f"结果: FAIL ({len(FAILS)}项) -> {FAILS}")
    sys.exit(1)
print("结果: 全部 PASS")
sys.exit(0)
