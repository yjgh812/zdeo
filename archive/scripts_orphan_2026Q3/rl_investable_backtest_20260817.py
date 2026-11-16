# -*- coding: utf-8 -*-
"""RL可投资回测 — 非重叠10日持有 (2026-08-17晚)
协议: 每10个交易日决策一次, 选Top20等权, 持有10日(收益=该期真实前向10日收益,无重叠)
对照: 同日等权全可交易组合 + 期间基准
输入: ckpt a2c_train2023_500k (真OOS训练版)
"""
from __future__ import annotations

import sys
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
import pandas as pd  # noqa: E402
from stable_baselines3 import A2C  # noqa: E402
from aurumq_rl.data_loader import FactorPanelLoader  # noqa: E402
from aurumq_rl.env import StockPickingConfig, StockPickingEnv, _apply_trading_mask  # noqa: E402

PQ = str(RL_DIR / "data" / "ashare_panel_20260816.parquet")
CKPT = str(RL_DIR / "ckpt" / "a2c_train2023_500k_20260817")
HOLD = 10
COST_BPS = 30.0
TOPK = 20


def main():
    loader = FactorPanelLoader(PQ)
    panel = loader.load_panel(start_date=dt.date(2024, 1, 1),
                              end_date=dt.date(2026, 7, 31),
                              n_factors=20, forward_period=HOLD)
    dates = list(panel.dates)
    cfg = StockPickingConfig(start_date=dates[0], end_date=dates[-1],
                             n_factors=panel.factor_array.shape[2],
                             top_k=TOPK, forward_period=HOLD, cost_bps=COST_BPS)
    env = StockPickingEnv(
        config=cfg, factor_panel=panel.factor_array,
        return_panel=panel.return_array, pct_change_panel=panel.pct_change_array,
        is_st_panel=panel.is_st_array, is_suspended_panel=panel.is_suspended_array,
        days_since_ipo_panel=panel.days_since_ipo_array,
        stock_codes=panel.stock_codes, close_panel=panel.close_array)

    model = A2C.load(CKPT, device="cpu")
    print(f"[env] test {dates[0]}~{dates[-1]} n_dates={env.n_dates}")

    obs, _ = env.reset()
    rl_log, ew_log, picks_log = [], [], []
    t = 0
    while t + HOLD < env.n_dates:
        a, _ = model.predict(obs, deterministic=True)
        scores = np.asarray(a).ravel()
        # 与env同构的Top-K(不过行业cap, env默认industry_codes全0时无约束)
        sel = np.argsort(scores)[::-1][:TOPK]
        mask = _apply_trading_mask(
            returns=env.return_panel[t].astype(np.float64),
            pct_changes=env._pct_change_panel[t].astype(np.float64),
            is_st=env._is_st_panel[t],
            is_suspended=env._is_suspended_panel[t],
            days_since_ipo=env._days_since_ipo_panel[t].astype(np.float64),
            stock_codes=env._stock_codes,
            close=env._close_panel[t].astype(np.float64))
        # 选中的票里不可交易的剔除(现实=买不进), 不足则顺延补
        ok_sel = [i for i in sel if mask[i] != 0]
        if len(ok_sel) < TOPK:  # 从剩余可交易池补足
            rest = [i for i in np.argsort(scores)[::-1] if mask[i] != 0 and i not in ok_sel]
            ok_sel += rest[:TOPK - len(ok_sel)]
        w = np.zeros(env.n_stocks)
        w[ok_sel] = 1.0 / max(len(ok_sel), 1)
        rl_log.append(float(np.dot(w, mask)))
        ew = mask[mask != 0]
        ew_log.append(float(ew.mean()) if len(ew) else 0.0)
        picks_log.append((dates[t], len(ok_sel)))
        # 快进10日
        for _ in range(HOLD):
            obs, _, term, trunc, _ = env.step(np.full(env.n_stocks, 0.5))
            t += 1
            if term or trunc:
                break
        if term or trunc:
            break

    rl = np.array(rl_log)
    ew = np.array(ew_log)
    cost = COST_BPS / 1e4
    rl_net = rl - cost  # 全换仓一次性双边近似
    nav_rl = np.exp(np.cumsum(rl_net))
    nav_ew = np.exp(np.cumsum(ew))
    yrs = len(rl) * HOLD / 244
    print(f"\n[可投资回测] {len(rl)}个持有期 x {HOLD}日 = {yrs:.2f}年")
    print(f"  RL  : 累计log={rl_net.sum():+.4f} -> 净值x{nav_rl[-1]:.3f} "
          f"年化CAGR={nav_rl[-1]**(1/yrs)-1:+.1%}")
    print(f"  等权: 累计log={ew.sum():+.4f} -> 净值x{nav_ew[-1]:.3f} "
          f"年化CAGR={nav_ew[-1]**(1/yrs)-1:+.1%}")
    dd = (nav_rl / np.maximum.accumulate(nav_rl) - 1).min()
    print(f"  RL最大回撤(持有期口径)={dd:.1%}")
    print(f"  分期收益RL: {np.round(rl_net, 4)[:8]}...")
    Path(r"D:\zcodesj\reports\RL_INVESTABLE_BT_20260817.txt").write_text(
        f"RL可投资回测(非重叠{HOLD}日持有, 成本{COST_BPS}bps)\n"
        f"期数{len(rl)} 跨度{yrs:.2f}年\n"
        f"RL累计log={rl_net.sum():+.4f} NAV={nav_rl[-1]:.3f} CAGR={nav_rl[-1]**(1/yrs)-1:+.1%} MDD={dd:.1%}\n"
        f"等权log={ew.sum():+.4f} NAV={nav_ew[-1]:.3f}\n"
        f"超额log={rl_net.sum()-ew.sum():+.4f}\n", encoding="utf-8")
    # 分期明细落盘(供袖筒/相关性分析)
    n_per = len(rl)
    idx = [p[0] for p in picks_log]
    pd.DataFrame({"period_start": idx, "rl_log": rl_net, "ew_log": ew}).to_csv(
        r"D:\zcodesj\reports\rl_period_returns_20260817.csv", index=False)
    print(f"[out] reports/RL_INVESTABLE_BT_20260817.txt + rl_period_returns_20260817.csv ({n_per}期)")


if __name__ == "__main__":
    main()
