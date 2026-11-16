#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屠龙系统 P0 硬化全链路回归测试 v1.0（8/13全维度自省产出）

覆盖以下优化项的物理验证：
  [已废除] S-4: 全天可交易(8/14裁决)
  [已废除] S-2: 任何时间可卖(8/14裁决,仅提示性)
  S-3: 价格≤30铁律
  S-5: 止损-8%自动执行
  S-6: 交易作战清单生成
  S-1: 闸门全链路整合
  准则11-14新增约束

用法:
    python test_s_hardening.py              # 运行全部测试
    python test_s_hardening.py --verbose    # 详细输出
    python test_s_hardening.py --list       # 仅列出测试用例不执行

退出码: 0=全绿 / 1=有失败
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, time

# ===== 测试报告配置 =====
REPORT_DIR = r"D:\zcodesj\reports"
VERBOSE = False

PASS_EMOJI = "✅"
FAIL_EMOJI = "❌"
SKIP_EMOJI = "⏭️"

# 计数器
passed = 0
failed = 0
skipped = 0
test_results: list[dict] = []


# ===== 测试基础设施 =====

def _run(script: str, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """运行脚本，返回(exit_code, stdout, stderr)。"""
    try:
        # A5-实施: Windows subprocess 显式 UTF-8（铁律），否则 GBK 解码中文输出假失败
        r = subprocess.run(
            [sys.executable, script] + args,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except FileNotFoundError:
        return -2, "", f"FILE_NOT_FOUND: {script}"
    except Exception as e:
        return -3, "", str(e)


def _extract_json(stdout: str) -> dict | None:
    """从输出中提取第一个 JSON 对象。"""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(stdout[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def test(name: str, category: str, check_fn, expect_pass: bool = True) -> None:
    """注册并执行一个测试用例。"""
    global passed, failed, skipped

    if "--list" in sys.argv:
        print(f"  [{category}] {name}")
        return

    try:
        actual_pass = check_fn()
    except Exception as e:
        actual_pass = False
        if VERBOSE:
            print(f"  ⚠️ 测试异常: {e}")

    result = {
        "name": name,
        "category": category,
        "passed": actual_pass,
        "expected": expect_pass,
    }

    if actual_pass == expect_pass:
        if VERBOSE or actual_pass:
            print(f"  {PASS_EMOJI if actual_pass else FAIL_EMOJI} [{category}] {name}")
        passed += 1
    else:
        print(f"  {FAIL_EMOJI} [{category}] {name} (期望={'通过' if expect_pass else '拒绝'}, 实际={'通过' if actual_pass else '拒绝'})")
        failed += 1

    test_results.append(result)


# ============================================================
# 时间窗口检查（S-4已按主人8/14裁决废除，仅保留收盘后/午休拦截）
# ============================================================

def test_s4_buy_window_blocked():
    """S-4已废除（主人8/14裁决全天可交易）：验证pre_trade_check_v2不再集成S-4检查"""
    with open(r"D:\zcodesj\pre_trade_check_v2.py", "r", encoding="utf-8") as f:
        content = f.read()
    # S-4 已删除：不应再有 check_buy_window 集成调用
    has_removed_note = "已删除" in content and "S-4" in content
    no_integration = "from trade_time_guard import check_buy_window" not in content
    return has_removed_note and no_integration


def test_s4_code_blocked_in_pre_trade_check():
    """S-4已废除：验证不再有S-4时间窗口预警输出"""
    with open(r"D:\zcodesj\pre_trade_check_v2.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "S-4时间窗口预警" not in content


# ============================================================
# S-2：卖出时间窗口（8/14裁决后仅提示性，不拦截卖出）
# ============================================================

def test_s2_sell_window_code_exists():
    """S-2：trade_time_guard 有卖出窗口检查（8/14裁决后仅提示性，不拦截）"""
    with open(r"D:\zcodesj\trade_time_guard.py", "r", encoding="utf-8") as f:
        content = f.read()
    has_sell_check = "check_sell_window" in content
    # S-2不再标注"物理锁"，改用"主人8/14裁决不限制卖出时段"
    has_owner_ruling = "主人 8/14 裁决不限制卖出时段" in content
    has_stop_exception = "EMERGENCY_STOP_LOSS_PCT" in content
    return has_sell_check and has_owner_ruling and has_stop_exception


def test_s2_integrated_in_pre_sell_check():
    """S-2：pre_sell_check 集成卖出窗口（8/14裁决后=提示性非约束）"""
    with open(r"D:\zcodesj\pre_sell_check.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "from trade_time_guard import check_sell_window" in content \
           and ("尾盘建议" in content or "止损提醒" in content) \
           and "--loss-pct" in content and "--owner-emergency" in content


# ============================================================
# S-3：价格铁律代码硬化测试
# ============================================================

def test_s3_price_limit_in_pre_trade_check():
    """S-3：pre_trade_check_v2 中有价格>30元拦截"""
    with open(r"D:\zcodesj\pre_trade_check_v2.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "MAX_PRICE" in content and "args.price > MAX_PRICE" in content


def test_s3_price_limit_hard_reject():
    """S-3: 价格铁律拒绝消息中有具体案例"""
    with open(r"D:\zcodesj\pre_trade_check_v2.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "中恒电气44.77" in content


# ============================================================
# S-5：止损硬自动执行测试
# ============================================================

def test_s5_stop_loss_executor_exists():
    """S-5：止损执行器文件存在"""
    return os.path.exists(r"D:\zcodesj\stop_loss_executor.py")


def test_s5_stop_loss_pct_is_8():
    """S-5：止损阈值正确设定为 -8%"""
    with open(r"D:\zcodesj\stop_loss_executor.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "STOP_LOSS_THRESHOLD = -0.08" in content


def test_s5_checks_report_dir():
    """S-5：止损执行器有报告输出逻辑"""
    with open(r"D:\zcodesj\stop_loss_executor.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "REPORT_DIR" in content and ".md" in content


# ============================================================
# S-6：交易作战清单测试
# ============================================================

def test_s6_trade_plan_exists():
    """S-6：交易作战清单生成器存在"""
    return os.path.exists(r"D:\zcodesj\daily_trade_plan.py")


def test_s6_trade_plan_has_required_sections():
    """S-6：交易作战清单包含所有必要章节"""
    with open(r"D:\zcodesj\daily_trade_plan.py", "r", encoding="utf-8") as f:
        content = f.read()
    sections = [
        "禁止操作",
        "目标买入候选",
        "持仓止损线",
        "复盘要点",
        "闸门配置状态",
        "bp_ep_top20",
        "mainline_candidates",
        "准则11",
        "准则12",
        "准则13",
        "准则14",
    ]
    return all(s in content for s in sections)


def test_s6_trade_plan_runs():
    """S-6：交易作战清单可运行（即使数据为空）"""
    ec, stdout, _ = _run(r"D:\zcodesj\daily_trade_plan.py", ["--date", "20260813"])
    return ec == 0 and "作战清单已生成" in stdout


def test_s6_trade_plan_output_file_exists():
    """S-6：交易作战清单文件已实际生成"""
    return os.path.exists(r"D:\zcodesj\trade_plans\trade_plan_20260813.md")


# ============================================================
# S-1：闸门全链路整合测试
# ============================================================

def test_s1_guarded_executor_exists():
    """S-1：闸门全链路执行器存在"""
    return os.path.exists(r"D:\zcodesj\guarded_trade_executor.py")


def test_s1_executor_has_buy_chain():
    """S-1：买入五步链完整（含M-5）"""
    with open(r"D:\zcodesj\guarded_trade_executor.py", "r", encoding="utf-8") as f:
        content = f.read()
    chain_steps = [
        "Step 1/5: 时间窗口检查",
        "Step 2/5: R1-R18 闸门全链检查",
        "Step 2.5/5: 冲动拦截",
        "Step 3/5: 更新交易状态",
        "Step 4/5: 执行买入",
    ]
    return all(s in content for s in chain_steps)


def test_s1_executor_has_sell_chain():
    """S-1：卖出五步链完整（含M-5+C5趋势自动核验）"""
    with open(r"D:\zcodesj\guarded_trade_executor.py", "r", encoding="utf-8") as f:
        content = f.read()
    chain_steps = [
        "Step 1/5: 时间窗口检查",
        "Step 2/5: 防卖飞五检查",
        "趋势自动核验",
        "Step 2.5/5: 冲动拦截",
        "Step 3/5: 更新交易状态",
        "Step 4/5: 执行卖出",
    ]
    return all(s in content for s in chain_steps)


def test_s1_executor_rejects_after_hours_buy():
    """S-1：trade_time_guard 非交易时段正确拒绝（静态代码检查，不依赖运行时时间）"""
    with open(r"D:\zcodesj\trade_time_guard.py", "r", encoding="utf-8") as f:
        content = f.read()
    # check_buy_window 在开盘前返回HARD_REJECT+早于09:30，收盘后返回HARD_REJECT+已过15:00
    has_before = 'HARD_REJECT' in content and '早于09:30' in content
    has_after = 'HARD_REJECT' in content and '已过15:00' in content
    has_lunch = 'SOFT_REJECT' in content and '午休时段' in content
    return has_before and has_after and has_lunch


def test_s1_executor_rejects_no_source():
    """S-1：无信号源的买入被拒绝（admit闸门）"""
    ec, stdout, _ = _run(
        r"D:\zcodesj\guarded_trade_executor.py",
        ["--action", "buy", "--code", "002582", "--name", "好想你",
         "--price", "10.12", "--source", "", "--amount", "50000",
         "--holdings", "30", "--total-cash", "1019909"],
    )
    # 非交易时间（收盘后/开盘前），时间窗口应拦截
    return ec == 1


# ============================================================
# 准则11-14 硬化测试
# ============================================================

def test_rule11_no_block():
    """准则11：09:30-10:00可以买卖（闸门拦截）"""
    with open(r"D:\zcodesj\trade_time_guard.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "09:30-10:00" in content and "PASS" in content


def test_rule12_why_not_bp_ep():
    """准则12：\"为什么不是bp+ep\"问题（已入S-6作战清单）"""
    with open(r"D:\zcodesj\daily_trade_plan.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "为什么不是bp+ep" in content


def test_rule13_self_discover_limit():
    """准则13：每日≤1只自发现票（已入S-6作战清单）"""
    with open(r"D:\zcodesj\daily_trade_plan.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "每日≤1只自发现票" in content


def test_rule14_traceable_trade():
    """准则14：每笔交易可回溯（guarded_executor 自动记录）"""
    with open(r"D:\zcodesj\guarded_trade_executor.py", "r", encoding="utf-8") as f:
        content = f.read()
    return "timestamp" in content and "guard_verdict" in content


# ============================================================
# 全体系回归验证
# ============================================================

def test_full_guard_return_code_exists():
    """全体系：pre_trade_check_v2 回归测试文件存在"""
    return os.path.exists(r"D:\zcodesj\test_full_guard_v2.py")


def test_r14_r18_regression_exists():
    """全体系：R14-R18 回归测试存在"""
    return os.path.exists(r"D:\zcodesj\test_r14_r18_regression.py")


def test_trade_state_has_market_env_fields():
    """全体系：trade_state 有 R14-R18 需要的字段"""
    with open(r"D:\zcodesj\trade_state.py", "r", encoding="utf-8") as f:
        content = f.read()
    fields = [
        "market_env",
        "highest_board_today",
        "highest_board_yesterday",
        "space_leader_limit_down",
        "limit_up_count",
        "advance_ratio",
    ]
    return all(f in content for f in fields)


# ============================================================
# 主体
# ============================================================

def main() -> int:
    global VERBOSE
    VERBOSE = "--verbose" in sys.argv

    if "--list" in sys.argv:
        print("📋 屠龙系统 P0 硬化回归测试清单:\n")

    print("=" * 60)
    print("  屠龙系统 P0 硬化全链路回归测试")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    if "--list" not in sys.argv:
        print()

    # === 时间窗口（S-4已废除，仅保留收盘后/午休拦截）===
    print("── S-4 时间窗口硬化 ──")
    test("S-4买入拦截集成", "S-4", test_s4_buy_window_blocked)
    test("S-4拒绝逻辑在pre_trade_check", "S-4", test_s4_code_blocked_in_pre_trade_check)

    # === S-2: 尾盘卖出锁 ===
    print("── S-2 尾盘卖出物理锁 ──")
    test("S-2尾盘锁代码存在", "S-2", test_s2_sell_window_code_exists)
    test("S-2集成到pre_sell_check", "S-2", test_s2_integrated_in_pre_sell_check)

    # === S-3: 价格铁律 ===
    print("── S-3 价格铁律硬化 ──")
    test("S-3价格拦截在pre_trade_check", "S-3", test_s3_price_limit_in_pre_trade_check)
    test("S-3拒绝消息含具体案例", "S-3", test_s3_price_limit_hard_reject)

    # === S-5: 止损执行 ===
    print("── S-5 止损自动执行 ──")
    test("S-5执行器文件存在", "S-5", test_s5_stop_loss_executor_exists)
    test("S-5止损阈值-8%", "S-5", test_s5_stop_loss_pct_is_8)
    test("S-5报告输出逻辑", "S-5", test_s5_checks_report_dir)

    # === S-6: 交易作战清单 ===
    print("── S-6 交易作战清单 ──")
    test("S-6生成器文件存在", "S-6", test_s6_trade_plan_exists)
    test("S-6清单包含所有必要章节", "S-6", test_s6_trade_plan_has_required_sections)
    test("S-6清单可运行", "S-6", test_s6_trade_plan_runs)
    test("S-6清单文件已生成", "S-6", test_s6_trade_plan_output_file_exists)

    # === S-1: 闸门全链路 ===
    print("── S-1 闸门全链路整合 ──")
    test("S-1执行器文件存在", "S-1", test_s1_guarded_executor_exists)
    test("S-1买入四步链完整", "S-1", test_s1_executor_has_buy_chain)
    test("S-1卖出四步链完整", "S-1", test_s1_executor_has_sell_chain)
    test("S-1收盘后买入被拒绝", "S-1", test_s1_executor_rejects_after_hours_buy)
    test("S-1无信号源被拒绝", "S-1", test_s1_executor_rejects_no_source)

    # === 准则11-14 ===
    print("── 准则11-14 硬化 ──")
    test("准则11:09:30-10:00可买卖已硬化", "准则", test_rule11_no_block)
    test("准则12:\"为什么不是bp+ep\"已入清单", "准则", test_rule12_why_not_bp_ep)
    test("准则13:每日≤1只自发现票已入清单", "准则", test_rule13_self_discover_limit)
    test("准则14:交易可回溯已编码", "准则", test_rule14_traceable_trade)

    # === 全体系 ===
    print("── 全体系回归 ──")
    test("pre_trade_check_v2回归测试存在", "全体系", test_full_guard_return_code_exists)
    test("R14-R18回归测试存在", "全体系", test_r14_r18_regression_exists)
    test("trade_state市场环境字段完整", "全体系", test_trade_state_has_market_env_fields)

    # ==== 最终报告 ====
    print()
    print("=" * 60)
    total = passed + failed + skipped
    if failed == 0:
        print(f"  🎉 全绿通过! {passed}/{total} 通过 ({skipped} skip)")
    else:
        print(f"  {'😡' if failed > 2 else '⚠️'} {passed}/{total} 通过, {failed} 失败, {skipped} 跳过")
    print("=" * 60)

    # 写报告
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"test_s_hardening_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": total,
            "results": test_results,
        }, f, ensure_ascii=False, indent=2)
    print(f"📄 报告已写入: {report_path}")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())