#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsr_gate.py — 屠龙系统回测准入门禁（DSR 多重检验一键检验）
=============================================================
封装 skill-backtest-overfit，输出统一 PASS/FAIL 判定。
门禁规则（2026-08-15 依据 droe_Top20 全检验确立）：
  【绝对模式(缺省, 向后兼容)】
  - DSR(N=3) > 0.95 且 DSR(N=5) > 0.95  → PASS（强）
  - DSR(N=3) > 0.95 但 DSR(N=5) <= 0.95 → WARN（边缘，需更多证据）
  - 其余 → FAIL（禁止实盘）
  - 附加：PSR>0.95 必须（连 Sharpe>0 都不显著直接 FAIL）
  【相对模式(--relative-baseline <基线DSR>, 2026-10-23 主人「继续」令裁决
    采纳 learning_plan_v3; R0-bp_ep 锚 0.2313/PSR 0.860 实证绝对 0.95 连生产基线都拒)】
  - DSR(N=3) >= 基线 且 DSR(N=5) >= 基线  → PASS（强）
  - DSR(N=3) >= 基线 但 DSR(N=5) < 基线  → WARN（边缘）
  - 其余 → FAIL
  - PSR 地板降为 0.50（Sharpe 方向显著性）; 0.95 绝对档降为「极强证据」标注级
  - PBO≤0.50 与 MinBTL 仍由 pbo_cscv_check/MinTRL 输出在案维持

用法:
  python dsr_gate.py <日收益CSV路径> --exp-id EXP-xxx
  python dsr_gate.py <CSV> --exp-id EXP-xxx --n-list 3 5 10   # 自定义档位
  （铁律：--exp-id 必填，试验必须先经 trial_registry.py register 登记）
"""
import subprocess, sys, argparse, re, os, json

# Windows 坑 #1 修复：显式 UTF-8 输出（GBK 控制台打印 emoji 会 UnicodeEncodeError）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 统计解释器固定 .venv_ml（系统 python 无 scipy；修复解释器 bug）
PYTHON = os.environ.get("ASTAT_PYTHON",
                        r"D:\workbuddyspace\.venv_ml\Scripts\python.exe")
OVERFIT = r"D:\zcodesj\statlib\overfit_report.py"
EXPERIMENT_LOG = r"D:\zcodesj\experiment_log.json"


def run_one(csv, n):
    cmd = [PYTHON, OVERFIT, "--returns", csv]
    if n > 0:
        cmd += ["--n-trials", str(n)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = r.stdout
    m = {}
    for key, pat in [
        ("sharpe", r"Observed Sharpe \(annual\)\s*:\s*([-\d.]+)"),
        ("dsr", r"Deflated Sharpe Ratio\s*:\s*([-\d.]+)"),
        ("psr", r"PSR vs 0\s*:\s*([-\d.]+)"),
        ("haircut", r"Haircut Sharpe \(bonferroni\):\s*([-\d.]+)"),
        ("mintrl", r"Min Track Record Length\s*:\s*([\d.]+)"),
        ("obs", r"Observations\s*:\s*(\d+)"),
    ]:
        mm = re.search(pat, out)
        m[key] = float(mm.group(1)) if mm else None
    m["obs"] = int(m["obs"]) if m["obs"] else 0
    return m


def gate_verdict(d3: float, d5: float, baseline: float = None):
    """判定纯函数。baseline=None→绝对模式(0.95); 给定→相对模式(候选DSR>=基线)。
    返回 (verdict_str, exit_code)。"""
    if baseline is None:
        if d3 > 0.95 and d5 > 0.95:
            return "PASS（强）", 0
        if d3 > 0.95:
            return "WARN（N=3过/N=5差，边缘策略，需更多证据）", 0
        return "FAIL（禁止实盘）", 1
    tag = f"候选DSR>=基线{baseline:.4f}"
    if d3 >= baseline and d5 >= baseline:
        return f"PASS（强, 相对{tag}）", 0
    if d3 >= baseline:
        return f"WARN（相对{tag}: N=3过/N=5差，边缘策略，需更多证据）", 0
    return f"FAIL（相对{tag}未过，禁止实盘）", 1


def check_registered(exp_id: str) -> dict:
    """校验试验是否已在 experiment_log.json 登记；未登记 → SystemExit(2)。"""
    if not os.path.exists(EXPERIMENT_LOG):
        raise SystemExit(
            f"[A-STAT] 未找到登记台账 {EXPERIMENT_LOG}\n"
            "铁律：任何策略进 DSR 门禁前必须先登记 experiment 条目。\n"
            "请先运行: python trial_registry.py register --name <策略名> --n-trials <N> ...")
    with open(EXPERIMENT_LOG, encoding="utf-8") as f:
        log = json.load(f)
    for exp in log.get("experiments", []):
        if exp.get("exp_id") == exp_id:
            return exp
    raise SystemExit(f"[A-STAT] exp_id '{exp_id}' 未在台账登记，拒绝运行。先 trial_registry.py register。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="日收益CSV路径")
    ap.add_argument("--n-list", type=int, nargs="+", default=[3, 5])
    ap.add_argument("--exp-id", required=True, help="experiment_log.json 中的试验 ID（必填，无登记拒绝）")
    ap.add_argument("--relative-baseline", type=float, default=None,
                    help="相对模式基线DSR(同口径生产基线; 裁决=2026-10-23 继续令, R0-bp_ep锚0.2313)。"
                         "缺省=绝对模式(0.95)。PSR地板: 相对0.50/绝对0.95")
    args = ap.parse_args()
    check_registered(args.exp_id)

    print("=" * 64)
    print(f"  DSR GATE — {args.csv}  模式={'相对(基线%.4f)' % args.relative_baseline if args.relative_baseline is not None else '绝对(0.95)'}")
    print("=" * 64)
    psr_floor = 0.50 if args.relative_baseline is not None else 0.95
    base = run_one(args.csv, 0)
    print(f"  Sharpe={base['sharpe']}  Obs={base['obs']}  PSR={base['psr']}")
    if base["psr"] is None or base["psr"] <= psr_floor:
        print(f"\n  ❌ FAIL: PSR<={psr_floor}，Sharpe 方向不显著，禁止实盘")
        return 1

    results = {}
    for n in args.n_list:
        m = run_one(args.csv, n)
        results[n] = m
        print(f"  N={n:<3d}: DSR={m['dsr']:.4f}  haircut={m['haircut']:.3f}  "
              f"MinTRL={m['mintrl']:.0f} (<{m['obs']}{'✅' if m['mintrl'] < m['obs'] else '❌'})")

    d3 = results.get(3, {}).get("dsr", 0)
    d5 = results.get(5, {}).get("dsr", 0)
    print("-" * 64)
    if args.relative_baseline is not None:
        strong = "（极强证据标注: DSR(N=3)&(N=5)≥0.95 绝对档）" if (d3 > 0.95 and d5 > 0.95) else ""
        print(f"  绝对0.95档标注: {strong or '未达'}")
    verdict, code = gate_verdict(d3, d5, args.relative_baseline)
    print(f"  判定: {verdict}")
    return code


if __name__ == "__main__":
    sys.exit(main())