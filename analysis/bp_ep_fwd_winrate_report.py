# -*- coding: utf-8 -*-
"""bp_ep 前瞻轨道 · 胜率报表 (2026-10-23)
用法: python analysis/bp_ep_fwd_winrate_report.py
口径: PREREG_BPEP_PAPER_WINRATE_20261023 (win=已平仓pnl>0, ≥30笔下统计结论, 基线64.1%)
现状: 卖出事件出现前, 报表输出建仓进度与协议提示(诚实呈现, 不伪造胜率)。
"""
import json, os, sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
LEDGER = os.path.join(BASE_LEDGER := r"D:\zcodesj\data\bpep_fwd_winrate_ledger.jsonl")
rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8")] if os.path.exists(LEDGER) else []
buys = [r for r in rows if r.get("action") == "BUY" and r.get("executed")]
sells = [r for r in rows if r.get("action") == "SELL" and r.get("executed")]
rej = Counter()
for r in rows:
    if r.get("action") == "BUY" and not r.get("executed"):
        reason = r.get("reason") or ""
        rej["时段闸" if ("09:30" in reason or "时段" in reason) else
            ("R37" if "R37" in reason else "其他")] += 1
print("=== bp_ep 前瞻胜率轨道 报表 ===")
print(f"ledger事件: {len(rows)} | 已成交BUY: {len(buys)} | 已成交SELL: {len(sells)}")
print(f"未成交拦截分布: {dict(rej)}")
if len(buys) < 19:
    print(f"建仓进度: {len(buys)}/19 (R37情绪档/时段闸开放后自动补齐, schtasks BpEpFwdDeploy_0937)")
if len(sells) == 0:
    print("胜率: n/a (尚无平仓事件; 月末轮换后开始积累, ≥30笔出统计结论 vs 基线64.1%)")
else:
    print("胜率: 平仓事件已出现, 逐笔pnl匹配需westock成交回报对账(月度检查点执行)")
print(f"协议: PREREG_BPEP_PAPER_WINRATE_20261023 | ≥55%继续 / 40-55%缩容 / <40%暂停复盘")
