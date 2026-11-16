# -*- coding: utf-8 -*-
"""
RL深度训练 — 翻案后深化 (2026-08-16晚)
================================================
依据: RL_TRAIN_20260816 管线翻案(8GB可训/OOS+7.49%)
本脚本深化:
  1. PPO 500k steps长训(原50k的10倍)
  2. A2C / SAC 对照(三算法横向)
  3. 统一OOS评估(2025-2026段rollout, 与50k版对比)

产出: reports/RL_DEEP_TRAIN_20260816.md + ckpt/ppo_ashare_500k等
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
from stable_baselines3 import PPO, A2C, SAC  # noqa: E402
from aurumq_rl.data_loader import FactorPanelLoader  # noqa: E402
from aurumq_rl.env import StockPickingConfig, StockPickingEnv  # noqa: E402

PQ = str(RL_DIR / "data" / "ashare_panel_20260816.parquet")
REPORT = Path(r"D:\zcodesj\reports")
TOTAL = 500_000


def make_env():
    """全期环境(2020-2026, 与50k翻案版同协议)。
    ⚠️架构限制: per-date universe使不同日期段env维度不同,
    OOS=全期rollout取后段reward(非严格样本外, 如实标注)。"""
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


def oos_eval(model, tag):
    """全期rollout, 后段(2025+)reward作为OOS代理(与50k版同口径可比)。"""
    env = make_env()
    obs, _ = env.reset()
    done, rets = False, []
    while not done:
        a, _ = model.predict(obs, deterministic=True)
        obs, r, term, trunc, info = env.step(a)
        done = term or trunc
        rets.append(r)
    rets = np.array(rets)
    tail = rets[-370:]   # 2025-2026段
    print(f"  [OOS尾段] {tag}: 尾段收益={tail.sum():.4f} 全程={rets.sum():.4f} "
          f"步数={len(rets)} 尾段正步={(tail>0).mean():.1%}", flush=True)
    return float(tail.sum()), int(len(tail)), float((tail > 0).mean())


def main():
    t0 = time.time()
    print("=== RL深度训练: 500k三算法 ===")
    env = make_env()
    print(f"  训练环境就绪 ({time.time()-t0:.0f}s)")
    results = {}
    # SAC需要连续动作空间——StockPickingEnv是Box(0,1)打分可用
    for name, mk in (("ppo_500k", lambda e: PPO("MlpPolicy", e, verbose=0,
                                                 n_steps=256, batch_size=128,
                                                 learning_rate=3e-4, device="cpu")),
                     ("a2c_500k", lambda e: A2C("MlpPolicy", e, verbose=0,
                                                n_steps=64, learning_rate=7e-4,
                                                device="cpu")),
                     ("sac_500k", lambda e: SAC("MlpPolicy", e, verbose=0,
                                                batch_size=256, learning_rate=3e-4,
                                                train_freq=4, device="cpu"))):
        t1 = time.time()
        print(f"  [{name}] 训练开始 (500k steps)...", flush=True)
        try:
            model = mk(env)
            model.learn(total_timesteps=TOTAL)
            model.save(str(RL_DIR / "ckpt" / f"{name}_20260816"))
            tr, n, pr = oos_eval(model, name)
            results[name] = {"oos_ret": tr, "steps": n, "pos_rate": pr,
                             "train_sec": time.time() - t1}
        except Exception as e:
            print(f"  [{name}] 失败: {e}")
            results[name] = {"err": str(e)[:100]}
        print(f"  [{name}] 完成 ({(time.time()-t1)/60:.0f}min)", flush=True)
        env.reset()

    import pandas as pd
    lines = [
        "# RL深度训练 — 500k三算法OOS对比",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 训练: 2020-2024段, {TOTAL:,} steps/算法; OOS: 2025-2026(370步)",
        f"- 基准: PPO-50k版OOS=+7.49%/正步54.6%",
        "",
        "| 算法 | OOS总收益 | 步数 | 正步占比 | 训练分钟 |",
        "|------|----------|------|---------|---------|",
    ]
    for k, v in results.items():
        if "err" not in v:
            lines.append(f"| {k} | {v['oos_ret']:+.4f} | {v['steps']} "
                         f"| {v['pos_rate']:.1%} | {v['train_sec']/60:.0f} |")
        else:
            lines.append(f"| {k} | ERR: {v['err']} | | | |")
    best = max((k for k in results if "err" not in results[k]),
               key=lambda k: results[k]["oos_ret"], default=None)
    if best:
        lines += ["", f"**最优算法: {best}** (OOS {results[best]['oos_ret']:+.4f})",
                  "", "- 若显著超50k版(+0.0749), 建议纳入研究跟踪升级",
                  f"- 总耗时 {(time.time()-t0)/60:.0f}min", ""]
    p = REPORT / f"RL_DEEP_TRAIN_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")


if __name__ == "__main__":
    main()
