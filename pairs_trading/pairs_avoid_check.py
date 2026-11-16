# -*- coding: utf-8 -*-
"""
配对规避信号检查器 — 13:30盘中扫描用 (cron第三步)
================================================
对当前持仓逐只: 同申万一级行业 + 120日相关>0.5 + EG协整 → 价差z-score
z>2 = 相对高估规避提示(减持观察); z>3.5 = 强制减持评估
输出: stdout JSON(供cron汇报), 退出码0=无预警 1=有预警

数据: qfq日收益缓存(昨收为止, 盘中13:30用昨日截面前后一致)
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\pairs_trading")

CACHE = Path(r"D:\zcodesj\_cache")
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
QFQ_CHG_CACHE = CACHE / "qfq_chg_pivot_2019.pkl"
WHITELIST = ("60", "00", "30", "68", "92")
Z_WARN, Z_STOP = 2.0, 3.5
CORR_TH, COINT_P = 0.5, 0.05


def load_positions() -> list:
    """当前持仓 [(code, name)]。优先--codes(westock MCP实时持仓传入), 否则本地库兜底。"""
    if len(sys.argv) > 1 and sys.argv[1] == "--codes" and len(sys.argv) > 2:
        # 格式: --codes 600750,华润江中;000825,粤桂股份  (代码,名称分号分隔)
        out = []
        for item in sys.argv[2].split(";"):
            parts = item.split(",")
            code = parts[0].strip()[-6:]
            name = parts[1].strip() if len(parts) > 1 else _lookup_name(code)
            out.append((code, name))
        return out
    conn = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=60)
    try:
        # 2026-08-29: position_history=冻结历史表(写入链路死亡, OPEN 行可能过时,
        # kb/rules/data-field-semantics.md §五); 消费方迁移待下次触达(改 westock MCP 持仓)
        df = pd.read_sql_query(
            "SELECT code, name FROM position_history WHERE status='OPEN'", conn)
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()
    if df.empty:
        return []
    df["code"] = df["code"].astype(str).str[-6:]
    return list(df[["code", "name"]].drop_duplicates().itertuples(index=False))


def main():
    t0 = time.time()
    positions = load_positions()
    if not positions:
        print(json.dumps({"warning": [], "note": "无OPEN持仓记录"}, ensure_ascii=False))
        return 0
    with open(QFQ_CHG_CACHE, "rb") as f:
        chg = pickle.load(f)
    chg = chg.loc[[c for c in chg.index if c[:2] in WHITELIST]] / 100.0
    chg.columns = pd.to_datetime(chg.columns.astype(str), format="%Y%m%d")
    chg = chg.sort_index(axis=1)
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    m = pd.read_sql_query("SELECT code, sector_name FROM stock_sector_sw1", conn)
    conn.close()
    m["code"] = m["code"].astype(str).str[-6:]
    imap = dict(zip(m["code"], m["sector_name"]))

    win_all = chg.iloc[:, -130:]  # 列=date, 取最近130个交易日
    warns = []
    for code, name in positions:
        if code not in chg.index or code not in imap:
            continue
        ind = imap[code]
        peers = [c for c in win_all.index if c in imap and imap[c] == ind
                 and c != code and c in chg.index]
        # 流动性Top20对手
        peers = sorted(peers, key=lambda c: -win_all.loc[c].abs().mean())[:20]
        try:
            a = win_all.loc[code]
        except KeyError:
            continue
        best = None
        for b_code in peers:
            pair = pd.DataFrame({"a": a, "b": win_all.loc[b_code]}).dropna()
            if len(pair) < 100:
                continue
            corr = pair.corr().iloc[0, 1]
            if corr < CORR_TH or np.isnan(corr):
                continue
            pa = np.log1p(pair["a"].clip(-0.99, 5)).cumsum()
            pb = np.log1p(pair["b"].clip(-0.99, 5)).cumsum()
            try:
                _, pval, _ = coint(pa, pb)
            except Exception:
                continue
            if pval >= COINT_P:
                continue
            spread = pa - pb
            roll_mu = spread.rolling(60, min_periods=30).mean()
            roll_sd = spread.rolling(60, min_periods=30).std()
            z = float((spread.iloc[-1] - roll_mu.iloc[-1]) /
                      (roll_sd.iloc[-1] + 1e-12))
            if abs(z) > abs(best["z"]) if best else True:
                peer_name = _lookup_name(b_code)
                best = {"z": z, "peer": b_code, "peer_name": peer_name,
                        "corr": round(float(corr), 2)}
        if best and abs(best["z"]) > Z_WARN:
            level = "强制减持评估" if abs(best["z"]) > Z_STOP else "减持观察"
            direction = "相对高估" if best["z"] > 0 else "相对低估(多头端无规避必要)"
            if best["z"] > 0:
                warns.append({"code": code, "name": name, "z": round(best["z"], 2),
                              "level": level, "direction": direction,
                              "peer": best["peer"], "peer_name": best["peer_name"],
                              "industry": ind})
    out = {"asof": str(chg.columns[-1].date()), "n_positions": len(positions),
           "warning": warns, "sec": round(time.time() - t0, 1)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if warns else 0


_NAME_CACHE = {}


def _lookup_name(code: str) -> str:
    if code in _NAME_CACHE:
        return _NAME_CACHE[code]
    try:
        sys.path.insert(0, r"D:\zcodesj")
        from stock_name_lookup import require_name
        nm = require_name(code)
    except Exception:
        nm = code
    _NAME_CACHE[code] = nm
    return nm


if __name__ == "__main__":
    sys.exit(main())
