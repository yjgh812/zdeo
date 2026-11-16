# -*- coding: utf-8 -*-
"""_smc_fm_displacement_sweep.py — 全市场 displacement 突破确认参数扫描（研究层预筛）

把"突破幅度"参数族从 40 只池扩到全市场（屠龙宇宙 ~4894 只），解决小池样本不足。
一次全市场扫描（swing+liquidity）收集全部 SSL 扫荡事件 + 每事件上下文（level、后续 bar 的
close/vol/vol5/open/change_pct），再对 CONF_WIN×VOL_MULT 网格做事件级确认 + 前视收益。

口径与 smc_s2_displacement.py B 臂一致：扫荡后 1..conf_win 日内 close>level 且 vol>vol5*mult
→ 确认日次日 open 买入，持有 K 根，卖出日跌停顺延，扣 COST。数据封顶 OOS 锁日 20260826。
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
START, END = "20200101", "20260826"   # 封顶 OOS 锁日（锁定后禁调参）
SWING, RANGE_PCT = 16, 0.01
K = 20
CONF_WIN_GRID = [1, 2, 3]
VOL_MULT_GRID = [1.0, 1.2, 1.5, 2.0]
COST = 0.003   # 与 smc_s2_trade_sim 一致：单边 0.15%×2


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

    # ── 一次全市场扫描：收集全部 SSL 扫荡事件 + 上下文 ──
    # 每只股票存 dict 上下文：date, open, close, vol, vol5, chg, n
    ctx = {}
    events = []   # (code, swept_idx, level)
    t1 = time.time()
    for code, o in uni.groupby("code"):
        o = o.sort_values("date").reset_index(drop=True)
        if len(o) < 60:
            continue
        ohlc = o[["open", "high", "low", "close", "volume"]]
        try:
            shl = smc.swing_highs_lows(ohlc, swing_length=SWING)
            liq = smc.liquidity(ohlc, shl, range_percent=RANGE_PCT)
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
        for i in np.where(liq["Liquidity"].values == -1)[0]:
            swept = int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
            lvl = liq["Level"].iloc[i]
            if swept > 0 and lvl and not np.isnan(lvl):
                events.append((code, swept, float(lvl)))
    print(f"全市场 SSL 扫荡事件 {len(events)} 个 / {len(ctx)} 只有上下文 ({time.time()-t1:.0f}s)")

    # ── 参数网格：事件级确认 + 前视收益 ──
    res = np.full((len(CONF_WIN_GRID), len(VOL_MULT_GRID)), np.nan)
    win = np.zeros_like(res)
    ntr = np.zeros_like(res, dtype=int)
    for i, cw in enumerate(CONF_WIN_GRID):
        for j, vm in enumerate(VOL_MULT_GRID):
            rets = []
            for code, swept, lvl in events:
                c = ctx[code]
                n = c["n"] = len(c["date"])
                # 扫荡后 1..cw 日内找确认（close>level 且 vol>vol5*vm）
                conf = -1
                for b in range(swept + 1, min(swept + 1 + cw, n)):
                    if c["close"][b] > lvl and c["vol"][b] > c["vol5"][b] * vm:
                        conf = b
                        break
                if conf < 0:
                    continue
                buy_idx = conf + 1
                if buy_idx >= n:
                    continue
                # 买入日涨停/跌停跳过（smc_s2 口径）
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
            print(f"  conf={cw}日 vol>vol5×{vm}: n={len(rets)} mean={rets.mean()*100:+.2f}% win={(rets>0).mean()*100:.1f}%")

    print("\n=== 全市场 displacement 突破确认（per-trade 均值 % / 胜率 % / 笔数）===")
    hdr = "CONF\\VOL " + "  ".join(f"{v:.1f}".rjust(8) for v in VOL_MULT_GRID)
    print(hdr)
    for i, cw in enumerate(CONF_WIN_GRID):
        cells = [f"{res[i, j]*100:8.2f}" if not np.isnan(res[i, j]) else "      - " for j in range(len(VOL_MULT_GRID))]
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
        "strategy": "smc_fm_displacement_confirm",
        "pool": f"fullmarket_uni_{len(ctx)}",
        "start": "20210101", "end": END, "price_source": "qfq",
        "dims": {
            "x": {"name": "CONF_WIN", "values": CONF_WIN_GRID},
            "y": {"name": "VOL_MULT", "values": VOL_MULT_GRID},
        },
        "fixed": {"SWING": SWING, "RANGE": RANGE_PCT, "K": K, "COST": COST},
        "cells": cells,
    }
    outdir = Path(r"D:\zcodesj\_smc_sweep_out")
    outdir.mkdir(exist_ok=True)
    (outdir / "smc_fm_gate_grid_displacement.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"\n门禁 JSON: _smc_sweep_out/smc_fm_gate_grid_displacement.json")
    print("SWEEP DONE (研究层预筛，禁对账绝对数字)")


if __name__ == "__main__":
    main()
