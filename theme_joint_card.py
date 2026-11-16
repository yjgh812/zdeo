#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""theme_joint_card.py — 情绪相位×主线结构 联合日卡生成器 v0（纯观测层，零交易权）

整合三源（全部已层数据，零新增抓取）：
  1) data/emotion_phase_v2_ledger.jsonl        → v2 五相位 + LU/BR/MB 分位（LOCKED 台账只读）
  2) 高度状态（台账内 mb 对比 T-5，无前视）     → height_rising/flat/collapse（第九轮交叉研究口径）
  3) data/theme_roles_YYYYMMDD.json            → 主线链五角色摘要（scanner 产物）

输出: reports/theme_joint_card_YYYYMMDD.md（人读日卡）
退出码: 0 正常 / 2 数据缺失
铁律: 相位与角色均为描述性叙述轴，禁作信号（PREREG_EMOTION_PHASE_V2 边界原样有效）；
      本件零交易权，不接闸门；接日更调度需主人另行裁决。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
LEDGER = os.path.join(ROOT, "data", "emotion_phase_v2_ledger.jsonl")
PHASE_CN = {"bottom": "冰点", "healing": "发酵/修复", "euphoric": "主升/亢奋",
            "ebbing": "退潮", "diverging": "分化(炸板极端)"}


def height_state(rows, i):
    if i < 5:
        return None
    mb_now, mb_prev = rows[i]["mb"], rows[i - 5]["mb"]
    if mb_now >= 4 and mb_now > mb_prev:
        return "height_rising"
    if mb_now <= 2:
        return "height_collapse"
    return "height_flat"


def main():
    ap = argparse.ArgumentParser(description="情绪×主线联合日卡 v0（观测层零交易权）")
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    ap.add_argument("--roles", default=None, help="scanner 输出路径（默认 data/theme_roles_<date>.json）")
    a = ap.parse_args()

    if not os.path.exists(LEDGER):
        print("[FAIL] emotion_phase_v2 台账不存在", file=sys.stderr)
        return 2
    rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
    rows.sort(key=lambda r: r["phase_date"])
    idx = {r["phase_date"]: i for i, r in enumerate(rows)}
    if a.date not in idx:
        print(f"[FAIL] 台账无 {a.date} 行（数据链未更新?）", file=sys.stderr)
        return 2
    i = idx[a.date]
    r = rows[i]
    hs = height_state(rows, i)
    hs_cn = {"height_rising": "上升", "height_flat": "走平", "height_collapse": "塌缩"}.get(hs, "N/A(样本<5日)")

    roles_path = a.roles or os.path.join(ROOT, "data", f"theme_roles_{a.date}.json")
    chains = []
    if os.path.exists(roles_path):
        rd = json.load(open(roles_path, encoding="utf-8"))
        chains = rd.get("chains", [])

    md = [f"# 情绪×主线联合日卡 {a.date}（观测层零交易权）\n"]
    md.append("## 情绪相位层（emotion_phase_v2，LOCKED 口径）")
    md.append(f"- 相位: **{PHASE_CN.get(r['phase'], r['phase'])}** ({r['phase']}) | 证据: {r['evidence']}")
    md.append(f"- 广度: 涨停 {r['lu']} 家（250日分位 {r['lu_pctl']}）| 炸板率 {r['br']}（分位 {r['br_pctl']}）| 最高板 {r['mb']}")
    md.append(f"- 高度状态: **{hs_cn}**（mb 对比 5 日前；第九轮交叉研究口径）")
    md.append("")
    md.append("## 主线结构层（theme_roles_scanner）")
    if chains:
        for ch in chains:
            ld = ch.get("leader", {})
            roles_line = []
            for role in ("中军", "助攻", "补涨", "卡位"):
                ms = [m for m in ch.get("members", []) if m.get("role") == role]
                if ms:
                    roles_line.append(f"{role}=" + "/".join(f"{m['name']}({m['code']}){m['boards']}板" for m in ms))
            takeover = " 【卡位接管】" if ch.get("takeover") else ""
            md.append(f"- **{ch.get('chain_name', ch.get('chain_id'))}**: 龙头 {ld.get('name')}({ld.get('code')}) "
                      f"{ld.get('boards')}板 市值{(ld.get('mv_wan') or 0)/1e4:.0f}亿{takeover}")
            if roles_line:
                md.append(f"  - " + " | ".join(roles_line))
    else:
        md.append("- scanner 标注缺失（需先跑 theme_roles_scanner）")
    md.append("")
    md.append("## 排序读法（情绪闸>主线>题材>买卖点）")
    md.append(f"1. 情绪闸先读: 相位={PHASE_CN.get(r['phase'], r['phase'])} + 高度{hs_cn} → 决定今日整体仓位姿态（闸门为准）")
    md.append("2. 主线次读: 有空间龙头的链优先（链内角色仅描述, 卡位/补涨位=预判毒区禁开仓）")
    md.append("3. 买卖点最后: 只在闸门放行格（G×强档）内执行既定信号")
    md.append("")
    md.append(f"> 边界: 描述性叙述轴禁作信号（prereg 原文）| 生成 {datetime.now().isoformat(timespec='seconds')} | 零交易权")

    mout = os.path.join(ROOT, "reports", f"theme_joint_card_{a.date}.md")
    open(mout, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"[DONE] 联合日卡 -> {mout}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
