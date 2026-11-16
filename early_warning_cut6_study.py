#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""early_warning_cut6_study.py — 乙档「-6% 预警先减半」误伤率量化（预案表乙档批示缺口数据, 2026-09-30）。

问题（proposal_limitdown_playbook 乙档）：浮亏 -6% 预警先减半仓，需要量化：
  保护端 = 后来真触发 -8% 的持仓，提前减半省下的损失（半仓×(-6%日到-8%日的额外跌幅)）；
  误伤端 = -6% 后反弹未到 -8% 的持仓，被提前砍半错过的反弹收益。
口径:
  样本 = attribution.trade_records 全历史 BUY（含已有 SELL 退出者），qfq 日线重放；
  -6%/-8% 判定 = 收盘价相对 entry(买均价, 多笔加权) 的回撤，收盘触发（与 S-5 盘中口径差异如实标注：
  盘中触发会更早, 本测算偏保守）;
  真止损组 = 达 -6% 后又达 -8%（收盘口径）; 误伤组 = 达 -6% 后至窗口末(或卖出日)未达 -8%;
  保护价值 = 半仓 × (entry×0.92 - entry×0.94)/entry ≈ 半仓 × 2% 相对量级, 报告实际 -6%日收盘到 -8%日收盘跌幅;
  误伤成本 = 半仓 × (max(卖出价/entry-1, 反弹最高收盘/entry-1) - (-6%日收盘/entry-1)) 的机会损失。
零交易权, 数据支撑预案表乙档批示。

用法: python early_warning_cut6_study.py [--window 40]
输出: reports/early_warning_cut6_study.md
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
from datetime import datetime

ROOT = r"D:\zcodesj"
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db") if (os := __import__("os")) else None
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = os.path.join(ROOT, "reports", "early_warning_cut6_study.md")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=40, help="买入后观察窗口(交易日)")
    a = ap.parse_args()

    conn = sqlite3.connect(ATTR_DB)
    # 持仓批次: 按 code 聚合 BUY 加权成本, 找该 code 的最后一笔 SELL 日(退出日)
    buys = conn.execute(
        "SELECT code, MIN(trade_date), SUM(amount), SUM(volume) FROM trade_records "
        "WHERE action='BUY' GROUP BY code").fetchall()
    last_sell = dict(conn.execute(
        "SELECT code, MAX(trade_date) FROM trade_records WHERE action='SELL' GROUP BY code"))
    conn.close()

    q = sqlite3.connect(QFQ_DB, uri=True)
    results = []
    for code, first_buy, amt, vol in buys:
        c6 = str(code)[-6:]
        if not amt or not vol or vol <= 0:
            continue
        entry = float(amt) / float(vol)
        exit_day = last_sell.get(code)
        rows = q.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>=? "
            "ORDER BY date LIMIT ?", (c6, str(first_buy).replace('-', ''), a.window + 1)).fetchall()
        if len(rows) < 2:
            continue
        # -6% 与 -8% 首次收盘触发日
        d6 = d8 = None
        for d, cl in rows:
            ret = float(cl) / entry - 1
            if d6 is None and ret <= -0.06:
                d6 = (str(d), float(cl), ret)
            if d6 is not None and d8 is None and ret <= -0.08:
                d8 = (str(d), float(cl), ret)
                break
        if d6 is None:
            continue  # 从未到预警线, 不适用乙档
        # 窗口末(或卖出日)结果
        end_row = rows[-1]
        if exit_day:
            exit_day_n = str(exit_day).replace('-', '')
            end_row = next((r for r in rows if str(r[0]) == exit_day_n), rows[-1])
        final_ret = float(end_row[1]) / entry - 1
        if d8 is not None:
            # 真止损组: 保护价值 = 半仓 × (d6收盘 相对 d8收盘 的额外跌幅)
            extra = d8[1] / d6[1] - 1  # 负值=额外下跌
            results.append({"code": code, "grp": "真止损", "d6": d6[0], "d8": d8[0],
                            "days_between": None, "extra_drop": round(extra, 4),
                            "final_ret": round(final_ret, 4)})
        else:
            # 误伤组: -6% 后未到 -8%——被砍半仓错过的机会 = 最终/反弹收益 相对 d6 价的涨幅
            peak = max(float(cl) for _, cl in rows)
            miss = peak / d6[1] - 1
            results.append({"code": code, "grp": "误伤", "d6": d6[0], "d8": None,
                            "days_between": None, "extra_drop": None,
                            "miss_gain": round(miss, 4), "final_ret": round(final_ret, 4)})
    q.close()

    ts = [r for r in results if r["grp"] == "真止损"]
    ms = [r for r in results if r["grp"] == "误伤"]
    extras = [r["extra_drop"] for r in ts]
    misses = [r["miss_gain"] for r in ms]

    def blk(v, scale=100):
        if not v:
            return "—"
        s = sorted(v)
        return (f"n={len(v)} 均值{sum(v) / len(v) * scale:+.2f}% "
                f"中位{s[len(s) // 2] * scale:+.2f}%")

    n_total = len(results)
    lines = [f"# 乙档「-6%预警先减半」误伤率量化（{datetime.now().strftime('%Y-%m-%d %H:%M')}）", "",
             f"- 样本: 全历史 BUY 批次中曾收盘触及 -6% 的 {n_total} 笔（窗口{a.window}交易日, qfq收盘口径）",
             f"- **真止损组（-6%后又到-8%）: {len(ts)} 笔 ({len(ts) / max(1, n_total) * 100:.1f}%)**",
             f"- **误伤组（-6%后反弹未到-8%）: {len(ms)} 笔 ({len(ms) / max(1, n_total) * 100:.1f}%)**",
             "", "## 保护端（真止损组: -6%日→-8%日 的额外跌幅, 半仓×该值=节省）", "",
             f"- {blk(extras)}", "",
             "## 误伤端（误伤组: -6%日后反弹最高收益, 半仓×该值=错过）", "",
             f"- {blk(misses)}", "",
             "## 组合判读（供预案表乙档批示）", "",
             f"- 误伤率 = {len(ms) / max(1, n_total) * 100:.1f}%（误伤占比越高, 乙档成本越大）；",
             f"- 期望净效应 ≈ 真止损占比×半仓×平均额外跌幅(节省) − 误伤占比×半仓×平均反弹(错过)——"
             "精确值需组合级重放(L5), 本件给单票级分量；",
             "- 口径限制: 收盘触发(盘中S-5会更早, 本测算偏保守)/多笔买入按加权均价/窗口40日截断。", ""]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[CUT6] n={n_total} 真止损={len(ts)}({len(ts) / max(1, n_total) * 100:.1f}%) "
          f"误伤={len(ms)}({len(ms) / max(1, n_total) * 100:.1f}%) "
          f"额外跌幅{blk(extras)} 误伤反弹{blk(misses)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
