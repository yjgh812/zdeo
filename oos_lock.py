#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""oos_lock — OOS 一票制锁（2026-08-26 O8 物化，主人方法论裁决）
=====================================================================
规则（WALK_FORWARD_LEARNING_20260825 / oos-one-vote 裁决）：
  - 锁定日（lock asof）之后的市场数据 = OOS 区，**禁止用于任何策略调参/反哺**；
  - 读取 OOS 区只允许 --validate（只验证不调参，试验记 validate 标记）；
  - 强制闯关（--force）允许执行但记 violation_log（no_reasoning_gate 类）。

真源: data/oos_lock.jsonl（每行 {"locked_from","note","ts"}，最新一行为准）
用法:
  python oos_lock.py lock --asof 20260826 --note "round9终态后锁死"
  python oos_lock.py status
  python oos_lock.py check --end 20260901 [--validate] [--force]
退出码: check 0=放行 2=OOS锁拦截
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
LOCK_FILE = ROOT / "data" / "oos_lock.jsonl"


def _norm(d: str) -> str:
    return str(d).replace("-", "")[:8]


def load_lock() -> dict | None:
    if not LOCK_FILE.exists():
        return None
    lines = [l for l in LOCK_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def lock(asof: str, note: str) -> dict:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {"locked_from": _norm(asof),
           "note": note[:300], "ts": datetime.now().isoformat(timespec="seconds")}
    cur = load_lock()
    if cur and cur.get("locked_from") == rec["locked_from"]:
        return {"appended": False, "reason": "同锁定日已存在(幂等)", "lock": cur}
    with open(LOCK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"appended": True, **rec}


def check(end: str, validate: bool = False, force: bool = False,
          strategy: str = "", actor: str = "wfa_gate") -> dict:
    cur = load_lock()
    if not cur:
        return {"ok": True, "reason": "无 OOS 锁(未设置), 放行"}
    lk = cur["locked_from"]
    if _norm(end) <= lk:
        return {"ok": True, "reason": f"窗口止 {end} ≤ 锁定日 {lk}, 未触碰 OOS 区"}
    if validate:
        return {"ok": True, "reason": f"--validate 只读验证模式允许读 OOS 区(锁 {lk})",
                "validate": True}
    if force:
        try:
            import violation_log as vl
            vl.add_entry("no_reasoning_gate", actor, "OOS一票制",
                         f"{strategy or '未知策略'} 窗口止{end} 越过锁定日{lk} 强制闯关调参")
        except Exception:
            pass
        return {"ok": True, "forced": True,
                "reason": f"⚠️ force 闯关(已记violation_log), 窗口止{end} > 锁定日{lk}"}
    return {"ok": False,
            "reason": f"🔴 OOS一票制拦截: 窗口止 {end} > 锁定日 {lk}"
                      f"（锁定后数据禁调参; 只读验证加 --validate; 强制闯关 --force 记违规）"}


def main() -> int:
    p = argparse.ArgumentParser(description="OOS 一票制锁")
    sub = p.add_subparsers(dest="cmd", required=True)
    alock = sub.add_parser("lock")
    alock.add_argument("--asof", required=True, help="锁定日 YYYYMMDD（此后数据为 OOS）")
    alock.add_argument("--note", default="")
    sub.add_parser("status")
    ack = sub.add_parser("check")
    ack.add_argument("--end", required=True, help="试验数据窗口止 YYYYMMDD")
    ack.add_argument("--validate", action="store_true")
    ack.add_argument("--force", action="store_true")
    ack.add_argument("--strategy", default="")
    a = p.parse_args()
    if a.cmd == "lock":
        print(json.dumps(lock(a.asof, a.note), ensure_ascii=False))
        return 0
    if a.cmd == "status":
        print(json.dumps(load_lock() or {"status": "未设置 OOS 锁"}, ensure_ascii=False))
        return 0
    out = check(a.end, a.validate, a.force, a.strategy)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
