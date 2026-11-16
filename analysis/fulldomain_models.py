#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_models.py — 四模型信号预计算（预注册模型定义，仅用T日及以前数据）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

import os
import sys
sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
from fulldomain_data import (
    END, LIQ_MIN, START, sealed_up,  # noqa: F401
)


def _series_ok(D, code):
    """返回 (qc Series, vol Series) 或 None(数据不足)。"""
    v = D[code]
    if len(v["d"]) < 63:
        return None
    qc = pd.Series(v["qc"].astype(float))
    vol = pd.Series(v["qv"].astype(float))
    return qc, vol


def m1_box_breakout(D, excl, vol_mult=1.5):
    out = {}
    for code, v in D.items():
        if code in excl:
            continue
        sv = _series_ok(D, code)
        if sv is None:
            continue
        qc, vol = sv
        ma5v = vol.rolling(5).mean()
        box = qc.rolling(60).max().shift(1)
        cond = ((qc > box * 0.995) & (vol > ma5v * vol_mult)
                & (qc.index >= 60))
        idx = cond[cond].index.to_numpy()
        out[code] = set(int(i) for i in idx
                        if int(v["d"][i]) >= START and int(v["d"][i]) <= END)
    return out


def m2_trend_pullback(D, excl):
    out = {}
    for code, v in D.items():
        if code in excl:
            continue
        sv = _series_ok(D, code)
        if sv is None:
            continue
        qc, vol = sv
        ma10 = qc.rolling(10).mean()
        ma20 = qc.rolling(20).mean()
        ma60 = qc.rolling(60).mean()
        vma5 = vol.rolling(5).mean()
        cond = ((ma20 > ma60) & (qc <= ma10 * 1.02) & (qc >= ma10 * 0.98)
                & (vol < vma5 * 0.7) & (qc.index >= 60))
        idx = cond[cond].index.to_numpy()
        out[code] = set(int(i) for i in idx
                        if int(v["d"][i]) >= START and int(v["d"][i]) <= END)
    return out


def rebalance_days(cal, kind):
    """M3月度/M4周度: 返回调仓日(日期int)列表。"""
    out = []
    seen = set()
    for d in cal:
        if d < START or d > END:
            continue
        key = (d // 10000, d // 100 % 100) if kind == "monthly" else d // 7
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out