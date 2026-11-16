#!/usr/bin/env python3
import json, os, subprocess, sys
from datetime import datetime

BASE = "D:/zcodesj"
REPORTS = BASE + "/reports"

checks = []

def add(name, script, args=None, expect=0):
    checks.append((name, script, args or [], expect))

add("时间闸-PASS检查", "trade_time_guard.py", ["check-buy"], 0)
add("闸门R1-R18", "pre_trade_check_v2.py", ["--code","002582","--name","test","--source","manual_confirmed","--skip-time-window","--no-log"], 0)
add("冲动拦截M-5", "m5_impulse_blocker.py", ["--action","buy","--code","002582","--skip-cooldown","--skip-questions"], 2)
add("情绪指数M-6", "attribution/emotion_index.py", ["--date","20260813","--json"], 0)
add("重合度体检M-1", "attribution/m1_alignment_check.py", ["--short"], 1)
add("归因过滤M-2", "attribution_feedback_filter.py", ["--report"], 0)
add("产业链匹配L-5", "attribution/l5_industry_chains.py", ["--match","碳酸锂","--json"], 0)
add("盘口解读L-6", "attribution/l6_orderflow_analyzer.py", ["--code","300750","--pattern","distribution","--json"], 0)

passed = 0
failed = 0
details = []

print("=" * 60)
print(f"  系统全链路健康检查 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

for name, script, args, exp in checks:
    path = BASE + "/" + script
    if not os.path.exists(path):
        print(f"  SKIP {name}: file not found")
        continue
    try:
        cmd = [sys.executable, path] + args
        # A5 铁律：Windows subprocess 显式 UTF-8（否则中文输出 GBK 解码失败 → stdout=None 假失败）
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        ok = (r.returncode == exp)
        tag = "PASS" if ok else "FAIL"
        print(f"  {tag} {name}: exit={r.returncode} (exp={exp})")
        if ok:
            passed += 1
        else:
            failed += 1
        details.append({"name": name, "status": tag, "exit": r.returncode, "detail": r.stdout.strip()[:150]})
    except Exception as e:
        print(f"  FAIL {name}: {e}")
        failed += 1
        details.append({"name": name, "status": "ERROR", "error": str(e)})

total = passed + failed
print("=" * 60)
print(f"  PASS {passed} / FAIL {failed} / TOTAL {total}")
if failed == 0:
    print("  ALL PASS - 系统全链路正常")
print("=" * 60)

report = {"timestamp": datetime.now().isoformat(), "passed": passed, "failed": failed, "total": total, "details": details}
path = os.path.join(REPORTS, f"system_health_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
os.makedirs(REPORTS, exist_ok=True)
with open(path, "w") as f:
    json.dump(report, f, indent=2)
print(f"Report: {path}")
