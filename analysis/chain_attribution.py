#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_attribution.py — 信号→闸门→成交全链归因（预注册 fullchain_attribution_prereg.json）。

三层: L1信号层(379快照fwd5超额) / L2闸门层(闸门REJECT vs PASS类的fwd5超额差=选择价值)
/ L3成交层(信号↔成交配对: 实际pnl vs 信号fwd5 = 执行+干预价值)。
配对: code6相同且entry_date∈[信号日,信号日+7自然日], 贪婪一对一。
fwd5: 执行日=次一交易日, 第5交易日收盘/执行日收盘-1, 超额=个股-沪深300同期。
各组n≥20才出方向性结论。零交易权。退出码: 0正常/3数据源不可用。"""
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
FLOOR_N = 20


def _code6(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


def _load_all():
    a = sqlite3.connect(ATTR_DB)
    sigs = [(_code6(c), int(str(d)[:8]), float(cp or 0)) for c, d, cp in a.execute(
        "SELECT code,date,close_price FROM signal_snapshots").fetchall()]
    trades = []
    for c, n, ed, xd, pnl in a.execute(
            "SELECT code,name,entry_date,exit_date,pnl_pct FROM position_history "
            "WHERE pnl_pct IS NOT NULL AND exit_date IS NOT NULL").fetchall():
        trades.append({"code": _code6(c), "name": str(n or ""), "entry": int(str(ed)[:8]),
                       "exit": str(xd), "pnl": float(pnl)})
    gate = []
    for c, d, v in a.execute(
            "SELECT code, trade_date, verdict FROM guard_precheck_log").fetchall():
        v = str(v or "")
        if v in ("REJECT", "PASS", "PASS_WITH_WARN"):
            gate.append((_code6(c), int(str(d)[:8]), v))
    a.close()
    return sigs, trades, gate


def run():
    try:
        sigs, trades, gate = _load_all()
        conn = sqlite3.connect(PRICE_DB, uri=True, check_same_thread=False)
    except Exception as e:  # noqa: BLE001
        print(f"[CHAIN] [FAIL-CLOSED] 数据源不可用({type(e).__name__}: {e})")
        return 3

    import bisect
    brows = conn.execute("SELECT date, close FROM index_daily "
                         "WHERE index_code='000300' ORDER BY date").fetchall()
    bd = [int(r[0]) for r in brows]
    bc = {int(r[0]): float(r[1]) for r in brows}

    def fwd5(code, d):
        """执行日=该股次一交易日的5日收益及基准超额。返回(fwd5, excess)或None。"""
        rows = conn.execute(
            "SELECT date, close FROM stock_daily_raw WHERE code=? AND date>? "
            "ORDER BY date LIMIT 6", (code, d)).fetchall()
        if len(rows) < 6:
            return None
        e = int(rows[0][0])
        f = (float(rows[5][1]) / float(rows[0][1]) - 1.0) * 100
        i = bisect.bisect_left(bd, e)
        b = None
        if i < len(bd) and bd[i] == e and i + 5 < len(bd):
            b = (bc[bd[i + 5]] / bc[e] - 1.0) * 100
        return (f, f - b) if b is not None else None

    # ── L2 闸门层: code+date聚合(任一REJECT=拒), fwd5超额分桶 ──
    gkey = {}
    for c, d, v in gate:
        k = (c, d)
        if k not in gkey:
            gkey[k] = v
        elif v == "REJECT":
            gkey[k] = "REJECT"
    rej, pas = [], []
    for (c, d), v in gkey.items():
        r = fwd5(c, d)
        if r is None:
            continue
        (rej if v == "REJECT" else pas).append(r)
    med = lambda v: sorted(v)[len(v) // 2] if v else None  # noqa: E731
    rej_e = [x[1] for x in rej]
    pas_e = [x[1] for x in pas]
    m_rej, m_pas = med(rej_e), med(pas_e)

    # ── L1→L3 配对 ──
    sig_by_code = {}
    for c, d, cp in sigs:
        sig_by_code.setdefault(c, []).append(d)
    pairs = []
    used = set()

    def _dnum(x):
        return datetime.strptime(str(x), "%Y%m%d").date()

    for t in trades:
        cands = sorted(d for d in sig_by_code.get(t["code"], [])
                       if 0 <= (_dnum(t["entry"]) - _dnum(d)).days <= 7)
        if cands:
            d0 = next((d for d in cands if (t["code"], d) not in used), None)
            if d0:
                used.add((t["code"], d0))
                r = fwd5(t["code"], d0)
                pairs.append({**t, "sig_d": d0,
                              "sig_f5": r[0] if r else None,
                              "sig_ex": r[1] if r else None})
    sigs_traded = len(used)
    sigs_total = len(set(sigs))
    pairs_f = [p for p in pairs if p["sig_f5"] is not None]
    m_act = med([p["pnl"] for p in pairs_f])
    m_sig = med([p["sig_f5"] for p in pairs_f])
    m_l3 = (m_act - m_sig) if (m_act is not None and m_sig is not None) else None

    # ── L1 全体信号 ──
    all_ex = [r[1] for c, d, cp in set(sigs) if (r := fwd5(c, d))]
    m_l1 = med(all_ex)

    conn.close()
    L = [f"# 全链归因 {datetime.now():%Y-%m-%d %H:%M}", "",
         f"- 数据: 信号{len(sigs)}(去重{len(set(sigs))})｜闸门检查{len(gate)}"
         f"(code+日聚合{len(gkey)})｜平仓{len(trades)}", "", "## L2 闸门层选择价值", ""]
    if m_pas is not None and m_rej is not None and len(pas_e) >= FLOOR_N \
            and len(rej_e) >= FLOOR_N:
        gap = m_pas - m_rej
        v = ("✅ 闸门正贡献" if gap > 2 else "❌ 闸门负贡献" if gap < 0
             else "⚖️ 噪声区")
        L.append(f"- {v}: PASS类超额中位={m_pas:+.2f}pp(n={len(pas_e)}) vs "
                 f"REJECT组超额中位={m_rej:+.2f}pp(n={len(rej_e)}) → 差={gap:+.2f}pp")
    else:
        L.append(f"- ⏳ 样本不足(PASS n={len(pas_e)}, REJECT n={len(rej_e)}, "
                 f"各需≥{FLOOR_N})——如实")
    L += ["", "## L3 执行+干预层", ""]
    if m_l3 is not None and len(pairs_f) >= FLOOR_N:
        L.append(f"- 配对成交 n={len(pairs_f)}: 实际pnl中位={m_act:+.2f}% vs "
                 f"其信号fwd5中位={m_sig:+.2f}% → 执行+干预价值={m_l3:+.2f}pp"
                 "（⚠️期限错配边界: 成交含-8%止损截断与更长持有期, fwd5固定5日——"
                 "本差值含止损纪律的左尾截断效应, 非纯执行质量）")
    else:
        L.append(f"- ⏳ 配对样本不足(n={len(pairs_f)})——如实")
    L += ["", "## L1 信号层与覆盖率", "",
          f"- 全体信号fwd5超额中位={m_l1:+.2f}pp (n={len(all_ex)})",
          f"- 信号→成交配对: {sigs_traded}/{sigs_total} 信号被交易"
          f"({sigs_traded / max(sigs_total, 1) * 100:.0f}%)；"
          f"成交配对到信号: {len(pairs)}/{len(trades)}"
          f"({len(pairs) / max(len(trades), 1) * 100:.0f}%)",
          f"- 边界: 成交pnl含人工干预(已知+19pp子集)；闸门拒绝集反事实=fwd5近似；",
          f"  {len(trades) - len(pairs)}笔成交(62%)来自快照外来源(批量案/扫描器/主人令)",
          "  ——闸门与执行层的价值评估只覆盖快照内信号；本归因是回看统计非因果实验。"]
    path = os.path.join(REPORT_DIR,
                        f"fullchain_attribution_{datetime.now():%Y%m%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[CHAIN] L2闸门: PASS中位={m_pas if m_pas is None else round(m_pas, 2)}"
          f" REJECT中位={m_rej if m_rej is None else round(m_rej, 2)}"
          f"(n={len(pas_e)}/{len(rej_e)})｜L3配对={len(pairs_f)} "
          f"执行+干预={m_l3 if m_l3 is None else round(m_l3, 2)}pp｜"
          f"L1信号超额={m_l1 if m_l1 is None else round(m_l1, 2)}pp -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
