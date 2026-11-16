# -*- coding: utf-8 -*-
"""quantrank1_buy_two_20261103.py — 主人令20261103凌晨「伟星(暂定可追)+博迈科两只都买入」。
09:31 schtasks触发: 伟星002003按现价追(限价12.76=11/2收盘×1.005, 主人特批HOLD_WAIT追买留痕);
博迈科603727按判定层买区挂20.63等回踩(不追现价21.75, 铁律语义=按区域买)。
各100股。全动作trade_executor执行+台账留痕。"""
import io
import json
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

ORDERS = [
    dict(code="002003", name="伟星股份", sector="纺织服饰", limit_price="12.76", volume="100",
         approve=["--owner-approve-r31"],
         note="主人令20261103[伟星暂定可追两只都买]: EV全A第一+5.4%但主升后段判定HOLD_WAIT, 主人特批追买; "
              "结构失效位9.58, -8%止损链保护"),
    dict(code="603727", name="博迈科", sector="石油石化", limit_price="20.63", volume="100",
         approve=[],
         note="主人令20261103[博迈科买入]: EV全A第二+4.4%三买区, 按判定层买区[20.03,20.63]挂20.63等回踩成交, "
              "不追现价21.75; 失效=跌破19.41"),
]

results = []
for o in ORDERS:
    cmd = [sys.executable, "trade_executor.py", "buy",
           "--code", o["code"], "--name", o["name"], "--source", "quant_rank_original",
           "--sector", o["sector"], "--limit-price", o["limit_price"], "--volume", o["volume"]]
    cmd += o["approve"]
    cmd += ["--note", o["note"][:190], "--execute"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
    out = r.stdout or ""
    ok = ("order_id" in out) or ("PENDING_CONFIRM" in out) or ('"executed": true' in out)
    line = ("BUY-OK " if ok else "BUY-ERR ") + o["code"] + " " + o["name"] + " @" + o["limit_price"]
    print(line)
    if not ok:
        print(out[-500:])
    results.append((o["code"], ok))

sys.exit(0 if all(ok for _, ok in results) else 1)