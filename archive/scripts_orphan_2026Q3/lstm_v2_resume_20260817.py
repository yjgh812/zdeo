# -*- coding: utf-8 -*-
"""
LSTM升级 V2专用续跑 — 2026-08-17晚
====================================
背景: V1已完成(5年全出,均值0.0047=FAIL); V2首跑被Kronos僵尸进程抢卡
(12:24-19:13 GPU显存被占,~16倍减速,5小时未出第一年)作废。
19:17双进程已清, GPU空lane后本脚本独占续跑V2。
V1结果从日志硬编码, 不重训(省~2h)。
"""
from __future__ import annotations

import time

import numpy as np
import torch

from lstm_upgrade_training_20260816 import (
    BASELINE_IC, CACHE, REPORT, LSTM3, expand_features, load_base, train_wf,
)

# V1实测结果(_lstm_upg.log 20260817, 12:24-14:16 GPU独占期, 数据无污染)
R1 = {2022: -0.0028, 2023: 0.0023, 2024: 0.0210, 2025: -0.0066, 2026: 0.0095}
TEST_YEARS = [2022, 2023, 2024, 2025, 2026]


def main():
    t0 = time.time()
    print("=== LSTM升级V2续跑 (GPU独占) ===", flush=True)
    data = load_base()
    dates = data["dates"]
    fwd = data["fwd"]

    print("  [V2] LSTM-128x3 (9特征增强) 续跑", flush=True)
    F9 = expand_features(data)
    m2 = LSTM3(cin=F9.shape[0], hidden=128, layers=3).to("cuda")
    r2 = train_wf(m2, F9, fwd, dates, TEST_YEARS)

    v1m = float(np.mean(list(R1.values())))
    v2m = float(np.nanmean(list(r2.values())))
    lines = [
        "# LSTM超参升级训练",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 基线(翻案版64x2×5特征): IC={BASELINE_IC:.4f}",
        "- 协议: rolling expanding归一+WF年度重训+3epochs, 完全同翻案实验",
        "- 注: V1为0817 12:24-14:16实测(GPU独占); V2为0817 19:20后GPU清空续跑",
        "",
        "| 版本 | " + " | ".join(map(str, TEST_YEARS)) + " | 均值 |",
        "|------|" + "---|" * 6,
    ]
    for name, r, m in (("V1放大128x3", R1, v1m), ("V2增强9特征", r2, v2m)):
        lines.append(f"| {name} | " + " | ".join(f"{r[y]:.4f}" for y in TEST_YEARS)
                     + f" | {m:.4f} |")
    if max(v1m, v2m) > BASELINE_IC + 0.005:
        winner = "V1放大" if v1m > v2m else "V2增强"
        lines += ["", f"**升级有效**: {winner}(IC={max(v1m, v2m):.4f})超基线{BASELINE_IC:.4f}",
                  "- 建议灰度轨道模型切换为升级版"]
        torch.save(m2.state_dict(), CACHE / "lstm_upgrade_best.pt")
    else:
        lines += ["", f"**升级无效**: 最好{max(v1m, v2m):.4f}未显著超基线{BASELINE_IC:.4f}",
                  "- 灰度轨道维持原64x2模型(简单优于复杂再验证)"]
    lines += ["", f"耗时 {(time.time() - t0) / 60:.0f}min(V2-only)", ""]
    p = REPORT / "LSTM_UPGRADE_20260817.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}\n  V1={v1m:.4f} V2={v2m:.4f} 基线={BASELINE_IC:.4f}", flush=True)


if __name__ == "__main__":
    main()
