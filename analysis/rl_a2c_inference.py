#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rl_a2c_inference.py — 复活·强化学习(revival_rl) A2C 推理出票
====================================================================
2026-11-09 补缺件令产物: RL缺件真身=观测面板文件名硬编码在归档驱动里(旧pkl止于
20260731), 而面板生产线一直活着(fml_v2 build_panels 83秒可建新窗)。本脚本打通:
  fml_v2双面板pkl拼接(旧2020窗+新20261106窗, 新值覆盖重叠区)
  → aurumq格式parquet(归档驱动export_parquet同构复刻: fund_*/tech_*前缀+主板过滤)
  → FactorPanelLoader.load_panel(股票维度对齐训练universe: 以训练期2020-02~2026-07
    加载的stock_codes为锚, 全期列对齐到该集合该顺序, 防新IPO撑爆obs维度)
  → A2C ckpt对末日因子截面predict → 动作权重=选股分数
  → 可交易掩码过滤(ST/停牌/次新, _apply_trading_mask同口径) → 权重降序TopN
  → data/rl_a2c_scores_{T}.json (revival_wave23_scanner.m_rl 消费)
合同: 复活实验PREREG(revival_paper_test_PREREG_20261027); ckpt=a2c_train2023_500k_20260817
(真OOS训练2020-02~2023-12, test段为2024-01~2026-07=训外); obs=纯因子截面(_get_obs实证)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import pickle
import sqlite3
import sys
import time
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import numpy as np
import pandas as pd

ROOT = Path(r"D:\zcodesj")
CACHE = ROOT / "_cache"
RL_DIR = ROOT / "strategy_libs" / "aurumq-rl"
CKPT = RL_DIR / "ckpt" / "a2c_train2023_500k_20260817.zip"
OUT_PARQUET = RL_DIR / "data" / "ashare_panel_20261106.parquet"

OLD_PKL = CACHE / "fml_v2_panels_20200115_20261106_fwd20.pkl"  # 全史单窗(2026-11-09建)
NEW_PKL = OLD_PKL

_pkg = types.ModuleType("aurumq_rl")
_pkg.__path__ = [str(RL_DIR / "src" / "aurumq_rl")]
sys.modules.setdefault("aurumq_rl", _pkg)
sys.path.insert(0, str(RL_DIR / "src"))
sys.path.insert(0, str(ROOT))

FUND_MAP = {"bp": "fund_bp", "ep": "fund_ep", "ag": "fund_ag", "droe": "fund_droe",
            "acc": "fund_acc", "sg": "fund_sg", "roa": "fund_roa", "pm": "fund_pm",
            "ato": "fund_ato", "opa": "fund_opa", "tbi": "fund_tbi", "bl": "fund_bl"}
TECH_MAP = {"turnover": "tech_turnover", "volume_ratio": "tech_vr",
            "dv_ratio": "tech_dv", "rev20": "tech_rev20", "vol20": "tech_vol20",
            "illiq20": "tech_illiq", "size": "tech_size", "max_ret20": "tech_maxret"}
SPLIT_NEW = pd.Timestamp("2024-03-24")  # 新面板起点, 重叠区以新值为准


