# -*- coding: utf-8 -*-
"""RL真OOS验证 — A2C时间切分 (2026-08-17晚)
================================================
背景: 上轮A2C +0.184"OOS"实为训内口径(500k步=319遍全期2020-2026, 尾段370步仍是训练数据)。
设计:
  全期一次加载(2020-02-01~2026-07-31, 固定universe=obs 71220维不变)
  → train env = 2020-02~2023-12 (切数组, 非重新加载, 保证维度同构)
  → test  env = 2024-01~2026-07 (训练从未见过)
  A2C同超参(n_steps=64, lr 7e-4, cpu) 500k步
  → test确定性rollout: 累计净收益/正步率/分半年 + 等权全市场基准对照
判定: test累计>0且跑赢等权基准 → RL真alpha; 否则RL入冰箱与Kronos同列
注意: panel无adj_factor(未复权前向收益, 除权日有污染)——与上轮同口径, 结论可比。
"""
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

import datetime as dt  # noqa: E402
from stable_baselines3 import A2C  # noqa: E402
from aurumq_rl.data_loader import FactorPanelLoader  # noqa: E402
from aurumq_rl.env import StockPickingConfig, StockPickingEnv, _apply_trading_mask  # noqa: E402

PQ = str(RL_DIR / "data" / "ashare_panel_20260816.parquet")
SPLIT = dt.date(2023, 12, 31)
TOTAL = 500_000


def build_envs():
    loader = FactorPanelLoader(PQ)
    panel = loader.load_panel(start_date=dt.date(2020, 2, 1),
                              end_date=dt.date(2026, 7, 31),
                              n_factors=20, forward_period=10)
    dates = list(panel.dates)
    split = max(i for i, d in enumerate(dates) if d <= SPLIT)
    print(f"panel {dates[0]}~{dates[-1]} n_dates={len(dates)} "
          f"n_stocks={panel.factor_array.shape[1]} "
          f"train=0..{split}({dates[split]}) test={split+1}..({dates[split+1]})", flush=True)

    def mk(lo, hi, sd, ed):
        cfg = StockPickingConfig(
            start_date=sd, end_date=ed,
            n_factors=panel.factor_array.shape[2],
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
    """等权全可交易组合前向收益(无成本) — no-skill基准。"""
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


def main():
    t0 = time.time()
    train_env, test_env, test_dates = build_envs()
    print(f"envs ready {time.time()-t0:.0f}s "
          f"train_days={train_env.n_dates} test_days={test_env.n_dates}", flush=True)

    model = A2C("MlpPolicy", train_env, verbose=0, n_steps=64,
                learning_rate=7e-4, device="cpu")
    model.learn(total_timesteps=TOTAL)
    model.save(str(RL_DIR / "ckpt" / "a2c_train2023_500k_20260817"))
    print(f"[train] done {(time.time()-t0)/60:.0f}min", flush=True)

    # ---- 真OOS测试 ----
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

    n = len(rets)
    print(f"\n[TRUE-OOS] {test_dates[0]}~{test_dates[-1]} 步数={n}", flush=True)
    print(f"  RL净累计={rets.sum():.4f} 正步率={(rets>0).mean():.1%}", flush=True)
    print(f"  RL毛累计={gross.sum():.4f} (前向log收益, 无成本)", flush=True)
    print(f"  等权基准={bench.sum():.4f} 超额(毛-基准)={gross.sum()-bench.sum():+.4f}", flush=True)
    # 分半年
    hy = {}
    for i, d in enumerate(test_dates[:n]):
        key = f"{d.year}H{1 if d.month <= 6 else 2}"
        hy.setdefault(key, [0.0, 0.0, 0])
        hy[key][0] += rets[i]
        hy[key][1] += bench[i]
        hy[key][2] += 1
    print("  半年          RL净      等权基准   超额", flush=True)
    for k in sorted(hy):
        r, b, c = hy[k]
        print(f"  {k}   {r:>+8.4f}   {b:>+8.4f}  {r-b:>+8.4f}  n={c}", flush=True)

    verdict = "PASS" if (rets.sum() > 0 and gross.sum() > bench.sum()) else "FAIL"
    print(f"\n[VERDICT] {verdict}", flush=True)
    Path(r"D:\zcodesj\reports\RL_TRUE_OOS_20260817.txt").write_text(
        f"A2C train(2020-02~2023-12)x500k -> test(2024-01~2026-07)\n"
        f"RL净累计={rets.sum():.4f} 正步率={(rets>0).mean():.1%}\n"
        f"RL毛累计={gross.sum():.4f} 等权基准={bench.sum():.4f} 超额={gross.sum()-bench.sum():+.4f}\n"
        f"VERDICT={verdict}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
