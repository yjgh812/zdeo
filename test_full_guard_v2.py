#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_trade_check_v2.py 全量测试套件 v1.0

覆盖范围：
  A. 基础铁律（板块排除/价格上限/ST排除；持仓上限 2026-10-26 主人令废除→改为不拦截回归）
  B. 原有规则 R1-R13（全部回归）
  C. 新增规则 R14-R18（8/13复盘落地）
  D. 组合场景（多规则叠加）
  E. trade_state.py 功能测试
  F. 边界条件与误伤测试

测试前自动备份并清空 trade_state，测试后恢复。
退出码: 0=全部通过 / 1=有失败

v1.1 (2026-08-16 A5-01/A5-02, qa-verifier):
  - UTF-8 自愈: 强制 stdout/stderr 为 UTF-8，避免 Windows GBK 管道下 emoji 打印
    UnicodeEncodeError 假失败（屠龙坑#1）。
  - --no-log: 闸门调用统一带 --no-log，杜绝测试行污染 guard_precheck_log
    （pre_trade_check_v2 的 log_precheck 无条件落库）。
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

# ===== A5-01 UTF-8 编码自愈（Windows GBK 管道防假失败）=====
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

STATE_DIR = r"D:\zcodesj\trade_state"
today = datetime.now().strftime("%Y%m%d")
state_file = os.path.join(STATE_DIR, f"trade_state_{today}.json")
backup_file = state_file + ".fulltest_bak"

# ===== A5-02 测试调用跳过 guard_precheck_log 落库 =====
GUARD = ["python", "D:/zcodesj/pre_trade_check_v2.py", "--no-log"]
STATE = ["python", "D:/zcodesj/trade_state.py"]

# 9/22: 基线执行价对齐最新白名单 002437 名单价, 防 R27 >3% 偏离闸对静态价误判 REJECT
def _align_base_price(default_px: float) -> float:
    try:
        import glob as _g
        _wls = sorted(_g.glob(r"D:\zcodesj\reports\buy_whitelist_*.csv"))
        if _wls:
            import csv as _c2
            with open(_wls[-1], encoding="utf-8-sig") as _fh:
                for _row in _c2.DictReader(_fh):
                    if str(_row.get("code") or "").strip().zfill(6) == "002437":
                        try:
                            _lp = float(_row.get("price") or 0)
                            if 1 < _lp <= 30:
                                return _lp
                        except (TypeError, ValueError):
                            pass
                        break
    except Exception:
        pass
    return default_px

BASE_PX = _align_base_price(4.25)

passed = 0
failed = 0
results = []


def guard(extra: list) -> dict:
    """运行闸门校验，返回解析后的 dict。失败自动重试1次。"""
    base = [
        "--code", "002437", "--name", "誉衡药业",
        "--price", f"{BASE_PX:.2f}", "--prev-close", f"{BASE_PX * 0.97:.2f}",
        "--source", "mainline_radar",
        "--holdings", "10", "--total-cash", "1000000",
        "--amount", "20000",
        "--pct-60d", "0.30",
        "--close", f"{BASE_PX * 0.99:.2f}", "--high", f"{BASE_PX * 1.02:.2f}",
        "--low", f"{BASE_PX * 0.97:.2f}",
    ]
    cmd = GUARD + base + extra
    for attempt in range(2):
        # A5-实施: Windows subprocess 显式 UTF-8（铁律），否则 GBK 解码中文输出假失败
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace")
        stdout = r.stdout.strip()
        if stdout:
            try:
                return json.loads(stdout)
            except Exception:
                if attempt == 0:
                    continue
        if attempt == 1:
            return {"verdict": "PARSE_ERROR", "rejects": [stdout[:300] or f"stderr={r.stderr[:300]}"],
                    "warnings": [], "_exit": r.returncode}
    return {"verdict": "PARSE_ERROR", "rejects": ["unknown"], "warnings": []}


