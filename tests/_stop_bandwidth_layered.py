#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STOP_BANDWIDTH_LAYERED 研究（长期方案11/L3，2026-08-28）：按市值×波动分层统计卖出后 V 反率。
只读；不改 -8% 铁律（一票制 OOS 通过前零松动）。输出 reports/STOP_BANDWIDTH_LAYERED_20260828.md。"""
from __future__ import annotations

import datetime
import sqlite3
import statistics as st

ATTR = r"D:\zcodesj\attribution\attribution.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

con = sqlite3.connect(ATTR)
sells = con.execute(
    "SELECT code, name, trade_date, price, volume FROM trade_records "
    "WHERE trade_date>='20260803' AND trade_date<='20260827' AND action='SELL' "
    "ORDER BY trade_date, id").fetchall()
con.close()
print("8月 SELL 行:", len(sells))

fund = sqlite3.connect(f"file:{FUND}?mode=ro", uri=True)

q = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)

def closes_before(code6: str, date: str, n: int):
    """该日及之前最近 n 根 qfq 收盘（含当日, 倒序）。"""
    rows = q.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code LIKE ? AND date<=? "
        "ORDER BY date DESC LIMIT ?", (f"%{code6}", date, n)).fetchall()
    return [(d, float(c)) for d, c in rows if c]

def circ_mv_on(code6: str, date: str):
    """该日或之前最近一期的流通市值(元)。
    注: ts_code 带 .SH/.SZ 后缀, 必须 substr(ts_code,1,6) 前缀匹配(2026-08-28 修正:
    此前 LIKE '%code' 尾部匹配恒不中=第三个前后缀 bug)。"""
    row = fund.execute(
        "SELECT circ_mv FROM daily_basic WHERE substr(ts_code,1,6)=? AND trade_date<=? "
        "ORDER BY trade_date DESC LIMIT 1", (code6, date)).fetchone()
    return float(row[0]) * 1e4 if row and row[0] else None

rows = []
for code, name, date, price, vol in sells:
    code6 = str(code)[-6:]
    hist = closes_before(code6, date, 21)
    if len(hist) < 21:
        continue
    closes = [c for _, c in hist]          # 倒序: hist[0]=当日
    # 20日波动率 = 前20个日收益(不含当日) 的 std
    prev = closes[1:21]
    rets = [(prev[i] / prev[i + 1] - 1) for i in range(len(prev) - 1)]
    vol20 = st.pstdev(rets) if len(rets) > 1 else None
    mv = circ_mv_on(code6, date)
    # T+3 / T+5 最大反弹（卖出后收盘 vs 卖出价）
    fwd = q.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code LIKE ? AND date>? "
        "ORDER BY date ASC LIMIT 5", (f"%{code6}", date)).fetchall()
    fwd_c = [float(c) for c, in fwd if c and c > 0]
    mx3 = max(fwd_c[:3]) / price - 1 if (fwd_c[:3] and price and price > 0) else None
    mx5 = max(fwd_c[:5]) / price - 1 if (fwd_c[:5] and price and price > 0) else None
    rows.append({"code": code6, "name": name, "date": date, "price": price,
                 "vol20": vol20, "mv": mv, "mx3": mx3, "mx5": mx5})
q.close(); fund.close()
print("可配对 SELL:", len(rows))

def bucket(v, edges, labels):
    if v is None:
        return "?"
    for e, l in zip(edges, labels):
        if v < e:
            return l
    return labels[-1]

def stats(rs):
    if not rs:
        return None
    mx3 = [r["mx3"] for r in rs if r["mx3"] is not None]
    vre = sum(1 for x in mx3 if x >= 0.03) / len(mx3) if mx3 else None
    return {"n": len(rs), "v3pct": round(vre * 100, 0) if vre is not None else None,
            "mx3_med": round(st.median(mx3) * 100, 1) if mx3 else None}

lines = ["# 止损带宽分层研究（2026-08-28 · 长期方案11/L3）\n",
         f"> 样本：8/3-8/27 可配对 SELL {len(rows)} 笔；波动=卖出前20日日收益std；市值=daily_basic 流通市值；"
         f"V反=卖出后 T+3 内最高收盘 ≥ +3%（准则41 ≥2后市交易日口径）\n",
         "> 结论前置：**-8% 硬止损铁律不因本报告松动**（一票制 OOS 通过前零调整）；本报告仅产分层事实与候选建议。\n\n"]

# 波动分层
lines.append("## 一、按 20 日波动率分层（全市值）\n")
lines.append("| 波动档 | n | V反率(T+3≥3%) | T+3最大反弹中位 |\n|---|---|---|---|\n")
for lab in ["低<3%", "中3-5%", "高>5%"]:
    rs = [r for r in rows if bucket(r["vol20"], [0.03, 0.05], ["低<3%", "中3-5%", "高>5%"]) == lab]
    s = stats(rs)
    if s:
        lines.append(f"| {lab} | {s['n']} | {s['v3pct']}% | +{s['mx3_med']}% |\n")
lines.append("\n")

# 市值分层
lines.append("## 二、按流通市值分层（全波动）\n")
lines.append("| 市值档 | n | V反率(T+3≥3%) | T+3最大反弹中位 |\n|---|---|---|---|\n")
for lab in ["小<50亿", "中50-300亿", "大>300亿"]:
    rs = [r for r in rows if bucket(r["mv"], [50e8, 300e8], ["小<50亿", "中50-300亿", "大>300亿"]) == lab]
    s = stats(rs)
    if s:
        lines.append(f"| {lab} | {s['n']} | {s['v3pct']}% | +{s['mx3_med']}% |\n")
lines.append("\n")

# 交叉: 大市值×高波动 与 小市值×低波动（止损带宽错配的两个极端候选）
lines.append("## 三、交叉分层（V反率最高的候选层）\n")
lines.append("| 层 | n | V反率 | T+3中位 |\n|---|---|---|---|\n")
combos = [("大市值(>300亿)×高波动(>5%)", lambda r: r["mv"] and r["mv"] >= 300e8 and bucket(r["vol20"], [0.05], ["x", "高>5%"]) == "高>5%"),
          ("中市值(50-300亿)×高波动(>5%)", lambda r: r["mv"] and 50e8 <= r["mv"] < 300e8 and bucket(r["vol20"], [0.05], ["x", "高>5%"]) == "高>5%"),
          ("大市值(>300亿)×低波动(<3%)", lambda r: r["mv"] and r["mv"] >= 300e8 and bucket(r["vol20"], [0.03], ["低<3%", "x"]) == "低<3%")]
for lab, fn in combos:
    rs = [r for r in rows if fn(r)]
    s = stats(rs)
    if s:
        lines.append(f"| {lab} | {s['n']} | {s['v3pct']}% | +{s['mx3_med']}% |\n")
lines.append("\n")

# 明细（全部，供复核）
lines.append("## 四、明细\n")
lines.append("| 代码 | 名称 | 卖出日 | 波动 | 市值(亿) | T+3最大反弹 |\n|---|---|---|---|---|---|\n")
for r in sorted(rows, key=lambda x: -(x["mx3"] or 0)):
    mv = f"{r['mv']/1e8:.0f}" if r["mv"] else "?"
    lines.append(f"| {r['code']} | {r['name']} | {r['date']} | {r['vol20']*100:.1f}% | {mv} | "
                 f"{'+'+str(round(r['mx3']*100,1))+'%' if r['mx3'] is not None else 'N/A'} |\n")

# 结论
lines.append("## 五、结论与候选建议（不改铁律）\n")
hi = [r for r in rows if r["mx3"] is not None and r["mx3"] >= 0.05]
def _fmt(r):
    mv = f"{r['mv']/1e8:.0f}亿" if r["mv"] else "?"
    return f"{r['name']}({r['code']}, {r['date']}, 波动{r['vol20']*100:.0f}%, 市值{mv}, T+3+{r['mx3']*100:.1f}%)"
lines.append(f"- V反≥5% 卖出 {len(hi)} 笔；其市值/波动分布："
             + "; ".join(_fmt(r) for r in hi[:8]) + "\n")
lines.append("- 候选（供主人裁决，非本报告落地）：若大市值×高波动层 V反率显著更高，可研究该层单独放宽止损带宽"
             "（如 -10% 或 Chandelier），但**一票制 OOS 通过前 -8% 零松动**；入场端 R27/R28 已先拦截追价。\n")
lines.append(f"\n*样本 {len(rows)} 笔 | 生成 {datetime.datetime.now().isoformat(timespec='seconds')}*\n")

out = r"D:\zcodesj\reports\STOP_BANDWIDTH_LAYERED_20260828.md"
with open(out, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("报告已写:", out)
