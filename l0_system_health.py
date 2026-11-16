#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
l0_system_health.py（在线文件, 20261011描述勘误）
本文件即 health_check_daily.py run_gate_smoke 实际调用的在线闸门冒烟（10 项 = 原 8 项 +
A6 纪律合规巡检 2 项）。旧头部"待部署副本/原文件"表述系 9 月部署期残留, 部署早已完成,
描述失真已按 20261011 批5尾修正。

新增（A6，接入 discipline_stats / naked_trade_audit 口径）：
  discipline_A6  = 纪律违规每日统计（discipline_stats.py，退出码 1=有 ERROR 级违规）
  naked_audit_S1 = 盘后裸奔对账（naked_trade_audit.py audit，退出码 1=存在裸奔单）

部署方式（二选一）：
  1) 用本文件覆盖 D:/zcodesj/l0_system_health.py（先备份原文件）；
  2) 或仅把下方两行 CHECKS 条目追加到原文件的 CHECKS 列表末尾。

铁律：纪律检查退出码 0=合规 / 1=违规（ERROR） / 2=脚本异常，与"无闸门不下单"审计口径一致。
"""
import json, os, subprocess, sys
from datetime import datetime

CHECKS = [
    ("S-4_time_guard", ["check-buy"], 2,
     [sys.executable, "D:/zcodesj/trade_time_guard.py"]),
    ("gate_R1-R18", [], 0,
     [sys.executable, "D:/zcodesj/pre_trade_check_v2.py",
      # 9/24: fixture 002582→601398(工商银行,超级大盘换手率恒底部分位,结构性不进R33清单)
      # +price 10→8.1(贴MA20,防R31偏离度): R33活跃度负面清单当日上线, 002582恰在9/22信号日
      # 458只禁买名单 → 冒烟测闸门存活非风格状态(与rs5/ddgate先例同语义), 换结构性免疫票
      # 优于加特批旗标(不动R33闸门语义)
      "--code", "601398", "--name", "test",
      "--price", "8.1", "--source", "manual_confirmed",
      "--skip-time-window", "--no-log",
      # 9/5: R-S5不追高闸上线后, 002582 mom20 随行情可能>+2%(会话源REJECT),
      # 冒烟测的是闸门存活非风格状态 → 特批旗标保证确定性 exit=0
      "--owner-approve-rs5",
      # 11/4: DD_GATE已退役(主人令), 无需特批旗标
      # 10/8: R38期望闸(LOW_VOL×gauge组级负期望→REJECT)上线后, 当前250日窗口四组全负,
      # 弱档下任何可分类票必REJECT → 特批旗标保证冒烟确定性exit=0(与rs5/ddgate同语义);
      # R39落刀闸同理(个股当日深跌随行情可能触发), 同批加入
      # 11/7: R39/P2/R23旗标/R36旗标已随并行窗口判定块清理删除 → l0冒烟同步摘除
      # (argparse未知参数exit2曾致gate冒烟假失败; R38本体仍在故r38旗标保留)
      "--owner-approve-r38",
      # 10/15: R37情绪档弱档日(涨停<40)REJECT exit2 → 同rs5/ddgate/r38/r39先例特批保证
      # 冒烟确定性exit=0(闸门存活语义; 真实下单不带旗标时R37照拒, 弱档禁买闸本体不变)
      "--owner-approve-r37",
      # 10/28: R30a行业集中度(E79)依赖当日主人真实买入分布——夹具工商银行(银行)当日主人已买
      # 8只银行股≥上限4只触发真保护REJECT(exit=2) → 特批旗标保证冒烟确定性exit=0(与
      # rs5/ddgate/r38/r39/r37/P2同语义, 测闸门存活非当日买入状态; 生产买入R30a照拒不变)
      "--owner-approve-r30a",
      # 11/3: R23持仓加仓闸(fail-closed)——夹具工商银行已进入主人真实持仓(233只),
      # 无加仓声明被R23正确拒绝(exit=2) → 按rs5/ddgate/r38/r39/r37/P2/r30a既定语义加
      # allow-add-position(闸门原生参数, help: 允许对已持仓标的加仓R13/R23豁免),
      # 冒烟测闸门存活非当日持仓状态; 生产买入不带旗标时R23照拒不变
      "--allow-add-position"]),
    # M-5: 002582 不在作战清单+无信号源 → 正确行为=BLOCKED(exit 2);预期2=脚本存活即可
    ("impulse_M-5", [], 2,
     [sys.executable, "D:/zcodesj/m5_impulse_blocker.py",
      "--action", "buy", "--code", "002582",
      "--skip-cooldown", "--skip-questions"]),
    ("emotion_M-6", [], 0,
     [sys.executable, "D:/zcodesj/attribution/emotion_index.py",
      # 日期为静态冒烟锚点(只验脚本存活, 非新鲜度检查); 20261011批5尾由20260813前滚
      "--date", "20261009", "--json"]),
    ("alignment_M-1", [], 1,
     [sys.executable, "D:/zcodesj/attribution/m1_alignment_check.py",
      "--short"]),
    ("attribution_feedback_M-2", [], 0,
     [sys.executable, "D:/zcodesj/attribution_feedback_filter.py",
      "--report"]),
    ("industry_chain_L-5", [], 0,
     [sys.executable, "D:/zcodesj/attribution/l5_industry_chains.py",
      "--match", "碳酸锂", "--json"]),
    ("orderflow_L-6", [], 0,
     [sys.executable, "D:/zcodesj/attribution/l6_orderflow_analyzer.py",
      "--code", "300750", "--pattern", "distribution", "--json"]),
    # ---- A6 纪律接入（新增）----
    ("discipline_A6", [], 0,
     [sys.executable, "D:/zcodesj/discipline_stats.py"]),
    ("naked_audit_S1", [], 0,
     [sys.executable, "D:/zcodesj/naked_trade_audit.py", "audit"]),
]

def main():
    passed = 0
    failed = 0
    skipped = 0
    results = []
    print("=" * 60)
    print(f"  系统全链路健康检查（含 A6 纪律）| {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    for name, _, expected, cmd in CHECKS:
        cmd_str = cmd[0] if len(cmd) == 1 else cmd[0].split("/")[-1] + " +args"
        # 20261011批4: 原判定 len(cmd)==1 才查存在性——现有检查项全是 [sys.executable, script]
        # 多元素形态 → SKIP 恒死代码; 且 SKIP 不计入 total, 全跳过时会打 ALL PASS 假绿。
        # 改为探测命令里的 .py 脚本存在性, SKIP 计入 total 单列。
        _script = next((c for c in reversed(cmd) if str(c).endswith(".py")), None)
        if _script and not os.path.exists(_script):
            print(f"  SKIP {name}: 文件不存在({_script})")
            results.append({"name": name, "status": "SKIP"})
            skipped += 1
            continue
        try:
            # A5 铁律：Windows subprocess 显式 UTF-8（否则中文输出 GBK 解码失败 → stdout/stderr None 假失败）
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                               encoding="utf-8", errors="replace")
            ok = (r.returncode == expected)
            tag = "PASS" if ok else "FAIL"
            print(f"  {tag} {name}: exit={r.returncode} (exp={expected})")
            if not ok:
                detail = (r.stderr.strip()[:120] or r.stdout.strip()[:120])
                print(f"     {detail}")
            if ok:
                passed += 1
            else:
                failed += 1
            results.append({"name": name, "status": tag, "exit": r.returncode})
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            failed += 1
            results.append({"name": name, "status": "ERROR", "error": str(e)})

    total = passed + failed + skipped
    print("=" * 60)
    print(f"  PASS {passed} / FAIL {failed} / SKIP {skipped} / TOTAL {total}")
    if failed == 0 and skipped == 0:
        print("  ALL PASS - 系统三层链路全部正常")
    elif failed == 0:
        print(f"  ⚠️ 无失败但有 {skipped} 项 SKIP——全部跳过不再打 ALL PASS 假绿(20261011批4)")
    print("=" * 60)

    report = {"timestamp": datetime.now().isoformat(), "passed": passed,
              "failed": failed, "skipped": skipped, "total": total, "details": results}
    path = os.path.join("D:/zcodesj/reports",
                        f"system_health_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    os.makedirs("D:/zcodesj/reports", exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report: {path}")
    return 1 if failed > 0 else 0

if __name__ == "__main__":
    raise SystemExit(main())
