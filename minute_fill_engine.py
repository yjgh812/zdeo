#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""minute_fill_engine — 分钟撮合语义唯一 owner（v3，预注册 minute_fill_engine_v3_prereg.json）。

三件能力：
  lc5_day(code, date)          通达信5分钟K加载（实测DWORD高16位=时刻,低16位=日期）
  bar_at(bars, filled_hms)     按成交时刻定位所在5分钟K（兼容完整日期时间串）
  walk_fill(code, date, action, limit, qty)  限价单盘中撮合：
      触达即成交（买=bar低≤限价→成交=min(限价,bar开)；卖对称），
      单bar量≤25%帽，无量bar不成交，当日未成交作废。
      语义经 v1/v2 验收（652腿：守法100%、无溢价子集偏差0.181pp、溢价方向96.6%）。

v1/v2 判据历史见 minute_fill_engine_prereg.json / minute_fill_engine_v2_prereg.json。
消费方：replay_reconciliation（lc5_day/bar_at）、minute_fill_replay（walk_fill）。
接线(v3.1)与信号回放适配器统一消费本模块，禁止撮合语义第二实现。"""
from __future__ import annotations

import os

VIPDOC = r"D:\anzhuang\tdx\vipdoc"
VOL_CAP = 0.25


def lc5_day(code, d):
    """通达信5分钟K：返回 (全日最低, 全日最高, {bar结束时刻hhmm: (o,h,l,c,vol)})。
    无分钟数据→None。"""
    mkt = ('bj' if code.startswith(('4', '8', '92')) else
           ('sh' if code.startswith(('60', '68', '9')) else 'sz'))
    path = os.path.join(VIPDOC, mkt, 'fzline', f'{mkt}{code}.lc5')
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        raw = f.read()
    import struct
    d_lo = d_hi = None
    bars = {}
    for i in range(len(raw) // 32):
        v = struct.unpack_from('<I', raw, i * 32)[0]
        d_part = v & 0xFFFF
        year = d_part // 2048 + 2004
        if year * 10000 + (d_part % 2048) != d:
            continue
        o, h, l, c = struct.unpack_from('<ffff', raw, i * 32 + 4)
        vol = struct.unpack_from('<I', raw, i * 32 + 24)[0]
        d_lo = l if d_lo is None else min(d_lo, l)
        d_hi = h if d_hi is None else max(d_hi, h)
        t_part = v >> 16
        hhmm = t_part // 60 * 100 + t_part % 60
        bars[hhmm] = (o, h, l, c, vol)
    if d_lo is None:
        return None
    return (d_lo, d_hi, bars)


def bar_at(bars, filled_hms):
    """按成交时刻取所在5分钟K(bar结束=向上取整到5分钟)；返回(o,h,l,c,vol)或None。"""
    if not filled_hms or not bars:
        return None
    s = str(filled_hms).strip().split(" ")[-1]
    try:
        parts = s.split(":")
        hh, mm = int(parts[0]), int(parts[1])
    except Exception:
        return None
    tmin = hh * 60 + mm
    if tmin >= 14 * 60 + 57:
        end = 1500
    else:
        end = (tmin // 5 + 1) * 5
    return bars.get((end // 60) * 100 + end % 60)


def walk_fill(code, d, action, limit, qty, day_cache=None):
    """限价单盘中撮合（v1/v2验收语义）。返回
    {fills:[(hhmm,px,qty)...], filled, avg或None, expired}；无分钟数据→None。"""
    cache = day_cache if day_cache is not None else {}
    if (code, d) not in cache:
        cache[(code, d)] = lc5_day(code, d)
    day = cache[(code, d)]
    if not day:
        return None
    bars = day[2]
    filled = 0
    fills = []
    for hhmm in sorted(bars):
        o, h, l, c, v = bars[hhmm]
        if v <= 0:
            continue
        cap = int(v * VOL_CAP) // 100 * 100
        take = min(qty - filled, cap)
        if take <= 0:
            continue
        if action == "BUY" and l <= limit:
            px = min(limit, o)
            fills.append((hhmm, px, take))
            filled += take
        elif action == "SELL" and h >= limit:
            px = max(limit, o)
            fills.append((hhmm, px, take))
            filled += take
        if filled >= qty:
            break
    if not fills:
        return {"fills": [], "filled": 0, "avg": None, "expired": True}
    avg = sum(px * q for _, px, q in fills) / sum(q for _, _, q in fills)
    return {"fills": fills, "filled": filled, "avg": avg,
            "expired": filled < qty}
