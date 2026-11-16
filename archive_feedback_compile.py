#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""archive_feedback_compile.py — 证伪档案→规则反哺编译器（L4，2026-09-29）。

自省报告 §五L4：falsified 档案+范式库编译为闸参数候选，季度评审进闸（防死灰+防过期）。
本件=首版编译：71 条证伪档案按**建议去向**六桶归类，产出季度评审呈报件。
裁决权=主人；本清单不改变任何闸行为。

六桶口径:
  A_已物化   证伪结论已落地为闸/防线（反哺通道已验证工作, 无需动作）
  B_铁律禁令 执行/风控层禁令（宪法§10邻域, 清单化防复活）
  C_接线候选 有数据支撑的推荐规则（评审后可进闸/接线的正向候选）
  D_研究负面 预算负面清单（禁复挖方向, 防trial预算浪费; 复活须新prereg+新证据）
  E_验证结论 验证性结论非路线否定（载体/参数未达, 路线保持开放）
  F_待补全   字段缺失待补登
措辞纪律: 评审治理文档, 禁名单制联想字样。

用法: python archive_feedback_compile.py
输出: reports/archive_feedback_review_20260929.md
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime

ROOT = r"D:\zcodesj"
SRC = os.path.join(ROOT, "data", "falsified_rules.json")
OUT = os.path.join(ROOT, "reports", "archive_feedback_review_20260929.md")

# 编号→桶（逐条人工归类, 依据=reason/status/evidence 原文）
MAP = {
    # B 铁律禁令（执行/风控）
    **{k: "B" for k in ("S-1", "S-2", "S-3", "S-4", "S-5", "M-3", "STOP-ATR2-VS-FIXED8")},
    # A 已物化
    **{k: "A" for k in ("M-2", "M-4", "L-2")},
    # C 接线候选（正向推荐/强知识）
    **{k: "C" for k in ("TIMING-DDPROTECT-STAGE4", "BREAKOUT-PULLBACK-ENTRY",
                        "M-1")},
    # D 研究负面清单（禁复挖）
    **{k: "D" for k in (
        "WINNER-MOMENTUM-Q10", "ENGINE-B-HIGHPOS-BREAKOUT", "WINRATE-RANK-TOP10",
        "BREAKOUT-MIDPOS", "KRONOS-ZS-STAGE1", "SELECTION-CHECKLIST", "PEAD-PREG",
        "SENTIMENT-CYCLE-V1", "SENTIMENT-CYCLE-DAILY-ALPHA", "EMOTION-PHASE-V2-NEXTDAY",
        "MAINLINE-CONSTRAINT-STAGE1", "MAINLINE-STAGE2", "MAINLINE-STAGE2-S2B-LOWPOS",
        "WEEKLY-FILTER-CHAN12", "WEEKLY-FILTER-CHAN3", "SECTOR-FILTER-CHAN12",
        "PROFILE-STARTUP-COND", "REGIME-MOM-IDENT", "PEAK-CLUSTER-AS-SIGNAL",
        "UP-BUCKET-UNCOND", "PULLBACK-DEPTH-TRADEABLE", "CB-DOUBLELOW-L3",
        "BOX-BREAKOUT-LOWPOS", "BREAKOUT-CLUSTER-FILTER", "BREAKOUT-CONFIRM-FILTER",
        "ENGINE-B2-PULLBACK-R31", "TIMING-PANIC-STAGE1", "PORTRAIT-REGIME-GATE",
        "PULLBACK-FORM-ENTRY-2026", "L2-LHB-SEAT-INST-STAGE1", "M2-UNLOCK-FILTER",
        "M2-GOODWILL-FILTER", "L2-SELFLOSS-FILTER", "B4-TURNOVER-TILT",
        "NEG-TURNOVER-POOL-TOP500", "SKILL-SWEEP-STAGE1", "SKILL-SWEEP2-STAGE1",
        "CHIP-MANUAL-3GATE", "STYLE-GATE-STAGE4", "STYLE-GATE-STAGE5",
        "Y-REALIZABLE-LABEL-VARIANT", "COMBOC-CHAN-LOOKAHEAD",
        "BREAKOUT-SCOUT-TRADEABLE", "HOLDING-DECAY-OBSERVATION",
        "ETF-MOM-ROTATION-L3", "SKILL-SWEEP-STAGE2")},
    # E 验证性结论（路线开放）
    **{k: "E" for k in (
        "REGIME-ADAPT-STAGE1", "RANKLOSS-STAGE1", "TIMING-VOLTARGET-STAGE1",
        "TIMING-VOLTARGET-STAGE2", "TIMING-DDPROTECT-STAGE1", "TIMING-DDPROTECT-STAGE2",
        "TIMING-DDPROTECT-STAGE3", "TIMING-CONDMATRIX-STAGE1",
        "S3-REOPEN-STAGE1", "L-1", "MOM-GATE-REPRODUCIBILITY-FIX", "MOM-GATE-FULLHIST")},
}

