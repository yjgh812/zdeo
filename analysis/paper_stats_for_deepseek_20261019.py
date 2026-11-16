# -*- coding: utf-8 -*-
"""模拟盘胜率诊断统计（供DeepSeek网页外审通报用）· 2026-10-19
数据源: attribution.db 的 position_history / attribution_monthly / trade_records（归因层本地库）。
声明: 交易真值=westock MCP 实时返回(总盈亏/在仓33只12盈21亏), 本脚本产出的是归因层平仓记录统计,
两者口径不同, 通报中分别标注。输出: 控制台 + analysis/deepseek_brief_stats_20261019_out.txt
"""
import sqlite3
import sys
import os
import statistics
import collections

sys.stdout.reconfigure(encoding="utf-8")
DB = r"D:\zcodesj\attribution\attribution.db"
OUT = r"D:\zcodesj\analysis\deepseek_brief_stats_20261019_out.txt"

lines = []


def emit(s=""):
    lines.append(s)
    print(s)


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# ---------- 1. position_history 状态分布 ----------
rows = cur.execute(
    "SELECT * FROM position_history ORDER BY entry_date DESC"
).fetchall()
emit("== position_history 全表: %d 行 ==" % len(rows))
status_cnt = collections.Counter(r["status"] for r in rows)
emit("status 分布: %s" % dict(status_cnt))

# 2026 年入场(或出场)的平仓记录
closed = [
    r
    for r in rows
    if (r["status"] or "").lower().startswith("closed")
    and (
        str(r["entry_date"] or "").startswith("2026")
        or str(r["exit_date"] or "").startswith("2026")
    )
]
emit("2026 平仓记录: %d 行" % len(closed))

if closed:
    pnls = [float(r["pnl_amount"] or 0) for r in closed]
    pcts = [float(r["pnl_pct"] or 0) for r in closed]
    holds = [float(r["holding_days"] or 0) for r in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    emit("平仓盈亏合计: %.0f 元" % sum(pnls))
    emit("盈利笔数: %d / 亏损笔数: %d / 胜率: %.1f%%" % (len(wins), len(losses), 100.0 * len(wins) / len(closed)))
    emit("平均盈利: %.0f 元 / 平均亏损: %.0f 元 / 盈亏比(avg_win/|avg_loss|): %.2f" % (
        statistics.mean(wins) if wins else 0,
        statistics.mean(losses) if losses else 0,
        (statistics.mean(wins) / abs(statistics.mean(losses))) if wins and losses else 0,
    ))
    emit("单笔收益%% 中位: %.2f%% / 均值: %.2f%%" % (statistics.median(pcts), statistics.mean(pcts)))
    emit("持有天数 中位: %.0f / 均值: %.1f" % (statistics.median(holds), statistics.mean(holds)))
    reason_cnt = collections.Counter((r["exit_reason"] or "?") for r in closed)
    emit("exit_reason 分布: %s" % dict(reason_cnt))
    emit("-- 2026 平仓 盈利Top5 --")
    for r in sorted(closed, key=lambda x: float(x["pnl_amount"] or 0), reverse=True)[:5]:
        emit("  %s %s %s→%s pnl=%+.0f元(%+.1f%%) 持有%.0f天 %s" % (
            r["exit_date"], r["code"], r["name"], r["sector"] or "-", float(r["pnl_amount"] or 0),
            float(r["pnl_pct"] or 0), float(r["holding_days"] or 0), r["exit_reason"]))
    emit("-- 2026 平仓 亏损Top5 --")
    for r in sorted(closed, key=lambda x: float(x["pnl_amount"] or 0))[:5]:
        emit("  %s %s %s %s→%s pnl=%+.0f元(%+.1f%%) 持有%.0f天 %s maxDD=%.1f%%" % (
            r["exit_date"], r["code"], r["name"], r["entry_price"], r["exit_price"],
            float(r["pnl_amount"] or 0), float(r["pnl_pct"] or 0), float(r["holding_days"] or 0),
            r["exit_reason"], float(r["max_drawdown_pct"] or 0)))
    emit("-- 2026 平仓全部逐笔 (exit_date|code|name|entry→exit|pnl元|pnl%%|持有天|exit_reason) --")
    for r in sorted(closed, key=lambda x: str(x["exit_date"] or "")):
        emit("  %s|%s|%s|%s→%s|%+.0f|%+.1f%%|%.0f|%s" % (
            r["exit_date"], r["code"], r["name"], r["entry_price"], r["exit_price"],
            float(r["pnl_amount"] or 0), float(r["pnl_pct"] or 0),
            float(r["holding_days"] or 0), r["exit_reason"]))

# 在仓(status非closed)的2026记录
open_pos = [
    r
    for r in rows
    if not (r["status"] or "").lower().startswith("closed")
    and str(r["entry_date"] or "").startswith("2026")
]
emit("2026 在仓归因记录: %d 行" % len(open_pos))

# ---------- 2. attribution_monthly 近12个月 ----------
emit("")
emit("== attribution_monthly 近12个月 (归因层月度) ==")
for r in cur.execute(
    "SELECT * FROM attribution_monthly ORDER BY month DESC LIMIT 12"
).fetchall():
    emit("  %s 总笔=%s 盈=%s 亏=%s 胜率=%s%% 均收=%s%% 止损命中率=%s%% 止盈=%s%% 持有=%s天 备注=%s" % (
        r["month"], r["total_trades"], r["win_count"], r["loss_count"], r["win_rate"],
        r["avg_return"], r["stop_loss_hit_rate"], r["take_profit_hit_rate"],
        r["avg_holding_days"], (r["remarks"] or "")[:60]))

# ---------- 3. trade_records 2026 信号来源分布 ----------
emit("")
emit("== trade_records 2026 买入信号来源分布 ==")
trs = cur.execute("SELECT * FROM trade_records WHERE trade_date LIKE '2026%' AND action='buy'").fetchall()
sig_cnt = collections.Counter((r["signal_type"] or "?") for r in trs)
emit("2026 买入记录: %d 条, signal_type 分布: %s" % (len(trs), dict(sig_cnt)))
sub_cnt = collections.Counter()
stop_cnt = collections.Counter()
for r in trs:
    subs = (r["subsystems"] or "")
    for s in subs.replace("[", "").replace("]", "").replace("'", "").replace('"', "").split(","):
        s = s.strip()
        if s:
            sub_cnt[s] += 1
    stop_cnt[str(r["stop_loss"])] += 1
emit("subsystems 分布(拆分计数): %s" % dict(sub_cnt.most_common(10)))
emit("stop_loss 值分布: %s" % dict(stop_cnt.most_common(5)))

conn.close()

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
emit("")
emit("[output written] %s" % OUT)
