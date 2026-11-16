#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_engine.py — 组合循环引擎（T+1/封板拦截/量帽/整手/风控框架/逐笔留痕）。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import market_rules_config as mrc  # noqa: E402
import stock_name_lookup as snl  # noqa: E402
from fulldomain_data import (
    CASH0, END, LOT, LIQ_MIN, MAX_POS_PCT, IND_CAP, CASH_FLOOR,
    SLIPPAGE, START, STOP_PCT, TRAIL_PCT, VOL_CAP, sealed_down, sealed_up,
)


def _fees(notional, sell, date):
    comm = max(notional * 0.00025, 5.0)
    transfer = notional * 0.00001
    stamp = notional * mrc.stamp_duty_rate(date) if sell else 0.0
    return comm + transfer + stamp


def _code6(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


def snl_name(code):
    try:
        return snl.get_name(code) or code
    except Exception:
        return code


def _equity(cash, pos, D, dmap, d, prev=True):
    """权益 = 现金 + Σ 股数×原始收盘(最近一根)。"""
    tot = cash
    for c, p in pos.items():
        i = dmap.get(c, {}).get(d)
        if i is None or i == 0:
            continue
        j = i - 1 if prev else i
        tot += p["sh"] * float(D[c]["rc"][j])
    return tot


def _last_i(dmap, code, d):
    return dmap.get(code, {}).get(d)


def _val_i(D, code, d):
    """最近一根 ≤d 的bar序号(停牌鲁棒)。"""
    import bisect
    ds = D[code]["d"]
    return bisect.bisect_right(ds, int(d)) - 1


def _equity(cash, pos, D, d):
    tot = cash
    for c, p in pos.items():
        j = _val_i(D, c, d)
        if j >= 0:
            tot += p["sh"] * float(D[c]["rc"][j])
    return tot


def _exec_sells(D, dmap, d, cash, pos, pend_s, trades, events):
    still = []
    for code, reason, _trig in pend_s:
        p = pos.get(code)
        if p is None:
            continue
        i = dmap.get(code, {}).get(d)
        if i is None or sealed_down(D, code, i):
            still.append((code, reason, _trig))
            events.append({"d": d, "code": code, "ev": "SELL_DEFER",
                           "why": "封死跌停/停牌"})
            continue
        px = float(D[code]["ro"][i]) * (1 - SLIPPAGE)
        nt = px * p["sh"]
        cash += nt - _fees(nt, True, d)
        qexit = float(D[code]["qc"][i])
        trades.append({
            "code": code, "name": snl_name(code), "model": p["model"],
            "entry_d": p["ed"], "entry_px": p["ep_raw"],
            "exit_d": d, "exit_px": px, "shares": p["sh"],
            "pnl_pct": (qexit / p["cq"] - 1.0) * 100,
            "reason_out": reason, "reason_in": p["rin"],
            "ind": p["ind"], "hold": i - p["ei"]})
        del pos[code]
    return cash, still


def _atrp(D, code, i):
    """ATR14/raw收盘（比价无量纲）。i不足15根返回None。"""
    if i < 15:
        return None
    v = D[code]
    trs = []
    for j in range(i - 13, i + 1):
        h, l = float(v["rh"][j]), float(v["rl"][j])
        pc = float(v["rc"][j - 1])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    c0 = float(v["rc"][i])
    return sum(trs) / 14.0 / c0 if c0 > 0 else None


def _exec_buys(D, dmap, d, cash, pos, pend_b, events, ind, model,
               sizing_mult_fn=None, gate_block=False, tp_atr_mult=None):
    for code, reason, _sig in pend_b:
        if gate_block:
            events.append({"d": d, "code": code, "ev": "GATE_BLOCKED",
                           "why": "买入闸门抑制(冰点禁开)"})
            continue
        i = dmap.get(code, {}).get(d)
        if i is None or sealed_up(D, code, i):
            events.append({"d": d, "code": code,
                           "ev": "LIMIT_UP_LOCKED" if i is not None else "NO_BARS",
                           "why": reason})
            continue
        if code in pos or float(D[code]["ramt"][i]) < LIQ_MIN:
            if code not in pos:
                events.append({"d": d, "code": code, "ev": "ILLIQUID",
                               "why": reason})
            continue
        equity = _equity(cash, pos, D, d)
        if equity <= 0:
            break
        industry = ind.get(code) or "?"
        used_ind = sum(p["sh"] * float(D[c]["rc"][_val_i(D, c, d)])
                       for c, p in pos.items() if p["ind"] == industry)
        if used_ind + equity * MAX_POS_PCT > equity * IND_CAP:
            events.append({"d": d, "code": code, "ev": "IND_CAP", "why": reason})
            continue
        floor_cash = CASH_FLOOR * equity
        mult = (sizing_mult_fn(code, d) or 1.0) if sizing_mult_fn else 1.0
        budget = min(MAX_POS_PCT * equity * mult,
                     max(cash - floor_cash, 0.0))
        px = float(D[code]["ro"][i]) * (1 + SLIPPAGE)
        if budget < px * LOT:
            events.append({"d": d, "code": code, "ev": "NO_CASH", "why": reason})
            continue
        cap = int(float(D[code]["rv"][i]) * VOL_CAP) // LOT * LOT
        shares = min(int(budget / px), cap) // LOT * LOT
        if shares < LOT:
            events.append({"d": d, "code": code, "ev": "VOL_CAP", "why": reason})
            continue
        nt = px * shares
        fees = _fees(nt, False, d)
        if cash < nt + fees:
            events.append({"d": d, "code": code, "ev": "NO_CASH", "why": reason})
            continue
        cash -= nt + fees
        cq_entry = float(D[code]["qo"][i]) * (1 + SLIPPAGE)
        pos[code] = {"sh": shares, "cq": cq_entry,
                     "ep_raw": px, "ed": d, "ei": i,
                     "peak": cq_entry,
                     "rin": reason, "ind": industry, "model": model,
                     "atrp": (_atrp(D, code, i) if tp_atr_mult else None)}
    return cash


def run_model(D, excl, ind, dmap, cal, model,
              scan_signals=None, exit_extra=None,
              rebal_fn=None, rebal_days=None,
              stop_pct=STOP_PCT, trail_pct=TRAIL_PCT,
              sizing_mult_fn=None, buy_gate_fn=None,
              tp_atr_mult=None):
    cash = CASH0
    pos = {}
    pend_s, pend_b, queued = [], [], set()
    trades, events, nav = [], [], []
    rdays = set(rebal_days or [])
    for d in cal:
        if d < START or d > END:
            continue
        cash, pend_s = _exec_sells(D, dmap, d, cash, pos, pend_s, trades, events)
        gate_block = bool(buy_gate_fn and buy_gate_fn(d))
        cash = _exec_buys(D, dmap, d, cash, pos, pend_b, events, ind, model,
                          sizing_mult_fn, gate_block, tp_atr_mult)
        pend_b, queued = [], set()
        # ---- 风控退出(T收盘判定 → T+1执行) ----
        for code in list(pos.keys()):
            p = pos[code]
            i = dmap.get(code, {}).get(d)
            if i is None or i <= p["ei"]:
                continue
            qc = float(D[code]["qc"][i])
            p["peak"] = max(p["peak"], qc)
            reason = None
            if qc <= p["cq"] * stop_pct:
                reason = f"S-5止损(-{round((1 - stop_pct) * 100)}%)"
            elif (tp_atr_mult is not None and p.get("atrp")
                  and qc >= p["cq"] * (1.0 + tp_atr_mult * p["atrp"])):
                reason = (f"ATR止盈(+{round(tp_atr_mult * p['atrp'] * 100)}%)")
            elif qc <= p["peak"] * trail_pct:
                reason = "波动止盈(峰值回撤8pp)"
            elif exit_extra is not None:
                reason = exit_extra(code, i)
            if reason:
                pend_s.append((code, reason, i))
        # ---- 买入信号 ----
        if scan_signals is not None:
            for code, sset in scan_signals.items():
                if code in pos or code in queued:
                    continue
                i = dmap.get(code, {}).get(d)
                if i is not None and i in sset:
                    pend_b.append((code, f"{model}: 触发信号", i))
                    queued.add(code)
        if rebal_fn is not None and d in rdays:
            top_list = rebal_fn(d)
            top_set = set(top_list)
            for c in list(pos.keys()):
                if c not in top_set and c not in queued:
                    pend_s.append((c, "调仓退出(跌出排名)", d))
                    queued.add(c)
            for c in top_list:
                if c not in pos and c not in queued:
                    pend_b.append((c, f"{model}: 调仓买入", None))
                    queued.add(c)
        nav.append((d, _equity(cash, pos, D, d)))
    return {"trades": trades, "events": events, "nav": nav,
            "final_cash": cash, "pos_left": pos}
