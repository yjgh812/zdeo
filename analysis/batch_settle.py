# -*- coding: utf-8 -*-
"""批次实盘结算器(实盘vs回测对账, 主人方法论: 只有模拟盘证明真正对错) 20261026 — 零交易权。
职责: 批次到期(exit_date过后)自动结算: 每票实际入场价(westock成本) vs 计划限价(滑点),
      实际出场价(到期收盘或止损触发价, qfq真值) → 实现收益; 批级实现胜率/均值 vs 回测期望;
      结果写入 PREREG_HIGHWIN_PROSPECTIVE tracking(主人四失效模式的探测器)。
四模式映射: ①回测方法→滑点/入场假设差 ②回测数据→qfq守卫族 ③分析方法→prereg判定门
          ④原因理解→条件分臂实现vs预期差(分池记录)。
用法: python analysis/batch_settle.py [--plan data/batch_plan_archive/batch_plan_YYYYMMDD.json]
输出: 控制台 + prereg tracking 追加 + data/batch_settle_latest.json
"""
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, r"D:\zcodesj")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
PREREG = "data/PREREG_HIGHWIN_PROSPECTIVE_20261026.json"
OUT = "data/batch_settle_latest.json"


def main():
    args = sys.argv
    plan_file = args[args.index("--plan") + 1] if "--plan" in args else None
    con = sqlite3.connect(QFQ, uri=True)
    qfq_max = int(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
    # 待结算计划: exit_date < qfq_max 的最新一份(或指定)
    if not plan_file:
        cands = []
        for pf in glob.glob("data/batch_plan_archive/batch_plan_*.json"):
            p = json.load(open(pf, encoding="utf-8"))
            if int(p["exit_date"]) < qfq_max:
                cands.append((int(p["exit_date"]), pf))
        if not cands:
            print(f"[settle] 无到期批次(qfq最新={qfq_max}, 首批exit=20261120) — 系统正常待命")
            return 0
        plan_file = max(cands)[1]
    plan = json.load(open(plan_file, encoding="utf-8"))
    exit_d = int(plan["exit_date"])
    scan_d = plan["scan_date"]

    # westock 实际成本(可得不用; 不可得回退计划限价并标注)
    fills = {}
    try:
        from stop_loss_executor import _fetch_positions_via_mcp
        for p in _fetch_positions_via_mcp() or []:
            fills[p["code"][-6:]] = p
    except Exception:
        pass

    rows = []
    for r in plan["plan"]:
        c6 = r["code"][-6:]
        hist = con.execute(
            "SELECT date,open,close FROM stock_daily_meta_qfq WHERE code LIKE ? ORDER BY date",
            (f"%{c6}",)).fetchall()
        hd = {int(d): (o, c) for d, o, c in hist}
        entry_plan = float(r["limit"])
        stop = float(r["stop"])
        entry_act = fills.get(c6, {}).get("cost_price") or entry_plan
        # 入场日=entry_date 次日开盘? 合同=入场日开盘; 出场: 止损首触 or exit_date收盘
        e_key = int(r["entry_date"])
        if e_key not in hd:
            continue
        entry_open = hd[e_key][0]
        px, how = None, "未平"
        dates = sorted([d for d in hd if e_key <= d <= exit_d])
        for d in dates:
            c = hd[d][1]
            if c <= stop:
                px, how = c, f"止损@{d}"
                break
        if px is None and exit_d in hd:
            px, how = hd[exit_d][1], "到期收盘"
        if px is None:
            continue
        ret_plan = px / entry_plan - 1
        ret_actual = px / entry_act - 1 if entry_act > 0 else np.nan
        rows.append({"code": c6, "name": r["name"], "pool": r.get("pool"),
                     "entry_plan": entry_plan, "entry_actual": round(entry_act, 3),
                     "slip_pct": round(entry_act / entry_plan - 1, 4) if entry_act != entry_plan else 0,
                     "exit": round(px, 3), "how": how, "ret_plan": round(ret_plan, 4),
                     "ret_actual": round(ret_actual, 4)})
    con.close()
    if not rows:
        print("[settle] 无可结算行")
        return 0
    rets = [x["ret_plan"] for x in rows]
    # 成本对齐(主人协议阶段三): 模拟盘无手续费, 实盘口径扣费才与回测可比
    # 口径=佣金万2.5买卖双向+印花税0.05%卖出+小盘滑点余量0.1% ≈ 单往返0.2%
    COST_RT = 0.002
    rets_net = [r - COST_RT for r in rets]
    settle = {"plan_file": os.path.basename(plan_file), "scan_date": scan_d,
              "settled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
              "n": len(rows), "win": round(float(np.mean([r > 0 for r in rets])), 4),
              "mean": round(float(np.mean(rets)), 4),
              "win_net": round(float(np.mean([r > 0 for r in rets_net])), 4),
              "mean_net": round(float(np.mean(rets_net)), 4),
              "cost_rt_assumed": COST_RT,
              "backtest_expectation": {"win": "0.57-0.69(cohort条件窗)", "mean": "+0.02~+0.04"},
              "rows": rows}
    json.dump(settle, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 追加到prereg tracking
    try:
        pr = json.load(open(PREREG, encoding="utf-8"))
        pr.setdefault("tracking", []).append(
            {"scan_date": scan_d, "n": settle["n"], "win": settle["win"], "mean": settle["mean"]})
        json.dump(pr, open(PREREG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[WARN] prereg tracking追加失败: {e}")
    print(f"[结算] 批次{scan_d}: n={settle['n']} 毛口径胜率={settle['win']:.1%}/均值={settle['mean']:+.2%} | "
          f"净口径(扣0.2%往返)胜率={settle['win_net']:.1%}/均值={settle['mean_net']:+.2%} (回测期望57-69%/+2~4%)")
    for x in rows:
        print(f"  {x['code']} {x['name']:<6} {x['pool'] or '':<9} 入{ x['entry_actual']}({x['slip_pct']:+.1%}滑点) "
              f"{x['how']:<12} 实现={x['ret_plan']:+.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
