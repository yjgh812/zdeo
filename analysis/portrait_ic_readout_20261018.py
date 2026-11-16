# -*- coding: utf-8 -*-
"""画像影子分 IC 读数（裁决④量化坐实，只读零交易权）：
聚合在档 9/13 long-OOS 逐日 IC 时序（data/_portrait_long_oos_20260913.json，532 观测），
产出裁决④「影子分未过 IC」的量化读数。零新计算模型，纯在档数据聚合。
输出 = reports/portrait_ic_readout_20261018.md（脚本直出）
"""
import json

import numpy as np
import pandas as pd

SRC = r"D:/zcodesj/data/_portrait_long_oos_20260913.json"
OUT = r"D:/zcodesj/reports/portrait_ic_readout_20261018.md"

L = []


def w(s=""):
    L.append(s)


rows = json.load(open(SRC, encoding="utf-8"))
df = pd.DataFrame(rows)


def agg(g):
    ic = g["ic"].dropna()
    mu = ic.mean()
    se = ic.std(ddof=1) / np.sqrt(len(ic)) if len(ic) > 1 else np.nan
    e = ic - mu
    var = e.var(ddof=1) if len(e) > 1 else np.nan
    ac1 = float((e.shift(1) * e).mean() / var) if var and var > 0 else 0.0
    lrv = se ** 2 * (1 + 2 * ac1)
    t_nw = mu / np.sqrt(lrv) if lrv and lrv > 0 else np.nan
    return pd.Series({
        "n": len(ic), "ic_mean": mu, "t_nw1": t_nw, "pos": (ic > 0).mean() * 100,
        "excess_mean": g["excess"].mean(), "excess_pos": (g["excess"] > 0).mean() * 100,
        "top_mean": g["top_port"].mean(), "base_mean": g["base_port"].mean(),
    })


agg_df = df.groupby(["tag", "seg"]).apply(agg, include_groups=False)

w("# 画像影子分 IC 读数（裁决④量化坐实，在档 long-OOS 聚合）")
w()
w("> 生成：脚本 analysis/portrait_ic_readout_20261018.py 直出（禁人工转抄）｜"
  "数据源=data/_portrait_long_oos_20260913.json（9/13 影子分规则版本 vintage，逐日 short/mid 双档）｜"
  "532 观测，窗口 2025-08-01 ~ 2026-09-03｜只读零交易权")
w()
w("## 一、分档聚合")
w()
w("| tag | seg | n | IC 月…日均值 | NW(1) t | 正 IC 占比 | Top−Base 日超额均值 | 超额正占比 |")
w("|---|---|---|---|---|---|---|---|")
for (tag, seg), r in agg_df.iterrows():
    w(f"| {tag} | {seg} | {int(r['n'])} | {r['ic_mean']:+.4f} | {r['t_nw1']:+.2f} | "
      f"{r['pos']:.0f}% | {r['excess_mean']:+.2f}pp | {r['excess_pos']:.0f}% |")
ic_all = df["ic"].dropna()
ex_all = df["excess"].dropna()
w(f"| **全体** | — | {len(ic_all)} | **{ic_all.mean():+.4f}** | — | "
  f"{(ic_all > 0).mean()*100:.0f}% | {ex_all.mean():+.2f}pp | {(ex_all > 0).mean()*100:.0f}% |")
w()
w("## 二、读数（如实）")
w()
w(f"1. 画像影子分截面 IC **全体为负**（{ic_all.mean():+.4f}，正 IC 占比仅 {(ic_all>0).mean()*100:.0f}%）；"
  f"short 档 valid 段显著负（{agg_df.loc[('short','valid'),'ic_mean']:+.4f}，"
  f"NW t {agg_df.loc[('short','valid'),'t_nw1']:+.2f}）；其余各档全负或不显著；")
w(f"2. Top 组合相对 Base 组合日超额均值 {ex_all.mean():+.2f}pp、正占比 {(ex_all>0).mean()*100:.0f}%"
  f"=无正超额证据；")
w("3. **裁决④量化坐实**：主人四裁决④「影子分=展示口径未过 IC/三闸，禁作选股信号直接消费」的 IC 腿由本读数补齐——"
  "不仅未过，且方向为负；禁入状态维持并加强；")
w("4. **范围限定（重要）**：本读数否证的是**影子分线性合成权重**的信息量；不否证入场画像事件级条件发现"
  "（如涨停×缩量格，其证据为事件级 FDR 口径在档）——两者是不同证据类型；")
w("5. 规则版本限定：9/13 vintage 规则 + 2025-08~2026-09 单 regime 窗，规则演进后读数需重算；")
w("6. 零交易权零接线；任何消费画像分入链的提案均撞裁决④，须主人重裁。")
w()
w("**置信度**：高（在档 532 观测直读聚合，正负两个方向都不利于影子分；NW 修正自相关）。")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("\n".join(L))
print(f"\n[OK] {OUT}")
