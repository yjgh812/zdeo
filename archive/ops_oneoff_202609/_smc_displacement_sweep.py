# -*- coding: utf-8 -*-
"""_smc_displacement_sweep.py — SMC displacement 突破确认参数扫描（研究层预筛）

扫你贴内容点名的"突破幅度"参数族：确认窗口 × 量比阈值。
生产逻辑（smc_signal_daily.py）：扫荡后 1-3 日内出现放量阳线反包
（close>流动性水平 且 volume>前5日均量×1.2）→ confirmed 后再入场。

网格：CONF_WIN（确认窗口天数, 1-3）× VOL_MULT（量比阈值）× 固定 SWING16/RANGE1%/K20（生产默认）。
指标 per-trade 均值收益/胜率/笔数（与 smc_s2_displacement.py 的 B 臂口径一致）。
绝对数字禁对账；结论走 wfa_gate --tech-grid 技术网格通道。
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from smartmoneyconcepts import smc

sys.path.insert(0, r"D:\zcodesj")
import vbt_ashare_adapter as A

# 数据窗口封顶 OOS 锁日（20260826，锁定后禁调参）
DETECT_START, DETECT_END = "2020-01-01", "2026-08-26"
EVAL_START = "2021-01-01"

# 生产默认（swing_length=16, range_percent=0.01, 持有20）
SWING = 16
RANGE_PCT = 0.01
K = 20

# 突破确认参数网格
CONF_WIN_GRID = [1, 2, 3]           # 扫荡后 N 日内找放量阳线反包
VOL_MULT_GRID = [1.0, 1.2, 1.5, 2.0]  # 量比阈值（volume > vol5 × mult）

SIZE = 100
INIT_CASH = 300_000
POOL_CAP = 40


def build_pool():
    codes = json.loads(Path(r"D:\zcodesj\smc_research\sample_codes.json").read_text(encoding="utf-8"))
    uni = [c for c in codes if c.startswith(("60", "000", "001", "002", "003", "300", "301"))]
    return sorted(set(uni))[:POOL_CAP]


def find_displacement_entries(df, conf_win, vol_mult):
    """扫荡事件 + displacement 确认。返回 [(ent_bar,)]——确认日次日 open 入场。
    与 smc_s2_displacement.py B 臂一致：扫荡后 1..conf_win 日内 close>level 且 vol>vol5*mult。"""
    o = df.reset_index(drop=True)
    ohlc = o[["open", "high", "low", "close", "volume"]]
    shl = smc.swing_highs_lows(ohlc, swing_length=SWING)
    liq = smc.liquidity(ohlc, shl, range_percent=RANGE_PCT)
    vol5 = o["volume"].rolling(5).mean()
    ents = []
    for i in np.where(liq["Liquidity"].values == -1)[0]:
        swept = int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
        if swept <= 0:
            continue
        lvl = liq["Level"].iloc[i]
        if not lvl or np.isnan(lvl):
            continue
        # 扫荡后 1..conf_win 日内找放量阳线反包
        for j in range(swept + 1, min(swept + 1 + conf_win, len(o))):
            if o["close"].iloc[j] > lvl and o["volume"].iloc[j] > vol5.iloc[j] * vol_mult:
                ents.append(j + 1)   # 确认日次日 open 入场
                break
    return ents


def run_combo(df, code, conf_win, vol_mult):
    ent_bars = find_displacement_entries(df, conf_win, vol_mult)
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
    print(f"样本池 {len(pool)} 只 | 固定 SWING={SWING} RANGE={RANGE_PCT} K={K} | 数据 {DETECT_START}~{DETECT_END}")
    A.START, A.END = DETECT_START, DETECT_END

    res = np.full((len(CONF_WIN_GRID), len(VOL_MULT_GRID)), np.nan)
    win = np.zeros_like(res)
    ntr = np.zeros_like(res, dtype=int)
    for c, code in enumerate(pool):
        try:
            df = A.load_stock(code)
        except Exception as e:
            print(f"  skip {code}: {e}")
            continue
        if len(df) < 60:
            continue
        for i, cw in enumerate(CONF_WIN_GRID):
            for j, vm in enumerate(VOL_MULT_GRID):
                r, n, rets = run_combo(df, code, cw, vm)
                if rets.size == 0:
                    continue
                cur_n = ntr[i, j]
                cur_sum = res[i, j] * cur_n if not np.isnan(res[i, j]) else 0.0
                cur_w = win[i, j] * cur_n if cur_n else 0.0
                new_n = cur_n + rets.size
                res[i, j] = (cur_sum + rets.sum()) / new_n
                win[i, j] = (cur_w + (rets > 0).sum()) / new_n
                ntr[i, j] = new_n
        if (c + 1) % 10 == 0:
            print(f"  {c+1}/{len(pool)} 只完成", flush=True)

    print("\n=== displacement 突破确认参数扫描（per-trade 均值 % / 胜率 % / 笔数）===")
    hdr = "CONF\\VOL " + "  ".join(f"{v:.1f}".rjust(8) for v in VOL_MULT_GRID)
    print(hdr)
    for i, cw in enumerate(CONF_WIN_GRID):
        cells = []
        for j in range(len(VOL_MULT_GRID)):
            cells.append(f"{res[i, j]*100:8.2f}" if not np.isnan(res[i, j]) else "      - ")
        print(f"   {cw}日   " + "  ".join(cells))
    print("  --胜率--")
    for i, cw in enumerate(CONF_WIN_GRID):
        cells = [f"{win[i, j]*100:7.1f}" if ntr[i, j] else "    -  " for j in range(len(VOL_MULT_GRID))]
        print(f"   {cw}日   " + "  ".join(cells))
    print("  --笔数--")
    for i, cw in enumerate(CONF_WIN_GRID):
        cells = [f"{ntr[i, j]:6d}" for j in range(len(VOL_MULT_GRID))]
        print(f"   {cw}日   " + "  ".join(cells))

    # 门禁输入 JSON
    cells = {}
    for i, cw in enumerate(CONF_WIN_GRID):
        for j, vm in enumerate(VOL_MULT_GRID):
            if np.isnan(res[i, j]):
                continue
            cells[f"{cw}|{vm}"] = {
                "ret": round(float(res[i, j]) * 100, 4),
                "win": round(float(win[i, j]) * 100, 2),
                "n": int(ntr[i, j]),
            }
    payload = {
        "strategy": "smc_displacement_confirm",
        "pool": f"sample40_uni_{POOL_CAP}",
        "start": EVAL_START, "end": DETECT_END, "price_source": "qfq",
        "dims": {
            "x": {"name": "CONF_WIN", "values": CONF_WIN_GRID},
            "y": {"name": "VOL_MULT", "values": VOL_MULT_GRID},
        },
        "fixed": {"SWING": SWING, "RANGE": RANGE_PCT, "K": K},
        "cells": cells,
    }
    outdir = Path(r"D:\zcodesj\_smc_sweep_out")
    outdir.mkdir(exist_ok=True)
    (outdir / "smc_gate_grid_displacement.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"\n门禁 JSON 已存 _smc_sweep_out/smc_gate_grid_displacement.json")
    print("SWEEP DONE (研究层预筛，禁对账绝对数字)")


if __name__ == "__main__":
    main()