def run(name: str, extra: list, expect: str, keyword: str | None = None, desc: str = "",
        expect_absent: str | None = None):
    """运行单个测试用例。expect 可以是 'REJECT'/'PASS'/'PASS_WITH_WARN'。
    expect_absent: 退役断言(9/22对齐r14_r18机制)——该关键词不得出现在 rejects+warnings。"""
    global passed, failed
    data = guard(extra)
    verdict = data.get("verdict", "")
    rejects = data.get("rejects", [])
    warns = data.get("warnings", [])
    combined = " | ".join(rejects + warns)

    ok = verdict.startswith(expect)
    if keyword:
        ok = ok and keyword in combined
    if expect_absent:  # 退役断言: 9/10已下线闸的关键词不得再出现
        ok = ok and expect_absent not in combined

    status = "✅" if ok else "❌"
    if ok:
        passed += 1
    else:
        failed += 1

    detail = ""
    if keyword:
        detail = f" [{('Y' if keyword in combined else 'N')}:{keyword[:20]}]"
    line = f"  {status} {name}: {verdict}≠{expect}{detail}"
    if not ok:
        line += f"\n     REJECTS: {rejects}"
        if warns:
            line += f"\n     WARNS: {warns}"
    print(line)
    results.append((name, ok))


def run_state(args: list, expect_json: bool = True) -> dict | str:
    """运行 trade_state.py 命令。"""
    cmd = STATE + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                       encoding="utf-8", errors="replace")
    if expect_json:
        try:
            return json.loads(r.stdout)
        except Exception:
            return {}
    return r.stdout


# ================================================================
# 准备：备份并清空 trade_state
# ================================================================
print("=" * 70)
print("  pre_trade_check_v2.py 全量测试套件")
print("=" * 70)

print("\n[准备] 备份并清空 trade_state...")
if os.path.exists(state_file):
    shutil.copy2(state_file, backup_file)
    print(f"  已备份 → {backup_file}")
clean = {
    "date": today, "buy_count": 0, "bought_codes": [],
    "sell_count": 0, "pending_orders": {}, "sector_breadth": 0.35,
    "market_env": None, "highest_board_today": None,
    "highest_board_yesterday": None, "space_leader_limit_down": False,
    "limit_up_count": None, "advance_ratio": None,
}
os.makedirs(STATE_DIR, exist_ok=True)
with open(state_file, "w", encoding="utf-8") as f:
    json.dump(clean, f, ensure_ascii=False, indent=2)
print("  已写入干净状态")


# ================================================================
# A. 基础铁律
# ================================================================
print("\n" + "─" * 70)
print("  A. 基础铁律")
print("─" * 70)

run("A1 科创板排除", ["--skip-time-window", "--code", "688001", "--name", "华兴源创"], "REJECT", "科创板")
run("A2 北交所排除", ["--skip-time-window", "--code", "920001", "--name", "新三板股"], "REJECT", "北交所")
run("A3 ST排除", ["--skip-time-window", "--is-st"], "REJECT", "ST")
run("A4 价格超上限", ["--skip-time-window", "--price", "50.0"], "REJECT", "股价")
run("A5 持仓数不设限(2026-10-26主人令废除≤50上限)", ["--skip-time-window", "--holdings", "5000"], "PASS")
run("A6 安全基线全通过", ["--skip-time-window", ], "PASS")


# ================================================================
# B. 原有规则 R1-R13 回归
# ================================================================
print("\n" + "─" * 70)
print("  B. 原有规则 R1-R13 回归测试")
print("─" * 70)

# R1 追高
run("B1 R1追高(已退役9/10)", ["--skip-time-window", "--pct-60d", "0.75", "--late-stage"], "PASS_WITH_WARN", expect_absent="R1追高")
run("B2 R1不触发(60日>70%但非主升)", ["--skip-time-window", "--pct-60d", "0.75"], "PASS")

# R3 批量买
# 先写入3只已买
run_state(["--record-buy", "000001"])
run_state(["--record-buy", "000002"])
run_state(["--record-buy", "000003"])
run("B3 R3批量买上限(已买3只)", ["--skip-time-window", ], "PASS")  # R3无硬上限，仅4只以上提示
# 清空重来
with open(state_file, "w", encoding="utf-8") as f:
    json.dump(clean, f, ensure_ascii=False, indent=2)

