#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PBO/CSCV 回测过拟合概率质检工具（PD-037 候选A 立项固化，主人 2026-10-07 批准）。

用途：对「参数网格类策略」定量计算回测过拟合概率（Probability of Backtest
Overfitting, Bailey et al. 2017 的 CSCV 框架），作为回测质检的一票否决参考：
  PBO < 0.2 可接受 / 0.2~0.5 存疑 / > 0.5 过拟合概率过半 = 否决线。
另输出：样本内最优参数的样本外夏普相关（越负越病态）与参数高原比。

出处：本地 dry-run 已实证（2026-10-07，tmp/pbo_cscv_dryrun_20261007.py）：
沪深300 × 790 组双均线参数 → PBO=0.659（否决），IS/OOS 最优夏普相关 -0.64。
框架出处：石川《美丽的回测》(xueqiu 1524373058/115541992)；本轮学习报告
reports/xueqiu_study_20261007.md §2.1。

数据源（只读）：通达信 vipdoc 日线（.day 32字节记录，标准格式解析）。
扩展说明：当前内建「双均线趋势」一族（与 dry-run 同规格，便于复现对照）；
其它策略族接入须实现 param_returns(closes, params) -> T×N 收益矩阵后复用
cscv_pbo()。注意石川原文警告：趋势类策略路径依赖会扭曲 CSCV，判读时须
结合 IS/OOS 散点斜率（深负=路径依赖症状）。

用法：
  python pbo_cscv_check.py                       # 默认沪深300 sh000300
  python pbo_cscv_check.py --kline D:\\...\\sz000001.day --window 1000
"""
from __future__ import annotations

import argparse
import itertools
import json
import struct

import numpy as np

DEFAULT_KLINE = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000300.day"


def read_tdx_day(path: str) -> tuple[list[int], np.ndarray]:
    """解析通达信 .day 文件 → (dates, closes)。只读。"""
    dates, closes = [], []
    with open(path, "rb") as f:
        while True:
            raw = f.read(32)
            if len(raw) < 32:
                break
            d, _o, _h, _l, c, _amt, _vol, _resv = struct.unpack("<IIIIIfII", raw)
            dates.append(d)
            closes.append(float(c))
    return dates, np.array(closes)


def double_ma_returns(closes: np.ndarray, params: list[tuple[int, int]]) -> np.ndarray:
    """双均线趋势策略收益矩阵：短均线上穿长均线持多（next-bar），空仓收益0。

    输入 closes 为含预热窗的收盘序列；输出按「截掉 max(sw,lw) 预热后」对齐。
    """
    n_avail = len(closes)
    ret = np.zeros(n_avail)
    ret[1:] = closes[1:] / closes[:-1] - 1.0
    M = np.zeros((n_avail, len(params)), dtype=np.float64)
    for j, (sw, lw) in enumerate(params):
        ma_s = np.convolve(closes, np.ones(sw) / sw, mode="valid")
        ma_l = np.convolve(closes, np.ones(lw) / lw, mode="valid")
        start = max(sw, lw) - 1
        sig = ma_s[start - (sw - 1): n_avail - 1 - (sw - 1)] > \
              ma_l[start - (lw - 1): n_avail - 1 - (lw - 1)]
        M[start + 1:, j] = sig * ret[start + 1:]
    return M


def cscv_pbo(M: np.ndarray, s_blocks: int = 10) -> dict:
    """CSCV 主流程：T×N 收益矩阵 → S 块 → C(S,S/2) 配对 → PBO 与诊断量。"""
    T, N = M.shape

    def sr(x):
        mu = x.mean(axis=0)
        sd = x.std(axis=0)
        sd = np.where(sd < 1e-12, np.nan, sd)
        return mu / sd

    blocks = np.array_split(np.arange(T), s_blocks)
    lam, is_best, oos_best = [], [], []
    for combo in itertools.combinations(range(s_blocks), s_blocks // 2):
        tr = np.concatenate([blocks[i] for i in combo])
        te = np.concatenate([blocks[i] for i in range(s_blocks) if i not in combo])
        sr_tr = sr(M[tr])
        n_star = int(np.nanargmax(sr_tr))
        sr_te = sr(M[te])
        valid = ~np.isnan(sr_te)
        rank_pos = int(np.sum(sr_te[valid] < sr_te[n_star]))
        w = (rank_pos + 1) / (int(valid.sum()) + 1)
        w = min(max(w, 1e-4), 1 - 1e-4)
        lam.append(np.log(w / (1 - w)))
        is_best.append(float(sr_tr[n_star]))
        oos_best.append(float(sr_te[n_star]))
    lam = np.array(lam)
    is_arr, oos_arr = np.array(is_best), np.array(oos_best)
    return {
        "pbo": round(float((lam <= 0).mean()), 4),
        "combos": len(lam),
        "is_oos_best_sr_corr": round(float(np.corrcoef(is_arr, oos_arr)[0, 1]), 4),
        "regression_slope": round(float(np.polyfit(is_arr, oos_arr, 1)[0]), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="PBO/CSCV 回测过拟合概率质检（PD-037-A）")
    ap.add_argument("--kline", default=DEFAULT_KLINE, help="通达信 .day 日线文件路径")
    ap.add_argument("--window", type=int, default=1000, help="回测窗口T（交易日）")
    ap.add_argument("--sw-max", type=int, default=20, help="短均线上限（网格 SW=1..sw_max）")
    ap.add_argument("--lw-max", type=int, default=50, help="长均线上限（LW=SW+1..lw_max）")
    ap.add_argument("--out", default="", help="结果JSON落盘路径（可选）")
    a = ap.parse_args()

    dates, closes_all = read_tdx_day(a.kline)
    need = a.window + a.lw_max + 5
    if len(closes_all) < need:
        print(json.dumps({"error": f"K线不足: 有{len(closes_all)}根 < 需要{need}根"}, ensure_ascii=False))
        return 1
    closes = closes_all[-need:]
    params = [(sw, lw) for sw in range(1, a.sw_max + 1)
              for lw in range(sw + 1, a.lw_max + 1)]
    M_full = double_ma_returns(closes, params)
    M = M_full[-a.window:]

    res = cscv_pbo(M)
    sr_full = M.mean(axis=0) / np.where(M.std(axis=0) < 1e-12, np.nan, M.std(axis=0))
    bj = int(np.nanargmax(sr_full))
    bsw, blw = params[bj]
    neigh = [sr_full[j] for j, (sw, lw) in enumerate(params)
             if abs(sw - bsw) <= 3 and abs(lw - blw) <= 3 and not np.isnan(sr_full[j])]
    res.update({
        "target": a.kline,
        "window": {"first": dates[-a.window], "last": dates[-1], "T": a.window},
        "grid": {"n_params": len(params), "rule": "MA短>MA长持多 next-bar 空仓0"},
        "plateau": {"best_params": [bsw, blw],
                    "best_ann_ret": round(float(M[:, bj].mean() * 244), 4),
                    "median_ann_ret": round(float(np.nanmedian(M.mean(axis=0)) * 244), 4),
                    "neighbor_ratio": round(float(sr_full[bj] / np.median(neigh)), 3)},
        "verdict_rule": "PBO<0.2可接受 / 0.2-0.5存疑 / >0.5否决线; slope深负=路径依赖症状",
        "verdict": ("REJECT(过拟合概率过半)" if res["pbo"] > 0.5 else
                    "SUSPECT" if res["pbo"] > 0.2 else "ACCEPTABLE"),
    })
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
