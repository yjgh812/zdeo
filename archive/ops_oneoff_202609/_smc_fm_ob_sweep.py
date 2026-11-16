# -*- coding: utf-8 -*-
"""_smc_fm_ob_sweep.py — 全市场 OB 缩量确认参数扫描（研究层预筛）

把"OB 缩量比例"参数族扩到全市场（屠龙宇宙 ~4800 只），解决 40 只池样本不足。
一次全市场扫描（swing+liquidity+ob）收集全部看多 order block 事件 + 上下文
（Top/Bottom、后续 bar 的 close/vol/vol5/open/chg），再对 SHRINK×RETRACE_WIN 网格
做事件级回踩缩量确认 + 前视收益。

口径：看多 OB 后 1..retrace_win 日内 close 进入 [Bottom,Top] 且 vol<vol5*shrink
→ 次日 open 买入，持有 K，卖出日跌停顺延，扣 COST。数据封顶 OOS 锁日 20260826。
绝对数字禁对账；结论走 wfa_gate --tech-grid。
"""
import json
import sqlite3
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd
from smartmoneyconcepts import smc

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
START, END = "20200101", "20260826"   # 封顶 OOS 锁日
SWING = 16
K = 20
SHRINK_GRID = [0.5, 0.7, 0.9, 1.0]
RETRACE_WIN_GRID = [10, 20, 30]
COST = 0.003


def limit_up_pct(code):
    c = str(code)
    if c.startswith(("300", "301", "688")):
        return 19.8
    if c.startswith(("8", "4", "92")):
        return 29.8
    return 9.8


def main():
    t0 = time.time()
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        f"WHERE date>='{START}' AND date<='{END}'", con)
    con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    uni = df[df["code"].str.match(r"^(60|000|001|002|003|300|301)")]
    print(f"读入 {len(uni)} 行 / {uni['code'].nunique()} 只 ({time.time()-t0:.0f}s)")

    ctx = {}
    ob_events = []   # (code, ob_idx, top, bot)
    t1 = time.time()
    for code, o in uni.groupby("code"):
        o = o.sort_values("date").reset_index(drop=True)
        if len(o) < 60:
            continue
        ohlc = o[["open", "high", "low", "close", "volume"]]
        try:
            shl = smc.swing_highs_lows(ohlc, swing_length=SWING)
            ob = smc.ob(ohlc, shl)
        except Exception:
            continue
        ctx[code] = {
            "date": o["date"].to_numpy(),
            "open": o["open"].to_numpy(),
            "close": o["close"].to_numpy(),
            "vol": o["volume"].to_numpy(),
            "vol5": o["volume"].rolling(5).mean().to_numpy(),
            "chg": o["change_pct"].to_numpy(),
            "lu": limit_up_pct(code),
        }
        for i in np.where(ob["OB"].values == 1)[0]:
            top, bot = ob["Top"].iloc[i], ob["Bottom"].iloc[i]
            if not np.isnan(top) and not np.isnan(bot):
                ob_events.append((code, i, float(top), float(bot)))
    print(f"全市场看多 OB 事件 {len(ob_events)} 个 / {len(ctx)} 只 ({time.time()-t1:.0f}s)")

    # ── 参数网格：回踩缩量确认 + 前视收益 ──
    res = np.full((len(SHRINK_GRID), len(RETRACE_WIN_GRID)), np.nan)
    win = np.zeros_like(res)
    ntr = np.zeros_like(res, dtype=int)
    for i, sh in enumerate(SHRINK_GRID):
        for j, rw in enumerate(RETRACE_WIN_GRID):
            rets = []
            for code, ob_i, top, bot in ob_events:
                c = ctx[code]
                n = len(c["date"])
                # OB 后 1..rw 日内 close 进入区间且缩量
                hit = -1
                for b in range(ob_i + 1, min(ob_i + 1 + rw, n)):
                    clz = c["close"][b]
                    if bot <= clz <= top and c["vol"][b] < c["vol5"][b] * sh:
                        hit = b
                        break
                if hit < 0:
                    continue
                buy_idx = hit + 1
                if buy_idx >= n:
                    continue
                if c["chg"][buy_idx] >= c["lu"] or c["chg"][buy_idx] <= -9.8:
                    continue
                bp = c["open"][buy_idx]
                if bp <= 0:
                    continue
                sell_idx = buy_idx + K
                while sell_idx < n and c["chg"][sell_idx] <= -9.8:
                    sell_idx += 1
                if sell_idx >= n:
                    continue
                sp = c["open"][sell_idx]
                if sp <= 0:
                    continue
                rets.append(sp / bp - 1 - COST)
            if not rets:
                continue
            rets = np.array(rets)
            res[i, j] = rets.mean()
            win[i, j] = (rets > 0).mean()
            ntr[i, j] = len(rets)
            print(f"  shrink<vol5×{sh} 回踩窗{rw}: n={len(rets)} mean={rets.mean()*100:+.2f}% win={(rets>0).mean()*100:.1f}%")

    print("\n=== 全市场 OB 缩量确认（per-trade 均值 % / 胜率 % / 笔数）===")
    hdr = "SHRINK\\RETR " + "  ".join(f"{v}".rjust(7) for v in RETRACE_WIN_GRID)
    print(hdr)
    for i, sh in enumerate(SHRINK_GRID):
        cells = [f"{res[i, j]*100:7.2f}" if not np.isnan(res[i, j]) else "     - " for j in range(len(RETRACE_WIN_GRID))]
        print(f"  ×{sh:.1f}  " + "  ".join(cells))
    print("  --胜率--")
    for i, sh in enumerate(SHRINK_GRID):
        cells = [f"{win[i, j]*100:6.1f}" if ntr[i, j] else "   -  " for j in range(len(RETRACE_WIN_GRID))]
        print(f"  ×{sh:.1f}  " + "  ".join(cells))
    print("  --笔数--")
    for i, sh in enumerate(SHRINK_GRID):
        cells = [f"{ntr[i, j]:6d}" for j in range(len(RETRACE_WIN_GRID))]
        print(f"  ×{sh:.1f}  " + "  ".join(cells))

    # 门禁输入 JSON
    cells = {}
    for i, sh in enumerate(SHRINK_GRID):
        for j, rw in enumerate(RETRACE_WIN_GRID):
            if np.isnan(res[i, j]):
                continue
            cells[f"{sh}|{rw}"] = {
                "ret": round(float(res[i, j]) * 100, 4),
                "win": round(float(win[i, j]) * 100, 2),
                "n": int(ntr[i, j]),
            }
    payload = {
        "strategy": "smc_fm_ob_retrace_shrink",
        "pool": f"fullmarket_uni_{len(ctx)}",
        "start": "20210101", "end": END, "price_source": "qfq",
        "dims": {
            "x": {"name": "SHRINK", "values": SHRINK_GRID},
            "y": {"name": "RETRACE_WIN", "values": RETRACE_WIN_GRID},
        },
        "fixed": {"SWING": SWING, "K": K, "COST": COST},
        "cells": cells,
    }
    outdir = Path(r"D:\zcodesj\_smc_sweep_out")
    outdir.mkdir(exist_ok=True)
    (outdir / "smc_fm_gate_grid_ob.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"\n门禁 JSON: _smc_sweep_out/smc_fm_gate_grid_ob.json")
    print("SWEEP DONE (研究层预筛，禁对账绝对数字)")


if __name__ == "__main__":
    main()