# R4 防御蓝筹（8/14主人裁决：行业不该拦截，已移除）
run("B4 R4防御蓝筹(银行)", ["--skip-time-window", "--code", "601398", "--name", "工商银行"], "PASS")
run("B5 R4防御蓝筹(煤炭)", ["--skip-time-window", "--name", "中国煤炭"], "PASS")

# R5 振幅（8/14复核：十年回测WRONG，降WARN）
run("B6 R5振幅禁令(40日>50%)", ["--skip-time-window", "--amplitude-40d", "0.55"], "PASS_WITH_WARN", "R5振幅")

# R6 收在高点
run("B7 R6收在高点(9/10降级WARN)", ["--skip-time-window", "--close", "4.29", "--high", "4.30", "--low", "4.10"], "PASS_WITH_WARN", "R6收在高点")
run("B8 R6低吸豁免", ["--skip-time-window", "--close", "4.29", "--high", "4.30", "--low", "4.10", "--intent", "low_suck"], "PASS")

# R7 超买/涨停价追入
run("B9 R7涨停价追入(涨幅≥95%)", ["--skip-time-window", "--price", "4.50", "--prev-close", "4.10"], "REJECT", "R7超买")
run("B10 R7高位追入(涨幅≥80%)", ["--skip-time-window", "--price", "4.43", "--prev-close", "4.10"], "REJECT", "R7超买")

# R8 分化日
run("B11 R8分化日(宽度<20%)", ["--skip-time-window", "--sector-breadth", "0.15"], "PASS_WITH_WARN", "R8分化日")

# R13 重复买入
run_state(["--record-buy", "002437"])
run("B12 R13重复买入", ["--skip-time-window", ], "REJECT", "R13重复买入")
# 清空
with open(state_file, "w", encoding="utf-8") as f:
    json.dump(clean, f, ensure_ascii=False, indent=2)

# admit闸门
run("B13 admit未声明source", ["--skip-time-window", "--source", ""], "REJECT", "admit闸门")
run("B14 admit退役源(t1_sealed)", ["--skip-time-window", "--source", "t1_sealed"], "REJECT", "已证伪")
run("B15 admit实验源(dragon_r3)", ["--skip-time-window", "--source", "dragon_r3"], "PASS_WITH_WARN", "experiment")


# ================================================================
# C. 新增规则 R14-R18
# ================================================================
print("\n" + "─" * 70)
print("  C. 新增规则 R14-R18")
print("─" * 70)

# R14 空间龙跌停→禁追连板
run("C1 R14空间龙跌停+3板(已退役9/10)", ["--skip-time-window", "--space-leader-limit-down", "--consecutive-boards", "3"], "PASS_WITH_WARN", expect_absent="R14")
run("C2 R14空间龙跌停+2板(不触发)", ["--skip-time-window", "--space-leader-limit-down", "--consecutive-boards", "2"], "PASS")
run("C3 R14无空间龙跌停+5板(不触发)", ["--skip-time-window", "--consecutive-boards", "5"], "PASS")

# R15 封板质量
run("C4 R15炸板3次(已退役9/10)", ["--skip-time-window", "--seal-break-count", "3"], "PASS_WITH_WARN", expect_absent="R15")
run("C5 R15炸板2次(不触发)", ["--skip-time-window", "--seal-break-count", "2"], "PASS")
run("C6 R15炸板5次(严重)(已退役9/10)", ["--skip-time-window", "--seal-break-count", "5"], "PASS_WITH_WARN", expect_absent="R15")

