#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ab_check_20261024.py — 2026双杀验证·事故自愈 A/B 复检一键件(只读)
==================================================================
用途: 10/23 全量测试(reports/full_test_battery_20261023.md)定性 health_check ERROR
  为 10/22 22:56 补跑波事故残留, 预测 10/23 晚自然槽(22:30/22:35/23:20)自愈。
  本件=该预测的 A/B 复检(原拟自动化被全局 20 任务上限拒, 改为一键脚本明晨手动执行)。
用法: python analysis/ab_check_20261024.py   (建议 10/24 09:30 后运行)
判定: PASS=自愈确认(两判据全过) / FAIL=未自愈升级立案(停下等主人令, 禁自行重跑任务)
零交易权只读; 唯一写动作=复检报告落盘。
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "reports", "full_test_battery_20261023_ab_check.md")
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
BASELINE_LIMIT_DOWN_MAX = "20261021"   # 10/23 基线
INCIDENT_SIGNATURES = ["AddPositionWatch_1400", "LimitDownBackfill_2235",
                       "LowConsolSignal_2230", "ActExclShadow_2320"]


def main():
    # 判据1: health_check schtasks_failures 行——事故签名任务应被新记录顶替清除
    r = subprocess.run([sys.executable, os.path.join(ROOT, "health_check_daily.py")],
                       capture_output=True, text=True, timeout=540)
    txt = r.stdout + r.stderr
    m = re.search(r"schtasks 失败[^\n]*", txt)
    fail_line = m.group(0) if m else "(未找到 schtasks_failures 行)"
    residue = [sig for sig in INCIDENT_SIGNATURES if sig in fail_line]
    concl = re.search(r"\*\*结论[^\n]*|结论:\s*\S+", txt)
    verdict1 = {
        "fail_line": fail_line[:300],
        "incident_signature_residue": residue,
        "health_conclusion": concl.group(0) if concl else "(未提取)",
        "pass": len(residue) == 0,
    }

    # 判据2: limit_down 自愈(区间语义 MAX+1→qfq MAX)
    con = sqlite3.connect(ATTR_DB)
    mx = con.execute("SELECT MAX(trade_date) FROM limit_down_risks").fetchone()[0]
    con.close()
    verdict2 = {"limit_down_max": mx, "baseline": BASELINE_LIMIT_DOWN_MAX,
                "pass": bool(mx and mx >= "20261022")}

    overall = "PASS=自愈确认" if (verdict1["pass"] and verdict2["pass"]) else "FAIL=未自愈升级立案(停下等主人令, 禁自行重跑任务)"

    md = ["# A/B 复检报告(预测: 10/23晚自然槽自愈)", "",
          f"- 运行时刻: {datetime.now().isoformat(timespec='seconds')}",
          f"- 判据1 事故签名清除: {'✅' if verdict1['pass'] else '❌ 残留=' + ','.join(residue)}",
          f"  - health schtasks行: {verdict1['fail_line'][:200]}",
          f"- 判据2 limit_down自愈: {'✅' if verdict2['pass'] else '❌'} (MAX={verdict2['limit_down_max']}, 基线={BASELINE_LIMIT_DOWN_MAX})",
          f"- health结论行: {verdict1['health_conclusion']}",
          f"- **总判定: {overall}**",
          "- 若 FAIL: 只呈报, 禁自行重跑任务/改文件, 等主人令(协议边界)"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("\n".join(md))
    print(f"\n[written] {OUT}")
    sys.exit(0 if (verdict1["pass"] and verdict2["pass"]) else 1)


if __name__ == "__main__":
    main()
