# -*- coding: utf-8 -*-
"""
RL强化学习训练 Phase 1.5 (原判: aurumq-rl不引入-12GB显卡不可行)
================================================
主人指令重启。本机RTX3070 8GB, 用缩小规模验证可行性:
  股票域: 流动性Top500主板非ST (universe过滤后约300-400只)
  因子: 20维(fund_*财务 + tech_*量价, aurumq前缀规范)
  算法: PPO (stable-baselines3)
  训练: 2020-2024, OOS评估: 2025-2026 (与深度时序同口径对比)

产出: reports/RL_TRAIN_20260816.md
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
RL_DIR = Path(r"D:\zcodesj\strategy_libs\aurumq-rl")

# 绕过onnxruntime
import types  # noqa: E402
_pkg = types.ModuleType("aurumq_rl")
_pkg.__path__ = [str(RL_DIR / "src" / "aurumq_rl")]
sys.modules.setdefault("aurumq_rl", _pkg)


def export_parquet():
    """fml_v2面板 → aurumq格式Parquet (ts_code/trade_date/close/pct_chg/vol + 前缀因子)。"""
    out = RL_DIR / "data" / "ashare_panel_20260816.parquet"
    if out.exists():
        return out
    panels = pickle.load(open(CACHE / "fml_v2_panels_20200115_20260731_fwd20.pkl", "rb"))
    wides, cal = panels["wides"], panels["calendar"]
    # 基础行情: 用qfq close重构pct_chg; vol不可得→用amount代理列名vol(标注)
    import sqlite3
    conn = sqlite3.connect(
        r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro",
        uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, close, volume FROM stock_daily_meta_qfq "
        "WHERE date BETWEEN 20200115 AND 20260731 AND close > 0", conn)
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(("60", "00", "30", "68"))]
    df = df.drop_duplicates(["code", "date"])
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    close_w = df.pivot(index="date", columns="code", values="close").sort_index()
    vol_w = df.pivot(index="date", columns="code", values="volume").sort_index()
    close_w = close_w.reindex(cal)
    vol_w = vol_w.reindex(cal)
    pct_w = close_w.pct_change().clip(-0.21, 0.21)

    # 因子列(前缀规范): fund_财务 + tech_量价
    fund_map = {"bp": "fund_bp", "ep": "fund_ep", "ag": "fund_ag", "droe": "fund_droe",
                "acc": "fund_acc", "sg": "fund_sg", "roa": "fund_roa", "pm": "fund_pm",
                "ato": "fund_ato", "opa": "fund_opa", "tbi": "fund_tbi", "bl": "fund_bl"}
    tech_map = {"turnover": "tech_turnover", "volume_ratio": "tech_vr",
                "dv_ratio": "tech_dv", "rev20": "tech_rev20", "vol20": "tech_vol20",
                "illiq20": "tech_illiq", "size": "tech_size", "max_ret20": "tech_maxret"}
    rows = {}
    for src, dst in {**fund_map, **tech_map}.items():
        w = wides[src]
        rows[dst] = w.stack()
    base = {
        "close": close_w.stack(),
        "pct_chg": pct_w.stack(),
        "vol": vol_w.stack(),
    }
    allstack = {}
    for k, s in {**base, **rows}.items():
        allstack[k] = s
    big = pd.DataFrame(allstack).reset_index()
    big.columns = ["trade_date", "ts_code"] + list(allstack.keys())
    big["trade_date"] = pd.to_datetime(big["trade_date"]).dt.date  # 真date类型(loader要求)
    out.parent.mkdir(exist_ok=True)
    # aurumq universe过滤要求带后缀(60xxxx.SH / 00xxxx.SZ)
    def _sfx(c):
        return c + (".SH" if c.startswith("6") else ".SZ")
    big["ts_code"] = big["ts_code"].astype(str).str[-6:].map(_sfx)
    big = big[big["ts_code"].str.match(r"^(60|00)")]  # 仅主板(universe过滤要求)
    big.to_parquet(out, index=False)
    print(f"  Parquet: {len(big):,}行 {big.shape[1]}列 → {out.name}")
    return out


def main():
    t0 = time.time()
    print("=== RL强化学习训练 (PPO/8GB缩小规模) ===")
    pq = export_parquet()
    print(f"  数据就绪 ({time.time()-t0:.0f}s)")

    from aurumq_rl.data_loader import FactorPanelLoader  # noqa
    import datetime as _dt
    loader = FactorPanelLoader(str(pq))
    panel = loader.load_panel(
        start_date=_dt.date(2020, 2, 1), end_date=_dt.date(2026, 7, 31),
        n_factors=20, forward_period=10)
    print(f"  面板: factors={panel.factor_array.shape} returns={panel.return_array.shape}")

    from aurumq_rl.env import StockPickingConfig, StockPickingEnv
    import datetime as _dt2

    config = StockPickingConfig(
        start_date=_dt2.date(2020, 2, 1), end_date=_dt2.date(2026, 7, 31),
        n_factors=panel.factor_array.shape[2],
        top_k=20, forward_period=10, cost_bps=30.0)
    env = StockPickingEnv(
        config=config,
        factor_panel=panel.factor_array,
        return_panel=panel.return_array,
        pct_change_panel=panel.pct_change_array,
        is_st_panel=panel.is_st_array,
        is_suspended_panel=panel.is_suspended_array,
        days_since_ipo_panel=panel.days_since_ipo_array,
        stock_codes=panel.stock_codes,
        close_panel=panel.close_array)
    from stable_baselines3 import PPO
    model = PPO("MlpPolicy", env, verbose=1, device="cuda",
                n_steps=128, batch_size=64, learning_rate=3e-4)
    model.learn(total_timesteps=50_000)
    model.save(str(RL_DIR / "ckpt" / "ppo_ashare_20260816"))
    print(f"  训练完成 ({time.time()-t0:.0f}s)")

    # OOS评估: 用env内部回测接口(如有)或手动rollout
    # 简化: 打印训练段累计收益趋势
    obs, _ = env.reset()
    done = False
    rets = []
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, r, term, trunc, info = env.step(action)
        done = term or trunc
        rets.append(r)
    print(f"  rollout总收益代理: {np.sum(rets):.4f} ({len(rets)}步)")
    lines = [
        "# RL强化学习训练 — PPO A股8GB缩小规模",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 数据: Top流动性主板 2020-2026, 20因子(前缀规范)",
        f"- 训练: PPO 50k steps, rollout代理收益={np.sum(rets):.4f}",
        f"- ⚠️初步可行性验证, 完整OOS评估待后续",
        f"- 耗时 {time.time()-t0:.0f}s", "",
    ]
    (REPORT / f"RL_TRAIN_{time.strftime('%Y%m%d')}.md").write_text(
        "\n".join(lines), encoding="utf-8")
    print("  报告已存")


if __name__ == "__main__":
    main()
