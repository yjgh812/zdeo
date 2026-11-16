#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pnl_ratio_study.py — 盈亏比复算（预注册 pnl_ratio_study_prereg.json）。

①盈利因子账本: 259笔平仓 Σ盈利/|Σ亏损| 与盈亏比(均盈/均亏)。
②止损反事实: 每笔S-5止损平仓后 fwd5/fwd10（从平仓日次一交易日收盘起算）与反弹判定。
判据冻结见预注册JSON。零交易权。退出码: 0正常/3数据源不可用。"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")
import stock_name_lookup as snl  # noqa: E402

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
PRICE_DB = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
            + "?mode=ro")
REPORT_DIR = r"D:\zcodesj\reports"
FLOOR_N = 15


def _code6(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


def _load():
    a = sqlite3.connect(ATTR_DB)
    rows = a.execute(
        "SELECT code,name,entry_date,exit_date,entry_price,exit_price,pnl_pct,exit_reason "
        "FROM position_history WHERE pnl_pct IS NOT NULL AND exit_date IS NOT NULL "
        "AND entry_price>0 AND exit_price>0").fetchall()
    a.close()
    out = []
    for c, n, ed, xd, ep, xp, pnl, reason in rows:
        out.append({"code": _code6(c), "name": str(n or ""), "ed": int(str(ed)[:8]),
                    "xd": int(str(xd)[:8]), "ep": float(ep), "xp": float(xp),
                    "pnl": float(pnl), "reason": str(reason or "")})
    return out


def _fwd(conn, code, xd, n):
    """平仓日次一交易日起第N交易日收盘相对次一交易日收盘的收益%。bar不足→None。"""
    rows = conn.execute(
        "SELECT date, close FROM stock_daily_raw WHERE code=? AND date>? "
        "ORDER BY date LIMIT ?", (code, xd, n + 1)).fetchall()
    if len(rows) < n + 1:
        return None, None, None
    base = float(rows[0][1])
    f = (float(rows[n][1]) / base - 1.0) * 100
    hi = max(float(r[1]) for r in rows[:n + 1])
    return f, hi, base


def run():
    try:
        trades = _load()
        conn = sqlite3.connect(PRICE_DB, uri=True, check_same_thread=False)
    except Exception as e:  # noqa: BLE001
        print(f"[PNL-RATIO] [FAIL-CLOSED] 数据源不可用({type(e).__name__}: {e})")
        return 3
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    sum_w = sum(t["pnl"] for t in wins)
    sum_l = sum(t["pnl"] for t in losses)
    pf = sum_w / abs(sum_l) if sum_l else None
    aw = sum_w / len(wins) if wins else None
    al = sum_l / len(losses) if losses else None
    wr = len(wins) / len(trades) * 100 if trades else 0.0

    def _is_stop(t):
        r = t["reason"]
        return "S-5" in r or "止损" in r

    stops = [t for t in trades if _is_stop(t)]
    cf = []
    pending = 0
    for t in stops:
        f5, hi5, _b = _fwd(conn, t["code"], t["xd"], 5)
        f10, hi10, _b = _fwd(conn, t["code"], t["xd"], 10)
        if f10 is None:
            pending += 1
            continue
        cf.append({"code": t["code"], "name": t["name"], "xd": t["xd"],
                   "pnl": t["pnl"], "xp": t["xp"],
                   "f5": f5, "f10": f10, "hi": hi10})
    med = lambda v: sorted(v)[len(v) // 2] if v else None  # noqa: E731
    m_f5 = med([c["f5"] for c in cf])
    m_f10 = med([c["f10"] for c in cf])
    n_rec = sum(1 for c in cf if c["hi"] > c["xp"])
    rec_rate = n_rec / len(cf) * 100 if cf else None
    deep = sum(1 for c in cf if c["f10"] < c["pnl"])
    L = [f"# 盈亏比复算 {datetime.now():%Y-%m-%d %H:%M}", "",
         f"- 平仓 {len(trades)} 笔：盈 {len(wins)}｜亏 {len(losses)}｜胜率 {wr:.1f}%",
         "", "## ① 盈利因子账本", ""]
    if pf is not None:
        v = ("✅ 右尾覆盖左尾有余量" if pf >= 1.2 else
             "⚖️ 勉强打平" if pf >= 1.0 else "❌ 右尾不覆盖左尾")
        L.append(f"- {v}: 盈利因子={pf:.2f} (Σ盈{sum_w:+.1f}pp / |Σ亏{sum_l:+.1f}pp|)")
        if aw and al:
            L.append(f"- 盈亏比(均盈/均亏)={aw / abs(al):.2f} "
                     f"(均盈{aw:+.2f}% vs 均亏{al:+.2f}%)")
        top = sorted(trades, key=lambda x: -x["pnl"])[:5]
        L.append("- 右尾Top5: " + "；".join(
            f"{snl.get_name(t['code']) or t['name']}{t['pnl']:+.1f}%" for t in top))
        bot = sorted(trades, key=lambda x: x["pnl"])[:5]
        L.append("- 左尾Bottom5: " + "。".join(
            f"{snl.get_name(t['code']) or t['name']}{t['pnl']:+.1f}%" for t in bot))
    L += ["", f"## ② 止损反事实（S-5止损 n={len(stops)}，可算{len(cf)}，"
          f"前瞻bar不足{pending}）", ""]
    if m_f10 is not None and len(cf) >= FLOOR_N:
        d10 = m_f10
        v2 = ("✅ 止损正确: 其后继续跌" if d10 < -1.0 else
              "⚠️ 止损吃了反弹" if d10 > 1.0 else "⚖️ 边界区")
        L.append(f"- {v2}: 止损后fwd10中位={d10:+.2f}% (fwd5中位={m_f5:+.2f}%)")
        L.append(f"- 反弹率: 前瞻10日内最高收盘>止损价的占 {rec_rate:.0f}% "
                 f"(n={len(cf)})")
        L.append(f"- 继续跌更深(反事实fwd10亏损>实际止损亏损)的占 "
                 f"{deep / len(cf) * 100:.0f}%")
        L += ["", "- 边界: 反事实只回答'止损日之后价格怎么走'，不回答'无止损全路径'",
              "  (路径依赖+条件选择偏差: 已跌8%的票本就在麻烦中)。",
              "- 附止损单明细:", "",
              "| 代码 | 名称 | 平仓日 | 止损损益% | fwd5% | fwd10% | 反弹过止损价 |",
              "|---|---|---|---|---|---|---|"]
        for c in sorted(cf, key=lambda x: x["pnl"])[:40]:
            nm = snl.get_name(c["code"]) or c["name"]
            L.append(f"| {c['code']} | {nm} | {c['xd']} | {c['pnl']:+.1f} "
                     f"| {c['f5']:+.1f} | {c['f10']:+.1f} | "
                     f"{'是' if c['hi'] > c['xp'] else '否'} |")
    else:
        L.append(f"- ⏳ 反事实样本不足(可算{len(cf)}, 需≥{FLOOR_N})——如实")
    path = os.path.join(REPORT_DIR,
                        f"pnl_ratio_study_{datetime.now():%Y%m%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    pf_s = f"{pf:.2f}" if pf is not None else "N/A"
    m10 = f"{m_f10:+.2f}" if m_f10 is not None else "N/A"
    print(f"[PNL-RATIO] 平仓{len(trades)} 盈利因子={pf_s} 胜率{wr:.0f}% "
          f"止损单{len(stops)} 反事实{len(cf)}笔fwd10中位={m10}% "
          f"反弹率={rec_rate if rec_rate is None else round(rec_rate)}% -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
