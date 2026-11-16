# -*- coding: utf-8 -*-
"""revival_reconcile_20261028.py — 复活批次对账器（零交易权，只读）

用法: python analysis/revival_reconcile_20261028.py
先调 write_positions_snapshot.py 刷新 westock 真值快照(fail-closed)，
再对照 executor 台账 revival_*/w2_* 源当日提交 → 输出各方法 提交/成交/被拒/未成交 明细。
"""
from __future__ import annotations
import json, os, re, subprocess, sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
TODAY = "20261028"
PLANS = [("W1", os.path.join(ROOT, "data", "revival_picks_20261028.json")),
         ("W2", os.path.join(ROOT, "data", "revival_w2_picks_20261028.json")),
         ("满弦", os.path.join(ROOT, "data", "manxian5_plan_20261029.json")),
         ("双优", os.path.join(ROOT, "data", "shuangyou5_plan_20261029.json")),
         ("破局", os.path.join(ROOT, "data", "swing_bos_plan_latest.json"))]


def main():
    # 1) westock 真值快照刷新（唯一写入方，fail-closed）
    r = subprocess.run([sys.executable, os.path.join(ROOT, "write_positions_snapshot.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT, timeout=120)
    print("[snapshot]", (r.stdout or r.stderr).strip()[:120])
    if r.returncode != 0:
        print("⛔ 快照刷新失败(fail-closed)，对账中止——不得用陈旧真值对账")
        return 3

    # 2) 台账当日提交
    ledger = []
    for line in open(os.path.join(ROOT, "data", "executor_orders.jsonl"), encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r2 = json.loads(line)
        except Exception:
            continue
        if str(r2.get("trade_date")) == TODAY and str(r2.get("source", "")).startswith(
                ("revival_", "w2_", "xushi_", "swing_")):
            ledger.append(r2)

    # 3) 计划名单
    planned = {}
    for wave, p in PLANS:
        if not os.path.exists(p):
            alt = p.replace(".json", ".executed")
            if os.path.exists(alt):
                p = alt
        try:
            doc = json.load(open(p, encoding="utf-8"))
            for src, m in doc["methods"].items():
                s = {"w2_bpep_weekly": "revival_bpep_weekly", "w2_indmom_tail": "revival_hy_tail",
                     "w2_mvp_fusion": "revival_mvp_fusion"}.get(src, src)
                for x in m["picks"]:
                    planned[(s, x["code"])] = (wave, x["name"], x["price"])
        except Exception as e:
            print(f"⚠️ {p} 读取失败: {e}")

    # 4) westock 成交（当日 B/S 快照）
    doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
    filled = set()
    for p in doc["positions"]:
        c = re.sub(r"^(sz|sh|bj)", "", str(p["code"]).lower()).zfill(6)
        filled.add(c)

    # 5) 汇总
    submitted = defaultdict(list)
    for r2 in ledger:
        submitted[(r2.get("source"), r2.get("code"))].append(r2)
    by_src = defaultdict(lambda: {"plan": 0, "sub": 0, "filled": 0, "reject": 0, "open": 0})
    miss = []
    for (src, code), (wave, name, price) in sorted(planned.items()):
        g = by_src[src]
        g["plan"] += 1
        rows = submitted.get((src, code))
        if not rows:
            miss.append((src, code, name, "台账无提交行"))
            continue
        st = rows[-1].get("status", "")
        g["sub"] += 1
        if st == "FILLED" or code in filled:
            g["filled"] += 1
        elif st == "REJECT" or "REJECT" in str(rows[-1]):
            g["reject"] += 1
        else:
            g["open"] += 1
    print(f"\n=== {TODAY} 复活批次对账（提交口径=executor台账, 成交口径=westock快照） ===")
    print(f"{'方法':<24}{'计划':>4}{'提交':>4}{'成交':>4}{'被拒':>4}{'挂单':>4}")
    tot = defaultdict(int)
    for src in sorted(by_src):
        g = by_src[src]
        for k in tot:
            tot[k] += g[k]
        print(f"{src:<24}{g['plan']:>4}{g['sub']:>4}{g['filled']:>4}{g['reject']:>4}{g['open']:>4}")
    print(f"{'TOTAL':<24}{tot['plan']:>4}{tot['sub']:>4}{tot['filled']:>4}{tot['reject']:>4}{tot['open']:>4}")
    if miss:
        print("\n⚠️ 计划在册但台账无提交行（闸门批内终止/脚本未达）:")
        for m_ in miss:
            print("  ", m_)
    print("\n注: 成交口径为westock持仓快照包含判定，当日卖出回转或异步未回报以 fill_confirm_monitor 为准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