BUCKET_TITLE = {
    "A": "A 已物化防线（反哺通道已验证, 无需动作）",
    "B": "B 执行/风控铁律禁令（清单化防复活, 复活=触碰已否定路线违规）",
    "C": "C 接线候选（有数据支撑的推荐规则, 季度评审进闸议题）",
    "D": "D 研究预算负面清单（禁复挖方向; 复活须新 prereg+条件不同证据）",
    "E": "E 验证性结论（载体/参数未达, 路线开放, 重开须新 prereg）",
    "F": "F 待补全字段（下轮补登）",
}
BUCKET_NOTE = {
    "C": "- TIMING-DDPROTECT-STAGE4: 滞回2%变体全面占优基线=原文标注「接线推荐规则」——回撤保护覆盖层进闸议题, 需组合级载体确认;\n"
         "- BREAKOUT-PULLBACK-ENTRY: 相对维度配对增量+2.656pp/t48.87=「突破族内回踩入场优于追价」的强知识——方向与 R31 闸一致, 可作 R31 注释级佐证;\n"
         "- M-1: 板块集中度 cap 双证伪=不加此类 cap 的否定性知识, 防 re-bring-up。",
    "D": "- 追涨/追高族(4条)+动量最强档+排名Top10: 与「涨≥3%禁追买」行为准则互证, 已有闸(R-S5/R31/R32/R24)覆盖;\n"
         "- 情绪相位族(3条, 含 9/29 新增): 日频情绪→次日判别机制性无增量, 情绪域预算转盘中结构(炸板时刻/封单流);\n"
         "- 主线聚合/技术滤波/画像条件族: prereg 判死, 复活须新证据。",
}


def main() -> int:
    d = json.load(open(SRC, encoding="utf-8"))
    rules = d.get("rules", {})
    buckets: dict[str, list[tuple]] = defaultdict(list)
    for k, v in rules.items():
        bucket = MAP.get(k, "F")
        reason = str(v.get("reason", ""))[:90]
        buckets[bucket].append((k, reason, str(v.get("status", ""))[:40]))

    lines = [f"# 证伪档案→规则反哺评审清单（{datetime.now().strftime('%Y-%m-%d')}，L4 首版）", "",
             f"- 档案总数 {len(rules)} 条 → 六桶归类。裁决权=主人；本清单不改变任何闸行为。",
             "- 生成器 archive_feedback_compile.py（映射表人工归类, 依据=档案原文）", ""]
    for b in "ABCDEF":
        items = buckets.get(b, [])
        lines += [f"## {BUCKET_TITLE[b]}（{len(items)} 条）", ""]
        if b in BUCKET_NOTE:
            lines += [BUCKET_NOTE[b], ""]
        for k, reason, st in sorted(items):
            lines.append(f"- **{k}** [{st}]: {reason}")
        lines.append("")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[L4] 归类=" + " ".join(f"{b}:{len(buckets.get(b, []))}" for b in "ABCDEF") + f" -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
