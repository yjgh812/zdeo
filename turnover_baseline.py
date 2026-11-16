# -*- coding: utf-8 -*-
"""turnover_baseline.py — L5周转率-收益前沿·第一阶段现状基线（纯测量，零交易权）
================================================================================
2026-09-29（SOP四十九-AB，接四十九-U欠账「L系列余L1/L5」）。
L5验收线=qlib回测前沿曲线+2026影子验证（重引擎，另回合）；本脚本=前沿画图前的事实底座：
真实成交(westock真值经attribution.db trade_records镜像)的 周转率/成本拖累 现状测量。
口径：费率假设=佣金万2.5双边+印花税卖出0.05%（2023-08后税率）→买0.025%/卖0.075%；
账户规模取敏感性区间（80/88/95万，锚=9/23~9/28 westock快照87.4~90.9万）。
边界：无账户日度净值序列，规模用区间而非精确值（如实标注）；费率为估算口径非券商实单。
输出：data/turnover_baseline_20260929.json + reports/turnover_baseline_20260929.md
"""
import json
import sqlite3
import sys
from collections import defaultdict

DB = r"file:D:/zcodesj/attribution/attribution.db?mode=ro"
CAPS = [80e4, 88e4, 95e4]
BUY_COST, SELL_COST = 0.00025, 0.00075


