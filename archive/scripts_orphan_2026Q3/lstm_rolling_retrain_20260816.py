# -*- coding: utf-8 -*-
"""
LSTM rolling归一重训 — 遗留#2 (消除全期z-score泄漏)
================================================
DEEP_SEQ_RETEST的翻案信号有一个瑕疵: 特征z-score用全期均值方差(轻微前视)。
本脚本改为**expanding-window归一**(仅用t及之前数据的mu/sd), 其余口径完全一致。
通过标准: LSTM rolling版IC > 同特征Ridge(0.0591) → 翻案确认, 接灰度评估
未通过 → 翻案信号降级为"归一泄漏产物", 维持关闭

产出: reports/LSTM_ROLLING_RETRAIN_20260816.md
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

sys.path.insert(0, r"D:\zcodesj")
from deep_sequence_retest_20260816 import (  # noqa: E402
    SeqLSTM, SeqGRU, train_year, eval_year, DEV, WIN, FWD)

CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")


def prepare_expanding_norm():
    """重导数据: 特征用expanding-window z-score(仅用<=t数据, 预热250日)。"""
    cache = CACHE / "deep_seq_data_rolling.pkl"
    if cache.exists():
        return pickle.load(open(cache, "rb"))
    base = pickle.load(open(CACHE / "deep_seq_data.pkl", "rb"))
    F = base["F"].copy()   # 注意: base里已做过全期z-score+nan_to_num
    # 恢复原始尺度无法直接做到——重新从raw特征构建更干净, 但raw未存;
    # 近似方案: 对已z-score序列再做expanding标准化(二次标准化, 单调变换不改变截面序)
    # 更严格: 直接对F做expanding mu/sd——若原全期z是线性变换, expanding后仍是"expanding z of 原始序列的线性变换"
    # 数学上: expanding_z(a*x+b) = expanding_z(x) (线性变换吸收进mu/sd) —— 等价于直接对原始序列做expanding z ✓
    C, N, T = F.shape
    cs = np.cumsum(F, axis=2)
    cs2 = np.cumsum(F * F, axis=2)
    cnt = np.arange(1, T + 1)[None, None, :]
    warm = 250
    mu = cs / cnt
    var = np.maximum(cs2 / cnt - mu ** 2, 0)
    sd = np.sqrt(var) + 1e-9
    mu[:, :, :warm] = np.nan
    sd[:, :, :warm] = np.nan
    F2 = (F - mu) / sd
    F2 = np.nan_to_num(F2, nan=0.0)
    base["F"] = F2.astype(np.float32)
    base["note"] = "expanding-window z-score(预热250日), 无未来信息"
    pickle.dump(base, open(cache, "wb"))
    print(f"  rolling归一完成: {F2.shape}")
    return base


def main():
    t0 = time.time()
    print(f"=== LSTM rolling归一重训 (device={DEV}) ===")
    data = prepare_expanding_norm()
    test_years = [2022, 2023, 2024, 2025, 2026]
    results = {}
    # Ridge基线(同数据重跑, 公平)
    from deep_sequence_retest_20260816 import ridge_baseline
    for yr in test_years:
        ics = ridge_baseline(data, yr)
        if ics:
            results.setdefault("ridge", {})[yr] = float(np.nanmean(ics))
    print(f"  ridge(rolling版): {results.get('ridge')}", flush=True)
    # LSTM + GRU(最强两个)
    for name, mk in (("lstm", SeqLSTM), ("gru", SeqGRU)):
        model = mk().to(DEV)
        for yr in test_years:
            train_years = tuple(y for y in range(yr - 3, yr))
            train_year(model, data, train_years)
            ics, rets = eval_year(model, data, yr)
            results.setdefault(name, {})[yr] = float(np.nanmean(ics)) if ics else np.nan
            print(f"  {name} {yr}: IC={np.nanmean(ics):.4f} n={len(ics)} "
                  f"({time.time()-t0:.0f}s)", flush=True)

    ridge_m = np.nanmean(list(results["ridge"].values()))
    lines = [
        "# LSTM Rolling归一重训 — 翻案确认/降级终审",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        "- 变更: 全期z-score → expanding-window z-score(预热250日, 严格无未来信息)",
        "- 其余口径与DEEP_SEQ_RETEST完全一致(Top800/5特征/60窗/WF年度重训)",
        "",
        "| 模型 | " + " | ".join(str(y) for y in test_years) + " | 均值 |",
        "|------|" + "---|" * (len(test_years) + 1),
    ]
    for name in ("ridge", "lstm", "gru"):
        if name in results:
            row = [f"{results[name].get(y, np.nan):.4f}" for y in test_years]
            lines.append(f"| {name} | " + " | ".join(row) +
                         f" | {np.nanmean(list(results[name].values())):.4f} |")
    lstm_m = np.nanmean(list(results.get("lstm", {}).values()))
    lines += ["", f"Ridge(rolling版)均值: {ridge_m:.4f}", f"LSTM(rolling版)均值: {lstm_m:.4f}", ""]
    if lstm_m > ridge_m + 0.003:
        lines.append(f"**✅翻案确认**: LSTM({lstm_m:.4f})超Ridge({ridge_m:.4f})且差值>0.003, "
                     "泄漏消除后优势保持 → 建议接灰度轨道(10万账本3个月观察)")
    elif lstm_m > ridge_m:
        lines.append(f"**⚠️边缘**: LSTM略超Ridge但差值≤0.003, 不足以支撑独立轨道, 维持研究")
    else:
        lines.append(f"**❌翻案降级**: rolling归一后LSTM({lstm_m:.4f})不再超Ridge({ridge_m:.4f}), "
                     "DEEP_SEQ的翻案信号系归一泄漏产物 → 深度时序维持关闭(回到原判)")
    lines += ["", f"耗时 {time.time()-t0:.0f}s", ""]
    p = REPORT / f"LSTM_ROLLING_RETRAIN_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")
    pickle.dump(results, open(CACHE / "lstm_rolling_results.pkl", "wb"))


if __name__ == "__main__":
    main()
