#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R14-R18 回归测试（8/13复盘经验库落地验证）。

2026-09-10 退役更新: R1/R14-R18 结构性空转六闸已下线(executor 无对应参数输入、
trade_state 市场字段无生产写入者、全史 REJECT=0 虚假防线面), 本测试改为退役断言——
六闸参数输入后不得再产出任何 R14-R18 判定(REJECT/WARN 均不得出现),
防止退役块被误复活。档案=SOP 2026-09-10(三)条。

测试前自动备份并清空trade_state（避免R3/R13误触发），测试后恢复。
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

STATE_DIR = r"D:\zcodesj\trade_state"
today = datetime.now().strftime("%Y%m%d")
state_file = os.path.join(STATE_DIR, f"trade_state_{today}.json")
backup_file = state_file + ".test_bak"

BASE = ["python", "D:/zcodesj/pre_trade_check_v2.py", "--no-log"]


def _pick_safe_base() -> list:
    """2026-09-10: 基线从当日白名单动态取数——R27名单价偏离闸(执行价 vs 名单价>3% REJECT)
    上线后, 静态基线(002437@4.25 vs 名单价3.70)随名单每日刷新漂移致误判REJECT。
    优先 bp_ep/full_market 价值段(低动量, 规避 R31 ext20≥20% 硬闸随机误触发),
    回退 universe 段, 再回退静态(仅无白名单时的兜底)。"""
    import csv as _csv
    try:
        path = os.path.join(r"D:\zcodesj\reports", f"buy_whitelist_{today}.csv")
        prefer, fallback = [], []
        with open(path, encoding="utf-8-sig") as f:
            for row in _csv.DictReader(f):
                try:
                    px = float(row.get("price") or 0)
                except (TypeError, ValueError):
                    px = 0.0
                code = str(row.get("code") or "").strip()
                name = (row.get("name") or "").strip()
                if not (code and name and 1 < px <= 30 and "ST" not in name.upper()):
                    continue
                entry = ["--code", code, "--name", name,
                         "--price", f"{px:.2f}", "--prev-close", f"{px * 0.97:.2f}",
                         "--source", "mainline_radar",
                         "--holdings", "10", "--total-cash", "1000000",
                         "--amount", "20000", "--pct-60d", "0.30",
                         "--close", f"{px:.2f}", "--high", f"{px * 1.02:.2f}",
                         "--low", f"{px * 0.99:.2f}"]
                (prefer if row.get("source") in ("bp_ep", "full_market") else fallback).append(entry)
        if prefer or fallback:
            chosen = (prefer or fallback)[0]
            print(f"  基线自适配: {chosen[1]} {chosen[3]} (白名单价 {chosen[5]})")
            return chosen
    except Exception as e:
        print(f"  ! 白名单读取失败({type(e).__name__}: {e}), 回退静态基线")
    # 静态兜底(可能漂移, 仅供无白名单时跑通)
    # 9/22: 执行价对齐最新白名单中 002437 的名单价, 防 R27 >3% 偏离闸误判 REJECT
    px = 4.25
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
                                px = _lp
                        except (TypeError, ValueError):
                            pass
                        break
    except Exception:
        pass
    return ["--code", "002437", "--name", "誉衡药业",
            "--price", f"{px:.2f}", "--prev-close", f"{px * 0.97:.2f}",
            "--source", "mainline_radar", "--holdings", "10",
            "--total-cash", "1000000", "--amount", "20000",
            "--pct-60d", "0.30",
            "--close", f"{px * 0.99:.2f}", "--high", f"{px * 1.02:.2f}",
            "--low", f"{px * 0.97:.2f}"]


# 安全基线参数：确保不触发R1/R4/R5/R6/R7/R13/R27等已有规则
SAFE_BASE = _pick_safe_base()

passed = 0
failed = 0


def run_case(name: str, extra_args: list, expect_verdict: str,
             expect_keyword: str | None = None, expect_absent: str | None = None):
    global passed, failed
    cmd = BASE + SAFE_BASE + extra_args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    try:
        data = json.loads(result.stdout)
    except Exception:
        print(f"  X {name}: JSON解析失败, stdout={result.stdout[:200]}")
        failed += 1
        return

    verdict = data.get("verdict", "")
    rejects = data.get("rejects", [])
    warns = data.get("warnings", [])
    combined = " | ".join(rejects + warns)

    ok = verdict.startswith(expect_verdict)
    if expect_keyword:
        ok = ok and expect_keyword in combined
    if expect_absent:  # 2026-09-10: 退役断言——六闸关键词不得再出现
        ok = ok and expect_absent not in combined

    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1

    print(f"  [{status}] {name}: verdict={verdict} (期望={expect_verdict})", end="")
    if expect_keyword:
        found = expect_keyword in combined
        print(f" keyword={'命中' if found else '未命中'}", end="")
    if expect_absent:
        gone = expect_absent not in combined
        print(f" 退役断言={'通过(无该闸输出)' if gone else '失败(该闸仍在输出)'}", end="")
    print()
    if not ok:
        print(f"     rejects={rejects}")
        print(f"     warns={warns}")


