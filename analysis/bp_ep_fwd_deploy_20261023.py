# -*- coding: utf-8 -*-
"""bp_ep 前瞻胜率验证 · 模拟盘部署引擎 (2026-10-23, 主人「继续，胜率还需模拟盘验证」授权)
篮子: bp_ep 20261021 月度 Top20 - 600035(已持仓不重复) = 19只; 源=bp_ep_core(PROVEN, 手册核心40%层)
规则: 每笔经 trade_executor buy --execute (双层防线全量闸门); R37弱档关闭日静默退出次日重试;
      002936郑州银行=篮子第4只银行 → --owner-approve-r30a WARN留痕(R30a摩擦0.2pp实测, 篮子≤4/行业设计);
      幂等: ledger 已 executed BUY 的 code 跳过; 全部动作落 data/bpep_fwd_winrate_ledger.jsonl
"""
import json, subprocess, sys, os, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj"
LEDGER = os.path.join(BASE, "data", "bpep_fwd_winrate_ledger.jsonl")
# (code, name, sector, volume, amount, owner_approve_r30a)
BASKET = [
    ("601997", "贵阳银行", "银行",     2300, 13000, False),
    ("600016", "民生银行", "银行",     3700, 13000, False),
    ("601668", "中国建筑", "建筑装饰", 2800, 13000, False),
    ("002958", "青农商行", "银行",     4500, 13000, False),
    ("002936", "郑州银行", "银行",     7600, 13000, True),
    ("601186", "中国铁建", "建筑装饰", 2100, 13000, False),
    ("600739", "辽宁成大", "医药生物", 1300, 13000, False),
    ("601390", "中国中铁", "建筑装饰", 3000, 13000, False),
    ("601800", "中国交建", "建筑装饰", 2100, 13000, False),
    ("000623", "吉林敖东", "医药生物",  700, 12000, False),
    ("600064", "南京高科", "房地产",   1600, 12500, False),
    ("600704", "物产中大", "交通运输", 2600, 12700, False),
    ("600694", "大商股份", "商贸零售",  800, 11700, False),
    ("000726", "鲁泰Ａ",   "纺织服饰", 2200, 12700, False),
    ("600827", "百联股份", "商贸零售", 1700, 12800, False),
    ("600638", "新黄浦",   "房地产",   2700, 12900, False),
    ("000544", "中原环保", "环保",     1800, 12800, False),
    ("002608", "江苏国信", "公用事业", 1800, 12700, False),
    ("000685", "中山公用", "环保",     1200, 12400, False),
]

def log(evt):
    evt["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    print(json.dumps(evt, ensure_ascii=False), flush=True)

def filled_codes():
    done = set()
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("action") == "BUY" and e.get("executed"):
                done.add(e.get("code"))
    return done

def main():
    done = filled_codes()
    todo = [(c, n, s, v, a, oa) for c, n, s, v, a, oa in BASKET if c not in done]
    if not todo:
        print("[bp_ep_fwd] 篮子已全部建仓, 无动作")
        return 0
    r37_blocks = 0
    for c, n, s, v, a, oa in todo:
        cmd = [sys.executable, os.path.join(BASE, "trade_executor.py"), "buy",
               "--code", c, "--name", n, "--source", "bp_ep_core", "--sector", s,
               "--volume", str(v), "--amount", str(a), "--execute"]
        if oa:
            cmd.append("--owner-approve-r30a")
        try:
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               encoding="utf-8", errors="replace", cwd=BASE, env=env)
        except subprocess.TimeoutExpired:
            log(dict(action="BUY", code=c, name=n, executed=False, reason="executor timeout"))
            continue
        out = (p.stdout or "") + (p.stderr or "")
        verdict, executed, reason = "UNKNOWN", False, out[-300:]
        try:
            j = json.loads(out[out.index("{"):out.rindex("}") + 1])
            verdict = j.get("gate_verdict", verdict)
            executed = bool(j.get("executed", False))
            reason = "; ".join(j.get("gate_rejects", []) or []) or j.get("reason", reason)
        except Exception:
            pass
        log(dict(action="BUY", code=c, name=n, sector=s, volume=v, amount=a,
                 gate_verdict=verdict, executed=executed, reason=reason[:400]))
        if "R37" in reason:
            r37_blocks += 1
    if r37_blocks >= len(todo) - 1:
        print(f"[bp_ep_fwd] R37弱档关闭({r37_blocks}/{len(todo)}笔被拦), 静默退出次日重试")
    else:
        print(f"[bp_ep_fwd] 本轮完成: 成功{len(done)}+本次增量见ledger, R37拦截{r37_blocks}笔")
    return 0

if __name__ == "__main__":
    sys.exit(main())
