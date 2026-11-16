#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tulong_position_correlation.py — 持仓相关性监控（借鉴 stock-trader-ai 八层风控 P2 层）
================================================================================
来源: strategy_libs/stock-trader-ai 持仓相关性监控（任意两只相关系数≥0.7=分散化警告）
——20260816 屠龙查漏: R1-R18+卖出铁律均无组合内两两相关性直接监控（行业≤4只是间接分散）
口径: qfq日收益(唯一收益源), 60交易日窗口(默认), Pearson相关
输出: 相关矩阵热区 + ≥0.70警告对 + 平均相关系数 + 分散度评级
用法:
  python tulong_position_correlation.py 600148,000717,600917        # 任意列表
  python tulong_position_correlation.py --from-ml                    # ML清单Top20
  python tulong_position_correlation.py --from-position              # 读实时持仓(westock MCP留接口,当前读本地)
"""
import argparse
import csv
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import require_name  # noqa: E402

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ML_CSV = r"D:\zcodesj\reports\ml_ridge_top20_latest.csv"
OUT_DIR = r"D:\zcodesj\reports"
WARN_TH = 0.70
WINDOW = 60


def load_returns(codes: list[str], window: int) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    try:
        q = ",".join(f"'{c}'" for c in codes)
        df = pd.read_sql_query(
            f"SELECT code, date, close FROM stock_daily_meta_qfq "
            f"WHERE code IN ({q}) AND close > 0 ORDER BY code, date", con)
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    ret = piv.pct_change(fill_method=None).tail(window)
    return ret


def main() -> int:
    ap = argparse.ArgumentParser(description="持仓相关性监控(60日qfq收益Pearson)")
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--from-ml", action="store_true")
    ap.add_argument("--window", type=int, default=WINDOW)
    ap.add_argument("--warn", type=float, default=WARN_TH)
    a = ap.parse_args()

    codes: list[str] = []
    for c in a.codes:
        codes += [x.strip() for x in c.replace("，", ",").split(",") if x.strip()]
    if a.from_ml and os.path.exists(ML_CSV):
        with open(ML_CSV, encoding="utf-8-sig") as f:
            codes += [r["code"] for r in csv.DictReader(f)]
    codes = list(dict.fromkeys(codes))
    if len(codes) < 2:
        print("[拒绝] 至少需要2只股票")
        return 1

    ret = load_returns(codes, a.window)
    valid = [c for c in codes if c in ret.columns and ret[c].notna().sum() >= a.window // 2]
    if len(valid) < 2:
        print(f"[拒绝] 有效数据不足({len(valid)}/2+)")
        return 1
    corr = ret[valid].corr()

    n = len(valid)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            r = corr.iloc[i, j]
            if not np.isnan(r):
                pairs.append((valid[i], valid[j], float(r)))
    pairs.sort(key=lambda x: -x[2])
    warn = [(c1, c2, r) for c1, c2, r in pairs if r >= a.warn]
    avg_r = float(np.mean([r for _, _, r in pairs])) if pairs else 0.0

    print(f"\n=== 持仓相关性监控（{a.window}日qfq收益, {n}只） ===")
    grade = ("🔴 分散不足" if len(warn) >= 3 or avg_r >= 0.6 else
             "🟡 偏高" if len(warn) >= 1 or avg_r >= 0.45 else "🟢 分散良好")
    print(f"平均相关系数 {avg_r:.3f} | 警告对(≥{a.warn:.2f}) {len(warn)}/{len(pairs)} | 评级: {grade}")
    if pairs:
        print("\nTop10 相关对:")
        for c1, c2, r in pairs[:10]:
            flag = " ⚠️" if r >= a.warn else ""
            print(f"  {require_name(c1)}({c1}) × {require_name(c2)}({c2})  r={r:.3f}{flag}")
    if warn:
        print(f"\n⚠️ 分散化警告: {len(warn)}对高相关——同涨同跌风险，建议检查行业/主题集中度")

    md = os.path.join(OUT_DIR, f"position_correlation_{pd.Timestamp.now():%Y%m%d}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(f"# 持仓相关性监控（{a.window}日, {n}只, 均值{avg_r:.3f}, 评级{grade}）\n\n"
                f"> 借鉴stock-trader-ai P2层; 阈值{a.warn}; 研究提示非交易信号\n\n"
                "| 股票A | 股票B | 相关系数 | 警告 |\n|---|---|---:|:-:|\n")
        for c1, c2, r in pairs:
            f.write(f"| {c1} {require_name(c1)} | {c2} {require_name(c2)} | {r:.3f} | "
                    f"{'⚠️' if r >= a.warn else ''} |\n")
    print(f"\n[输出] {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
