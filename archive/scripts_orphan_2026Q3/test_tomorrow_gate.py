#!/usr/bin/env python3
"""模拟明日环境测试闸门选票流程"""
import json, subprocess, sys

candidates = [
    ("002172", "澳洋健康", 2, 5, 0.30, "4.25", "4.30", "4.05"),  # 8/16修正: 原high 4.20<close 4.25自相矛盾致R6异常拒绝
    ("300404", "博济医药", 1, 5, 0.40, "14.45", "14.45", "14.45"),
    ("301060", "兰卫医学", 1, 5, 0.30, "11.96", "11.96", "11.96"),
    ("002644", "佛慈制药", 1, 3, 0.20, "8.80", "8.80", "8.80"),
    ("603998", "方盛制药", 1, 3, 0.20, "10.77", "10.77", "10.77"),
]

passed = 0
for code, name, board, scnt, pct, close, high, low in candidates:
    prev = f"{float(close)*0.98:.2f}"
    cmd = [sys.executable, "D:/zcodesj/pre_trade_check_v2.py", "--no-log",
           "--code", code, "--name", name, "--source", "mainline_radar",
           "--holdings", "15", "--total-cash", "1000000", "--amount", "50000",
           "--pct-60d", str(pct), "--close", close, "--high", high, "--low", low,
           "--sector-limit-up-count", str(scnt), "--prev-close", prev]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                       encoding="utf-8", errors="replace")
    try:
        data = json.loads(r.stdout)
    except:
        print(f"  PARSE_ERROR {code} {name}")
        continue
    v = data["verdict"]
    rej = " | ".join(data.get("rejects", [])[:2])
    warn = " | ".join(data.get("warnings", [])[:2])
    
    if v == "PASS":
        icon = "PASS"
        passed += 1
        info = warn or "(无警告)"
    elif "PASS" in v:
        icon = "WARN" 
        passed += 1
        info = warn or "(无警告)"
    else:
        icon = "REJECT"
        info = rej[:80] if rej else "(无原因)"
    
    print(f"  [{icon}] {code} {name} ({board}板/{scnt}板块涨停): {info[:80]}")

print(f"\n结果: {passed}/{len(candidates)} 通过")
print("（此测试运行在干净trade_state环境下，明早开盘时闸门将实际生效）")
