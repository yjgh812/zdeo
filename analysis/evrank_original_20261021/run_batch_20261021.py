# -*- coding: utf-8 -*-
"""20261021 下午盘一键执行: 8笔A1恢复减仓 + 买入奥飞娱乐(主人已确认订单, 三闸预演完毕).
用法: python run_batch_20261021.py [--execute]  (不带--execute=dry-run全流程)
守卫: 交易时段/奥飞价格异动(>9.60或<8.90全停)/卖出额<14万不买入/逐笔留痕.
"""
import json
import math
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
EXECUTOR = r"D:\zcodesj\trade_executor.py"

SELLS = [
    ("600848", "上海临港", 2500),
    ("601866", "中远海发", 9900),
    ("001390", "古麒绒材", 400),      # SELL_OK 无需 override
    ("300978", "东箭科技", 1600),
    ("002053", "云南能投", 3600),
    ("603209", "兴通股份", 1600),
    ("603098", "森特股份", 1800),
    ("002966", "苏州银行", 2100),
]
BUY_CODE, BUY_NAME, BUY_VOL = "002292", "奥飞娱乐", 22600
ABUY_MAX, ABUY_MIN = 9.60, 8.90        # 奥飞价格异动守卫
MIN_SELL_AMT = 140_000.0               # 卖出提交额低于此不买入(A1需降至35%以下)
NOTE_SELL = "主人20261021确认的A1仓位恢复减仓清单(浮亏趋势走坏);减仓后买入002292奥飞娱乐"
NOTE_BUY = ("主人20261021裁决:1/4资金买入奥飞;选股链=四重复验框架"
            "(传媒双确认强势方向,胜率68%/期望+2.34%/均盈+8.57%);DD_GATE防御档特批")


def gtimg(code6):
    pre = "sh" if code6.startswith("6") else "sz"
    raw = urllib.request.urlopen(f"http://qt.gtimg.cn/q={pre}{code6}", timeout=8) \
        .read().decode("gbk", errors="replace")
    f = raw.split('"')[1].split("~")
    return {"price": float(f[3]), "prev_close": float(f[4]), "open": float(f[5]),
            "high": float(f[33]) if len(f) > 33 else float(f[3])}


def run(cmd):
    r = subprocess.run(cmd, cwd=r"D:\zcodesj", capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**__import__("os").environ, "PYTHONUTF8": "1"})
    return r.returncode, r.stdout or ""


def main():
    execute = "--execute" in sys.argv
    now = time.localtime()
    in_session = (now.tm_hour, now.tm_min) >= (13, 0) and (now.tm_hour, now.tm_min) <= (14, 57)
    print(f"[mode] {'EXECUTE' if execute else 'DRY-RUN'} | time={time.strftime('%H:%M:%S')} "
          f"| in_session={in_session}")
    if execute and not in_session:
        print("[exit] 非下午盘时段, 拒绝执行")
        return 2

    # ---- 守卫: 奥飞价格异动 ----
    q = gtimg(BUY_CODE)
    aprice = q["price"]
    print(f"[guard] 奥飞娱乐现价={aprice} (守卫区间 {ABUY_MIN}~{ABUY_MAX})")
    if not (ABUY_MIN <= aprice <= ABUY_MAX):
        print("[exit] 奥飞价格超出守卫区间, 全部暂停, 等主人指示")
        return 2

    # ---- 8笔卖出 ----
    sell_results, sell_amt = [], 0.0
    for code6, name, qty in SELLS:
        p = gtimg(code6)["price"]
        px = math.floor(p * 0.999 * 100) / 100       # 让价0.1%贴市价保快速成交
        cmd = [sys.executable, "-u", EXECUTOR, "sell", "--code", code6, "--name", name,
               "--source", "manual_confirmed", "--note", NOTE_SELL,
               "--price", str(px), "--volume", str(qty),
               "--confirmed", "--owner-override-hold"]
        if code6 == "001390":                        # 古麒绒材 SELL_OK 免 override
            cmd.remove("--owner-override-hold")
        if execute:
            cmd.append("--execute")
        rc, out = run(cmd)
        placed = '"executed": true' in out           # 严格判定: executor 真实提交才算
        status = "PLACED" if (rc == 0 and placed) else f"NOT_PLACED_rc{rc}"
        if status == "PLACED":
            sell_amt += px * qty
        sell_results.append({"code": code6, "name": name, "qty": qty, "limit": px,
                             "status": status})
        print(f"[{status}] SELL {code6} {name} {qty}股 limit={px}", flush=True)
        if status != "PLACED":
            print("  output:", out[-500:], flush=True)
        time.sleep(1)
    print(f"[sell-summary] 提交卖出额≈{sell_amt:.0f}元 "
          f"({sum(1 for r in sell_results if r['status'] == 'PLACED')}/8笔)")

    # ---- 卖出额校验 ----
    if sell_amt < MIN_SELL_AMT:
        print(f"[exit] 卖出提交额{sell_amt:.0f} < {MIN_SELL_AMT:.0f}, 仓位可能未降至35%以下, "
              f"不买入, 等主人指示")
        return 2

    # ---- 买入奥飞 ----
    px = math.ceil(aprice * 1.005 * 100) / 100    # 上浮0.5%保成交(实际按对手价撮合)
    cmd = [sys.executable, "-u", EXECUTOR, "buy", "--code", BUY_CODE, "--name", BUY_NAME,
           "--source", "manual_confirmed", "--sector", "传媒", "--note", NOTE_BUY,
           "--price", str(px), "--volume", str(BUY_VOL), "--execute",
           "--owner-approve-ddgate"]
    if not execute:
        cmd.remove("--execute")
        cmd.remove("--owner-approve-ddgate")
    rc, out = run(cmd)
    print("[buy-result]", out[-1200:])
    try:
        d = json.loads(out)
        print(f"[buy-summary] verdict={d.get('gate_verdict')} executed={d.get('executed')}")
    except Exception:
        pass

    print(f"[done] execute={execute} 卖出{sum(1 for r in sell_results if r['status'] == 'PLACED')}/8笔 "
          f"提交≈{sell_amt:.0f}元; 成交核验请用westock MCP portfolio_paper_positions/history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
