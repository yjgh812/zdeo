#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""universe_portrait_daily.py — 宇宙画像三件套日更编排器
================================================================================
步骤(顺序执行, 单步失败不中断后续):
  1. universe_portrait_stats.py --snapshot   全宇宙画像快照 + 横截面分布
  2. universe_portrait_brief.py              三池简报(含市况双门状态+机读json)
  3. portrait_gate_shadow.py                 双门影子记账(幂等)

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
