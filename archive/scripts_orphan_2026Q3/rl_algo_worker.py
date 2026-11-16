# -*- coding: utf-8 -*-
"""RL单算法worker — 三算法并行版(每进程一个算法)。"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import numpy as np

RL_DIR = Path(r"D:\zcodesj\strategy_libs\aurumq-rl")
_pkg = types.ModuleType("aurumq_rl")
_pkg.__path__ = [str(RL_DIR / "src" / "aurumq_rl")]
sys.modules.setdefault("aurumq_rl", _pkg)
sys.path.insert(0, str(RL_DIR / "src"))
sys.path.insert(0, r"D:\zcodesj")

ALGO = sys.argv[1]            # ppo / a2c / sac
TOTAL = 500_000

import datetime as dt  # noqa: E402
from stable_baselines3 import PPO, A2C, SAC  # noqa: E402
from aurumq_rl.data_loader import FactorPanelLoader  # noqa: E402
from aurumq_rl.env import StockPickingConfig, StockPickingEnv  # noqa: E402

PQ = str(RL_DIR / "data" / "ashare_panel_20260816.parquet")


def make_env():
    loader = FactorPanelLoader(PQ)
    panel = loader.load_panel(start_date=dt.date(2020, 2, 1),
                              end_date=dt.date(2026, 7, 31),
                              n_factors=20, forward_period=10)
    config = StockPickingConfig(
        start_date=dt.date(2020, 2, 1), end_date=dt.date(2026, 7, 31),
        n_factors=panel.factor_array.shape[2],
        top_k=20, forward_period=10, cost_bps=30.0)
    return StockPickingEnv(
        config=config, factor_panel=panel.factor_array,
        return_panel=panel.return_array, pct_change_panel=panel.pct_change_array,
        is_st_panel=panel.is_st_array, is_suspended_panel=panel.is_suspended_array,
        days_since_ipo_panel=panel.days_since_ipo_array,
        stock_codes=panel.stock_codes, close_panel=panel.close_array)


def main():
    t0 = time.time()
    env = make_env()
    print(f"[{ALGO}] env ready {time.time()-t0:.0f}s", flush=True)
    if ALGO == "ppo":
        model = PPO("MlpPolicy", env, verbose=0, n_steps=256, batch_size=128,
                    learning_rate=3e-4, device="cpu")
    elif ALGO == "a2c":
        model = A2C("MlpPolicy", env, verbose=0, n_steps=64,
                    learning_rate=7e-4, device="cpu")
    else:
        model = SAC("MlpPolicy", env, verbose=0, batch_size=256,
                    learning_rate=3e-4, train_freq=4, device="cpu",
                    buffer_size=10_000)  # obs 71220维, 100万buffer=265GB爆内存
    model.learn(total_timesteps=TOTAL)
    model.save(str(RL_DIR / "ckpt" / f"{ALGO}_500k_20260816"))
    print(f"[{ALGO}] trained {(time.time()-t0)/60:.0f}min", flush=True)
    # OOS尾段
    obs, _ = env.reset()
    done, rets = False, []
    while not done:
        a, _ = model.predict(obs, deterministic=True)
        obs, r, term, trunc, info = env.step(a)
        done = term or trunc
        rets.append(r)
    rets = np.array(rets)
    tail = rets[-370:]
    print(f"[{ALGO}-OOS] 尾段={tail.sum():.4f} 全程={rets.sum():.4f} "
          f"步数={len(rets)} 尾段正步={(tail>0).mean():.1%}", flush=True)


if __name__ == "__main__":
    main()
