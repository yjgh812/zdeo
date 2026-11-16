#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dualkill_p2_gap_vwap_20261023.py — 10/19双杀协议 P2: T+1开盘缺口分层 + 30分钟VWAP反事实
======================================================================================
依据: dualkill_verification_protocol_20261019 建议3(主人批准 P2 链):
  gap五桶(<-2/-2~0/0~2/2~4/>4%), 高开>2~3%桶胜率显著低且30分钟VWAP反事实改善胜率/盈亏比、
  右尾保留>=95% → 模拟盘测"高开过滤/延迟成交"; VWAP改善但砍右尾 → 不接线。
数据: L4在档账本(reports/backtest_full_20261019_ladder_debug, entry>=2025-01-01)
      + qfq日频(open/prev_close) + vipdoc分钟线(tdx_reader, 收盘分钟口径575=09:35)。
VWAP代理: 9:35-10:00五根bar的成交量加权收盘 Σ(vol*c)/Σ(vol)(单位不变量, 与amount口径解耦)。
只读研究件: 零交易权零生产链改动。样本功效注记: n=123, 分桶后格内n<30按协议不下强结论。
"""
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import tdx_reader  # 规范口径单一真源

L4_CSV = os.path.join(ROOT, "reports", "backtest_full_20261019_ladder_debug",
                      "bp_ep_core_trades.csv")
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT_DIR = os.path.join(ROOT, "reports", "dualkill_p2_gap_vwap_20261023")
BUCKETS = [(-99, -2), (-2, 0), (0, 2), (2, 4), (4, 99)]
BUCKET_NAMES = ["<-2%", "-2~0%", "0~2%", "2~4%", ">4%"]


def bucket_of(gap_pct):
    for (lo, hi), name in zip(BUCKETS, BUCKET_NAMES):
        if lo <= gap_pct < hi:
            return name
    return BUCKET_NAMES[-1]


def stats(rets):
    n = len(rets)
    if not n:
        return {"n": 0}
    wins = [t for t in rets if t > 0]
    losses = [t for t in rets if t <= 0]
    pl = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else None
    return {"n": n, "win": round(len(wins) / n, 4),
            "avg_pct": round(sum(rets) / n, 3),
            "sum_pct": round(sum(rets), 1),
            "pl": round(pl, 3) if pl else None}


def main():
    # 1. L4 账本
    trades = []
    with open(L4_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("entry_date") or "") < "2025-01-01":
                continue
            try:
                trades.append({"code": str(r["symbol"]).zfill(6),
                               "entry": r["entry_date"], "exit": r["exit_date"],
                               "entry_px": float(r["entry_price"]),
                               "exit_px": float(r["exit_price"]),
                               "pnl": float(r["pnl_pct"])})
            except (KeyError, ValueError):
                continue
    print(f"[P2] L4 逐笔 {len(trades)} 笔(2025+)")

    # 2. qfq: 入场日 open + 前收盘(逐code一次拉全史close序列)
    con = sqlite3.connect(QFQ, uri=True)
    codes = sorted({t["code"] for t in trades})
    hist = {}
    for c in codes:
        try:
            hist[c] = con.execute(
                "SELECT date, open, close FROM stock_daily_meta_qfq "
                "WHERE code=? ORDER BY date", (c,)).fetchall()
        except sqlite3.Error:
            hist[c] = []
    con.close()

    gap_info = {}
    for i, t in enumerate(trades):
        d = t["entry"].replace("-", "")  # 库内date为字符串, 保持同型
        rows = hist.get(t["code"], [])
        dates = [r[0] for r in rows]
        if d not in dates:
            continue
        k = dates.index(d)
        op = rows[k][1]
        pc = rows[k - 1][2] if k > 0 else None
        if not op or not pc:
            continue
        gap_info[i] = {"gap_pct": round((op / pc - 1) * 100, 3), "open": op}

    # 3. 缺口五桶 × 实际盈亏
    buckets = defaultdict(list)
    for i, g in gap_info.items():
        buckets[bucket_of(g["gap_pct"])].append(trades[i]["pnl"])
    gap_table = {name: stats(buckets[name]) for name in BUCKET_NAMES}

    # 高开>2% vs 其余(协议关注桶)
    hi = [trades[i]["pnl"] for i, g in gap_info.items() if g["gap_pct"] >= 2]
    lo = [trades[i]["pnl"] for i, g in gap_info.items() if g["gap_pct"] < 2]
    hi_s, lo_s = stats(hi), stats(lo)

    # 4. VWAP 反事实: 原始价空间自洽计算(.lc5=不复权; qfq混space=伪影, 10/23实测-5%系统性偏移)
    #    原始口径: tdx read_day 的 raw open/exit + 原始VWAP; orig_raw 为对照组(同space)
    alt, missing = {}, 0
    day_cache = {}

    def raw_days(code, mkt):
        if code not in day_cache:
            try:
                day_cache[code] = {int(r[0]): r for r in tdx_reader.read_day(market=mkt, code=code)}
            except Exception:
                day_cache[code] = {}
        return day_cache[code]

    for i, g in gap_info.items():
        t = trades[i]
        d_int = int(t["entry"].replace("-", ""))
        mkt = "sh" if t["code"].startswith(("6", "9")) else "sz"
        try:
            bars = [b for b in tdx_reader.minute_bars_on(market=mkt, code=t["code"],
                                                         date_int=d_int)
                    if 935 <= int(b["time"]) <= 1000]  # 09:35..10:00 六根收盘分钟bar
        except Exception:
            bars = []
        days = raw_days(t["code"], mkt)
        raw_open = days.get(d_int, (None,))[1]
        raw_exit = days.get(int(t["exit"].replace("-", "")), (None,))[4]
        if not bars or not raw_open or not raw_exit:
            missing += 1
            continue
        vwap = sum(float(b["vol"]) * float(b["c"]) for b in bars) / sum(float(b["vol"]) for b in bars)
        if not vwap or vwap <= 0:
            missing += 1
            continue
        alt[i] = {"vwap": round(vwap, 4),
                  "gap_vs_vwap_pct": round((raw_open / vwap - 1) * 100, 3),
                  "pnl_orig_raw": round((raw_exit / raw_open - 1) * 100, 3),
                  "pnl_alt": round((raw_exit / vwap - 1) * 100, 3),
                  "pnl_qfq_ledger": t["pnl"]}

    orig_all = [v["pnl_orig_raw"] for v in alt.values()]
    alt_all = [v["pnl_alt"] for v in alt.values()]
    alt_by_bucket = defaultdict(list)
    for i, v in alt.items():
        alt_by_bucket[bucket_of(gap_info[i]["gap_pct"])].append(v["pnl_alt"])
    alt_table = {name: stats(alt_by_bucket[name]) for name in BUCKET_NAMES}
    # 右尾保留: 原口径Top5%盈利单在反事实下的收益保留率
    order = sorted(alt.values(), key=lambda v: -v["pnl_orig_raw"])
    k = max(1, int(0.05 * len(order)))
    top = order[:k]
    tail_keep = (sum(v["pnl_alt"] for v in top) / sum(v["pnl_orig_raw"] for v in top)
                 if sum(v["pnl_orig_raw"] for v in top) > 0 else None)

    result = {
        "window": "2025-01-01~", "n_trades": len(trades), "n_with_gap": len(gap_info),
        "n_minute_missing": missing,
        "gap_buckets_actual": gap_table,
        "gap_buckets_vwap_alt": alt_table,
        "high_open_ge2pct": {"actual": hi_s, "rest": lo_s},
        "vwap_counterfactual_overall": {"actual": stats(orig_all), "alt": stats(alt_all),
                                        "tail5_retention": round(tail_keep, 3) if tail_keep else None},
        "power_note": "n=123 分桶后格内 n<30, 按协议不下强结论; 方向性呈现",
        "vwap_proxy_def": "9:35-10:00五bar成交量加权收盘(单位不变量, 非Σamount/Σvol)",
        "zero_trade_power": True}
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(result, open(os.path.join(OUT_DIR, "p2_gap_vwap_result.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"buckets": gap_table, "high_open": hi_s, "rest": lo_s,
                      "vwap_alt_overall": result["vwap_counterfactual_overall"]["alt"],
                      "tail5_retention": result["vwap_counterfactual_overall"]["tail5_retention"],
                      "minute_missing": missing}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
