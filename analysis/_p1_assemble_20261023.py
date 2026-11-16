#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_p1_assemble_20261023.py — P1 判别树判定装配(L2/L4 正确百分位 + 判定表落盘)。"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
P1 = os.path.join(ROOT, "reports", "dualkill_p1_replay_20261023")
L2_CSV = os.path.join(P1, "L2", "bp_ep_core_trades.csv")
L4_CSV = os.path.join(ROOT, "reports", "backtest_full_20261019_ladder_debug",
                      "bp_ep_core_trades.csv")


def metrics(path, min_entry=None):
    ts = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if min_entry and (r.get("entry_date") or "") < min_entry:
                continue
            try:
                ts.append(float(r["pnl_pct"]))
            except (KeyError, ValueError):
                continue
    n = len(ts)
    s = sorted(ts)
    wins = sorted(t for t in ts if t > 0)
    losses = sorted(t for t in ts if t <= 0)
    tail = s[::-1][:max(1, int(0.05 * n))]
    pl = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else None

    def pc(arr, q):
        return round(arr[int(q * (len(arr) - 1))], 2) if arr else None
    return {"n": n, "win": round(len(wins) / n, 4),
            "pl": round(pl, 3) if pl else None, "sum_pct": round(sum(ts), 1),
            "win_p90": pc(wins, 0.9), "win_p99": pc(wins, 0.99),
            "loss_p50": pc(losses, 0.5), "loss_p10": pc(losses, 0.1),
            "min_loss": round(s[0], 2),
            "tail5_contrib": round(sum(tail) / sum(ts), 3) if sum(ts) > 0 else None}


def main():
    l2 = metrics(L2_CSV)
    l4 = metrics(L4_CSV, "2025-01-01")
    a = json.load(open(os.path.join(P1, "p1_analytic_result.json"), encoding="utf-8"))
    minl2 = abs(l2["min_loss"])
    verdict = {
        "window": a["window"],
        "layers": {
            "L0a_信号收盘_无闸无费": a["layers"]["L0a"],
            "L0b_T1开盘_无闸无费": a["layers"]["L0b"],
            "L1_闸门_无费": a["layers"]["L1"],
            "L2_闸门_止损8pct_20td帽_含费(引擎)": l2,
            "L4_生产结构(在档引用)": l4},
        "ic_term_structure": a["ic_term_structure"],
        "l3_boundary": a["l3_boundary"],
        "protocol_judgement": {
            "L0b": "49.82%/1.173 未双杀(历史基线52.52%的弱窗下沿) → 非信号/regime层崩塌",
            "L4": "30.89%/0.921 双杀确认 → 执行/风控/出场层问题(协议判定命中)",
            "L1_minus_L0b": "闸门贡献≈中性(+0.04pp/PL+0.005)",
            "L2_vs_L1": "L2 54.0%含费含止损 vs L1 49.8%无费: 止损+20td帽结构本身不减分",
            "L4_minus_L2": ("核心损失通道=持有期结构: L4无时间帽→长持仓暴露-8%止损"
                            "(L4 loss_p10=-8.13), L2短帽止损几乎不触发(min_loss=-{}%)"
                            .format(minl2)),
            "IC20": "IC20=-0.016为负 → 期限错配假设证伪(协议判据: 20日IC为正才支持)"}}
    json.dump(verdict, open(os.path.join(P1, "p1_verdict_final.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
    md = ["# P1 判别树影子回放 判定报告(窗口 {} ~ {}, 主人批准 P1)".format(*[a["window"][0], a["window"][1]]), "",
          "> 判定(协议§一): **L0b 未双杀(49.82%/盈亏比1.173) + L4 双杀(30.89%/0.921) → 损失在执行/风控/出场层, 不在信号/regime层**", "",
          "| 层 | 口径 | n | 胜率 | 盈亏比 | loss_p10 | sum(pct) |",
          "|---|---|---|---|---|---|---|"]
    for k, v0 in verdict["layers"].items():
        v = {"n": v0.get("n", v0.get("n_trades")), "win": v0.get("win", v0.get("win_rate")),
             "pl": v0.get("pl", v0.get("pl_ratio")), "loss_p10": v0.get("loss_p10"),
             "sum_pct": v0.get("sum_pct")}
        engine = "引擎" if "引擎" in k else "解析"
        md.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            k, engine, v["n"], v["win"], v["pl"], v["loss_p10"], v["sum_pct"]))
    md += ["", "## 分层归因",
           "- L1−L0b(闸门): 中性(+0.04pp)",
           "- L2−L1(止损+20td帽+费率): 引擎口径54.0%不减分",
           "- **L4−L2(持有期结构): 核心损失通道** — L4 无时间帽长持仓暴露 -8% 止损"
           "(L4 loss_p10=-8.13%), L2 短帽下止损几乎不触发(min_loss=-{:.2f}%)".format(minl2),
           "- 与 #851『先跌后涨中位-4.49%被硬止损打掉』机理同族; 与 10/19 双杀协议『L0b正常但L4差→执行/风控/出场』判定一致",
           "", "## IC期限结构",
           "- IC1=+0.0093 / IC5=-0.0103 / IC20=-0.0162 (139采样日) → **期限错配证伪**(20日IC为负)",
           "", "## 边界与口径",
           "- L3(趋势走坏卖出): bp_ep回测腿无此组件(Week-0账本三退出可证), 不伪造; L4−L2 归因调仓/持有结构",
           "- 引擎 max_holding_bars 实为**日历日**(strategy.py:94 .days), L2 用 29 日历日≈20 交易日; S-5 变体族(60/90/120)历史语义同此注记",
           "- L2/L4 批次结构不同(300 vs 123 笔): L4 长持仓跨月重置少出笔, 胜率不可与 L2 直比, 结论以方向为准",
           "- 全部 pnl_pct 单位=百分数; 零交易权纯呈现, 判定呈报主人"]
    open(os.path.join(P1, "p1_verdict.md"), "w", encoding="utf-8").write("\n".join(md))
    print("L2:", json.dumps(l2, ensure_ascii=False))
    print("L4:", json.dumps(l4, ensure_ascii=False))
    print("verdict written:", os.path.join(P1, "p1_verdict.md"))


if __name__ == "__main__":
    main()
