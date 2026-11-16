# -*- coding: utf-8 -*-
"""9/7 09:50 幂等重试: 模拟盘"走的差"4股清仓(主人2026-09-06指令)。
09:32 主任务可能因 westock 断供/瞬时故障静默失败(该源历史断供5次)。
幂等判据(与 executor C6 减半语义相容):
  已不在持仓            -> 跳过(09:32已清)
  仍持有=全量(原数量)   -> 补卖(09:32未成交)
  部分成交(<全量)       -> 跳过(C6板块强势减半属系统保护, 余量不动)
诊断依据: reports/PAPER_WEAK_SELL_SCAN_20260906.md
"""
import subprocess
import sys
import os
import datetime

sys.path.insert(0, r"D:\zcodesj")
from trade_executor import WeStockClient, norm  # 仅用其只读持仓查询

STOCKS = [
    ("300252", "金信诺", 8600, "趋势走坏:主人9/6指令清仓(浮亏-5.7%全场最深+MA60下方-9.5%)"),
    ("301256", "华融化学", 1800, "趋势走坏:主人9/6指令清仓(MA20/MA60双下+ret5未止跌)"),
    ("603993", "洛阳钼业", 900, "趋势走坏:主人9/6指令清仓(ret20-11.3%双均线下方,浮盈止盈离场)"),
    ("300077", "国民技术", 2500, "趋势走坏:主人9/6指令清仓(MA20/MA60双下+ret5-1.7%走弱)"),
]
LOG = r"D:\zcodesj\reports\sell_weak4_20260907.log"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def held_qty(ws, code):
    positions = ws.positions().get("data", {}).get("positions", [])
    for x in positions:
        if norm(x.get("code", "")) == norm(code):
            return int(x.get("quantity", 0) or 0)
    return 0


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n===== retry run {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
        try:
            ws = WeStockClient()
        except Exception as e:
            f.write(f"[FATAL] WeStockClient init fail: {e!r}\n")
            return
        for code, name, orig_qty, reason in STOCKS:
            try:
                q = held_qty(ws, code)
            except Exception as e:
                f.write(f"[SKIP] {code} {name}: 持仓查询失败 {e!r}\n")
                continue
            if q <= 0:
                f.write(f"[SKIP] {code} {name}: 已清仓(qty=0)\n")
                continue
            if q < orig_qty:
                f.write(f"[SKIP] {code} {name}: 部分成交 {q}/{orig_qty} (疑C6减半保护, 余量不动)\n")
                continue
            cmd = [sys.executable, "trade_executor.py", "sell",
                   "--code", code, "--name", name, "--source", "manual_confirmed",
                   "--reason", reason + "(09:50重试)", "--confirmed", "--execute"]
            f.write(f"\n--- RETRY {code} {name} (持有{q}股) ---\n")
            try:
                r = subprocess.run(cmd, cwd=r"D:\zcodesj", capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   env=ENV, timeout=300)
                f.write(r.stdout or "")
                f.write(r.stderr or "")
                f.write(f"[exit={r.returncode}]\n")
            except Exception as e:
                f.write(f"[EXC]{e!r}\n")


if __name__ == "__main__":
    main()
