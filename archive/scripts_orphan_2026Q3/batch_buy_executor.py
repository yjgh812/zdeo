# -*- coding: utf-8 -*-
"""
batch_buy_executor.py — 批量买入唯一合规通道（2026-08-25 四任务优化①落地）
=============================================================================
背景（reports/PAPER_PORTFOLIO_4TASK_20260825.md）: 8/25 实测"名单合规+通道绕过"
新形态——13 笔买入全在白名单但直连 MCP 下单, 闸门/limit_price/落库全失效。
本脚本让"批量买入"与"逐笔调 trade_executor"一样容易, 从工程上消除绕过动机。

铁律: 每笔买入必须经 trade_executor.py（双层防线 R1-R29 + C 层归因）,
本脚本只做编排循环, 绝不直连 MCP 下单。

用法:
  python batch_buy_executor.py --top 5                          # dry-run 白名单前5
  python batch_buy_executor.py --codes 600588,001979 --amount 15000 --execute
  python batch_buy_executor.py --source master_hist --top 3 --execute

输出: 每笔 闸门verdict/成交/拒绝原因 汇总表; dry-run 默认安全。
"""
import argparse
import csv
import os
import subprocess
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
EXECUTOR = os.path.join(BASE, "trade_executor.py")


def load_whitelist(date_str: str) -> list[dict]:
    """读白名单 CSV（tulong_buy_whitelist 产物, 列: code,name,source,holding_horizon,price,limit_price,industry,tier,note）"""
    path = os.path.join(BASE, "reports", f"buy_whitelist_{date_str}.csv")
    if not os.path.exists(path):
        # 回退最近一份（白名单生成晚于收盘, 次日早盘用昨日文件属正常时序）
        cands = sorted(
            f for f in os.listdir(os.path.join(BASE, "reports"))
            if f.startswith("buy_whitelist_") and f.endswith(".csv"))
        if not cands:
            raise SystemExit(f"[FAIL] 无白名单文件: {path} 且 reports/ 下无任何 buy_whitelist_*.csv")
        path = os.path.join(BASE, "reports", cands[-1])
        print(f"[warn] {date_str} 无白名单, 回退最近: {os.path.basename(path)}")
    with open(path, encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r.get("code", "").strip()]
    if not rows:
        raise SystemExit(f"[FAIL] 白名单为空: {path}")
    return rows


def pick_targets(rows: list[dict], codes: str, source: str, top: int) -> list[dict]:
    if codes:
        want = {c.strip() for c in codes.split(",")}
        got = [r for r in rows if r["code"].strip() in want]
        missing = want - {r["code"].strip() for r in got}
        if missing:
            print(f"[warn] 不在白名单(跳过, 名单外须主人逐笔特批): {sorted(missing)}")
        return got
    if source:
        rows = [r for r in rows if r.get("source", "") == source]
    return rows[:top]


def run_one(r: dict, amount: float, execute: bool) -> dict:
    cmd = [sys.executable, EXECUTOR, "buy",
           "--code", r["code"].strip(),
           "--name", r.get("name", ""),
           "--source", r.get("source", ""),
           "--sector", r.get("industry", ""),
           "--amount", str(amount),
           "--price", r.get("price", ""),          # dry-run 参考价(非交易时段 quote 无价)
           "--limit-price", r.get("limit_price", "") or r.get("price", "")]
    if execute:
        cmd.append("--execute")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace", cwd=BASE)
        out = p.stdout or ""
        # 提取 verdict/executed 关键行（executor 输出 JSON）
        verdict = executed = reason = ""
        for line in out.splitlines():
            s = line.strip()
            if '"gate_verdict"' in s:
                verdict = s.split(":", 1)[1].strip(' ",')
            elif '"executed"' in s:
                executed = s.split(":", 1)[1].strip(' ",')
            elif '"reason"' in s and not reason:
                reason = s.split(":", 1)[1].strip(' ",')[:60]
        if p.returncode != 0 and not verdict:
            verdict = f"RC{p.returncode}"
            reason = (out or p.stderr or "")[-120:]
        return {"code": r["code"], "name": r.get("name", ""), "source": r.get("source", ""),
                "verdict": verdict or "?", "executed": executed or "?", "reason": reason}
    except Exception as e:
        return {"code": r["code"], "name": r.get("name", ""), "source": r.get("source", ""),
                "verdict": "ERROR", "executed": "False", "reason": f"{type(e).__name__}: {e}"[:80]}


def main():
    ap = argparse.ArgumentParser(description="批量买入唯一合规通道(逐笔经trade_executor双层防线)")
    ap.add_argument("--codes", default="", help="逗号分隔6位码; 省略则按白名单顺序")
    ap.add_argument("--source", default="", help="按白名单 source 过滤(如 master_hist)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--amount", type=float, default=15000, help="单票金额(元)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--execute", action="store_true", help="真实下单(默认dry-run)")
    a = ap.parse_args()

    rows = load_whitelist(a.date)
    targets = pick_targets(rows, a.codes, a.source, a.top)
    if not targets:
        raise SystemExit("[FAIL] 筛选后无目标票")
    mode = "EXECUTE" if a.execute else "DRY-RUN"
    print(f"=== 批量买入 {mode} | 目标 {len(targets)} 只 | 单票 {a.amount:.0f} 元 ===")
    results = []
    for r in targets:
        res = run_one(r, a.amount, a.execute)
        results.append(res)
        mark = "✅" if res["executed"] == "True" else ("🟡" if "PASS" in res["verdict"] else "❌")
        print(f"  {mark} {res['code']} {res['name']} [{res['source']}] verdict={res['verdict']} "
              f"executed={res['executed']} {res['reason']}")
    n_ok = sum(1 for x in results if "PASS" in x["verdict"])
    n_ex = sum(1 for x in results if x["executed"] == "True")
    print(f"=== 汇总: {len(results)} 笔 | 过闸 {n_ok} | 成交 {n_ex} | 模式 {mode} ===")
    if not a.execute:
        print("（dry-run 未下单; 加 --execute 真实执行, 每笔自动过 R1-R29+limit_price+落库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
