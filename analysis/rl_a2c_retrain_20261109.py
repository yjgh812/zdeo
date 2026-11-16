#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rl_a2c_retrain_20261109.py — 复活·强化学习(revival_rl) A2C 重训+真OOS自验
================================================================================
2026-11-09 补缺件令: 原ckpt(a2c_train2023_500k_20260817)在8月universe(3561只/obs71220)
上训练, 其后qfq活票库价格行漂移(训练窗主板3548只)→旧ckpt推理obs维度不可精确复原
(退市库无此9只/备份10/18起暂停/旧parquet已删; 因子列零丢失、漂移在价格行)。
零填充=近似观测冒充真判断, 违反反臆想纪律, 不采纳 → 走登记表写明的解锁路径=重训。
设计(rl_true_oos_20260817同构复刻, 数据延展):
  训练 2020-02~2023-12 / 真OOS测试 2024-01~2026-11-06(比原版多4个月OOS)
  A2C同超参(n_steps=64, lr 7e-4, cpu, 500k步); 判定=test累计>0且跑赢等权基准
产物: strategy_libs/aurumq-rl/ckpt/a2c_train2023_500k_20261109.zip + 日志
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
import time
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import numpy as np

ROOT = Path(r"D:\zcodesj")
RL_DIR = ROOT / "strategy_libs" / "aurumq-rl"
PQ = RL_DIR / "data" / "ashare_panel_20261106.parquet"   # rl_a2c_inference.py 已构建
CKPT_OUT = RL_DIR / "ckpt" / "a2c_train2023_500k_20261109"
SPLIT = dt.date(2023, 12, 31)
TOTAL = 500_000

_pkg = types.ModuleType("aurumq_rl")
_pkg.__path__ = [str(RL_DIR / "src" / "aurumq_rl")]
sys.modules.setdefault("aurumq_rl", _pkg)
sys.path.insert(0, str(RL_DIR / "src"))
sys.path.insert(0, str(ROOT))

from stable_baselines3 import A2C  # noqa: E402
from aurumq_rl.data_loader import FactorPanelLoader  # noqa: E402
from aurumq_rl.env import StockPickingConfig, StockPickingEnv, _apply_trading_mask  # noqa: E402


def build_envs():
    loader = FactorPanelLoader(str(PQ))
    panel = loader.load_panel(start_date=dt.date(2020, 2, 1), end_date=dt.date(2026, 11, 6),
                              n_factors=20, forward_period=10)
    dates = list(panel.dates)
    split = max(i for i, d in enumerate(dates) if d <= SPLIT)
    print(f"panel {dates[0]}~{dates[-1]} n_dates={len(dates)} "
          f"n_stocks={panel.factor_array.shape[1]} "
          f"train=0..{split}({dates[split]}) test={split+1}..({dates[-1]})", flush=True)

    def mk(lo, hi, sd, ed):
        cfg = StockPickingConfig(
            start_date=sd, end_date=ed, n_factors=panel.factor_array.shape[2],
            top_k=20, forward_period=10, cost_bps=30.0)
        return StockPickingEnv(
            config=cfg, factor_panel=panel.factor_array[lo:hi],
            return_panel=panel.return_array[lo:hi],
            pct_change_panel=panel.pct_change_array[lo:hi],
            is_st_panel=panel.is_st_array[lo:hi],
            is_suspended_panel=panel.is_suspended_array[lo:hi],
            days_since_ipo_panel=panel.days_since_ipo_array[lo:hi],
            stock_codes=panel.stock_codes, close_panel=panel.close_array[lo:hi])

    train_env = mk(0, split + 1, dates[0], dates[split])
    test_env = mk(split + 1, len(dates), dates[split + 1], dates[-1])
    assert train_env.observation_space.shape == test_env.observation_space.shape
    return train_env, test_env, dates[split + 1:]


def bench_equal_weight(env):
    out = []
    for t in range(env.n_dates - env.config.forward_period):
        m = _apply_trading_mask(
            returns=env.return_panel[t].astype(np.float64),
            pct_changes=env._pct_change_panel[t].astype(np.float64),
            is_st=env._is_st_panel[t],
            is_suspended=env._is_suspended_panel[t],
            days_since_ipo=env._days_since_ipo_panel[t].astype(np.float64),
            stock_codes=env._stock_codes,
            close=env._close_panel[t].astype(np.float64) if env._close_panel is not None else None)
        out.append(float(m[m != 0].mean()) if (m != 0).any() else 0.0)
    return np.array(out)


def main() -> int:
    t0 = time.time()
    train_env, test_env, test_dates = build_envs()
    print(f"envs ready {time.time()-t0:.0f}s train_days={train_env.n_dates} "
          f"test_days={test_env.n_dates}", flush=True)

    model = A2C("MlpPolicy", train_env, verbose=0, n_steps=64,
                learning_rate=7e-4, device="cpu")
    model.learn(total_timesteps=TOTAL)
    model.save(str(CKPT_OUT))
    print(f"[train] done {(time.time()-t0)/60:.0f}min → {CKPT_OUT.name}", flush=True)

    obs, _ = test_env.reset()
    done, rets, gross = False, [], []
    while not done:
        a, _ = model.predict(obs, deterministic=True)
        obs, r, term, trunc, info = test_env.step(a)
        done = term or trunc
        rets.append(r)
        gross.append(info["portfolio_return"])
    rets = np.array(rets)
    gross = np.array(gross)
    bench = bench_equal_weight(test_env)
    n = min(len(gross), len(bench))
    net_cum = float((1 + rets[:n]).prod() - 1)
    gross_cum = float((1 + gross[:n]).prod() - 1)
    bench_cum = float((1 + bench[:n]).prod() - 1)
    pos_rate = float((rets > 0).mean())
    verdict = "PASS" if (net_cum > 0 and gross_cum > bench_cum) else "FAIL"
    summary = {
        "ckpt": str(CKPT_OUT) + ".zip",
        "train": "2020-02~2023-12 x500k", "test": f"{test_dates[0]}~{test_dates[-1]}",
        "net_cum": round(net_cum, 4), "gross_cum": round(gross_cum, 4),
        "bench_equal_weight": round(bench_cum, 4),
        "excess": round(gross_cum - bench_cum, 4), "pos_step_rate": round(pos_rate, 4),
        "n_stocks": int(train_env.observation_space.shape[0] // 20),
        "obs_dim": int(train_env.observation_space.shape[0]),
        "verdict": verdict, "trained_at": dt.datetime.now().isoformat(),
    }
    out = ROOT / "data" / "rl_a2c_retrain_result_20261109.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"总耗时 {(time.time()-t0)/60:.0f}min | {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
