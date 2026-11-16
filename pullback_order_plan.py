# -*- coding: utf-8 -*-
"""pullback_order_plan.py — 回踩带限价买入计划生成器（提请制, 零自动执行）

背景(2026-10-08 12只首日反思): 唯一已验证入场触发器=回踩企稳(10/3框架对照+9/30全宇宙验证+
R31池内研究), 开盘批量买入=零确认入场(结构性追入)。本工具把候选清单转为「回踩带限价单计划」
供主人逐笔裁决; 只输出计划与可直接粘贴的 executor 命令, 不下单。

口径(v1, 与R31/回踩企稳研究同源):
  回踩带 = [MA20×0.98, MA20×1.02]
  建议限价 = MA20（回踩至20日线一带承接）
  破位警戒 = MA20×0.97（跌破-3%即段失效, 对应赢家画像v3段起-3%失效条款）
数据源 = qfq库(只读)。用法:
    python pullback_order_plan.py --codes 600603,002053 --names 广汇物流,云南能投 --budget 20000
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime

QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
BAND_LOW = 0.98     # 回踩带下沿系数(MA20)
BAND_HIGH = 1.02    # 回踩带上沿系数(MA20)
BREAK_LINE = 0.97   # 破位警戒系数(MA20)


def _fetch(code6: str):
    con = sqlite3.connect(QFQ_DB, uri=True, timeout=10)
    try:
        rows = con.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            (code6,)).fetchall()
    finally:
        con.close()
    return [(str(d), float(c)) for d, c in rows]


def plan_one(code6: str, name: str, budget: float) -> dict | None:
    rows = _fetch(code6)
    if len(rows) < 61:
        return None
    closes = [c for _, c in rows]
    asof_date, close = rows[-1]
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    low20 = min(closes[-20:])
    band_lo, band_hi = ma20 * BAND_LOW, ma20 * BAND_HIGH
    limit = round(ma20, 2)
    qty = int(budget / limit / 100) * 100 if limit > 0 else 0
    trend = "多头" if ma20 > ma60 else "空头"
    below_ma20 = close < ma20
    return {
        "code": code6, "name": name, "asof": asof_date, "close": close,
        "ma20": round(ma20, 2), "ma60": round(ma60, 2), "low20": round(low20, 2),
        "trend": trend, "bias20": f"{(close / ma20 - 1) * 100:+.1f}%",
        "band": f"{band_lo:.2f}~{band_hi:.2f}", "limit": limit, "qty": qty,
        "break": round(ma20 * BREAK_LINE, 2),
        "note": "已在MA20下方" if below_ma20 else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="回踩带限价买入计划生成器(提请制零自动执行)")
    ap.add_argument("--codes", required=True, help="逗号分隔6位代码")
    ap.add_argument("--names", default="", help="逗号分隔名称(与codes对齐, 可省略)")
    ap.add_argument("--budget", type=float, default=20000, help="单票预算元(算股数用, 默认2万)")
    a = ap.parse_args()
    codes = [c.strip()[-6:] for c in a.codes.split(",") if c.strip()]
    names = a.names.split(",") if a.names else []
    names += [""] * (len(codes) - len(names))

    print(f"# 回踩带限价买入计划 (生成 {datetime.now().strftime('%Y-%m-%d %H:%M')}, "
          f"口径=MA20±2%带/限价=MA20/破位警戒-3%; 提请制零自动执行, 买入须主人逐笔裁决+闸门)\n")
    print("| 名称(代码) | 收盘(asof) | MA20 | MA60 | 趋势 | 乖离20 | 回踩带 | 建议限价 | 建议股数 | 破位警戒 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    cmds = []
    for c, n in zip(codes, names):
        p = plan_one(c, n.strip(), a.budget)
        if p is None:
            print(f"| {c} | - | - | - | - | - | - | 库缺/次新不可算 | - | - |")
            continue
        print(f"| {p['name']}({p['code']}){p['note']} | {p['close']}({p['asof']}) | {p['ma20']} | "
              f"{p['ma60']} | {p['trend']} | {p['bias20']} | {p['band']} | "
              f"**{p['limit']}** | {p['qty']} | {p['break']} |")
        cmds.append(
            f"python trade_executor.py buy --code {p['code']} --name {p['name'] or p['code']} "
            f"--source manual_confirmed --sector <申万L1> --price {p['limit']} "
            f"--volume {p['qty']} --budget {a.budget:.0f} --execute")
    print("\n## 待裁决命令模板(逐笔过闸, --price=回踩带限价非市价追单)\n")
    for c in cmds:
        print(f"    {c}")
    print("\n边界: ①限价低于市价=不回踩不成交(等回踩确认协议); ②高开>2%天然不成交(禁追); "
          "③跌破破位警戒线即段失效撤单; ④本工具只出计划, 下单唯一入口=trade_executor。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
