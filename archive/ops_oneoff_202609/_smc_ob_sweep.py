# -*- coding: utf-8 -*-
"""_smc_ob_sweep.py — SMC Order Block 缩量确认参数扫描（研究层预筛）

扫"OB 缩量比例"参数族：看多 order block（OB=1）后，价格回踩进入 OB 区间
（Bottom~Top）且缩量（volume < vol5 × shrink_ratio）→ 次日 open 入场，持有 K。
网格：SHRINK_RATIO（缩量比例）× 固定回踩窗口/持有期（生产口径）。
绝对数字禁对账；结论走 wfa_gate --tech-grid。
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from smartmoneyconcepts import smc

sys.path.insert(0, r"D:\zcodesj")
import vbt_ashare_adapter as A

DETECT_START, DETECT_END = "2020-01-01", "2026-08-26"   # 封顶 OOS 锁日
EVAL_START = "2021-01-01"
SWING = 16
K = 20
RETRACE_WIN = 20          # OB 后 N 日内允许回踩
SHRINK_GRID = [0.5, 0.7, 0.9, 1.0]   # 缩量比例（volume < vol5 × ratio）
SIZE = 100
INIT_CASH = 300_000
POOL_CAP = 40


def build_pool():
    codes = json.loads(Path(r"D:\zcodesj\smc_research\sample_codes.json").read_text(encoding="utf-8"))
    uni = [c for c in codes if c.startswith(("60", "000", "001", "002", "003", "300", "301"))]
    return sorted(set(uni))[:POOL_CAP]


def find_ob_retrace_entries(df, shrink):
    """看多 OB 后回踩区间且缩量。返回 [(ent_bar,)]。"""
    o = df.reset_index(drop=True)
    ohlc = o[["open", "high", "low", "close", "volume"]]
    shl = smc.swing_highs_lows(ohlc, swing_length=SWING)
    ob = smc.ob(ohlc, shl)
    vol5 = o["volume"].rolling(5).mean()
    ents = []
    ob_idx = np.where(ob["OB"].values == 1)[0]
    for i in ob_idx:
        top, bot = ob["Top"].iloc[i], ob["Bottom"].iloc[i]
        if np.isnan(top) or np.isnan(bot):
            continue
        for j in range(i + 1, min(i + 1 + RETRACE_WIN, len(o))):
            c = o["close"].iloc[j]
            if bot <= c <= top and o["volume"].iloc[j] < vol5.iloc[j] * shrink:
                ents.append(j + 1)   # 回踩缩量确认次日入场
                break
    return ents


def run_combo(df, code, shrink):
    ent_bars = find_ob_retrace_entries(df, shrink)
    entries_raw = pd.Series(False, index=df.index)
    exits_raw = pd.Series(False, index=df.index)
    dates = df.index
    for ent in ent_bars:
        ext = ent + K
        if ent >= len(df) or ext >= len(df):
            continue
        if dates[ent] < pd.Timestamp(EVAL_START) or dates[ext] <= dates[ent]:
            continue
        entries_raw.iloc[ent] = True
        exits_raw.iloc[ext] = True
    if entries_raw.sum() == 0:
        return np.nan, 0, np.array([])
    pf, *_ = A.run_ashare(df, entries_raw, exits_raw, size=SIZE, init_cash=INIT_CASH, code=code)
    tr = pf.trades.records_arr
    closed = tr[tr["status"] == 1]
    return float(pf.total_return().iat[0]), len(tr), closed["return"].astype(float)


def main():
    pool = build_pool()
    print(f"样本池 {len(pool)} 只 | SWING={SWING} K={K} 回踩窗={RETRACE_WIN} | 数据 {DETECT_START}~{DETECT_END}")
    A.START, A.END = DETECT_START, DETECT_END

    res = np.full(len(SHRINK_GRID), np.nan)
    win = np.zeros(len(SHRINK_GRID))
    ntr = np.zeros(len(SHRINK_GRID), dtype=int)
    for c, code in enumerate(pool):
        try:
            df = A.load_stock(code)
        except Exception as e:
            print(f"  skip {code}: {e}")
            continue
        if len(df) < 60:
            continue
        for j, sh in enumerate(SHRINK_GRID):
            r, n, rets = run_combo(df, code, sh)
            if rets.size == 0:
                continue
            cur_n = ntr[j]
            cur_sum = res[j] * cur_n if not np.isnan(res[j]) else 0.0
            cur_w = win[j] * cur_n if cur_n else 0.0
            new_n = cur_n + rets.size
            res[j] = (cur_sum + rets.sum()) / new_n
            win[j] = (cur_w + (rets > 0).sum()) / new_n
            ntr[j] = new_n
        if (c + 1) % 10 == 0:
            print(f"  {c+1}/{len(pool)} 只完成", flush=True)

    print("\n=== OB 缩量确认参数扫描（per-trade 均值 % / 胜率 % / 笔数）===")
    for j, sh in enumerate(SHRINK_GRID):
        r = f"{res[j]*100:+.2f}%" if not np.isnan(res[j]) else "  N/A "
        print(f"  缩量<vol5×{sh:.1f}: {r}  胜率 {win[j]*100:.1f}%  笔数 {ntr[j]}")

    # 门禁输入 JSON（1D → 用 x=SHRINK, y 固定单值占位）
    cells = {}
    for j, sh in enumerate(SHRINK_GRID):
        if np.isnan(res[j]):
            continue
        cells[f"{sh}|{sh}"] = {
            "ret": round(float(res[j]) * 100, 4),
            "win": round(float(win[j]) * 100, 2),
            "n": int(ntr[j]),
        }
    payload = {
        "strategy": "smc_ob_retrace_shrink",
        "pool": f"sample40_uni_{POOL_CAP}",
        "start": EVAL_START, "end": DETECT_END, "price_source": "qfq",
        "dims": {
            "x": {"name": "SHRINK", "values": SHRINK_GRID},
            "y": {"name": "SHRINK", "values": SHRINK_GRID},
        },
        "fixed": {"SWING": SWING, "K": K, "RETRACE_WIN": RETRACE_WIN},
        "cells": cells,
    }
    outdir = Path(r"D:\zcodesj\_smc_sweep_out")
    outdir.mkdir(exist_ok=True)
    (outdir / "smc_gate_grid_ob.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print("\n门禁 JSON 已存 _smc_sweep_out/smc_gate_grid_ob.json")
    print("SWEEP DONE (研究层预筛，禁对账绝对数字)")


if __name__ == "__main__":
    main()