def build_parquet(end_date: int) -> Path:
    """双面板拼接 → aurumq parquet(与归档驱动export_parquet同构)"""
    if OUT_PARQUET.exists():
        return OUT_PARQUET
    po = pickle.load(open(OLD_PKL, "rb"))
    if NEW_PKL == OLD_PKL:  # 全史单窗模式(2026-11-09): 无需拼接
        wo = po["wides"]
        cal = list(po["calendar"])
        wides = {k: wo[k] for k in {**FUND_MAP, **TECH_MAP}}
    else:
        pn = pickle.load(open(NEW_PKL, "rb"))
        wo, wn = po["wides"], pn["wides"]
        cal = list(po["calendar"]) + [d for d in pn["calendar"] if d >= SPLIT_NEW]
        wides = {}
        for k in {**FUND_MAP, **TECH_MAP}:
            w_old, w_new = wo[k], wn[k]
            keep_old = w_old.loc[w_old.index < SPLIT_NEW]
            wides[k] = pd.concat([keep_old, w_new])

    conn = sqlite3.connect(
        r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro",
        uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, close, volume FROM stock_daily_meta_qfq "
        "WHERE date BETWEEN 20200115 AND ? AND close > 0", conn, params=(end_date,))
    conn.close()
    # 2026-11-09 双源补齐: 训练后(2026-08~11)退市的9只已从活票库清除, obs股票维度
    # 71040≠ckpt71220 → 从退市库补 close/volume(loader按close在场决定股票列)。
    conn = sqlite3.connect(
        r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_delisted.db?mode=ro",
        uri=True, timeout=60)
    df_dl = pd.read_sql_query(
        "SELECT code, date, close, volume FROM stock_daily_delisted "
        "WHERE date BETWEEN 20200115 AND ? AND close > 0", conn, params=(end_date,))
    conn.close()
    have = set(df["code"].astype(str).str[:6])
    df_dl = df_dl[df_dl["code"].astype(str).str[:6].isin(
        {c for c in df_dl["code"].astype(str).str[:6].unique() if c not in have})]
    df = pd.concat([df, df_dl], ignore_index=True)
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(("60", "00", "30", "68"))]
    df = df.drop_duplicates(["code", "date"])
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    close_w = df.pivot(index="date", columns="code", values="close").sort_index()
    vol_w = df.pivot(index="date", columns="code", values="volume").sort_index()
    cal_idx = pd.DatetimeIndex(cal)
    close_w = close_w.reindex(cal_idx)
    vol_w = vol_w.reindex(cal_idx)
    pct_w = close_w.pct_change().clip(-0.21, 0.21)

    rows = {dst: wides[src].stack() for src, dst in {**FUND_MAP, **TECH_MAP}.items()}
    base = {"close": close_w.stack(), "pct_chg": pct_w.stack(), "vol": vol_w.stack()}
    allstack = {**base, **rows}
    big = pd.DataFrame(allstack).reset_index()
    big.columns = ["trade_date", "ts_code"] + list(allstack.keys())
    big["trade_date"] = pd.to_datetime(big["trade_date"]).dt.date

    def _sfx(c):
        return c + (".SH" if c.startswith("6") else ".SZ")
    big["ts_code"] = big["ts_code"].astype(str).str[-6:].map(_sfx)
    big = big[big["ts_code"].str.match(r"^(60|00)")]  # 主板(与训练驱动同口径)
    OUT_PARQUET.parent.mkdir(exist_ok=True)
    big.to_parquet(OUT_PARQUET, index=False)
    print(f"[INFO] parquet: {len(big):,}行×{big.shape[1]}列 → {OUT_PARQUET.name}")
    return OUT_PARQUET


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=int, default=20261106, help="信号日(=面板末日)")
    ap.add_argument("--topn", type=int, default=30)
    ap.add_argument("--ckpt", default="", help="ckpt路径(缺省=重训版20261109, 不在则旧版20260817)")
    a = ap.parse_args()
    t0 = time.time()
    global CKPT
    if a.ckpt:
        CKPT = Path(a.ckpt)
    elif (RL_DIR / "ckpt" / "a2c_train2023_500k_20261109.zip").exists():
        CKPT = RL_DIR / "ckpt" / "a2c_train2023_500k_20261109.zip"
    if not CKPT.exists():
        print(json.dumps({"error": f"ckpt缺失: {CKPT}"})); return 2

    pq = build_parquet(a.date)
    from aurumq_rl.data_loader import FactorPanelLoader
    from stable_baselines3 import A2C

    loader = FactorPanelLoader(str(pq))
    # 训练期口径加载(2020-02-01~2026-07-31)=训练universe锚(新股2026-08+不在其中)
    ref = loader.load_panel(start_date=dt.date(2020, 2, 1),
                            end_date=dt.date(2026, 7, 31),
                            n_factors=20, forward_period=10)
    full = loader.load_panel(start_date=dt.date(2020, 2, 1),
                             end_date=dt.date(int(str(a.date)[:4]), int(str(a.date)[4:6]), int(str(a.date)[6:])),
                             n_factors=20, forward_period=10)
    print(f"[INFO] ref股票数={len(ref.stock_codes)} 全期股票数={len(full.stock_codes)} "
          f"末日={full.dates[-1]} 耗时{time.time()-t0:.0f}s")

    # 维度对齐: 旧ckpt(20260817)训练于旧universe → 对齐到训练窗参考集;
    # 新ckpt(20261109重训版)训练于本parquet全期universe → 直接用全集不对齐
    use_ref_align = "20261109" not in CKPT.name
    if use_ref_align:
        ref_codes = list(ref.stock_codes)
        idx_map = {c: i for i, c in enumerate(full.stock_codes)}
        sel = [idx_map[c] for c in ref_codes if c in idx_map]
        aligned_codes = [c for c in ref_codes if c in idx_map]
        aligned = len(sel) != len(full.stock_codes)
    else:
        sel = list(range(len(full.stock_codes)))
        aligned_codes = list(full.stock_codes)
        aligned = False
    f_last = full.factor_array[-1]          # (n_stocks, 20) 末日截面
    obs = (f_last[sel, :] if aligned else f_last).reshape(-1).astype(np.float32)
    print(f"[INFO] 对齐后obs维度={obs.shape[0]} (=股票数×20)")

    model = A2C.load(str(CKPT), device="cpu")
    exp_dim = model.observation_space.shape[0]
    if obs.shape[0] != exp_dim:
        print(json.dumps({"error": f"obs维度不匹配: 推理{obs.shape[0]} vs ckpt{exp_dim}"})); return 2
    action, _ = model.predict(obs, deterministic=True)
    w = np.asarray(action).reshape(-1)

    # 可交易掩码(与env同口径): ST/停牌/上市<30日/收盘缺失
    from aurumq_rl.env import _apply_trading_mask
    codes6 = [c[:6] for c in aligned_codes]
    ret_t = full.return_array[-1][sel] if aligned else full.return_array[-1]
    pct_t = full.pct_change_array[-1][sel] if aligned else full.pct_change_array[-1]
    st_t = full.is_st_array[-1][sel] if aligned else full.is_st_array[-1]
    susp_t = full.is_suspended_array[-1][sel] if aligned else full.is_suspended_array[-1]
    ipo_t = full.days_since_ipo_array[-1][sel].astype(np.float64) if aligned \
        else full.days_since_ipo_array[-1].astype(np.float64)
    close_t = full.close_array[-1][sel].astype(np.float64) if aligned \
        else full.close_array[-1].astype(np.float64)
    # 掩码传入pct(有限值)替代forward returns: 末日截面forward收益按定义为NaN,
    # _apply_trading_mask的isfinite(returns)条件是回测奖励口径(无未来=不可计奖)
    # 而非可交易性——选票场景只保留涨跌停/ST/停牌/次新四真条件。
    mask = _apply_trading_mask(returns=pct_t.astype(np.float64), pct_changes=pct_t.astype(np.float64),
                               is_st=st_t, is_suspended=susp_t, days_since_ipo=ipo_t,
                               stock_codes=np.array(aligned_codes), close=close_t)
    tradeable = mask != 0

    from stock_name_lookup import get_name
    ranked = sorted(
        [{"code": c, "name": get_name(c) or "?", "weight": round(float(w[i]), 6),
          "close": (None if np.isnan(close_t[i]) else round(float(close_t[i]), 2))}
         for i, c in enumerate(codes6) if tradeable[i]],
        key=lambda x: -x["weight"])[: a.topn]
    out = {"signal_date": a.date, "ckpt": str(CKPT), "parquet": str(pq),
           "n_ref_stocks": len(ref.stock_codes), "n_aligned": len(aligned_codes),
           "obs_dim": int(obs.shape[0]), "n_tradeable": int(tradeable.sum()),
           "weights_stats": {"max": round(float(w.max()), 6), "min": round(float(w.min()), 6),
                             "pos": int((w > 0).sum())},
           "ranked": ranked, "generated": dt.datetime.now().isoformat()}
    outf = ROOT / "data" / f"rl_a2c_scores_{a.date}.json"
    outf.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[INFO] 出票{len(ranked)}只 | 权重正数{int((w>0).sum())} | → {outf.name} "
          f"| 总耗时{time.time()-t0:.0f}s")
    for r in ranked[:10]:
        print("   %s %s w=%.4f close=%s" % (r["code"], r["name"], r["weight"], r["close"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
