# -*- coding: utf-8 -*-
"""yaoyao_buy_20261103.py — 主人令20261102晚「明日早上9点27开始模拟盘买入亚宝600351买100股」。
09:27 schtasks 定时触发: 查重→trade_executor单笔买入(盘前挂单授权--owner-override-session, 历史winrate批先例)。
限价7.16=11/2收盘7.12×1.005; 开盘≤限价即成交, 高开不追(挂单等待)。全动作executor台账留痕。"""
import io
import json
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PLAN = os.path.join(ROOT, "data", "quant_rank_top1_pick_20261103.json")
plan = json.load(open(PLAN, encoding="utf-8"))
o = plan["orders"][0]
assert o["code"] == "600351" and o["volume"] == 100, "计划单不符"

# 查重: 亚宝不应在持仓
from stop_loss_executor import _fetch_positions_via_mcp
pos = {str(p["code"])[-6:]: p for p in (_fetch_positions_via_mcp() or [])}
if "600351" in pos and int(float(pos["600351"].get("quantity") or 0)) > 0:
    print("SKIP: 600351已在持仓(可能已成交), 不重复买")
    sys.exit(0)

r = subprocess.run([sys.executable, "trade_executor.py", "buy",
                    "--code", "600351", "--name", "亚宝药业",
                    "--source", "quant_rank_original", "--sector", "医药生物",
                    "--limit-price", str(o["limit_price"]), "--volume", "100",
                    "--owner-override-session", "--owner-approve-ddgate",
                    "--note", o["reason"][:190],
                    "--execute"],
                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env={**os.environ, "PYTHONUTF8": "1"})
out = r.stdout or ""
good = "order_id" in out or '"executed": true' in out or "PENDING_CONFIRM" in out
print(("BUY-OK " if good else "BUY-ERR"), "600351 亚宝药业 100股@" + str(o["limit_price"]))
if not good:
    print(out[-600:])
sys.exit(0 if good else 1)