# === 备份并清空 trade_state ===
print("准备测试环境...")
if os.path.exists(state_file):
    shutil.copy2(state_file, backup_file)
    print(f"  已备份 trade_state → {backup_file}")

# 写入干净的测试状态
clean_state = {
    "date": today, "buy_count": 0, "bought_codes": [],
    "sell_count": 0, "pending_orders": {},
    "sector_breadth": 0.35,
    "market_env": None, "highest_board_today": None,
    "highest_board_yesterday": None, "space_leader_limit_down": False,
    "limit_up_count": None, "advance_ratio": None,
}
os.makedirs(STATE_DIR, exist_ok=True)
with open(state_file, "w", encoding="utf-8") as f:
    json.dump(clean_state, f, ensure_ascii=False, indent=2)
print("  已写入干净测试状态")

print()
print("=" * 60)
print("R14-R18 回归测试（8/13复盘经验库落地验证）")
print("=" * 60)

# T1: R14 空间龙跌停+3板 → 退役断言: 无 R14 输出
run_case("T1 R14空间龙跌停+3板(已退役)",
         ["--space-leader-limit-down", "--consecutive-boards", "3"],
         "PASS", None, "R14")

# T2: R14 空间龙跌停+2板 → 无 R14 输出
run_case("T2 R14空间龙跌停+2板(已退役)",
         ["--space-leader-limit-down", "--consecutive-boards", "2"],
         "PASS", None, "R14")

# T3: R15 炸板3次 → 退役断言: 无 R15 输出
run_case("T3 R15炸板3次(已退役)",
         ["--seal-break-count", "3"],
         "PASS", None, "R15")

# T4: R15 炸板2次 → 无 R15 输出
run_case("T4 R15炸板2次(已退役)",
         ["--seal-break-count", "2"],
         "PASS", None, "R15")

# T5: R16 3板+换手25% → 退役断言: 无 R16 输出
run_case("T5 R16高换手提示(已退役)",
         ["--consecutive-boards", "3", "--turnover-rate", "0.25"],
         "PASS", None, "R16")

# T6: R17 最高板7→5 → 退役断言: 无 R17 输出
run_case("T6 R17 BOARD_COLLAPSE退潮(已退役)",
         ["--highest-board-yesterday", "7", "--highest-board-today", "5"],
         "PASS", None, "R17")

# T7: R18 极端结构+板块涨停3只 → 退役断言: 无 R18 输出
run_case("T7 R18极端结构板块共振不足(已退役)",
         ["--market-env", "EXTREME_STRUCTURE", "--limit-up-count", "62",
          "--advance-ratio", "0.20", "--sector-limit-up-count", "3"],
         "PASS", None, "R18")

# T8: R18 极端结构+板块涨停6只 → 退役断言: 无 R18 输出
run_case("T8 R18极端结构板块共振达标(已退役)",
         ["--market-env", "EXTREME_STRUCTURE", "--limit-up-count", "62",
          "--advance-ratio", "0.20", "--sector-limit-up-count", "6"],
         "PASS", None, "R18")

# T9: R4防御蓝筹已按主人8/14裁决移除（行业不该拦截）→ 银行股应PASS
run_case("T9 R4已移除银行股放行",
         ["--code", "601398", "--name", "工商银行", "--price", "6.50",
          "--prev-close", "6.40", "--source", "mainline_radar",
          "--holdings", "10", "--total-cash", "1000000", "--amount", "20000",
          "--close", "6.48", "--high", "6.55", "--low", "6.40",
          "--pct-60d", "0.10"],
         "PASS", None)

# T10: 组合——R14+R17同时触发 → 退役断言: 无 R14/R17 输出
run_case("T10 组合R14+R17(已退役)",
         ["--space-leader-limit-down", "--consecutive-boards", "4",
          "--highest-board-yesterday", "7", "--highest-board-today", "5"],
         "PASS", None, "R14")

# T11: 完整通过用例——安全基线+无任何新规则触发
run_case("T11 安全基线全PASS",
         [],
         "PASS", None)

print()
print(f"{'=' * 60}")
print(f"结果: PASS {passed} / FAIL {failed} / 共 {passed + failed} 用例")

# === 恢复 trade_state ===
if os.path.exists(backup_file):
    shutil.move(backup_file, state_file)
    print("已恢复 trade_state 原始状态")
elif os.path.exists(state_file):
    os.remove(state_file)
    print("已清除测试用 trade_state（无原始备份）")

if failed == 0:
    print("全部通过！R14-R18 退役断言成立（六闸参数输入后零判定输出）。")
else:
    print("有失败用例，需检查退役实现。")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
