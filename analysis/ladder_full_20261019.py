# -*- coding: utf-8 -*-
"""P1判别树全景聚合（L0a/L0a_open/L0b/L1/L2/L4 × 2024/2025/2026），脚本直出"""
import csv, json, os
from collections import defaultdict

R = "D:/zcodesj/reports"
def year_stats(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    by = defaultdict(list)
    for r in rows:
        by[r["买入日期"][:4]].append(float(r["盈亏金额"]))
    out = {}
    for y, v in by.items():
        wins = [x for x in v if x > 0]
        losses = [x for x in v if x < 0]
        aw = sum(wins)/len(wins) if wins else 0
        al = sum(losses)/len(losses) if losses else 0
        # 右尾: Top5%盈利单贡献(金额)
        srt = sorted(v, reverse=True)
        k = max(1, int(len(srt)*0.05))
        out[y] = {"n": len(v), "win": round(len(wins)/len(v)*100, 2),
                  "pl": round(aw/abs(al), 3) if al else None,
                  "avg": round(sum(v)/len(v), 1),
                  "tail5": round(sum(srt[:k]), 0)}
    return out

l0a = json.load(open("D:/zcodesj/analysis/ladder_L0a_20261019_out.json", encoding="utf-8"))
l0a_open = json.load(open("D:/zcodesj/analysis/ladder_L0a_open_20261019_out.json", encoding="utf-8"))
l0b = year_stats(f"{R}/backtest_full_20261019_ladder_L0b/bp_ep_core_trades_full.csv")
l1 = year_stats(f"{R}/backtest_full_20261019_ladder_L1/bp_ep_core_trades_full.csv")
l2 = year_stats(f"{R}/backtest_full_20261019_ladder_L2/bp_ep_core_trades_full.csv")
l4 = year_stats(f"{R}/backtest_full_20261018_dose_n15/bp_ep_core_trades_full.csv")

result = {"L0a_close": l0a, "L0a_open": l0a_open, "L0b": l0b, "L1": l1, "L2": l2, "L4_prod": l4}
json.dump(result, open("D:/zcodesj/analysis/ladder_full_20261019_out.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

md = ["# P1 判别树全景（bp_ep，2024-2026）", "",
      "| 层 | 口径 | 段 | n | 胜率% | 盈亏比 | avg元 | Top5%右尾贡献元 |",
      "|---|---|---|---|---|---|---|---|"]
labels = {
    "L0a_close": "L0a 信号日收盘买20日(raw名单,纯价格)",
    "L0a_open": "L0a_open T+1开盘买20交易日(raw名单,纯价格)",
    "L0b": "L0b raw名单+引擎(涨停约束/成本/20自然日)",
    "L1": "L1 = L0b+闸门(close<=30生产名单)",
    "L2": "L2 = L1+止损(8%)",
    "L4_prod": "L4 = L2+月度换仓+熔断10%(=生产)",
}
for arm, lab in labels.items():
    d = result[arm]
    for y in ["2024", "2025", "2026"]:
        s = d.get(y) or d.get(int(y) if arm in ("L0a_close", "L0a_open") else y)
        if not s:
            continue
        win = s.get("win_pct", s.get("win"))
        pl = s.get("pl_ratio", s.get("pl"))
        avg = s.get("avg_r20", s.get("avg", s.get("avg_r20")))
        tail = s.get("tail5", s.get("p90", "-"))
        md.append(f"| {lab} | | {y} | {s['n']} | {win} | {pl} | {avg} | {tail} |")
    md.append("")
open("D:/zcodesj/analysis/ladder_full_20261019_out.md", "w", encoding="utf-8").write("\n".join(md))
print("\n".join(md))
