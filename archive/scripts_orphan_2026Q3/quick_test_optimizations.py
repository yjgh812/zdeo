#!/usr/bin/env python3
"""快速验证所有优化后的闸门运行正常"""
import subprocess, json

tests = [
    ("R3上限4+PASS", [
        "--code", "002437", "--name", "誉衡药业",
        "--source", "mainline_radar", "--price", "4.25",
        "--prev-close", "4.10", "--holdings", "10",
        "--total-cash", "1000000", "--amount", "50000",
        "--pct-60d", "0.30", "--close", "4.20",
        "--high", "4.30", "--low", "4.10",
        "--sector-limit-up-count", "5",
    ], "PASS"),
    ("R4+R18豁免(电力主线6只)", [
        "--code", "601991", "--name", "大唐发电",
        "--source", "mainline_radar", "--price", "6.50",
        "--prev-close", "6.30", "--holdings", "10",
        "--total-cash", "1000000", "--amount", "50000",
        "--pct-60d", "0.40", "--close", "6.45",
        "--high", "6.60", "--low", "6.30",
        "--market-env", "EXTREME_STRUCTURE",
        "--sector-limit-up-count", "6",
    ], "PASS_WITH_WARN"),  # R4+R18豁免→WARN不是REJECT
    ("R4拦截(非主线电力)", [
        "--code", "600236", "--name", "桂冠电力",
        "--source", "mainline_radar", "--price", "10.50",
        "--prev-close", "10.00", "--holdings", "10",
        "--total-cash", "1000000", "--amount", "50000",
        "--pct-60d", "0.20", "--close", "10.45",
        "--high", "10.60", "--low", "10.30",
        "--market-env", "EXTREME_STRUCTURE",
        "--sector-limit-up-count", "2",
    ], "REJECT"),  # R4拒绝（板块涨停仅2只，不满足R18豁免条件）
    ("R6→REJECT(收在高点)", [
        "--code", "300404", "--name", "博济医药",
        "--source", "mainline_radar", "--price", "14.45",
        "--prev-close", "14.00", "--holdings", "10",
        "--total-cash", "1000000", "--amount", "50000",
        "--pct-60d", "0.30", "--close", "14.45",
        "--high", "14.45", "--low", "14.00",
        "--sector-limit-up-count", "5",
    ], "REJECT"),  # R6收在高点=REJECT硬拦截（十年回测1706万行CORRECT，2026-08-17同步预期）
    ("R21→WARN(复牌风险)", [
        "--code", "300862", "--name", "蓝盾光电",
        "--source", "mainline_radar", "--price", "29.00",
        "--prev-close", "28.00", "--holdings", "10",
        "--total-cash", "1000000", "--amount", "50000",
        "--halt-resume", "--post-halt-boards", "4",
        "--post-halt-turnover", "0.08",
    ], "PASS_WITH_WARN"),  # R21复牌=WARN（8/14复核 REJECT→WARN）；价格29≤30铁律（2026-08-17修正参数）
    ("R18阈值50→30早触发)", [
        "--code", "002437", "--name", "誉衡药业",
        "--source", "mainline_radar", "--price", "4.25",
        "--prev-close", "4.10", "--holdings", "10",
        "--total-cash", "1000000", "--amount", "50000",
        "--pct-60d", "0.30", "--close", "4.20",
        "--high", "4.30", "--low", "4.10",
        "--limit-up-count", "35",
        "--advance-ratio", "0.22",
        "--sector-limit-up-count", "3",
    ], "REJECT"),  # R18: 涨停35≥30 + 上涨22%<25%
]

all_pass = True
for name, args, expect in tests:
    cmd = ["python", "D:/zcodesj/pre_trade_check_v2.py", "--no-log"] + args
    # A5 铁律：Windows subprocess 显式 UTF-8（否则中文输出 GBK 解码失败 → JSON解析假失败）
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                       encoding="utf-8", errors="replace")
    try:
        data = json.loads(r.stdout)
    except:
        print(f"❌ {name}: JSON解析失败")
        all_pass = False
        continue
    verdict = data["verdict"]
    ok = verdict.startswith(expect.split("_")[0] if "_" in expect else expect)
    status = "✅" if ok else "❌"
    if not ok:
        all_pass = False
    rejects = " | ".join(data.get("rejects", [])[:2])
    warns = " | ".join(data.get("warnings", [])[:2])
    print(f"  {status} {name}: {verdict} (期望{expect})")
    if not ok:
        print(f"      拒绝: {rejects[:80] if rejects else '(无)'}")
        print(f"      警告: {warns[:80] if warns else '(无)'}")

print(f"\n{'='*50}")
if all_pass:
    print("✅ 全部优化验证通过")
else:
    print("⚠️ 存在优化失败，需检查")