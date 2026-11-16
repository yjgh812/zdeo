# -*- coding: utf-8 -*-
"""主人 2026-09-10 逐笔特批批量买入编排（9/11 开盘执行, 一次性）。

裁决来源: 交互会话 2026-09-10 主人指令"明天开盘买入这十只"+AskUserQuestion 裁决
(通道=westock模拟盘trade_executor唯一入口; 范围=十只全买)。
十只中 600256 广汇能源 被闸门预检 R22 双口径闸 REJECT(EM/THS 5日资金流方向相反,
无豁免口) → 本编排仅执行其余 9 只, 广汇能源待主人二次裁决。

铁律合规: 每笔均经 trade_executor.py buy（双层防线+归因落库）, 本脚本只编排循环,
绝不直连 MCP 下单; source=manual_confirmed(主人逐笔特批); R-S5 为 WARN 留痕
(9/8 主人裁决撤销 REJECT 分支), 不传豁免参数让 WARN 自然留痕。
单票金额 25000 元(9/9 可用约26万, 9只共22.5万留安全边际); executor 自动取实时价。
重试1次防 westock 断供(9/6 先例), executor/审计层幂等。
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXECUTOR = os.path.join(BASE, "trade_executor.py")
OUT = os.path.join(BASE, "data", "_manual_batch_20260911_result.json")
AMOUNT = 25000
EXECUTE = "--execute" in sys.argv

# code, name, sector(申万L1)
TARGETS = [
    ("002320", "海峡股份", "交通运输"),
    ("000983", "山西焦煤", "煤炭"),
    ("601919", "中远海控", "交通运输"),
    ("603209", "兴通股份", "交通运输"),
    ("601022", "宁波远洋", "交通运输"),
    ("000937", "冀中能源", "煤炭"),
    ("601866", "中远海发", "交通运输"),
    ("600348", "华阳股份", "煤炭"),
    ("601975", "招商南油", "交通运输"),
]


def run_buy(code: str, name: str, sector: str) -> dict:
    cmd = [
        sys.executable, EXECUTOR, "buy",
        "--code", code, "--name", name,
        "--source", "manual_confirmed", "--sector", sector,
        "--amount", str(AMOUNT),
    ] + (["--execute"] if EXECUTE else [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=BASE,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        # 解析 executor JSON 判决（returncode 对"闸门拒绝"返回 0, 必须以 executed 字段为准）
        verdict = re.search(r'"gate_verdict"\s*:\s*"([^"]+)"', out)
        executed = re.search(r'"executed"\s*:\s*(true|false)', out)
        reason = re.search(r'"reason"\s*:\s*"([^"]+)"', out)
        rejects = re.findall(r'"gate_rejects"\s*:\s*\[(.*?)\]', out, re.S)
        return {
            "returncode": p.returncode,
            "gate_verdict": verdict.group(1) if verdict else "PARSE_FAIL",
            "executed": executed.group(1) == "true" if executed else False,
            "reason": reason.group(1) if reason else "",
            "rejects": (re.sub(r'"|\\n|\\"', "", rejects[0])[:160] if rejects else ""),
            "tail": out[-700:],
        }
    except Exception as e:
        return {"returncode": -1, "gate_verdict": "EXC", "executed": False,
                "reason": f"{type(e).__name__}: {e}", "rejects": "", "tail": ""}


def main() -> int:
    # 2026-09-10 晚加固：本批已于 9/10 日间执行过（结果 json execute=true）。
    # 次日同分钟幂等守护重置，任何重跑都会真实重复下单（9 只×2.5 万）→
    # 结果文件在案即拒绝重跑，除非显式 --allow-rerun（人工二次裁决用）。
    if EXECUTE and os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            prev = None
        if prev and prev.get("execute") and "--allow-rerun" not in sys.argv:
            print(f"[guard] 本批已于 {prev.get('run_at')} 执行过(execute=true, n_ok={prev.get('n_ok')}"
                  f"/n_fail={prev.get('n_fail')})，拒绝重复下单；确需重跑请显式加 --allow-rerun")
            return 1
    results = []
    for code, name, sector in TARGETS:
        r = run_buy(code, name, sector)
        ok = bool(EXECUTE) and r["executed"]
        if not ok and EXECUTE:  # 断供/失败重试1次(幂等); 闸门REJECT不重试(判定确定性)
            if r["gate_verdict"] not in ("REJECT", "PARSE_FAIL", "EXC"):
                r2 = run_buy(code, name, sector)
                r = {"retry": True, **r2}
                ok = bool(EXECUTE) and r["executed"]
        results.append({"code": code, "name": name, "sector": sector,
                        "ok": ok, "verdict": r["gate_verdict"],
                        "executed": r["executed"], "reason": r["reason"],
                        "rejects": r["rejects"], "detail": r["tail"]})
        print(f"[{r['gate_verdict']}] {code} {name} executed={r['executed']}"
              + (f" | {r['rejects']}" if r['rejects'] else ""))
    summary = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "execute": EXECUTE,
        "amount_each": AMOUNT,
        "excluded_by_gate": {"600256": "广汇能源 R22双口径闸REJECT(EM/THS 5日资金流方向相反), 待主人裁决"},
        "results": results,
        "n_ok": sum(1 for r in results if r["ok"]),
        "n_fail": sum(1 for r in results if EXECUTE and not r["ok"]),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"summary: execute={EXECUTE} n_ok={summary['n_ok']} n_fail={summary['n_fail']} -> {OUT}")
    if not EXECUTE:
        print("预演模式: 未真实下单, verdict 汇总供审阅")
        return 0
    return 0 if summary["n_fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