# R16 高换手HIGH_RISK
run("C7 R16 3板+换手25%(已退役9/10)", ["--skip-time-window", "--consecutive-boards", "3", "--turnover-rate", "0.25"], "PASS_WITH_WARN", expect_absent="R16")
run("C8 R16 2板+换手30%(不触发板数)", ["--skip-time-window", "--consecutive-boards", "2", "--turnover-rate", "0.30"], "PASS")
run("C9 R16 4板+换手15%(不触发换手)", ["--skip-time-window", "--consecutive-boards", "4", "--turnover-rate", "0.15"], "PASS")

# R17 BOARD_COLLAPSE
run("C10 R17 7板→5板退潮(已退役9/10)", ["--skip-time-window", "--highest-board-yesterday", "7", "--highest-board-today", "5"], "PASS_WITH_WARN", expect_absent="R17")
run("C11 R17 5板→5板(不触发)", ["--skip-time-window", "--highest-board-yesterday", "5", "--highest-board-today", "5"], "PASS")
run("C12 R17 8板→4板(严重退潮)(已退役9/10)", ["--skip-time-window", "--highest-board-yesterday", "8", "--highest-board-today", "4"], "PASS_WITH_WARN", expect_absent="R17")

# R18 极端结构行情
run("C13 R18极端+板块涨停3只(不足)(已退役9/10)", ["--skip-time-window", "--market-env", "EXTREME_STRUCTURE", "--limit-up-count", "62",
    "--advance-ratio", "0.20", "--sector-limit-up-count", "3"], "PASS_WITH_WARN", expect_absent="R18")
run("C14 R18极端+板块涨停6只(达标)(已退役9/10)", ["--skip-time-window", "--market-env", "EXTREME_STRUCTURE", "--limit-up-count", "62",
    "--advance-ratio", "0.20", "--sector-limit-up-count", "6"], "PASS_WITH_WARN", expect_absent="R18")
run("C15 R18非极端行情(不触发)", ["--skip-time-window", "--market-env", "NORMAL", "--sector-limit-up-count", "2"], "PASS")
run("C16 R18自动判定(涨停60+上涨20%)(已退役9/10)", ["--skip-time-window", "--limit-up-count", "60", "--advance-ratio", "0.20",
    "--sector-limit-up-count", "3"], "PASS_WITH_WARN", expect_absent="R18")

# M-4: R14-R18 实战边界扩充（8/13全维度自省）
print("\n  [M-4扩展] R14-R18 边界场景")
run("M4_1 R14+3板高换手(已退役9/10)",
    ["--skip-time-window", "--space-leader-limit-down", "--consecutive-boards", "3",
     "--turnover-rate", "0.25"],
    "PASS_WITH_WARN", expect_absent="R14")
run("M4_2 R15炸板2次(边界)", ["--skip-time-window", "--seal-break-count", "2"], "PASS")  # 刚好不触发
run("M4_3 R18上涨25%边界(已退役9/10)", ["--skip-time-window", "--market-env", "EXTREME_STRUCTURE", "--limit-up-count", "60",
    "--advance-ratio", "0.25", "--sector-limit-up-count", "4"], "PASS_WITH_WARN", expect_absent="R18")  # 25%≤25%触发
run("M4_4 R18上涨26%+板块4只(已退役9/10)", ["--skip-time-window", "--market-env", "EXTREME_STRUCTURE", "--limit-up-count", "60",
    "--advance-ratio", "0.26", "--sector-limit-up-count", "4"], "PASS_WITH_WARN", expect_absent="R18")  # 26%>25%不触发
run("M4_5 R18板块涨停5只达标", ["--skip-time-window", "--market-env", "EXTREME_STRUCTURE", "--limit-up-count", "60",
    "--advance-ratio", "0.20", "--sector-limit-up-count", "5"], "PASS_WITH_WARN")
run("M4_6 R17昨天6→今天5(精确阈值)(已退役9/10)", ["--skip-time-window", "--highest-board-yesterday", "6",
    "--highest-board-today", "5"], "PASS_WITH_WARN", expect_absent="R17")
run("M4_7 R17昨天6→今天6(不触发)", ["--skip-time-window", "--highest-board-yesterday", "6",
    "--highest-board-today", "6"], "PASS")
