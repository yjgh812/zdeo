# -*- coding: utf-8 -*-
"""主人 2026-09-14 指令"让你下单"·胜率Top10批量买入编排 (一次性, 时段内即时执行)。

裁决来源: 交互会话 2026-09-14 主人令"屠龙系统选最优胜率最高10只...明天腾讯自选股模拟盘买入验证"→
"没看见你下单"→"让你下单" (腾讯自选股无接口, 经通道澄清主人授权用可用通道下单)。
名单: tulong_stock_select.py 终版Top10 (画像Top500∪bp_ep候选池+61月胜率排序, reports/tulong_top10_three_views_20260913.md §六)。
验证台账: data/PREREG_WINRATE_TOP10_TENCENT_20260914.json (判据冻结; 通道变更不影响判据)。

铁律合规: 每笔经 trade_executor.py buy 唯一入口(双层防线+归因落库), 本脚本只编排循环;
source=manual_confirmed(主人显式指令 2026-09-14); 单票 20000 元(现金保守, 资金不足者 executor REJECT 留痕);
重试1次防断供; 幂等(result json 存在且 execute=true 则跳过)。
"""
import json
import os
import subprocess
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXECUTOR = os.path.join(BASE, "trade_executor.py")
OUT = os.path.join(BASE, "data", "_manual_batch_winrate10_result.json")
AMOUNT = 20000
EXECUTE = "--execute" in sys.argv

# code, name, sector(申万L1) — 按胜率排名
TARGETS = [
    ("603328", "依顿电子", "电子"),
    ("002966", "苏州银行", "银行"),
    ("601328", "交通银行", "银行"),
    ("603637", "镇海股份", "建筑装饰"),
    ("002478", "常宝股份", "钢铁"),
    ("603162", "海通发展", "交通运输"),
    ("002942", "新农股份", "农林牧渔"),
    ("300214", "日科化学", "基础化工"),
    ("601077", "渝农商行", "银行"),
    ("300154", "瑞凌股份", "机械设备"),
]


def run_buy(code, name, sector):
    cmd = [
        sys.executable, EXECUTOR, "buy",
        "--code", code, "--name", name, "--sector", sector,
        "--source", "manual_confirmed", "--amount", str(AMOUNT),
    ] + (["--execute"] if EXECUTE else [])
    for attempt in range(2):  # 重试1次防断供
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return r
    return r


def main() -> int:
    if os.path.exists(OUT) and EXECUTE:
        d = json.load(open(OUT, encoding="utf-8"))
        if d.get("execute") and d.get("n_ok", 0) > 0:
            print("[batch] 幂等: 已有成交记录, 跳过")
            return 0
    print(f"[batch] 胜率Top10 x {AMOUNT}元/只 execute={EXECUTE}")
    results = []
    n_ok = 0
    for code, name, sector in TARGETS:
        r = run_buy(code, name, sector)
        out = (r.stdout or "") + (r.stderr or "")
        verdict = "UNKNOWN"
        for line in out.splitlines():
            if "PASS" in line or "REJECT" in line or "成交" in line or "买入" in line:
                verdict = line.strip()[:120]
                break
        ok = r.returncode == 0 and ("PASS" in out or "成交" in out)
        if ok:
            n_ok += 1
        results.append({"code": code, "name": name, "rc": r.returncode, "ok": ok, "verdict": verdict})
        print(f"  {code} {name}: rc={r.returncode} {'OK' if ok else 'REJECT/FAIL'} | {verdict}")
    summary = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "execute": EXECUTE,
               "amount_each": AMOUNT, "n_ok": n_ok, "n_total": len(TARGETS), "results": results}
    json.dump(summary, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[batch] 完成: 成交/通过 {n_ok}/{len(TARGETS)}, 结果 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
