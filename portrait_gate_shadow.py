#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_gate_shadow.py — 画像双门影子台账（侦察级·零交易权）
================================================================================
PREREG_PORTRAIT_GATES_20261002 的影子记账件。

规则(全部来自 2026-10-02 深度研究 M2 定谳, 零新发明):
  入场信号 = 门A开(低区占比≥70%) ∧ 门C关(20日上涨票占比≤50%) ∧ A池命中(brief 引擎同式)
  对照组   = 其余门组合下的 A池命中(同规则不同市况 = 市况门增量对照)
  判读     = 60 交易日影子成熟后回查 qfq 次开起 -8%止损口径(三门: G1 fwd20>基线1pp /
             G2 胜率≥52% / G3 n≥30; 另: 开门组-关门组差≥2pp)

账本: data/portrait_gate_shadow_ledger.jsonl (追加式, 同 asof 幂等跳过)
信号日只记 asof/code/name/px(收盘); 判读期回查 qfq 补算, 账本保持最小。

用法: python portrait_gate_shadow.py            # 读最新 brief json 记账
      python portrait_gate_shadow.py --replay   # 扫全部历史 brief json 补账(如有)
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
LEDGER = os.path.join(ROOT, "data", "portrait_gate_shadow_ledger.jsonl")
BRIEF_GLOB = os.path.join(ROOT, "data", "universe_portrait_brief_*.json")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_ledger_asofs():
    done = set()
    if not os.path.exists(LEDGER):
        return done
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["asof"])
            except Exception:
                continue
    return done


def record(brief_path: str) -> int:
    with open(brief_path, encoding="utf-8") as f:
        b = json.load(f)
    asof = b["asof"]
    combo = f"门A{b['gateA']}∧门C{b['gateC']}"
    entries = b.get("pool_A") or []
    rows = []
    for e in entries:
        rows.append(dict(asof=asof, ts=datetime.now().strftime("%Y-%m-%d %H:%M"),
                         combo=combo, gateA=b["gateA"], gateC=b["gateC"],
                         code=e["code"], name=e["name"], px=e["px"],
                         state=e.get("state"), golden=e.get("golden"),
                         prereg="PREREG_PORTRAIT_GATES_20261002",
                         prereg_goldenx="PREREG_GRID_GOLDENX_OBSERVE_20261003" if e.get("golden") else None))
    # A池空也要记门状态行(门持续天数是右侧确认候选的观察量)
    if not rows:
        rows = [dict(asof=asof, ts=datetime.now().strftime("%Y-%m-%d %H:%M"),
                     combo=combo, gateA=b["gateA"], gateC=b["gateC"],
                     code=None, name=None, px=None, state=None,
                     prereg="PREREG_PORTRAIT_GATES_20261002")]
    with open(LEDGER, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"[GATE-SHADOW] asof={asof} {combo} A池命中={len(entries)} 行={len(rows)}")
    return len(rows)


def main(replay=False):
    done = load_ledger_asofs()
    files = sorted(glob.glob(BRIEF_GLOB))
    if not files:
        print("[GATE-SHADOW] 无 brief json(先跑 universe_portrait_brief.py)")
        return 1
    todo = files if replay else [files[-1]]
    n_new = 0
    for fp in todo:
        asof = os.path.basename(fp).replace("universe_portrait_brief_", "").replace(".json", "")
        if asof in done:
            continue
        n_new += record(fp)
    print(f"[GATE-SHADOW] 完成: 新增 {n_new} 行 -> {LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main(replay="--replay" in sys.argv))
