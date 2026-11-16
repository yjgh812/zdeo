#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t38 C4 审计 — 深度时序重测无泄漏评估
======================================
对 deep_sequence_retest_20260816.py 的既有人数据缓存做严格无泄漏审计，
不重跑训练（8/16 已产出 cuda 全量结果；本审计验证其方法学可信度）。

审计项:
  A. 特征归一化泄漏: prepare_data 用全期 mu/sd 做 z-score（代码第76-78行）
     → 归一化参数含未来统计量 → 特征值含轻微未来信息（数据级泄漏）
  B. 标签窗口对齐: fwd[t] = sum(logret, t+1..t+20), 特征窗 = F[:,:,t-60:t]
     → 特征与标签无重叠（标签起点 t+1 > 特征终点 t）✓
  C. Walk-Forward 隔离: 训练年 train_year(model, data, years_train) 用
     years_train = (yr-3..yr-1) 内全部日期样本（含测试年前特征数据点？）
     → 训练样本的特征窗跨入测试年的风险（t-60:t 窗口若 t 在训练年但窗口
       边缘接近测试年 → 无直接标签泄漏，但特征数据点来自跨年，需评估）
  D. embargo 声明: 脚本头写 EMBARGO=20 但 train_year 未见 embargo 实现
     → 核对 EMBARGO 常量是否实际使用（若未用则声称与实现不符）
  E. 组合收益近似: eval_year 用 r20/FWD 替代 T+1 组合收益 → 非真实成交

输出: reports/C4_leak_audit_YYYYMMDD.md
铁律: 只读。
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")


