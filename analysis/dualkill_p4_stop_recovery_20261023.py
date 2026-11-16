#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dualkill_p4_stop_recovery_20261023.py — 10/19双杀协议 P4 可行补完: 止损笔退出后修复率分析
==========================================================================================
背景: 协议建议1原设计依赖 L3「趋势走坏卖出」组件, 该组件在bp_ep回测腿不存在(不伪造)。
本脚本做可行版本=协议判定所需的止损格反事实: 对 L4 2025-2026 全部 stop_loss 退出笔,
统计退出后20交易日收益(退出价→20日后收盘), 对照非止损退出笔同期基线。
机理检验: 若止损笔退出后系统性正收益(修复率>50%) → 「先跌后涨被止损打掉」在弱窗逐笔实证
  (与#851机理/P1之L2-L4对照三方互证); 若退出后继续跌 → 止损在弱窗是有效保护。
零交易权纯呈现; 日频近似(退出后路径以收盘计), 如实注明。
"""
import csv
import json
import os
import sqlite3
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
L4_CSV = os.path.join(ROOT, "reports", "backtest_full_20261019_ladder_debug",
                      "bp_ep_core_trades.csv")
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT_DIR = os.path.join(ROOT, "reports", "dualkill_p2_gap_vwap_20261023")
FWD_BARS = 20


def main():
    trades = []
    with open(L4_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("entry_date") or "") < "2025-01-01":
                continue
            try:
                trades.append({"code": str(r["symbol"]).zfill(6),
                               "exit": r["exit_date"].replace("-", ""),
                               "exit_px": float(r["exit_price"]),
                               "pnl": float(r["pnl_pct"]),
                               "reason": (r.get("exit_reason") or "").strip(),
                               "hold": float(r.get("holding_bars") or 0)})
            except (KeyError, ValueError):
                continue

    con = sqlite3.connect(QFQ, uri=True)
    codes = sorted({t["code"] for t in trades})
    hist = {}
    for c in codes:
        hist[c] = con.execute("SELECT date, close FROM stock_daily_meta_qfq "
                              "WHERE code=? ORDER BY date", (c,)).fetchall()
    con.close()

    def fwd20(code, exit_day, exit_px):
        dates = [r[0] for r in hist.get(code, [])]
        if exit_day not in dates:
            return None
        k = dates.index(exit_day)
        if k + FWD_BARS >= len(dates):
            return None
        tgt = rows_close(hist[code], dates[k + FWD_BARS])
        if not tgt:
            return None
        return (tgt / exit_px - 1) * 100

    def rows_close(rows, d):
        for r in rows:
            if r[0] == d:
                return r[1]
        return None

    stop, other = [], []
    hold2 = []
    for t in trades:
        f = fwd20(t["code"], t["exit"], t["exit_px"])
        rec = {"code": t["code"], "exit": t["exit"], "pnl": t["pnl"],
               "fwd20_after_exit": round(f, 2) if f is not None else None}
        if t["reason"] == "stop_loss":
            stop.append(rec)
        else:
            other.append(rec)
        if t["reason"] == "stop_loss" and t["hold"] <= 2:
            hold2.append(rec)

    def agg(recs):
        vals = [r["fwd20_after_exit"] for r in recs if r["fwd20_after_exit"] is not None]
        if not vals:
            return {"n_with_fwd": 0}
        pos = [v for v in vals if v > 0]
        s = sorted(vals)
        return {"n_with_fwd": len(vals), "win_after": round(len(pos) / len(vals), 3),
                "avg_after": round(sum(vals) / len(vals), 2),
                "median_after": round(s[len(s) // 2], 2),
                "worst_after": round(s[0], 2), "best_after": round(s[-1], 2)}

    out = {
        "window": "2025-01-01~2026-10-22(L4在档)", "n_total": len(trades),
        "stop_loss_cell": {"n": len(stop), "post_exit_20d": agg(stop),
                           "detail": [r for r in stop if r["fwd20_after_exit"] is not None]},
        "non_stop_cell": {"n": len(other), "post_exit_20d": agg(other)},
        "stop_hold_le2d": {"n": len(hold2), "post_exit_20d": agg(hold2)},
        "mechanism_rule": "止损笔退出后20日修复率>50%且均值显著为正→「先跌后涨被止损打掉」逐笔实证; "
                          "退出后继续跌→止损在弱窗为有效保护",
        "boundary": "日频收盘近似; 未计退出后费用; fwd20缺尾部样本者剔除",
        "zero_trade_power": True}
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(out, open(os.path.join(OUT_DIR, "p4_stop_recovery_result.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    compact = {"stop": out["stop_loss_cell"]["post_exit_20d"],
               "non_stop": out["non_stop_cell"]["post_exit_20d"],
               "hold_le2d": out["stop_hold_le2d"]["post_exit_20d"]}
    print(json.dumps(compact, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