run("M4_8 R14+0连板(不触发)", ["--skip-time-window", "--space-leader-limit-down", "--consecutive-boards", "0"], "PASS")
run("M4_9 多警告无拒绝(PASS_WITH_WARN)", ["--skip-time-window", "--consecutive-boards", "3", "--turnover-rate", "0.25",
    "--highest-board-yesterday", "7", "--highest-board-today", "5"], "PASS_WITH_WARN")
run("M4_10 R15+R16+R17三警告(无拒绝)", ["--skip-time-window", "--consecutive-boards", "3", "--turnover-rate", "0.22",
    "--seal-break-count", "2", "--highest-board-yesterday", "7", "--highest-board-today", "5"],
    "PASS_WITH_WARN")


# ================================================================
# D. 组合场景
# ================================================================
print("\n" + "─" * 70)
print("  D. 组合场景（多规则叠加）")
print("─" * 70)

run("D1 R14+R15+R17三杀(已退役9/10)",
    ["--space-leader-limit-down", "--consecutive-boards", "4",
     "--seal-break-count", "3", "--highest-board-yesterday", "7", "--highest-board-today", "5"],
    "PASS_WITH_WARN", expect_absent="R14")

run("D2 R18+R14(已退役9/10)",
    ["--market-env", "EXTREME_STRUCTURE", "--space-leader-limit-down",
     "--consecutive-boards", "3", "--sector-limit-up-count", "2"],
    "PASS_WITH_WARN", expect_absent="R14")

run("D3 全部新规则不触发(正常行情)",
    ["--skip-time-window", "--consecutive-boards", "1", "--seal-break-count", "0",
     "--turnover-rate", "0.05", "--sector-limit-up-count", "8"],
    "PASS")

run("D4 极端结构+板块达标+R16高风险(已退役9/10)",
    ["--skip-time-window", "--market-env", "EXTREME_STRUCTURE", "--sector-limit-up-count", "8",
     "--consecutive-boards", "3", "--turnover-rate", "0.28"],
    "PASS_WITH_WARN", expect_absent="R16")


# ================================================================
# E. trade_state 功能测试
# ================================================================
print("\n" + "─" * 70)
print("  E. trade_state.py 功能测试")
print("─" * 70)

# 清空状态
with open(state_file, "w", encoding="utf-8") as f:
    json.dump(clean, f, ensure_ascii=False, indent=2)

def test_state(name, func, expected_check):
    global passed, failed
    try:
        result = func()
        ok = expected_check(result)
        status = "✅" if ok else "❌"
        if ok: passed += 1
        else: failed += 1
        print(f"  {status} {name}")
        if not ok:
            print(f"     result={json.dumps(result, ensure_ascii=False)[:200]}")
    except Exception as e:
        failed += 1
        print(f"  ❌ {name}: EXCEPTION {e}")

# record-buy
test_state("E1 record-buy",
    lambda: run_state(["--record-buy", "000001"]),
    lambda r: "000001" in r.get("bought_codes", []))

test_state("E2 record-buy 第二只",
    lambda: run_state(["--record-buy", "000002"]),
    lambda r: len(r.get("bought_codes", [])) == 2)

# record-sell
test_state("E3 record-sell",
    lambda: run_state(["--record-sell", "000001"]),
    lambda r: r.get("sell_count") == 1)

# add-pending / clear-pending
test_state("E4 add-pending",
    lambda: run_state(["--add-pending", "000001", "order123"]),
    lambda r: "000001" in r.get("pending_orders", {}))

test_state("E5 clear-pending",
    lambda: run_state(["--clear-pending", "000001"]),
    lambda r: "000001" not in r.get("pending_orders", {}))

# set-breadth
test_state("E6 set-breadth",
    lambda: run_state(["--set-breadth", "0.42"]),
    lambda r: r.get("sector_breadth") == 0.42)

