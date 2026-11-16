# -*- coding: utf-8 -*-
"""revival_trim_sell_20261028.py — ≤5上限收敛卖出执行器 v2（主人令20261028两度申明）

收敛两组:
  A) method_picks 20→5: 保留最先入场头排5只(001234泰慕士/603949雪龙集团/603668天马科技/300380安硕信息/603638艾迪精密)
  B) type_sample 四臂 12→5(主人令:四臂合并视为一个方法): 保留每臂第一名+臂序第5
     (600522中天科技/300059东方财富/601166兴业银行/000498山东路桥/000938紫光股份)
卖出=westock真值持仓量; 限价=qfq昨收×0.98成交垫; 幂等flag; 只处理持仓中实际存在的票。
"""
from __future__ import annotations
import json, os, subprocess, sys, datetime, re

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
TRIMS = [
    {"srcs": {"method_picks"},
     "keep": {"001234", "603949", "603668", "300380", "603638"},
     "tag": "A_method_picks"},
    {"srcs": {"type_sample_B_oversold", "type_sample_C_starter",
              "type_sample_D_control", "type_sample_F_driver"},
     "keep": {"600522", "300059", "601166", "000498", "000938"},
     "tag": "B_type_sample"},
]
FLAG = os.path.join(ROOT, "data", "revival_trim_sell_done_20261028.flag")
LOG = os.path.join(ROOT, "data", "revival_trim_sell_log_20261028.jsonl")


def norm(c):
    c = re.sub(r"^(sz|sh|bj)", "", str(c).lower())
    return c.zfill(6) if c.isdigit() else c


def main():
    if os.path.exists(FLAG):
        print("[SKIP] 已执行, 幂等退出")
        return 0
    sys.path.insert(0, os.path.join(ROOT, "analysis"))
    from revival_scanner_20261027 import load_kline
    series, dates = load_kline()
    doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
    src_of = {}
    for line in open(os.path.join(ROOT, "data", "executor_orders.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("action") == "BUY" and r.get("code"):
            src_of[norm(r["code"])] = r.get("source") or ""
    results = []
    for grp in TRIMS:
        for p in doc["positions"]:
            c = norm(p["code"])
            if src_of.get(c) not in grp["srcs"] or c in grp["keep"]:
                continue
            qty = int(p.get("quantity") or 0)
            rows = series.get(c)
            prev_close = rows[-1][4] if rows else None
            if qty <= 0 or not prev_close:
                print(f"[SKIP] {grp['tag']} {p['name']} qty={qty}")
                continue
            limit = round(prev_close * 0.98, 2)
            cmd = [sys.executable, os.path.join(ROOT, "trade_executor.py"), "sell",
                   "--code", c, "--name", p["name"], "--volume", str(qty),
                   "--price", str(limit), "--source", "manual_confirmed",
                   "--note", f"主人令20261028每方法≤5只上限收敛({grp['tag']},保留头排/每臂第一5只)",
                   "--execute"]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=ROOT, timeout=180)
            tail = ((r.stdout or "") + (r.stderr or ""))[-300:].replace("\n", " | ")
            status = next((k for k in ("FILLED", "OPEN", "REJECT", "SKIP", "ERROR") if k in tail), "UNKNOWN")
            line_j = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "group": grp["tag"],
                      "code": c, "name": p["name"], "qty": qty, "limit": limit,
                      "rc": r.returncode, "status": status, "tail": tail}
            results.append(line_j)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(line_j, ensure_ascii=False) + "\n")
            print(f"SELL[{grp['tag']}] {p['name']:<8} {qty}股 @{limit} rc={r.returncode} {status}")
    with open(FLAG, "w", encoding="utf-8") as f:
        f.write(f"done {datetime.datetime.now().isoformat()} sells={len(results)}\n")
    print(f"[DONE] 收敛卖出提交 {len(results)} 笔（method_picks留5 + type_sample留5）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
