# -*- coding: utf-8 -*-
"""trade_records 写入守卫: 格式归一化查重 (2026-09-25, recon 根因守卫)
根因: 同笔成交三来源重复导入, 且 code/trade_date 格式漂移(sz002191 vs 002191 / 2026-07-22 vs 20260722)
击穿了 naked_trade_audit 内置 T8 五元组查重——不同格式互不匹配=各自"查无重复"。
本模块=唯一查重协议: 归一化到(6位裸码, YYYYMMDD)后再比对, 容忍价格±0.02。
"""
import sqlite3

TOL_PRICE = 0.02

def norm_code(code) -> str:
    s = str(code).strip().lower()
    for pfx in ("sz", "sh", "bj"):
        if s.startswith(pfx):
            s = s[len(pfx):]
    return s.zfill(6)[-6:]

def norm_date(d) -> str:
    return str(d).replace("-", "").replace(" ", "")[:8]

def exists(conn: sqlite3.Connection, code, trade_date, action, volume, price) -> bool:
    """归一化五元组查重: 同(裸码,日期,向,量)且价差<=TOL 即视为重复。
    兼容库内存量双格式: 用 LIKE 只做裸码/日期子串匹配, 再归一化精确比对。"""
    c6, d8 = norm_code(code), norm_date(trade_date)
    act = str(action).strip().upper()
    vol = int(volume or 0)
    rows = conn.execute(
        """SELECT code, trade_date, action, volume, price FROM trade_records
           WHERE REPLACE(REPLACE(code,'sz',''),'sh','') LIKE ?
             AND REPLACE(REPLACE(trade_date,'-',''),' ','') LIKE ?
             AND UPPER(action)=?""",
        (f"%{c6}%", f"{d8}%", act),
    ).fetchall()
    for rc, rd, ra, rv, rp in rows:
        if norm_code(rc) == c6 and norm_date(rd) == d8 and int(rv or 0) == vol:
            if price is None or rp is None:
                return True
            if abs(float(rp) - float(price)) <= TOL_PRICE:
                return True
    return False

def insert_checked(conn, table_cols, values, *, price_idx=None, code_idx=0, date_idx=None,
                   action_idx=None, volume_idx=None, log=print):
    """通用守卫写入: values 顺序与 table_cols 一致; 命中查重则拒绝并返回 False。
    price_idx 等为 values 中的下标; 未指定时按 table_cols 名推断。"""
    cols = [c.strip().lower() for c in table_cols]
    if price_idx is None:
        price_idx = cols.index("price") if "price" in cols else None
    if date_idx is None:
        date_idx = cols.index("trade_date") if "trade_date" in cols else None
    if action_idx is None:
        action_idx = cols.index("action") if "action" in cols else None
    if volume_idx is None:
        volume_idx = cols.index("volume") if "volume" in cols else None
    code = values[code_idx]
    d8 = values[date_idx]
    act = values[action_idx]
    vol = values[volume_idx]
    price = values[price_idx] if price_idx is not None else None
    if exists(conn, code, d8, act, vol, price):
        log(f"[guard] trade_records duplicate blocked: {norm_code(code)} {norm_date(d8)} "
            f"{act} x{vol} @{price} (归一化查重, 20260925守卫)")
        return False
    ph = ",".join("?" * len(cols))
    conn.execute(f"INSERT INTO trade_records ({','.join(cols)}) VALUES ({ph})", values)
    return True