# set-market-env
test_state("E7 set-market-env(极端结构)",
    lambda: run_state(["--set-market-env", "env", "EXTREME_STRUCTURE", "highest-today", "5",
                       "highest-yesterday", "7", "space-leader-down", "limit-up", "62", "advance-ratio", "0.20"]),
    lambda r: r.get("market_env") == "EXTREME_STRUCTURE" and r.get("highest_board_today") == 5
              and r.get("space_leader_limit_down") == True)

# set-market-env 部分更新
test_state("E8 set-market-env部分更新(只改env)",
    lambda: run_state(["--set-market-env", "env", "NORMAL"]),
    lambda r: r.get("market_env") == "NORMAL" and r.get("highest_board_today") == 5)


# ================================================================
# F. 边界条件与误伤测试
# ================================================================
print("\n" + "─" * 70)
print("  F. 边界条件与误伤测试")
print("─" * 70)

# 清空状态
with open(state_file, "w", encoding="utf-8") as f:
    json.dump(clean, f, ensure_ascii=False, indent=2)

# R14 边界：刚好3板
run("F1 R14边界(空间龙跌停+刚好3板)(已退役9/10)", ["--skip-time-window", "--space-leader-limit-down", "--consecutive-boards", "3"], "PASS_WITH_WARN", expect_absent="R14")

# R15 边界：刚好3次炸板
run("F2 R15边界(刚好炸板3次)(已退役9/10)", ["--skip-time-window", "--seal-break-count", "3"], "PASS_WITH_WARN", expect_absent="R15")

# R16 边界：换手刚好20%
run("F3 R16边界(换手刚好20%+3板)", ["--skip-time-window", "--consecutive-boards", "3", "--turnover-rate", "0.20"], "PASS")

# R17 边界：昨日刚好6板→今日刚好5板
run("F4 R17边界(6→5)(已退役9/10)", ["--skip-time-window", "--highest-board-yesterday", "6", "--highest-board-today", "5"], "PASS_WITH_WARN", expect_absent="R17")

# R18 边界：涨停刚好50只+上涨刚好25%
run("F5 R18边界(涨停50+上涨25%)(已退役9/10)", ["--skip-time-window", "--market-env", "EXTREME_STRUCTURE", "--limit-up-count", "50",
    "--advance-ratio", "0.25", "--sector-limit-up-count", "3"], "PASS_WITH_WARN", expect_absent="R18")

# 最低仓位门槛（主人8/14裁决：迷你单允许，不拦截）
run("F6 最低仓位(迷你单1393元)", ["--skip-time-window", "--amount", "1393", "--total-cash", "1000000"], "PASS")

# 首板不应被R14误伤（consecutive=1）
run("F7 首板不被R14误伤", ["--skip-time-window", "--space-leader-limit-down", "--consecutive-boards", "1"], "PASS")

# 低换手首板不应被R16误伤
run("F8 低换手首板不被R16误伤", ["--skip-time-window", "--consecutive-boards", "1", "--turnover-rate", "0.03"], "PASS")


# ================================================================
# 恢复 trade_state
# ================================================================
print("\n" + "─" * 70)
if os.path.exists(backup_file):
    shutil.move(backup_file, state_file)
    print("[恢复] trade_state 已恢复原始状态")
elif os.path.exists(state_file):
    os.remove(state_file)
    print("[清理] 已清除测试用 trade_state")


# ================================================================
# 汇总
# ================================================================
print("\n" + "=" * 70)
print(f"  全量测试结果: ✅ {passed} PASS  /  ❌ {failed} FAIL  /  共 {passed+failed} 用例")
print("=" * 70)

# 按模块统计
modules = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}
mod_fail = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}
for name, ok in results:
    prefix = name[0]
    if prefix in modules:
        modules[prefix] += 1
        if not ok:
            mod_fail[prefix] += 1
# E模块是test_state直接统计，不results list，补算
e_total = passed + failed - len(results)
# Actually results only tracks run() calls not test_state, let me just print overall

if failed == 0:
    print("  🎉 全部通过！所有规则（R1-R18）+trade_state 功能均正确运行。")
else:
    print("  ⚠️ 存在失败用例，需排查。")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
