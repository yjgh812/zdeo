# -*- coding: utf-8 -*-
"""一键全量测试 runner（2026-09-08，全量测试日 §五跑法自动化）

用法: python data/run_full_system_test.py [--quick]
--quick: 跳过数据链与健康巡检(分钟级), 只跑秒级测试套件
退出码: 失败项数量 (0=全绿)
"""
import subprocess
import sys

CASES = [
    # (名称, 命令, 是否秒级)
    ("stop_loss_pressure_test", [sys.executable, "tests/stop_loss_pressure_test.py"], True),
    ("test_fill_confirm_monitor", [sys.executable, "tests/test_fill_confirm_monitor.py"], True),
    ("test_fill_confirm_regression", [sys.executable, "tests/test_fill_confirm_regression.py"], True),
    ("test_fill_safety", [sys.executable, "tests/test_fill_safety.py"], True),
    ("test_o07_budget_cap", [sys.executable, "tests/test_o07_budget_cap.py"], True),
    ("test_o13_ledger_status", [sys.executable, "tests/test_o13_ledger_status.py"], True),
    ("test_poa_source_gate", [sys.executable, "tests/test_poa_source_gate.py"], True),
    ("test_portrait_shadow", [sys.executable, "tests/test_portrait_shadow.py"], True),
    ("test_style_ab_track", [sys.executable, "tests/test_style_ab_track.py"], True),
    ("test_vibe_module", [sys.executable, "tests/test_vibe_module.py"], True),
    ("test_wfa_gate_smoke", [sys.executable, "tests/test_wfa_gate_smoke.py"], True),
    ("test_winrate_replay_20260828", [sys.executable, "tests/test_winrate_replay_20260828.py"], True),
    ("test_constitution_mechanism", [sys.executable, "tests/test_constitution_mechanism.py"], True),
    ("l0_system_health", [sys.executable, "l0_system_health.py"], True),
    ("health_check_daily", [sys.executable, "health_check_daily.py"], False),
    ("check_db_freshness", [sys.executable, "check_db_freshness.py"], False),
    ("daily_chain_update", [sys.executable, "daily_chain_update.py"], False),
]


def main(quick: bool) -> int:
    fails = []
    for name, cmd, is_quick in CASES:
        if quick and not is_quick:
            print(f"[SKIP] {name} (分钟级, --quick 跳过)")
            continue
        print(f"[RUN ] {name} ...", flush=True)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=".")
            # health_check 结论 ERROR 时 exit code 仍为 0, 需查输出结论行
            if name == "health_check_daily":
                tail = (r.stdout or "")[-2000:]
                ok = "结论: OK" in tail or "结论: WARN" in tail
            else:
                ok = r.returncode == 0
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" (exit={r.returncode})"))
            if not ok:
                fails.append(name)
                tail = (r.stdout or "")[-500:] + (r.stderr or "")[-500:]
                print("  tail:", tail.replace("\n", "\n  ")[:600])
        except subprocess.TimeoutExpired:
            print(f"[FAIL] {name} (timeout 900s)")
            fails.append(name)
        except Exception as e:
            print(f"[FAIL] {name} ({type(e).__name__}: {e})")
            fails.append(name)
    print("=" * 50)
    print(f"TOTAL: {len([c for c in CASES if not (quick and not c[2])])} 项 | FAIL: {len(fails)} | {fails or '全绿'}")
    return len(fails)


if __name__ == "__main__":
    sys.exit(main(quick="--quick" in sys.argv))
