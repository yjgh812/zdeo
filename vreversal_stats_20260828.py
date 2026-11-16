# -*- coding: utf-8 -*-
"""止损V反统计 (2026-08-28, 胜率提升方案证据件)

目的: 量化 round10 全域回测中 stop_loss 卖出后的价格修复幅度,
为「ATR自适应止损 / 止损后二次入场(re-entry)」规则提供回测侧证据。

口径:
- 输入: reports/tulong_backtest_round10_full_20260827/{bp_ep_core,ml_signal}_trades_full.csv
- 对每笔 exit_reason=stop_loss 的交易, 取卖出日之后 20 个交易日(qfq)的最高收盘价,
  计算 (max_close/sell_price - 1)。
- 不含未来函数问题: 这是描述性统计(事后分布), 不是策略回测。

只读脚本, 无写库。
"""
import csv
import json
import sqlite3
import sys
from collections import defaultdict

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
TRADE_FILES = {
    "bp_ep_core": r"D:\zcodesj\reports\tulong_backtest_round10_full_20260827\bp_ep_core_trades_full.csv",
    "ml_signal": r"D:\zcodesj\reports\tulong_backtest_round10_full_20260827\ml_signal_trades_full.csv",
}
OUT_JSON = r"D:\zcodesj\reports\vreversal_stats_20260828.json"
N_DAYS = 20  # 观察窗口: 卖出后20个交易日


def main() -> int:
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    closes_by_code: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for code, date, close in con.execute(
        "SELECT code, date, close FROM stock_daily_meta_qfq ORDER BY code, date"
    ):
        closes_by_code[code].append((date, float(close)))
    con.close()

    report: dict = {"n_days_window": N_DAYS, "strategies": {}}

    for strat, path in TRADE_FILES.items():
        rows = []
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("卖出触发理由") or "").strip() == "stop_loss":
                    rows.append(r)
        recovers = []
        top5_examples = []
        for r in rows:
            code = r["股票代码"].strip()
            sell_date = r["卖出日期"].replace("-", "")
            sell_px = float(r["卖出价格"])
            series = closes_by_code.get(code, [])
            future = [c for d, c in series if d > sell_date][:N_DAYS]
            if not future or sell_px <= 0:
                continue
            rec = (max(future) / sell_px - 1.0) * 100.0
            recovers.append(rec)
            top5_examples.append((rec, code, r["股票名称"], r["卖出日期"], round(sell_px, 3)))

        recovers.sort()
        n = len(recovers)
        def pct(p: float) -> float:
            return round(100.0 * sum(1 for x in recovers if x >= p) / n, 1) if n else 0.0

        top5_examples.sort(reverse=True)
        report["strategies"][strat] = {
            "stop_loss_trades": len(rows),
            "analyzed": n,
            "median_recover_pct": round(recovers[n // 2], 2) if n else None,
            "mean_recover_pct": round(sum(recovers) / n, 2) if n else None,
            "recover_ge_5pct": pct(5.0),
            "recover_ge_10pct": pct(10.0),
            "recover_ge_20pct": pct(20.0),
            "never_recovered_pct": round(100.0 * sum(1 for x in recovers if x < 0) / n, 1) if n else None,
            "top5_vreversal": [
                {"code": c, "name": nm, "exit_date": d, "exit_px": px, "recover_pct": round(rv, 1)}
                for rv, c, nm, d, px in top5_examples[:5]
            ],
        }
        s = report["strategies"][strat]
        print(f"[{strat}] stop_loss笔数={len(rows)} 可分析={n} | 20日内修复: 中位 {s['median_recover_pct']}% | "
              f">=5%占 {s['recover_ge_5pct']}% | >=10%占 {s['recover_ge_10pct']}% | "
              f">=20%占 {s['recover_ge_20pct']}% | 从未收复 {s['never_recovered_pct']}%")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"saved -> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
