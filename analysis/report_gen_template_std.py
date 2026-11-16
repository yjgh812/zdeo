# -*- coding: utf-8 -*-
"""报告计数纪律标准模板 (2026-10-12 净增量项7, 主人「继续」令)
=====================================================================
背景铁律: 报告里的计数/名次/清单**禁止人工转抄** —— 必须 tmp 生成器脚本直出
+ 自检断言 + 读回核验(2026-10-11 三轮复核连抓幽灵行+计数错+名次错事故入库)。
本模板沿 analysis/backtest_template_std.py 先例固化该三件套, 新报告生成器
复制此骨架起步。

三件套:
  1) 直出   —— 所有计数/清单由源数据计算生成, 函数内禁止出现手写字面计数
  2) 断言   —— 生成时对总数/单调性/非空做 assert, 断言失败=不产出报告
  3) 读回   —— 写盘后重新读入文件, 核对关键计数与源数据一致

用法: 复制本文件为 <你的生成器>.py, 填充 SOURCE/load_source/build_report
三个函数, 保留 assert_report/write_and_verify 骨架。自检: 直接运行本文件。
"""
import sys

TEMPLATE_VERSION = "count-discipline-v1 (2026-10-12)"


# ---- 1) 直出层: 示例源数据与报告构建(替换为你自己的) --------------------
def load_source():
    """返回源数据。示例: [(分组, 数值), ...]。禁在此层写死任何结论计数。"""
    return [("A", 3), ("A", 4), ("B", 1), ("C", 2), ("C", 9)]


def build_report(items):
    """由源数据计算全部计数, 返回 (文本, 计数字典)。"""
    counts = {}
    for grp, _ in items:
        counts[grp] = counts.get(grp, 0) + 1
    total = len(items)
    lines = ["# 示例报告(计数纪律模板)", "",
             "| 分组 | 计数 |", "|---|---|"]
    for grp in sorted(counts):
        lines.append("| %s | %d |" % (grp, counts[grp]))
    lines += ["", "总计 %d 项 / %d 组" % (total, len(counts))]
    return "\n".join(lines) + "\n", {"total": total, "groups": counts}


# ---- 2) 断言层: 产出前自检(断言失败=不产出) ----------------------------
def assert_report(items, meta):
    assert meta["total"] == len(items), "总数断言失败"
    assert sum(meta["groups"].values()) == len(items), "分组合计断言失败"
    assert len(items) > 0, "空源数据不产出报告"


# ---- 3) 读回层: 写盘后核对(不一致=失败) --------------------------------
def write_and_verify(text, meta, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    with open(out_path, encoding="utf-8") as f:
        back = f.read()
    assert ("总计 %d 项" % meta["total"]) in back, "读回: 总计不符"
    for grp, n in meta["groups"].items():
        assert ("| %s | %d |" % (grp, n)) in back, "读回: %s 行不符" % grp
    return True


def selfcheck(out_path="tmp_count_template_demo.md"):
    items = load_source()
    text, meta = build_report(items)
    assert_report(items, meta)
    write_and_verify(text, meta, out_path)
    import os
    os.remove(out_path)
    print("[count-template] 自检 PASS (%s): 直出/断言/读回 三件套全通" % TEMPLATE_VERSION)
    return 0


if __name__ == "__main__":
    sys.exit(selfcheck())
