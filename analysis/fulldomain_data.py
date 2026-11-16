#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_data.py — 全域回测数据层（唯一数据 owner，预注册 fulldomain_multimodel_prereg）。

装载 20151001 起 qfq 与 raw 双价数组按日期对齐；股票池过滤（主板+创业板代码前缀、
ST 双标记、次新60日、流动性）；封板判定（mrc 分段）。参数绑定查询。"""
from __future__ import annotations

import sqlite3

import numpy as np

sys_path_fixed = None
import sys
sys.path.insert(0, r"D:\zcodesj")
import market_rules_config as mrc  # noqa: E402

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
RAW_DB = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
          + "?mode=ro")
SNAPSHOT_DB = r"D:\zcodesj\data\fulldomain\snapshot_20261108.db"


def src(db):
    """快照优先: 快照在位则一律读钉定数据(修复WAL并行写导致的读态漂移)。"""
    import os
    if os.path.exists(SNAPSHOT_DB) and db not in (QFQ_DB,):
        return SNAPSHOT_DB
    return db


def src_qfq():
    import os
    return SNAPSHOT_DB if os.path.exists(SNAPSHOT_DB) else QFQ_DB
CASH0 = 100_000.0
WARMUP = 20151001
START = 20160104
END = 20261106
SLIPPAGE = 0.001
VOL_CAP = 0.25
LOT = 100
MAX_POS_PCT = 0.15
IND_CAP = 0.30
CASH_FLOOR = 0.10
STOP_PCT = 0.92
TRAIL_PCT = 0.92
LIQ_MIN = 10_000_000.0
MODELS = ("M1_box_breakout", "M2_trend_pullback",
          "M3_value_monthly", "M4_momentum_weekly")


def _code6(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


def load_universe():
    """返回 (excl, ind, listd)。excl=ST/退集合; ind=行业表; listd=上市日。"""
    conn = sqlite3.connect(src(RAW_DB), uri=("file:" in src(RAW_DB)),
                           check_same_thread=False)
    excl, ind, listd = set(), {}, {}
    for c, st, nm, indus, ld in conn.execute(
            "SELECT code, is_st, name, industry, list_date FROM stock_info"):
        k = _code6(c)
        if (str(st or "") == "1" or "ST" in str(nm or "")
                or "退" in str(nm or "")):
            excl.add(k)
        if indus:
            ind[k] = str(indus)
        if ld:
            listd[k] = int(str(ld)[:8])
    conn.close()
    return excl, ind, listd


def _load_side(db, table, cols):
    conn = sqlite3.connect(src(db), uri=("file:" in src(db)),
                           check_same_thread=False)
    cur = conn.execute(
        "SELECT code, date, " + ",".join(cols) + " FROM " + table
        + " WHERE date >= ? ORDER BY code, date", (WARMUP,))
    D = {}
    cur_code, rows = None, []
    for code, d, *vals in cur:
        k = _code6(code)
        if k.startswith(("68", "4", "8", "92")):
            continue
        if k != cur_code:
            if cur_code is not None:
                D[cur_code] = {c: np.asarray(a)
                               for c, a in zip(["date"] + cols, zip(*rows))}
            cur_code, rows = k, []
        rows.append((d, *vals))
    if cur_code is not None:
        D[cur_code] = {c: np.asarray(a)
                       for c, a in zip(["date"] + cols, zip(*rows))}
    conn.close()
    return D


def load_bars():
    """返回 (DQ, DR, cal)。DQ/DR: code→{date,o,h,l,c,v/amt}; cal=交易日列表。"""
    dq = _load_side(src_qfq(), "stock_daily_meta_qfq",
                    ["open", "high", "low", "close", "volume"])
    dr = _load_side(src(RAW_DB), "stock_daily_raw",
                    ["open", "high", "low", "close", "amt", "volume"])
    cal = sorted({int(d) for v in dq.values() for d in v["date"]})
    return dq, dr, cal


def align_pair(dq, dr):
    """按日期交集对齐双价: 返回 D={code:{d,qo,qh,qc,qv,ro,rh,rc,ramt,ridx}}。"""
    D = {}
    for code, qv in dq.items():
        rv = dr.get(code)
        if rv is None:
            continue
        qd = [int(x) for x in qv["date"]]
        rd = [int(x) for x in rv["date"]]
        ridx = {d: i for i, d in enumerate(rd)}
        keep = [(i, ridx[d]) for i, d in enumerate(qd) if d in ridx]
        if len(keep) < 63:
            continue
        qi, ri = zip(*keep)
        D[code] = {
            "d": np.asarray(qd, dtype=np.int32)[list(qi)],
            "qo": qv["open"][list(qi)].astype(np.float32),
            "qh": qv["high"][list(qi)].astype(np.float32),
            "qc": qv["close"][list(qi)].astype(np.float32),
            "qv": qv["volume"][list(qi)].astype(np.float64),
            "ro": rv["open"][list(ri)].astype(np.float32),
            "rh": rv["high"][list(ri)].astype(np.float32),
            "rl": rv["low"][list(ri)].astype(np.float32),
            "rc": rv["close"][list(ri)].astype(np.float32),
            "ramt": rv["amt"][list(ri)].astype(np.float64),
            "rv": rv["volume"][list(ri)].astype(np.float64),
        }
    return D


def sealed_up(D, code, i):
    """第i根bar封死涨停判定(原始价, mrc分段)。"""
    v = D[code]
    if i < 1:
        return False
    pct = mrc.limit_pct(code, int(v["d"][i]))
    lim = float(v["rc"][i - 1]) * (1.0 + pct)
    tol = max(lim * 1e-3, 1e-6)
    return float(v["rl"][i]) >= lim - tol


def sealed_down(D, code, i):
    v = D[code]
    if i < 1:
        return False
    pct = mrc.limit_pct(code, int(v["d"][i]))
    lim = float(v["rc"][i - 1]) * (1.0 - pct)
    tol = max(lim * 1e-3, 1e-6)
    return float(v["rh"][i]) <= lim + tol
