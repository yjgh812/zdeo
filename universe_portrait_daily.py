#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""universe_portrait_daily.py — 宇宙画像日更编排器（五步, 2026-11-17 扩容）
================================================================================
步骤(顺序执行, 单步失败不中断后续):
  1. universe_portrait_stats.py --snapshot   全宇宙画像快照 + 横截面分布
  2. universe_portrait_fullseries.py         D层全史库重建(幂等~78s; 2026-11-17 补调度缺位, D1动态=qfq最新)
  3. analysis/portrait_ts2vec_daily.py       TS2Vec形态检索日更面板(鲜度闸/GPU闸 fail-N/A)
  4. universe_portrait_brief.py              三池简报+形态检索面板(机读json)
  5. portrait_gate_shadow.py                 双门影子记账(幂等)

挂载: PortraitDailyPipeline_2300 step4(23:00 夜链 qfq 更新后, 数据已新)。
侦察级零交易权; 手动入口: python universe_portrait_daily.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = r"D:\zcodesj"
STEPS = [
    ("snapshot", [sys.executable, os.path.join(ROOT, "universe_portrait_stats.py"), "--snapshot"]),
    ("fullseries", [sys.executable, "-I", os.path.join(ROOT, "universe_portrait_fullseries.py")]),
    ("ts2vec", [sys.executable, "-I", os.path.join(ROOT, "analysis", "portrait_ts2vec_daily.py")]),
    ("brief", [sys.executable, os.path.join(ROOT, "universe_portrait_brief.py")]),
    ("gate_shadow", [sys.executable, os.path.join(ROOT, "portrait_gate_shadow.py")]),
]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    t0 = time.time()
    print(f"=== 宇宙画像三件套 {time.strftime('%F %T')} ===")
    rcs = {}
    for name, cmd in STEPS:
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800)
            rcs[name] = r.returncode
            tail = (r.stdout or "").strip().splitlines()[-2:]
            for line in tail:
                print(f"  [{name}] {line}")
            if r.returncode != 0:
                err = (r.stderr or "").strip().splitlines()[-3:]
                for line in err:
                    print(f"  [{name}:ERR] {line}")
        except Exception as e:
            rcs[name] = -1
            print(f"  [{name}:EXC] {e}")
    ok = all(v == 0 for v in rcs.values())
    print(f"=== 三件套完成 {'OK' if ok else 'WARN'} rc={rcs} {time.time()-t0:.1f}s ===")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
