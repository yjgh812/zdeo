# -*- coding: utf-8 -*-
"""v25 E1 判定执行件 (2026-10-24 冻结, 零交易权)

依据: data/PREREG_V25_2026REEVAL_DRAFT_20261024.json (status=locked_active)
  G1 = v25 2026段累计OOS > 0 (绝对转正)
判定公式(冻结, 口径反推自 2026-10-09 在案 trial C4=-0.02%):
  基数 = equity 曲线中 date < 2026-01-01 的最后一行 value (前2025末基数)
  终值 = equity 曲线中 date <= asof (默认2026-12-31) 的最后一行 value
  G1  = 终值/基数 - 1 > 0
  (口径实证: 本公式于 iter4_wfa_ml_h20 旧产物复现 -0.04%≈在案-0.02%;
   朴素首2026行基数=-0.55%≠trial口径, 已弃用——见 SOP 一百九十五)

本件只算不动库: 不写 wfa_trials(判定入账由执行者按指纹 20261024-v25-2026reeval 手工/脚本追加),
不触碰信号表。反窥探: 本件在 E1/E2 之外运行时, 输出一律标注「非判定口径·不构成G1证据」。

用法: python analysis/v25_e1_judge_20261231.py <equity_csv> [--asof 2026-12-31] [--e1]
  --e1  仅当此旗标存在且 asof≥2026-12-31 时输出才计为判定口径(E1/E2 执行时用)
"""
import argparse, sys
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ap = argparse.ArgumentParser()
ap.add_argument("equity_csv")
ap.add_argument("--asof", default="2026-12-31")
ap.add_argument("--e1", action="store_true", help="E1/E2 注册时点执行旗标")
A = ap.parse_args()

e = pd.read_csv(A.equity_csv)
for c in ("date", "value"):
    if c not in e.columns:
        sys.exit(f"FATAL: 缺列 {c} (期望 ml_signal_equity.csv 格式: date,value)")
base_row = e[e["date"] < "2026-01-01"]
if base_row.empty:
    sys.exit("FATAL: 曲线无 2026 前历史段, 无法取前2025末基数")
base = float(base_row["value"].iloc[-1])
end_row = e[e["date"] <= A.asof]
if end_row.empty:
    sys.exit(f"FATAL: 曲线无 <= {A.asof} 数据")
end = float(end_row["value"].iloc[-1])
seg = end / base - 1
is_judgment = A.e1 and A.asof >= "2026-12-31"
tag = "【判定口径·E1/E2】" if is_judgment else "【非判定口径·不构成G1证据】"
print(f"{tag} v25 2026段累计OOS = {seg*100:+.2f}%")
print(f"  基数(前2025末 {base_row['date'].iloc[-1]})={base:,.0f} | 终值({end_row['date'].iloc[-1]})={end:,.0f}")
print(f"  G1(>0): {'PASS' if seg > 0 else 'FAIL'}" + ("" if not is_judgment else " | 判定入账: wfa_trials 指纹 20261024-v25-2026reeval"))
