# -*- coding: utf-8 -*-
"""_smc_param_sweep.py — SMC SSL 扫荡做多 参数扫描 PoC（研究层 Path B 技术预筛）

复用 vbt_ashare_adapter.run_ashare 给每个参数组合套 A 股规则 R1-R8
（next-bar open / T+1 / 涨跌停禁买卖 / 停牌禁交易 / 最低佣金5元 / 印花税 / 整手 / 滑点）。
事件检测用 smartmoneyconcepts 逐参数重算（swing_highs_lows + liquidity），
这是 SMC 无法像 MA 那样做 vbt 广播广播的点，所以"一次性张量"只体现在组合评估层。

绝对数字禁对账：本脚本只用于参数相对排序 / 平台期结构，不产出生产参数；
可采纳结论必须过 wfa_gate 新指纹（当前门禁无"技术网格"通道，正式研究前需先定通道）。
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from smartmoneyconcepts import smc

sys.path.insert(0, r"D:\zcodesj")
import vbt_ashare_adapter as A

QFQ_DB = A.QFQ_DB
# 载入更早窗口做摆动点/流动性检测，但只统计 2021-01-01 之后的交易事件
# END 封顶到 OOS 锁（20260826），锁定日后数据禁用于调参（oos_lock 纪律）
DETECT_START, DETECT_END = "2020-01-01", "2026-08-26"
EVAL_START = "2021-01-01"

# 参数网格
SWING_GRID = [8, 12, 16, 20, 24]     # 摆动点 N（当前生产 16）
RANGE_GRID = [0.005, 0.01, 0.02, 0.03]  # 流动性池范围/扫荡幅度（当前生产 0.01）
K_GRID = [10, 20, 30]                # 持有周期 bars（当前生产 20）
SIZE = 100
INIT_CASH = 300_000
POOL_CAP = 40                        # 样本池上限（研究层，非全市场）


def build_pool():
    """从 smc_research/sample_codes.json 取池子，过滤到屠龙宇宙（主板+创业板，剔科创/北交）。"""
    codes = json.loads(Path(r"D:\zcodesj\smc_research\sample_codes.json").read_text(encoding="utf-8"))
    uni = [c for c in codes if c.startswith(("60", "000", "001", "002", "003", "300", "301"))]
    uni = sorted(set(uni))[:POOL_CAP]
    return uni


def sweep_events(df, swing, rng):
    """单股单参数：检测 SSL 流动性扫荡事件（Liquidity=-1 且 Swept 有效）。
    返回 [(swept_idx, level)]；swept_idx 为扫荡日原始 bar 下标。"""
    o = df.reset_index(drop=True)
    ohlc = o[["open", "high", "low", "close", "volume"]]
    shl = smc.swing_highs_lows(ohlc, swing_length=swing)
    liq = smc.liquidity(ohlc, shl, range_percent=rng)
    ev = []
    idxs = np.where(liq["Liquidity"].values == -1)[0]
    for i in idxs:
        swept = int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
        if swept > 0:
            ev.append((swept, float(liq["Level"].iloc[i])))
    return ev


def run_combo(df, code, swing, rng, k, events=None):
    """构造 raw entries/exits（扫荡日→适配器 shift 到次日 open 买入；持有 K bars），
    跑 run_ashare。返回 (pool_ret, n_trades, per_trade_returns)。events 为 (swept_idx, level) 缓存。"""
    ev = events if events is not None else sweep_events(df, swing, rng)
    entries_raw = pd.Series(False, index=df.index)
    exits_raw = pd.Series(False, index=df.index)
    dates = df.index
    for swept, _lvl in ev:
        ent_bar = swept + 1          # 适配器再 shift(1) → open[swept+1] 成交
        ext_bar = swept + 1 + k      # 适配器再 shift(1) → open[swept+1+k] 成交
        if ent_bar >= len(df) or ext_bar >= len(df):
            continue
        if dates[ent_bar] < pd.Timestamp(EVAL_START):
            continue
        if dates[ext_bar] <= dates[ent_bar]:
            continue
        entries_raw.iloc[ent_bar] = True
        exits_raw.iloc[ext_bar] = True
    if entries_raw.sum() == 0:
        return np.nan, 0, np.array([])
    pf, *_ = A.run_ashare(df, entries_raw, exits_raw, size=SIZE, init_cash=INIT_CASH, code=code)
    tr = pf.trades.records_arr
    closed = tr[tr["status"] == 1]
    rets = closed["return"].astype(float)
    return float(pf.total_return().iat[0]), len(tr), rets


def main():
    pool = build_pool()
    print(f"样本池 {len(pool)} 只（屠龙宇宙过滤后）")
    A.START, A.END = DETECT_START, DETECT_END

    # 结果：shape (SWING, RANGE, K) —— 主指标为 per-trade 平均收益，附胜率/笔数
    res = np.full((len(SWING_GRID), len(RANGE_GRID), len(K_GRID)), np.nan)
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
        # 缓存每 (swing,rng) 的事件，避免 run_combo 内重复检测
        ev_cache = {}
        for i, sw in enumerate(SWING_GRID):
            for j, rg in enumerate(RANGE_GRID):
                ev_cache[(i, j)] = sweep_events(df, sw, rg)
        for i, sw in enumerate(SWING_GRID):
            for j, rg in enumerate(RANGE_GRID):
                for l, k in enumerate(K_GRID):
                    r, n, rets = run_combo(df, code, sw, rg, k, events=ev_cache[(i, j)])
                    if rets.size == 0:
                        continue
                    # 池内滚动累积：per-trade 均值、胜率、笔数
                    cur_n = ntr[i, j, l]
                    cur_sum = res[i, j, l] * cur_n if not np.isnan(res[i, j, l]) else 0.0
                    cur_w = win[i, j, l] * cur_n if cur_n else 0.0
                    new_n = cur_n + rets.size
                    res[i, j, l] = (cur_sum + rets.sum()) / new_n
                    win[i, j, l] = (cur_w + (rets > 0).sum()) / new_n
                    ntr[i, j, l] = new_n
        if (c + 1) % 10 == 0:
            print(f"  {c+1}/{len(pool)} 只完成", flush=True)

    # ===== 汇总（per-trade 平均收益 %，胜率 %，池内总笔数）=====
    print("\n=== SMC SSL 参数扫描汇总（per-trade 平均收益 % / 胜率 % / 笔数）===")
    for l, k in enumerate(K_GRID):
        print(f"\n-- 持有 K={k} --")
        hdr = "SWING\\RANGE " + "  ".join(f"{r*100:.1f}%".rjust(7) for r in RANGE_GRID)
        print(hdr)
        for i, sw in enumerate(SWING_GRID):
            cells = []
            for j in range(len(RANGE_GRID)):
                m = res[i, j, l]
                if np.isnan(m):
                    cells.append("   -   ")
                else:
                    cells.append(f"{m*100:7.2f}")
            print(f"  {sw:>4}    " + "  ".join(cells))
        # 胜率视图
        print("  --胜率--")
        for i, sw in enumerate(SWING_GRID):
            cells = []
            for j in range(len(RANGE_GRID)):
                w = win[i, j, l]
                if ntr[i, j, l] == 0:
                    cells.append("  -  ")
                else:
                    cells.append(f"{w*100:5.1f}")
            print(f"  {sw:>4}    " + "  ".join(cells))
        print("  --笔数--")
        for i, sw in enumerate(SWING_GRID):
            cells = []
            for j in range(len(RANGE_GRID)):
                cells.append(f"{ntr[i, j, l]:5d}")
            print(f"  {sw:>4}    " + "  ".join(cells))

    # ===== 平台期结构 =====
    print("\n=== 平台期结构（per-trade 平均收益）===")
    for l, k in enumerate(K_GRID):
        M = res[:, :, l]
        valid = ~np.isnan(M)
        if not valid.any():
            continue
        best_i, best_j = np.unravel_index(np.nanargmax(M), M.shape)
        best = M[best_i, best_j]
        neigh = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                ii, jj = best_i + di, best_j + dj
                if (di, dj) != (0, 0) and 0 <= ii < len(SWING_GRID) and 0 <= jj < len(RANGE_GRID) and not np.isnan(M[ii, jj]):
                    neigh.append(M[ii, jj])
        spike = best - np.mean(neigh) if neigh else np.nan
        cv = np.nanstd(M[valid]) / (abs(np.nanmean(M[valid])) + 1e-9)
        print(f"K={k}: 最优 SWING={SWING_GRID[best_i]} RANGE={RANGE_GRID[best_j]} per-trade {best*100:.2f}% "
              f"8邻域均值 {np.mean(neigh)*100:.2f}% (尖峰 {spike*100:.2f}pp) 有效格点 {int(valid.sum())} 变异系数 {cv:.2f}")
        print(f"   交易笔数(最优处) {ntr[best_i, best_j, l]} 笔  胜率 {win[best_i, best_j, l]*100:.1f}%")

    # ===== 导出热力图 =====
    outdir = Path(r"D:\zcodesj\_smc_sweep_out")
    outdir.mkdir(exist_ok=True)
    for l, k in enumerate(K_GRID):
        dfx = pd.DataFrame(res[:, :, l] * 100,
                           index=[f"SWING{s}" for s in SWING_GRID],
                           columns=[f"RANGE{r:.1f}%" for r in RANGE_GRID])
        dfx.to_csv(outdir / f"smc_sweep_K{k}_ret_pct.csv", encoding="utf-8-sig")
        dfw = pd.DataFrame(win[:, :, l] * 100,
                           index=[f"SWING{s}" for s in SWING_GRID],
                           columns=[f"RANGE{r:.1f}%" for r in RANGE_GRID])
        dfw.to_csv(outdir / f"smc_sweep_K{k}_winrate_pct.csv", encoding="utf-8-sig")
        dfn = pd.DataFrame(ntr[:, :, l],
                           index=[f"SWING{s}" for s in SWING_GRID],
                           columns=[f"RANGE{r:.1f}%" for r in RANGE_GRID])
        dfn.to_csv(outdir / f"smc_sweep_K{k}_ntrades.csv", encoding="utf-8-sig")
    # 门禁输入 JSON（每 K 一张，cells 键 "SWING|RANGE"，per-trade 均值%）
    import json as _json
    for l, k in enumerate(K_GRID):
        cells = {}
        for i, sw in enumerate(SWING_GRID):
            for j, rg in enumerate(RANGE_GRID):
                if np.isnan(res[i, j, l]):
                    continue
                cells[f"{sw}|{rg}"] = {
                    "ret": round(float(res[i, j, l]) * 100, 4),
                    "win": round(float(win[i, j, l]) * 100, 2),
                    "n": int(ntr[i, j, l]),
                }
        payload = {
            "strategy": "smc_ssl_sweep",
            "pool": f"sample40_uni_{POOL_CAP}",
            "start": EVAL_START, "end": DETECT_END, "price_source": "qfq",
            "dims": {
                "x": {"name": "SWING", "values": SWING_GRID},
                "y": {"name": "RANGE", "values": RANGE_GRID},
            },
            "fixed": {"K": int(k)},
            "cells": cells,
        }
        (outdir / f"smc_gate_grid_K{k}.json").write_text(
            _json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"\n热力图与门禁 JSON 已存 {outdir}（每 K 三张 CSV + 一张 gate JSON）")
    print("SWEEP DONE (研究层预筛，禁对账绝对数字)")


if __name__ == "__main__":
    main()
