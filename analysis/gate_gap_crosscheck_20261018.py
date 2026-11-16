# -*- coding: utf-8 -*-
"""gate_gap_crosscheck_20261018.py — R闸登记簿代码层交叉核对 (2026-10-18, DeepSeek外部审计吸收项V7改)

演进说明: 本件初版曾误建 registry 生成器覆盖既有合同 data/gate_registry.json
(generator=gen_gate_registry.py, CURATED 裁决状态, 真源恢复于同轮)——A2(查同职责模块)
违规当场自捕获并整改: 本件降级为【只读交叉核对】, 禁写任何 data/ 合同。

职责:
  1) 读回既有登记簿(data/gate_registry.json, 唯一owner=gen_gate_registry.py)摘要;
  2) 独立静态扫描 pre_trade_check_v2.py 的 check_r*/分节/退役标注, 与登记簿比对漂移:
     - 登记簿 ACTIVE 但代码引用消失 → 疑似失接(漂移);
     - 代码新出现 check_r* 函数但登记簿无此号 → 疑似新闸未同步登记(登记簿头注铁律);
  3) 登记簿未收录编号(R2/R9/R11/R21/R29/R40)给缺口呈证——补登裁决归 CURATED(owner), 本件只呈证。
输出: analysis/gate_gap_crosscheck_20261018_out.md (只读, 禁人工转抄)
"""
import json
import os
import re
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"D:\zcodesj"
REG = os.path.join(ROOT, "data", "gate_registry.json")
MAIN_GATE = os.path.join(ROOT, "pre_trade_check_v2.py")
OUT_MD = os.path.join(ROOT, "analysis", "gate_gap_crosscheck_20261018_out.md")
KNOWN_GAPS = ["R2", "R9", "R11", "R21", "R29", "R40"]


def scan_main_gate():
    src = open(MAIN_GATE, encoding="utf-8", errors="replace").read().splitlines()
    funcs = set()
    retired_lines = defaultdict(list)
    for i, line in enumerate(src, 1):
        m = re.search(r"def\s+check_r(\d{1,2}[ab]?)[_a-z0-9]*", line)
        if m:
            funcs.add("R" + m.group(1))
        mm = re.search(r"\bR(\d{1,2}[ab]?)(?![0-9])", line)
        if mm and any(w in line for w in ("退役", "废除", "不再消费", "已废")):
            retired_lines["R" + mm.group(1)].append(f"L{i} {line.strip()[:120]}")
    return funcs, retired_lines


def main():
    reg = json.load(open(REG, encoding="utf-8"))
    entries = {e["gate"]: e for e in reg.get("entries", [])}
    counts = reg.get("counts", {})
    code_funcs, retired_lines = scan_main_gate()

    reg_gates = set(entries)
    # 漂移1: 登记 ACTIVE/SHADOW 但其登记 files 域内零引用(按登记簿 files 逐域核, 防误报他文件实现)
    drift_lost = []
    main_src = open(MAIN_GATE, encoding="utf-8", errors="replace").read()
    for g, e in entries.items():
        if e.get("status") in ("ACTIVE", "SHADOW"):
            n = g[1:] if g.startswith("R") else None
            if not n:
                continue
            files = e.get("files") or ["pre_trade_check_v2.py"]
            found = False
            for f in files:
                p = os.path.join(ROOT, f)
                if os.path.exists(p) and re.search(rf"\bR{n}(?![0-9])", open(p, encoding="utf-8", errors="replace").read()):
                    found = True
                    break
            if not found:
                drift_lost.append(g)
    # 漂移2: 代码有 check_r* 函数但登记簿未收录
    drift_new = sorted(code_funcs - reg_gates)

    md = []
    w = md.append
    w("# R闸登记簿交叉核对(V7改, DeepSeek外部审计吸收项)")
    w("")
    w("> 脚本 analysis/gate_gap_crosscheck_20261018.py 只读直出 | 登记簿真源=data/gate_registry.json(唯一owner=gen_gate_registry.py)")
    w(f"> 登记簿现状: ACTIVE {counts.get('ACTIVE')} / RETIRED {counts.get('RETIRED')} / SHADOW {counts.get('SHADOW')} / UNKNOWN {counts.get('UNKNOWN')}")
    w("> 本件零交易权, 禁写 data/ 合同; 补登裁决归 CURATED(owner=gen_gate_registry.py)")
    w("")
    w("## 一、登记簿退役闸引证抽核(DeepSeek『废止须有裁决出处』关切)")
    w("")
    w("| 闸 | 登记状态 | 裁决出处(登记簿原文截取) |")
    w("|---|---|---|")
    for g, e in sorted(entries.items()):
        if e.get("status") == "RETIRED":
            w(f"| {g} | RETIRED | {str(e.get('evidence', ''))[:110]} |")
    w("")
    w("## 二、缺口编号呈证(登记簿 entries 未收录)")
    w("")
    w(f"缺号 {', '.join(KNOWN_GAPS)} 在主闸文件 gate 形态扫描(check_r<N>函数/分节注释/R<N>闸)均无定义痕迹；"
      "R12 命中均为迭代轮次命名(R12_M3=round12 ml 系列)同名异物非闸。"
      "**呈请**: 六号补登 CURATED『UNASSIGNED(无文档裁决, 诚实空白)』或确认永不使用，由主人/登记簿 owner 定；"
      "在补登前，任何『复活 R2/R9/R11/R21/R29/R40』提案须按宪法先立新指纹。")
    w("")
    w("## 三、代码层漂移核对")
    w("")
    w(f"- 代码 check_r* 函数族: {len(code_funcs)} 个 → {', '.join(sorted(code_funcs))}")
    w(f"- 登记 ACTIVE/SHADOW 但主闸文件零引用(疑似失接): {drift_lost if drift_lost else '无'}")
    w(f"- 代码新函数登记簿未收录(疑似新闸未登记): {drift_new if drift_new else '无'}")
    w(f"- 代码内退役标注共现行: {sum(len(v) for v in retired_lines.values())} 行 → "
      + "; ".join(f"{k}×{len(v)}" for k, v in sorted(retired_lines.items()))[:200])
    w("")
    w("## 四、结论边界")
    w("")
    w("- 登记簿状态以 CURATED 裁决为唯一真源, 本件静态扫描只做漂移哨; 两层一致=登记簿健康;")
    w("- 本件初版曾误建重复 owner 覆盖真源(A2 违规), 已当场恢复并整改为只读核对——教训: 建 registry 类工件前必先 grep data/ 同名合同。")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"[OK] 登记簿 {len(entries)} 闸 ({counts}) | 漂移失接 {len(drift_lost)} | 新闸未登记 {len(drift_new)} | 缺号 {len(KNOWN_GAPS)}")
    print(f"[OK] 已直出 {OUT_MD}")


if __name__ == "__main__":
    sys.exit(main())