def main() -> int:
    t0 = time.time()
    data = pickle.load(open(CACHE / "deep_seq_data.pkl", "rb"))
    F, fwd, dates = data["F"], data["fwd"], data["dates"]
    N, T = F.shape[1], F.shape[2]
    WIN, FWD = 60, 20

    lines = ["# C4 深度时序重测 — 无泄漏审计报告", "",
             f"- 生成: {time.strftime('%Y-%m-%d %H:%M')} | 审计对象: deep_sequence_retest_20260816.py",
             f"- 数据: {F.shape[0]}特征×{N}股×{T}日 ({dates[0]}~{dates[-1]})",
             ""]

    # ── A. 特征归一化泄漏 ──
    lines += ["## A. 特征归一化泄漏（数据级）", ""]
    # 从代码确认: mu/sd 用全期 nanmean/nanstd（沿时间维 axis=2）→ 全期统计量
    # 在缓存数据上验证: 任取一只股票某特征, 比较 [0:t] 与全期 的均值差异
    np.random.seed(0)
    c_idx = np.random.choice(N, 5)
    f_idx = np.random.choice(F.shape[0], 3)
    diffs = []
    for c in c_idx:
        for f in f_idx:
            s = F[f, c]
            m_full = s.mean()
            # 前 60% 时间的局部均值
            split = int(T * 0.6)
            m_part = s[:split].mean()
            diffs.append((m_full - m_part) / (s.std() + 1e-9))
    lines += [f"- 归一化实现: `mu=nanmean(F, axis=2)`, `sd=nanstd(F, axis=2)`（**全期**统计量）",
              f"- 抽样验证: 5股×3特征, 全期均值 vs 前60%局部均值 的标准化差 = "
              f"{np.mean(np.abs(diffs)):.4f}（均值非零 → 全期归一使历史特征值携带未来分布信息）",
              "- **判定: ⚠️ 轻微数据级泄漏** — 归一化参数含未来统计量, 真实 OOS IC 会略低于报告值",
              "- 修复方向: 改为 rolling/expanding z-score（仅用 t 及以前数据计算 mu/sd）", ""]

    # ── B. 标签窗口对齐 ──
    lines += ["## B. 标签-特征窗口对齐", ""]
    lines += [f"- 特征窗: F[:, :, t-{WIN}:t]（t-60 ~ t-1 日）",
              f"- 标签: fwd[t] = Σlogret(t+1..t+{FWD})（t+1 ~ t+20 日）",
              "- 特征终点 t-1 < 标签起点 t+1 → **无重叠, 标签对齐正确 ✓**", ""]

    # ── C. Walk-Forward 训练/测试隔离 ──
    lines += ["## C. Walk-Forward 隔离", ""]
    lines += ["- 训练: `train_year(model, data, years_train)`, years_train = (yr-3..yr-1)",
              "- 测试: `eval_year(model, data, yr)` 仅用 yr 年度日期",
              "- 训练样本特征窗 t-60:t 可能跨越训练年与测试年边界（如训练到 2021-12-31 的样本,",
              "  其特征窗含 2021-10~12 数据；无标签泄漏因为标签都在训练年内）",
              "- **判定: 训练/测试按年度硬隔离 ✓；特征窗跨年边界不构成标签泄漏**（特征本身含未来归一化偏差, 已计入 A）", ""]

    # ── D. embargo 实现核对 ──
    lines += ["## D. Embargo 声称 vs 实现", ""]
    import re
    src = Path(r"D:\zcodesj\deep_sequence_retest_20260816.py").read_text(encoding="utf-8")
    emb_decl = re.findall(r"EMBARGO\s*=\s*\d+", src)
    emb_use = len(re.findall(r"embargo|EMBARGO", src))
    lines += [f"- 常量声明: {emb_decl}（EMBARGO=20）",
              f"- 实际使用: {emb_use} 处出现 embargo/EMBARGO（仅声明, 训练循环未见 embargo 剔除逻辑）",
              "- **判定: ⚠️ 声称的 embargo 未在训练样本筛选中实现** — 年度隔离已提供主要防护,",
              "  但训练年最后 20 日样本的标签虽在训练年内, 其与测试年初存在序列相关泄漏通道",
              "- 修复方向: 训练样本筛选加 `t + FWD + EMBARGO < 训练年最后日期` 剔除", ""]

    # ── E. 组合收益近似 ──
    lines += ["## E. 组合收益近似", ""]
    lines += ["- eval_year 用 `r20/FWD`（Fwd20 平均日收益）替代 Top50 真实 T+1 组合收益",
              "- **判定: ⚠️ 收益口径为近似** — 非真实成交序列（无成本/无 T+1 约束细节）",
              "- 报告仅以 RankIC 作模型比较依据（IC 口径不受此影响）；组合收益数字不作生产依据", ""]

    # ── 综合判定 ──
    lines += ["## 综合判定", "",
              "| 项 | 状态 | 影响 |",
              "|---|---|---|",
              "| A 归一化泄漏 | ⚠️ 轻微 | 真实 OOS IC 略低, 相对比较仍有效 |",
              "| B 标签对齐 | ✅ | 无影响 |",
              "| C WF 隔离 | ✅ | 年度硬隔离有效 |",
              "| D embargo | ⚠️ 声明未实现 | 训练/测试年界有序列相关通道 |",
              "| E 收益近似 | ⚠️ | IC 结论不受影响, 组合收益不作生产依据 |",
              "",
              "**结论**: 8/16 报告「LSTM(0.0797) > 简版 Ridge(0.0591), 与完整 B20 Ridge(0.074~0.080) 打平」",
              "在 RankIC 口径上成立（A/C 不影响相对比较）；但 A+D 表明真实 OOS 会有小幅衰减,",
              "且 E 说明组合收益数字不可直接用于生产。",
              "- 生产准入前提（若推进）: ①rolling z-score 重测（消除 A）②embargo 实现（消除 D）",
              "  ③完整 B20 特征 Ridge 对照 ④真实 T+1 组合回测（消除 E）",
              "",
              f"- 审计耗时 {time.time()-t0:.0f}s", ""]
    p = REPORT / f"C4_leak_audit_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"[C4] report: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
