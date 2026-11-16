#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mainline_context.py — 会话选股主线归属诊断（只读观测层工具）

动机：2026-09-04 主人批评会话选股 15 笔 0/15 踩主线、13/15 跑输所属板块。
本工具让任何选股动作前/后一键核对每只票的主线归属与板块内相对位置。
仅为观测提示，不做任何买卖决策、不写任何库。

数据源（只读）：
  industry_map_ths   个股→THS行业   (fundamental_data.db)
  industry_kline_ths 行业K线        (workbuddyspace/sw_industry.db, 90行业)
  stock_daily_meta_qfq 个股行情     (workbuddyspace qfq库)
  daily_basic         市值/换手      (fundamental_data.db, 风格三闸体检, STYLE-GATE-STAGE4)

风格三闸（观测提示，2026-09-04 stage-4 画像，无决策语义）：
  闸1 流通市值 ≤ 70亿 | 闸2 mom20 ≤ +2%(不追高) | 闸3 换手率 ≤ 3%

用法:
  python mainline_context.py 600694,600717,002174
  python mainline_context.py --top   # 只看当前主线榜(T-1口径)
"""
from __future__ import annotations

import bisect
import sqlite3
import sys

FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
SWDB = r"D:\workbuddyspace\databases\sw_industry.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
TOPN = 10


def load():
    con = sqlite3.connect(FUND)
    m = {}      # code -> 股票名
    m_ind = {}  # code -> THS二级行业名
    for code, name_, ind in con.execute("select code, name, industry from industry_map_ths"):
        m[code] = name_ or ""
        m_ind[code] = ind.split("-")[1] if "-" in ind else ind
    con.close()
    con = sqlite3.connect(SWDB)
    rows = con.execute(
        "select industry_name, trade_date, close, change_pct from industry_kline_ths").fetchall() \
        if False else con.execute(
        "select industry_name, trade_date, close from industry_kline_ths").fetchall()
    con.close()
    ik = {}
    for name, d, c in rows:
        ik.setdefault(name, []).append((d.replace("-", ""), c))
    for k in ik:
        ik[k].sort()
    con = sqlite3.connect(QFQ)
    px = {}
    for code, d, c in con.execute(
            "select code, date, close from stock_daily_meta_qfq where date >= '20260801'"):
        px.setdefault(code, []).append((d, c))
    con.close()
    for k in px:
        px[k].sort()
    # 风格三闸数据（daily_basic 最新快照）
    style = {}
    con = sqlite3.connect(FUND)
    try:
        last_db = con.execute("select max(trade_date) from daily_basic").fetchone()[0]
        style["_asof"] = last_db
        for code, to_, mv in con.execute(
                "select ts_code, turnover_rate, circ_mv from daily_basic where trade_date=?",
                (last_db,)):
            try:
                style[code[:6]] = (float(to_), float(mv) / 10000.0)
            except (TypeError, ValueError):
                pass
    except Exception:
        style = {}
    con.close()
    # 资金流快照（em_flow_snapshot.db, 候选+持仓池; R22 教训: 双口径可反向,分歧仅警示禁强结论）
    flow = {}
    try:
        fcon = sqlite3.connect(r"D:\zcodesj\data\em_flow_snapshot.db")
        fd = fcon.execute("select max(trade_date) from flow_snapshot").fetchone()[0]
        flow["_asof"] = fd
        for code, em5, ths5 in fcon.execute(
                "select code, em_5d, ths_5d from flow_snapshot where trade_date=?", (fd,)):
            try:
                flow[code] = (float(em5) / 1e8, float(ths5) / 1e8)
            except (TypeError, ValueError):
                pass
        fcon.close()
    except Exception:
        flow = {}
    return m, m_ind, ik, px, style, flow


def industry_rank(ik, name, win):
    series = ik.get(name)
    if not series or len(series) < win + 1:
        return None, None
    d1, c1 = series[-1]
    d0, c0 = series[-1 - win]
    if not c0:
        return None, None
    return (c1 / c0 - 1) * 100, d1


def build_checkup(codes: list[str]) -> dict:
    """四维体检核心（供系统内导入: daily_trade_plan 等）。纯只读。

    返回: {asof, style_asof, flow_asof, ranks5/ranks20(前10), rows:[...], n_main, summary}
    row: {code, stock, ind, r5, pos5, r20, pos20, in5, m20, rel, mv, to,
          gates(g1,g2,g3,marks), hard(bool), em5, ths5, flow_diverge, warn}
    """
    m, m_ind, ik, px, style, flow = load()
    ranks5, ranks20 = [], []
    for name in ik:
        r5, _ = industry_rank(ik, name, 5)
        r20, _ = industry_rank(ik, name, 20)
        if r5 is not None:
            ranks5.append((r5, name))
        if r20 is not None:
            ranks20.append((r20, name))
    ranks5.sort(reverse=True)
    ranks20.sort(reverse=True)
    top5 = {n for _, n in ranks5[:TOPN]}
    asof = max((d for d in (series[-1][0] for series in ik.values() if series)), default="?")
    pos5 = {n: i + 1 for i, (r, n) in enumerate(ranks5)}
    pos20 = {n: i + 1 for i, (r, n) in enumerate(ranks20)}
    rows, n_main = [], 0
    for code in codes:
        stock_nm = m.get(code)
        ind_nm = m_ind.get(code)
        if not stock_nm and not ind_nm:
            rows.append({"code": code, "warn": "无行业映射"})
            continue
        if not ind_nm:
            rows.append({"code": code, "stock": stock_nm, "warn": "无行业K线"})
            continue
        warn = ""
        if "ST" in (stock_nm or "").upper():
            warn = "⛔ST票(禁买域)"
        elif code.startswith(("68", "4", "8", "92")):
            warn = "⛔科创/北交所(禁买域)"
        if warn:
            rows.append({"code": code, "stock": stock_nm, "ind": ind_nm, "warn": warn})
            continue
        r5, _ = industry_rank(ik, ind_nm, 5)
        r20, _ = industry_rank(ik, ind_nm, 20)
        in5 = ind_nm in top5
        n_main += in5
        s = px.get(code)
        m20 = None
        if s and len(s) >= 21 and s[-21][1]:
            m20 = (s[-1][1] / s[-21][1] - 1) * 100
        rel = ""
        if m20 is not None and r20 is not None:
            rel = "强于板块" if m20 > r20 else "弱于板块"
        to_, mv = style.get(code, (None, None))
        g1 = mv is not None and mv <= 70.0
        g2 = m20 is not None and m20 <= 2.0
        g3 = to_ is not None and to_ <= 3.0
        em5, ths5 = flow.get(code, (None, None))
        rows.append({"code": code, "stock": stock_nm, "ind": ind_nm,
                     "r5": r5, "pos5": pos5.get(ind_nm, 0),
                     "r20": r20, "pos20": pos20.get(ind_nm, 0),
                     "in5": in5, "m20": m20, "rel": rel, "mv": mv, "to": to_,
                     "gates": {"g1": g1, "g2": g2, "g3": g3,
                               "marks": f"{'V' if g1 else 'X'}{'V' if g2 else 'X'}{'V' if g3 else 'X'}"},
                     "hard": not g2, "em5": em5, "ths5": ths5,
                     "flow_diverge": bool(em5 is not None and ths5 is not None and em5 * ths5 < 0),
                     "warn": ""})
    return {"asof": asof, "style_asof": style.get("_asof", "?"),
            "flow_asof": flow.get("_asof", "?"),
            "ranks5": ranks5[:TOPN], "ranks20": ranks20[:TOPN],
            "rows": rows, "n_main": n_main, "n": len(codes)}


def _fmt_top(ranks, title):
    print(title)
    for i, (r, n) in enumerate(ranks, 1):
        print(f"  {i:2}. {n:8} {r:+.2f}%")


def main():
    # --top: 只打印主线榜
    if len(sys.argv) < 2 or sys.argv[1] == "--top":
        _c = build_checkup([])
        print(f"== THS行业主线榜(T-1口径,行业K线截至 {_c['asof']}) ==")
        _fmt_top(_c["ranks5"], "[5日涨幅榜前10]")
        _fmt_top(_c["ranks20"], "[20日涨幅榜前10]")
        return 0
    # 代码体检: 空白/全角逗号/半角逗号统一分隔, 支持多参数合并
    import re as _re
    codes = [c.strip() for c in _re.split(r"[,\s，、]+", " ".join(sys.argv[1:])) if c.strip()]
    if "--top" in codes:
        codes.remove("--top")
    c = build_checkup(codes)
    print(f"(市值/换手取自 daily_basic {c['style_asof']}；三闸=市值≤70亿/mom20≤+2%/换手≤3%；资金流取自 flow_snapshot {c['flow_asof']} 单位亿,⚠=双口径分歧)")
    print(f"{'代码':8}{'THS二级行业':10}{'行业5日%':>9}{'排名':>5}{'行业20日%':>10}{'排名':>5}  主线归属   个股20日%  板块内相对  市值亿  换手%   三闸  EM5日  THS5日")
    nan = float("nan")
    for row in c["rows"]:
        if row.get("warn") in ("无行业映射", "无行业K线"):
            print(f"{row['code']:8}{row.get('stock') or row['warn']:10}")
            continue
        if row.get("warn"):
            print(f"{row['code']:8}{row['stock']:10}{row['ind']:10}{'':>9}{'':>5}{'':>10}{'':>5}{'':12}{'':>9}{'':8}{'':7}{'':6}  {row['warn']}")
            continue
        g = row["gates"]
        hard = "" if not row["hard"] else "  ⛔禁买(不追高硬约束,特批例外须主人显式批准)"
        em5, ths5 = row["em5"], row["ths5"]
        fstr = f"{'--':>7}{'--':>7}" if em5 is None else \
            f"{em5:>+6.2f}{'⚠' if row['flow_diverge'] else ' '}{ths5:>+7.2f}"
        print(f"{row['code']:8}{row['ind']:10}{(row['r5'] if row['r5'] is not None else nan):>9.2f}"
              f"{row['pos5']:>5}{(row['r20'] if row['r20'] is not None else nan):>10.2f}"
              f"{row['pos20']:>5}  {'★主线前10' if row['in5'] else '·主线外':10}"
              f"{(row['m20'] if row['m20'] is not None else nan):>9.2f}  {row['rel']:6}"
              f"{(row['mv'] if row['mv'] is not None else nan):>7.1f}{(row['to'] if row['to'] is not None else nan):>7.2f}  {g['marks']}{hard} {fstr}")
    print(f"\n小结: {c['n_main']}/{c['n']} 在5日主线榜前10; 三闸=市值≤70亿/mom20≤+2%/换手≤3%; ⛔标记=mom20>+2%触发不追高硬约束(9/5主人裁决A,STYLE-GATE-STAGE5); MACD分析须用macd skill(plotille 6.0.5)+本地源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
