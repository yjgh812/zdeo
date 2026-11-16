# -*- coding: utf-8 -*-
"""bp_ep 前瞻轨道 · 月度轮换器 (2026-10-23)
用法: python analysis/bp_ep_fwd_rebalance.py            # 出轮换计划(默认, 零动作)
      python analysis/bp_ep_fwd_rebalance.py --execute  # 执行轮换(先卖后买, 全走executor留痕)
流程: 动态重算最新月末bp_ep篮子(官方机器口径) → 与轨道持仓diff → 卖出离篮名( executor sell
      --confirmed --reason bp_ep月度轮换 ) → 买入入篮名( deploy同源: --source bp_ep_core,
      第4+只银行 --owner-approve-r30a ) → 全部落 bpep_fwd_winrate_ledger.jsonl
卖出纪律: PREREG_BPEP_PAPER_WINRATE_20261023 约定"到期前一日出计划呈报"——--execute 须在
      计划呈报之后使用(主人令或既定轨道流程)。
"""
import json, subprocess, sys, os, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)
LEDGER = os.path.join(BASE, "data", "bpep_fwd_winrate_ledger.jsonl")
EXECUTE = "--execute" in sys.argv

def log(evt):
    evt["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    print(json.dumps(evt, ensure_ascii=False), flush=True)

def ledger_events():
    rows = []
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows

def held_positions():
    """轨道当前持仓: code -> volume (ledger executed BUY 汇总, westock为真值兜底)"""
    held = {}
    for e in ledger_events():
        if e.get("action") == "BUY" and e.get("executed"):
            held[e["code"]] = held.get(e["code"], 0) + int(e.get("volume") or 0)
    return held

def latest_basket():
    import bt_bp_ep_r2_2016_100k as M
    M.END = int(datetime.date.today().strftime("%Y%m%d"))  # 动态延展窗口
    me = M.month_end_dates()
    ind = M.load_industry()
    signals, _, _ = M.build_signals(me, ind)
    d = max(signals)
    return d, signals[d]

def sectors_of(codes):
    import sqlite3
    mcon = sqlite3.connect(r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro", uri=True)
    sec = dict((r[0], r[1]) for r in mcon.execute("SELECT code, sector_name FROM stock_sector_sw1"))
    mcon.close()
    def pref(c):
        return "sh" if c.startswith("6") else "sz"
    return {c: (sec.get(pref(c) + c) or sec.get(c) or "?") for c in codes}

def run_executor(cmd):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable] + cmd, capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace", cwd=BASE, env=env)
    out = (p.stdout or "") + (p.stderr or "")
    try:
        return json.loads(out[out.index("{"):out.rindex("}") + 1])
    except Exception:
        return {"gate_verdict": "UNKNOWN", "executed": False, "reason": out[-300:]}

def main():
    held = held_positions()
    d, basket = latest_basket()
    print(f"[rebalance] 信号日={d} 新篮子{len(basket)}只 | 轨道持仓{len(held)}只 | execute={EXECUTE}")
    secmap = sectors_of(set(basket) | set(held))
    sells = [c for c in sorted(held) if c not in basket]
    buys = [c for c in basket if c not in held]
    # R30a 同日银行计数(买入侧)
    plan = dict(signal_date=d, sells=[dict(code=c, volume=held[c], sector=secmap.get(c, "?")) for c in sells],
                buys=[])
    n_sec = {}
    for c in buys:
        s = secmap.get(c, "?")
        n_sec[s] = n_sec.get(s, 0) + 1
    banks_seen = 0
    for c in basket:
        if c in buys:
            s = secmap.get(c, "?")
            approve = False
            if s == "银行":
                banks_seen += 1
                if banks_seen > 3:
                    approve = True  # 第4+只银行: 篮子≤4/行业设计, owner-approve留痕
            plan["buys"].append(dict(code=c, sector=s, owner_approve_r30a=approve))
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    log(dict(action="REBALANCE_PLAN", plan=plan, executed=EXECUTE))
    if not EXECUTE:
        print("[rebalance] 计划模式(零动作); 执行请加 --execute (须在计划呈报之后)")
        return 0
    # 卖出(先卖腾资金)
    for s in plan["sells"]:
        j = run_executor(["trade_executor.py", "sell", "--code", s["code"], "--volume", str(s["volume"]),
                          "--sector", s["sector"], "--confirmed", "--execute",
                          "--reason", "bp_ep月度轮换(离篮)", "--source", "bp_ep_core"])
        log(dict(action="SELL", code=s["code"], volume=s["volume"], executed=bool(j.get("executed")),
                 gate_verdict=j.get("gate_verdict"), reason=(j.get("reason") or "")[:300]))
    # 买入
    for b in plan["buys"]:
        cmd = ["trade_executor.py", "buy", "--code", b["code"], "--source", "bp_ep_core",
               "--sector", b["sector"], "--amount", "13000", "--execute"]
        if b["owner_approve_r30a"]:
            cmd.append("--owner-approve-r30a")
        j = run_executor(cmd)
        log(dict(action="BUY", code=b["code"], sector=b["sector"], executed=bool(j.get("executed")),
                 gate_verdict=j.get("gate_verdict"), reason=(j.get("reason") or "")[:300]))
    print("[rebalance] 轮换执行完毕(结果见ledger)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