def main():
    con = sqlite3.connect(DB, uri=True)
    rows = con.execute("SELECT trade_date, action, amount, signal_type, subsystems FROM trade_records "
                       "ORDER BY trade_date").fetchall()
    con.close()
    if not rows:
        return print("[TURN-BASE][FAIL] trade_records 空")

    monthly = defaultdict(lambda: {"buy_amt": 0.0, "sell_amt": 0.0, "buy_n": 0, "sell_n": 0})
    src_split = defaultdict(lambda: [0.0, 0.0])  # executor系 vs 其他(盘感/manual) 买入额
    total_buy = total_sell = 0.0
    total_cost = 0.0
    for d, action, amount, sig, subs in rows:
        m = str(d)[:6]
        amt = float(amount or 0)
        act = (action or "").lower()
        executor_side = "trade_executor" in (subs or "")
        if act in ("buy", "买入", "b"):
            monthly[m]["buy_amt"] += amt
            monthly[m]["buy_n"] += 1
            total_buy += amt
            total_cost += amt * BUY_COST
            src_split["executor" if executor_side else "其他"][0] += amt
        elif act in ("sell", "卖出", "s"):
            monthly[m]["sell_amt"] += amt
            monthly[m]["sell_n"] += 1
            total_sell += amt
            total_cost += amt * SELL_COST
            src_split["executor" if executor_side else "其他"][1] += amt
    months = sorted(monthly)
    n_months = len(months)
    trade_days = len({str(r[0]) for r in rows})
    out = {"version": "turnover_baseline_v1", "asof": "20260929", "window": [rows[0][0], rows[-1][0]],
           "n_trades": len(rows), "trade_days_observed": trade_days,
           "annualize_factor": round(244 / trade_days, 3),
           "total_buy": round(total_buy), "total_sell": round(total_sell),
           "cost_assumption": {"buy": BUY_COST, "sell": SELL_COST},
           "total_cost_est": round(total_cost), "months": {}}
    for m in months:
        mm = monthly[m]
        out["months"][m] = {**mm, "buy_amt": round(mm["buy_amt"]), "sell_amt": round(mm["sell_amt"])}
    sens = {}
    annualize = 244.0 / trade_days  # 交易日口径年化（partial月不失真）
    for cap in CAPS:
        cap = float(cap)
        annual_turnover = (total_buy + total_sell) / cap * annualize
        drag_pct = total_cost / cap * 100
        sens[int(cap / 1e4)] = {"annual_turnover_x": round(annual_turnover, 2),
                                "cost_drag_pct_of_capital": round(drag_pct, 3),
                                "period_turnover_x": round((total_buy + total_sell) / cap, 2)}
    out["sensitivity_by_capital_wan"] = sens
    out["src_split"] = {k: {"buy": round(v[0]), "sell": round(v[1])} for k, v in src_split.items()}
    path = r"D:/zcodesj/data/turnover_baseline_20260929.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    lines = [
        "# L5 周转率-收益前沿·第一阶段现状基线（纯测量） 20260929",
        "",
        f"> 数据=trade_records真实成交镜像（{out['window'][0]}~{out['window'][0] and out['window'][1]}，{len(rows)}笔）；费率=买0.025%/卖0.075%估算；账户规模=敏感性区间（80/88/95万），无日度净值序列为口径限制（如实标注）。",
        "",
        "## 一、原始数据",
        "",
        f"- 总买入 {total_buy/1e4:.1f}万 / 总卖出 {total_sell/1e4:.1f}万 / 估算总成本 {total_cost:.0f}元（观测窗{trade_days}个交易日）",
        "- 月度：",
    ]
    for m in months:
        mm = out["months"][m]
        lines.append(f"  - {m}: 买入{mm['buy_amt']/1e4:.1f}万({mm['buy_n']}笔) 卖出{mm['sell_amt']/1e4:.1f}万({mm['sell_n']}笔)")
    lines += ["", "## 二、换手与成本拖累（敏感性，交易日口径年化）", "",
              "| 账户规模(万) | 观测窗换手 | 年化换手 | 成本拖累(%规模) |", "|---|---|---|---|"]
    for k, v in sens.items():
        lines.append(f"| {k} | {v['period_turnover_x']}x | {v['annual_turnover_x']}x | {v['cost_drag_pct_of_capital']}% |")
    lines += [
        "",
        "## 三、通道结构",
        "",
        f"- executor系(合规通道): 买{out['src_split'].get('executor',{}).get('buy',0)/1e4:.1f}万/卖{out['src_split'].get('executor',{}).get('sell',0)/1e4:.1f}万；"
        f"其他通道(盘感/手动历史批): 买{out['src_split'].get('其他',{}).get('buy',0)/1e4:.1f}万/卖{out['src_split'].get('其他',{}).get('sell',0)/1e4:.1f}万。",
        "",
        "## 四、结论与边界",
        "",
        f"- 结论【测量，非预判】：观测窗{trade_days}个交易日实盘换手与成本拖累如上表；88万锚下年化换手≈{sens.get(88,{}).get('annual_turnover_x')}x，**观测窗成本拖累≈{sens.get(88,{}).get('cost_drag_pct_of_capital')}%规模（年化外推≈{sens.get(88,{}).get('cost_drag_pct_of_capital',0)*annualize:.2f}%）**——高换手的复利拖累为主要结构性成本。",
        f"- 通道结构定量：executor合规通道仅占买入额{out['src_split'].get('executor',{}).get('buy',0)/max(total_buy,1)*100:.0f}%——与9/25执行差量化（裸奔批-22.4万）互证，周转与亏损集中于合规通道之外（历史批）。",
        "- 边界：①规模无日度净值序列（区间敏感性替代）；②费率为估算口径；③前沿网格（最优周转档位）需qlib引擎反事实重跑=另回合（L5验收线不变）。",
        "- L1边界记录（本轮不启动设计）：仓位结构语义=OWNER保留域（PD-015明示），且L1原定义的情绪相位输入轴已被M6证伪（四十九-M：相位对次日方向/结构无可交易判别力）——L1若立项须换轴（regime/env+回撤类输入）并与PD-012/PD-015在10/1合流，属主人裁决域。",
    ]
    md = r"D:/zcodesj/reports/turnover_baseline_20260929.md"
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[TURN-BASE] n={len(rows)} buy={total_buy/1e4:.1f}万 sell={total_sell/1e4:.1f}万 cost={total_cost:.0f}元 -> {path}")
    for k, v in sens.items():
        print(f"  cap {k}万: 观测窗换手{v['period_turnover_x']}x 年化{v['annual_turnover_x']}x 拖累{v['cost_drag_pct_of_capital']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
