# -*- coding: utf-8 -*-
r"""SOP_INDEX.md 生成器 (2026-10-12 净增量项8, 主人「继续」令)
=====================================================================
目的: 给 790KB 超大行 DAILY_WORKFLOW_SOP.md 配一份行号级索引, 让"grep 定位"
有现成地图 —— 不拆分 SOP 本体(V1 审计裁定: append-only 审计链形态保留),
零破坏补索引。

纪律: 标题/行号由脚本直出(禁人工转抄), 生成后读回核验段数一致。

用法: python analysis/gen_sop_index.py
输出: D:\zcodesj\SOP_INDEX.md
"""
import datetime
import os
import re
import sys

BASE = r"D:\zcodesj"
SOP = os.path.join(BASE, "DAILY_WORKFLOW_SOP.md")
OUT = os.path.join(BASE, "SOP_INDEX.md")


def main():
    n_bytes = os.path.getsize(SOP)
    h1, h2 = [], []
    with open(SOP, encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            if line.startswith("## "):
                h2.append((i, line[3:].strip()[:80]))
            elif line.startswith("# "):
                h1.append((i, line[2:].strip()[:80]))
    assert len(h2) >= 10, "二级标题过少(%d), SOP 结构漂移须排查" % len(h2)
    lines = [
        "# SOP 行号级索引 (SOP_INDEX)",
        "",
        "> 生成: analysis/gen_sop_index.py 直出(生成于 %s)。真源 = DAILY_WORKFLOW_SOP.md" % datetime.date.today().isoformat(),
        "(%.0fKB, 超大行文件**禁止整读**)。定位法: `sed -n '<行号>,<行号+N>p' DAILY_WORKFLOW_SOP.md`。" % (n_bytes / 1024),
        "> 段落数随 SOP 追加增长, 过期重跑本脚本刷新。",
        "",
        "## 一级标题 (%d)" % len(h1),
        "",
    ]
    lines += ["- L%d %s" % (ln, t) for ln, t in h1]
    lines += ["", "## 二级章节 (%d)" % len(h2), ""]
    lines += ["- L%-5d %s" % (ln, t) for ln, t in h2]
    lines += [
        "",
        "## 高频章节速查(按当前运营惯例注记, 过期以上方行号为准)",
        "",
        "- cron 台账 = 「一、全自动环节」段; 交易执行 = 「二、交易执行环节」段;",
        "- 铁律红线 = 「六、铁律红线」段; 单一事实源分域主从表 = 「八」段。",
        "",
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT, encoding="utf-8") as f:
        back = f.read()
    n_back = len(re.findall(r"^- L\d+ ", back, re.M))
    assert n_back == len(h1) + len(h2), "读回核验失败: 条目 %d != %d" % (
        n_back, len(h1) + len(h2))
    print("[sop-index] 直出 H1=%d H2=%d (SOP %.0fKB) -> %s (读回 PASS)" % (
        len(h1), len(h2), n_bytes / 1024, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
