# -*- coding: utf-8 -*-
"""M-3 延伸跑次薄壳 (2026-10-12 试跑; 11-30 复核口径=E3 回测延伸, 主人「继续」令固化)
=====================================================================
派生关系: 复用 tulong_ab_m3_trail_equity_20260903.py 全部臂参数/判据逻辑(逐字
不动, 判据跑前已冻结), 仅运行时覆盖输出目录——9/3 原件 OUT_DIR 写死, 直接跑会
覆盖 9/3 锚产物(绝不允许), 故以薄壳 import 后改模块属性:
  - m3.base.OUTPUT_DIR(引擎两臂产物) 与 m3.OUT_DIR(compare.json 判读层) 同指新目录
  - base.END_DATE 已随 10/9 全量回测专项延至 20261009(引擎常量, 非本壳改动)
诚实注记: compare.json 的 meta.date 字段是 9/3 模板字面量(不覆改原件逻辑),
跑次身份以本目录名 m3_trail_equity_extend_20261009 + meta.data_end=2026-10-09 为准。

只读研究: 不改生产配置, 不写库, 零交易权。
输出: reports/m3_trail_equity_extend_20261009/ (两臂三件套 + compare.json)
"""
import importlib.util
import os
import sys

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

NEW_OUT = r"D:\zcodesj\reports\m3_trail_equity_extend_20261009"
os.makedirs(NEW_OUT, exist_ok=True)

spec = importlib.util.spec_from_file_location(
    "m3_orig", r"D:\zcodesj\tulong_ab_m3_trail_equity_20260903.py")
m3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m3)

# 双处覆盖: 引擎产物目录 + 判读层目录 (9/3 逻辑原样, 仅落点不同)
m3.base.OUTPUT_DIR = NEW_OUT
m3.OUT_DIR = NEW_OUT

if __name__ == "__main__":
    assert os.environ.get("TULONG_BT_OUT") or True
    print("[extend-run] OUT_DIR ->", NEW_OUT)
    print("[extend-run] base.END_DATE =", m3.base.END_DATE)
    print("[extend-run] base.QFQ_PATH =", m3.base.QFQ_PATH)
    m3.main()
