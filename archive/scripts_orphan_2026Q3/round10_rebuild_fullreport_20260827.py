# -*- coding: utf-8 -*-
"""
round10 FULL_REPORT.html 产物重建 (2026-08-28)
====================================================
目的: 首跑时组合段受 date 键失配 bug 影响显示 0.00%(源已修); 本脚本从落盘产物
(metrics/equity/trades/yearly/修正组合CSV) 重建 FULL_REPORT.html, 复用原脚本的
generate_charts + build_comprehensive_report 生成函数——非手改生成物, 是生成器重跑。
只读消费, 不重算回测。
"""
import sys, os, json, warnings
import pandas as pd
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
_src = open(SRC, encoding="utf-8").read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns = {}
exec(compile(_src[:_cut], SRC, "exec"), _ns)

OUT = _ns["OUTPUT_DIR"]
ALL = ["bp+ep_core", "breakout_signal", "v375_signal", "p2_signal", "composite_signal", "ml_signal"]
CN2EN = {"买入日期": "entry_date", "卖出日期": "exit_date", "股票代码": "symbol",
         "股票名称": "symbol_name", "买入价格": "entry_price", "卖出价格": "exit_price",
         "持股数量": "shares", "盈亏金额": "pnl", "盈亏比例%": "pnl_pct",
         "买入决策理由": "entry_reason", "卖出触发理由": "exit_reason",
         "交易成本": "cost", "持仓天数": "holding_bars"}


def load_strategy(name):
    prefix = name.replace("+", "_").replace(" ", "_")
    with open(os.path.join(OUT, f"{prefix}_summary.json"), encoding="utf-8") as f:
        metrics = json.load(f).get("summary", {})
    ec = pd.read_csv(os.path.join(OUT, f"{prefix}_equity.csv"))
    ec = ec.rename(columns={"value": "equity"})[["date", "equity"]]
    tdf = pd.read_csv(os.path.join(OUT, f"{prefix}_trades_full.csv"))
    tdf = tdf.rename(columns={v: k for k, v in CN2EN.items() if v in tdf.columns})
    trades = tdf.to_dict("records")
    return {"metrics": metrics, "equity_curve": ec, "trades": trades,
            "trades_with_reason": trades, "name": name}


def build_diagnosis(results):
    diag = {}
    for name, res in results.items():
        tr = res["trades"]
        if not tr:
            continue
        win = [t for t in tr if float(t.get("pnl") or 0) > 0]
        loss = [t for t in tr if float(t.get("pnl") or 0) <= 0]
        wpc = [float(t.get("pnl_pct") or 0) for t in win]
        lpc = [float(t.get("pnl_pct") or 0) for t in loss]
        big = [t for t in loss if float(t.get("pnl_pct") or 0) <= -5]
        by_reason = {}
        for t in tr:
            r = str(t.get("exit_reason") or "unknown")
            by_reason[r] = by_reason.get(r, 0) + 1
        diag[name] = {
            "n_total": len(tr), "n_win": len(win), "n_loss": len(loss),
            "win_rate": len(win) / len(tr) * 100 if tr else 0,
            "avg_win_pct": float(np.mean(wpc)) if wpc else 0,
            "avg_loss_pct": float(np.mean(lpc)) if lpc else 0,
            "avg_win_hold": float(np.mean([t.get("holding_bars") or 0 for t in win])) if win else 0,
            "avg_loss_hold": float(np.mean([t.get("holding_bars") or 0 for t in loss])) if loss else 0,
            "big_loss_count": len(big),
            "big_loss_avg": float(np.mean([float(t.get("pnl_pct") or 0) for t in big])) if big else 0,
            "exit_reason_breakdown": by_reason,
        }
    return diag


def main():
    results = {n: load_strategy(n) for n in ALL}
    combo_ec = pd.read_csv(os.path.join(OUT, "portfolio_combo_equity.csv"))[["date", "equity"]]
    combo_total = (float(combo_ec["equity"].iloc[-1]) / 100000.0 - 1) * 100
    combo_result = {"combined": {
        "total_return_pct": round(combo_total, 2),
        "equity_curve": combo_ec,
        "weights": {"bp+ep_core": 0.60, "ml_signal": 0.40}}}
    ys = pd.read_csv(os.path.join(OUT, "yearly_summary_all.csv"))
    yearly_summary = ys.to_dict("records")

    diag = build_diagnosis(results)
    charts = _ns["generate_charts"](results, combo_result, yearly_summary)
    _ns["build_comprehensive_report"](results, combo_result, yearly_summary, diag, charts)
    print(f"[rebuild] FULL_REPORT.html 重建完成 (组合={combo_total:+.2f}%) -> {OUT}")


if __name__ == "__main__":
    main()
