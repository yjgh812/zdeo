#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dualkill_p3_price30_20261023.py — 10/19双杀协议 P3: 价格30断点+闸门消融(只读统计)
================================================================================
依据: dualkill_verification_protocol_20261019 建议2(主人批准 P3):
  重点"价格>30不买"断点: 被价格闸排除票的后续20日超额是否为正、排除率2026是否升高。
事实边界: 协议所称"18条闸门"为 pre_trade_check 下单闸; 回测腿内嵌实体闸仅
  价格≤30(+默认关的M-2/画像/行业动量开关), 情绪乘数为权重非闸门 → 消融对象=价格30闸。
方法: 逐调仓日双调 bp_ep_signal(gated/raw) → gated-Top15 与 raw-Top15(含>30票),
  被排30组=raw15中qfq收盘>30者; 三组合 fwd20 超额(全宇宙等权基准)。
零交易权纯呈现; 判定呈报主人(接线撞宪法§10.3 须主人裁决)。
"""
import json
import os
import sqlite3
import statistics
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT_DIR = os.path.join(ROOT, "reports", "dualkill_p3_price30_20261023")
WINDOW_START = "2025-01-01"
HOLD_BARS = 20
TOP_N = 15
REBALANCE_BARS = 22


def dash(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def load_px(d0, d1):
    con = sqlite3.connect(QFQ, uri=True)
    try:
        rows = con.execute(
            "SELECT date, code, close FROM stock_daily_meta_qfq "
            "WHERE date>=? AND date<=? AND close>0",
            (int(d0.replace("-", "")), int(d1.replace("-", "")))).fetchall()
    finally:
        con.close()
    days = sorted({str(r[0]) for r in rows})
    px = defaultdict(dict)
    for dt, code, c in rows:
        px[str(code).zfill(6)][str(dt)] = float(c)
    return days, px


def fwd_excess(px, codes, day_idx, days, i, h=HOLD_BARS):
    """组合(等权)未来h日收益 - 全宇宙等权基准未来h日收益。"""
    if i + h >= len(days):
        return None
    ed = days[i + h]
    sd = days[i]
    mk = []
    codes_all = sorted(px.keys())
    step = max(1, len(codes_all) // 500)
    for c in codes_all[::step]:
        a = px[c].get(sd)
        b = px[c].get(ed)
        if a and b:
            mk.append(b / a - 1.0)
    if len(mk) < 50:
        return None
    mk_ret = sum(mk) / len(mk)
    rets = []
    for c in codes:
        p = px.get(c)
        if not p:
            continue
        a = p.get(sd)
        b = p.get(ed)
        if a and b:
            rets.append(b / a - 1.0)
    if len(rets) < max(5, TOP_N // 2):
        return None
    return (sum(rets) / len(rets)) - mk_ret, len(rets)


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ladder_tpl",
                                                  os.path.join(HERE, "bt_full_report_ladder_20261019.py"))
    tpl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tpl)
    loader = tpl.TulongDataLoader(tpl.DB_PATH, tpl.QFQ_PATH)

    days, px = load_px(WINDOW_START, "2026-12-31")
    day_idx = {d: i for i, d in enumerate(days)}
    rbs = [days[i] for i in range(0, len(days), REBALANCE_BARS) if i + HOLD_BARS < len(days)]
    print(f"[P3] 窗口交易日 {len(days)} 调仓批 {len(rbs)}")

    rows = []
    for rb in rbs:
        i = day_idx[rb]
        gated, raw = {}, {}
        try:
            os.environ.pop("TULONG_LADDER_SIGNAL", None)
            gated, _ = tpl.bp_ep_signal(loader, dash(rb))
            os.environ["TULONG_LADDER_SIGNAL"] = "raw"
            raw, _ = tpl.bp_ep_signal(loader, dash(rb))
        finally:
            os.environ.pop("TULONG_LADDER_SIGNAL", None)
        if not gated or not raw:
            continue
        g15 = sorted(gated, key=lambda c: -gated[c])[:TOP_N]
        r15 = sorted(raw, key=lambda c: -raw[c])[:TOP_N]
        # qfq 当日收盘判 >30 (与信号close同源性近似, 口径注记)
        excl30 = [c for c in r15 if (px.get(str(c).zfill(6), {}).get(rb) or 0) > 30]
        g_ex = fwd_excess(px, [str(c).zfill(6) for c in g15], day_idx, days, i)
        e_ex = fwd_excess(px, [str(c).zfill(6) for c in excl30], day_idx, days, i)
        rows.append({"date": rb, "year": rb[:4],
                     "excl_rate": round(len(excl30) / len(r15), 3),
                     "gated_excess": round(g_ex[0], 4) if g_ex else None,
                     "excl30_excess": round(e_ex[0], 4) if e_ex else None,
                     "n_excl30": len(excl30)})

    by_year = defaultdict(lambda: {"batches": 0, "excl_rates": [], "gated": [], "excl": []})
    for r in rows:
        y = by_year[r["year"]]
        y["batches"] += 1
        y["excl_rates"].append(r["excl_rate"])
        if r["gated_excess"] is not None:
            y["gated"].append(r["gated_excess"])
        if r["excl30_excess"] is not None:
            y["excl"].append(r["excl30_excess"])
    summary = {}
    for y, v in sorted(by_year.items()):
        def ms(a):
            return round(sum(a) / len(a), 4) if a else None
        spread = (ms(v["excl"]) - ms(v["gated"])) if v["excl"] and v["gated"] else None
        summary[y] = {"batches": v["batches"],
                      "avg_excl_rate": ms(v["excl_rates"]),
                      "gated_top15_fwd20_excess": ms(v["gated"]),
                      "excl30_top15_fwd20_excess": ms(v["excl"]),
                      "excl_minus_gated_spread": round(spread, 4) if spread is not None else None}
    out = {"window": WINDOW_START, "per_batch": rows, "by_year": summary,
           "scope_note": "回测腿实体闸仅价格30(+默认关开关族), 情绪乘数为权重非闸门; "
                         "18条闸门为pre_trade_check下单闸不在回测腿内",
           "verdict_rule": "被拒票(>30组Top15)后续超额持续为正且2026升高→闸门机会成本实证; "
                           "接线撞宪法§10.3须主人裁决",
           "zero_trade_power": True}
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(out, open(os.path.join(OUT_DIR, "p3_price30_result.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
