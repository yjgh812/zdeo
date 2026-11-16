# -*- coding: utf-8 -*-
"""DD_GATE 回撤门控契约测试（方案3, 2026-09-15）。

三分支:
  A 防御档 → REJECT（gate 含"防御"）
  B 防御档 + --owner-approve-ddgate → WARN 豁免（verdict 非 REJECT）
  C 满仓（真实 state）→ 允许 WARN
  D state 日期过期 → fail-open WARN
运行后恢复原 state 文件。--no-log 防 guard_precheck_log 污染。
"""
import json
import shutil
import subprocess
import sys
from datetime import date, timedelta

ROOT = r"D:\zcodesj"
STATE = ROOT + r"\data\ddprotect_gate_state.json"
BAK = STATE + ".ddgate_test_bak"
PY = sys.executable

# 9/22 修复时间炸弹: 原用例硬编码 2026-09-14/09-01, 跨日必 fail-open; 改动态日期
TODAY = date.today().strftime("%Y-%m-%d")
TODAY_M30 = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
TODAY_M30_TS = TODAY_M30 + " 08:00:00"

FAILS = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" | {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def run_precheck(extra_args):
    argv = ["pre_trade_check_v2.py", "--code", "600737", "--name", "中粮糖业",
            "--source", "manual_confirmed", "--sector", "农林牧渔", "--price", "16.2",
            "--amount", "20000", "--no-log"] + extra_args
    r = subprocess.run([PY, "-c",
                        f"import os,runpy,sys; os.chdir(r'{ROOT}'); "
                        f"sys.argv={argv!r}; "
                        f"runpy.run_path(r'{ROOT}\\pre_trade_check_v2.py', run_name='__main__')"],
                       capture_output=True, text=True, timeout=180,
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    return r.stdout + r.stderr


shutil.copy2(STATE, BAK)
try:
    real = json.load(open(STATE, encoding="utf-8"))
    # A 防御档 → REJECT
    json.dump({**real, "gate": "防御档", "computed_at": TODAY + " 08:00:00",
               "date": TODAY}, open(STATE, "w", encoding="utf-8"))
    out = run_precheck([])
    check("A1 防御档 REJECT", "DD_GATE回撤门控: 当前gate=防御档" in out and "新买入拒绝" in out)
    check("A2 verdict=REJECT", '"verdict": "REJECT"' in out)
    # B 防御档 + 豁免 → DD闸自身放行（WARN 留痕）
    # 20261023 断言收窄: 原断"全输出无REJECT"在 R37/P2 等后加闸门合法拒绝时误报(闸家族增长=断言腐烂);
    # 本测试只管 DD 闸契约 → 改判"未豁免版DD拒绝文案不出现"。
    out = run_precheck(["--owner-approve-ddgate"])
    check("B1 豁免分支 owner-approve WARN", "owner-approve" in out and "主人特批豁免" in out)
    check("B2 DD闸未REJECT(豁免后)", "DD_GATE回撤门控: 当前gate=" not in out)
    # D state 日期过期 → fail-open（DD闸自身标注; 其他闸合法REJECT不归本测试管）
    json.dump({**real, "gate": "防御档", "computed_at": TODAY_M30_TS,
               "date": TODAY_M30}, open(STATE, "w", encoding="utf-8"))
    out = run_precheck([])
    check("D 过期 fail-open WARN", ("state日期%s非今日" % TODAY_M30) in out)
    # C 恢复真实 state → 输出与活状态一致（原硬编码"满仓"假设随市场状态腐烂, 20261023 改动态对照）
    shutil.copy2(BAK, STATE)
    out = run_precheck([])
    real_gate = real.get("gate", "")
    check("C 真实state一致", f"当前gate={real_gate}" in out
          and ("允许新买入" in out or "新买入拒绝" in out))
finally:
    shutil.copy2(BAK, STATE)
    import os
    os.remove(BAK)
    print("[restore] state 已恢复")

print("=" * 40)
print("FAILED: " + str(FAILS) if FAILS else "全部 %d 项 PASS" % 5)
sys.exit(1 if FAILS else 0)
