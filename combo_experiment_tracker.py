#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
combo_experiment_tracker.py — A/B 因子试验组合每日追踪器

A/B 试验设计（2026-08-15 主人批准，周期1个月）:
  A组(影子): bp_ep Top20（不占资金，仅对比基准）
  B组(实盘): droe+roa WF-icir Top20（卫星仓50%）
  基准日: 2026-08-14 | 月度换仓: 9月初首次调仓

每日工作日收盘后跑:
  1. 拉两组最新价 → 各自等权累计收益 / 组内胜率 / 当日收益
  2. 写入 attribution.db combo_experiment 表（幂等: 同日重复跑覆盖）
  3. 输出对比表 + 相对差

用法:
    python combo_experiment_tracker.py              # 日常追踪
    python combo_experiment_tracker.py --rebase     # 重建基准（调仓日）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
ATT_DB = r"D:\zcodesj\attribution\attribution.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")

# ── 试验基准清单（2026-08-15 生成，名称已机械校验）──
# B组差异化仓位（主人8/15裁决: 趋势好/胜率高→重, 一般→标准, 测试/不确定→轻）
# 档位依据: trend_checker TA-Lib 判定（8/14收盘）+ 8/15盘前功课收益质量
#   重1.0=强趋势+收益质量确认 | 标准0.6=中性/或趋势强但估值风险(惠天热电PB13降档)
#   轻0.3=偏弱/震荡/测试股；权重和=11.0
B_GROUP = [  # (code, name, base_px, weight)
    ("002582", "好想你", 11.05, 1.0),      # 强多头ADX37.5, PE2.6/PB0.97安全垫
    ("603626", "科森科技", 16.59, 1.0),     # 多头排列ADX26.8, 反转画像最正常
    ("000430", "张家界", 6.68, 0.6),        # 多头排列, ADX21过渡
    ("000506", "招金黄金", 17.64, 0.6),     # ADX29.8趋势态, 均线缠绕
    ("000692", "惠天热电", 4.58, 0.6),      # 8/15功课降档: 趋势强但PB13+换手19%+净资产薄
    ("002795", "永和智控", 7.14, 0.6),      # ADX25.2, +DI强
    ("300492", "华图山鼎", 25.24, 0.6),     # 中性过渡
    ("600961", "株冶集团", 28.75, 0.6),     # 中性过渡+DI占优
    ("300839", "博汇股份", 10.59, 0.6),     # 中性过渡
    ("002520", "日发精机", 6.65, 0.6),      # 强多头但ADX20.4震荡边缘
    ("300444", "双杰电气", 10.67, 0.6),     # 中性过渡
    ("601069", "西部黄金", 28.00, 0.6),     # 8/15候补顶位(章源钨业已在实盘移除), ADX26.5趋势态
    ("002291", "遥望科技", 5.49, 0.6),      # ADX31.3趋势态
    ("603399", "永杉锂业", 16.73, 0.6),     # 多头排列但ADX18.1震荡
    ("002640", "跨境通", 3.31, 0.3),        # 偏弱+测试股
    ("300159", "新研股份", 2.98, 0.3),      # 中性但-DI占优
    ("300313", "天山生物", 8.94, 0.3),      # 强多头形态但测试股
    ("002379", "宏桥控股", 19.29, 0.3),     # 20日线下偏弱
    ("300773", "拉卡拉", 15.37, 0.3),       # 偏弱+震荡
    ("300511", "雪榕生物", 5.37, 0.3),      # 震荡无方向
]
A_GROUP = [  # bp_ep 影子（等权对比基准）
    ("600016", "民生银行", 3.42, 1.0), ("601186", "中国铁建", 6.05, 1.0), ("601997", "贵阳银行", 5.56, 1.0),
    ("600015", "华夏银行", 6.61, 1.0), ("601390", "中国中铁", 4.36, 1.0), ("601800", "中国交建", 5.93, 1.0),
    ("600048", "保利发展", 5.27, 1.0), ("601818", "光大银行", 3.00, 1.0), ("002936", "郑州银行", 1.76, 1.0),
    ("601668", "中国建筑", 4.40, 1.0), ("601169", "北京银行", 4.96, 1.0), ("000401", "金隅冀东", 3.56, 1.0),
    ("001227", "兰州银行", 2.12, 1.0), ("600104", "上汽集团", 10.10, 1.0), ("600000", "浦发银行", 9.10, 1.0),
    ("000656", "金科股份", 1.27, 1.0), ("002958", "青农商行", 2.93, 1.0), ("601155", "新城控股", 11.15, 1.0),
    ("600928", "西安银行", 3.49, 1.0), ("000498", "山东路桥", 4.92, 1.0),
]
BASE_DATE = 20260814


def ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS combo_experiment (
            track_date INTEGER NOT NULL,
            grp TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            base_price REAL NOT NULL,
            last_price REAL,
            ret_pct REAL,
            weight REAL DEFAULT 1.0,
            created_at TEXT,
            PRIMARY KEY (track_date, grp, code)
        )
    """)
    # 旧表补列（幂等）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(combo_experiment)")]
    if "weight" not in cols:
        conn.execute("ALTER TABLE combo_experiment ADD COLUMN weight REAL DEFAULT 1.0")
    conn.commit()


def get_latest_prices(codes: list[str]) -> dict[str, float]:
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=30)
    try:
        latest = conn.execute("SELECT MAX(date) FROM stock_daily_meta").fetchone()[0]
        ph = ",".join("?" * len(codes))
        rows = conn.execute(
            f"SELECT code, close FROM stock_daily_meta WHERE date=? AND code IN ({ph})",
            [latest, *codes]).fetchall()
    finally:
        conn.close()
    return {r[0][:6]: float(r[1]) for r in rows}, latest


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B试验组合每日追踪")
    ap.add_argument("--rebase", action="store_true", help="重建基准（调仓日更新B_GROUP后用）")
    a = ap.parse_args()

    t0 = time.time()
    all_codes = [item[0] for item in B_GROUP + A_GROUP]
    latest_prices, latest_date = get_latest_prices(all_codes)

    conn = sqlite3.connect(ATT_DB)
    ensure_table(conn)
    now = datetime.now().isoformat(timespec="seconds")

    results = {}
    for grp, group in (("B", B_GROUP), ("A", A_GROUP)):
        rets, weights, rows = [], [], []
        for item in group:
            code, name, base_px = item[0], item[1], item[2]
            w = item[3] if len(item) > 3 else 1.0
            last = latest_prices.get(code)
            ret = (last / base_px - 1) * 100 if last and base_px else None
            if ret is not None:
                rets.append(ret)
                weights.append(w)
            rows.append((int(str(latest_date)[:8]), grp, code, name, base_px, last,
                         round(ret, 4) if ret is not None else None, w, now))
        conn.executemany(
            "INSERT OR REPLACE INTO combo_experiment "
            "(track_date, grp, code, name, base_price, last_price, ret_pct, weight, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", rows)
        rets = np.array(rets) if rets else np.array([0.0])
        weights = np.array(weights) if weights else np.array([1.0])
        mean_w = float((rets * weights).sum() / weights.sum())  # B组加权/A组等权(全1)
        results[grp] = {
            "mean": mean_w, "mean_ew": float(rets.mean()),
            "win": float((rets > 0).mean()),
            "best": (float(rets.max()), group[int(rets.argmax())][1]),
            "worst": (float(rets.min()), group[int(rets.argmin())][1]),
            "n_valid": len(rets),
        }
    conn.commit()

    # 当日收益（相对上一追踪日）
    prev = pd.read_sql_query(
        "SELECT grp, AVG(ret_pct) m FROM combo_experiment "
        "WHERE track_date < ? GROUP BY grp ORDER BY track_date DESC",
        conn, params=[int(str(latest_date)[:8])])
    conn.close()

    b, a_ = results["B"], results["A"]
    print(f"\n{'=' * 66}")
    print(f"  A/B 因子试验追踪 — 数据日 {str(latest_date)[:8]}（基准 {BASE_DATE}）")
    print(f"  B组=差异化仓位(重1.0×3/标0.6×11/轻0.3×6) A组=bp_ep影子等权")
    print(f"{'=' * 66}")
    print(f"{'组':<4}{'加权累计':>10}{'等权累计':>10}{'胜率':>8}{'最佳':>16}{'最差':>16}")
    print("-" * 66)
    print(f"{'B组':<4}{b['mean']:>+9.2f}%{b['mean_ew']:>+9.2f}%{b['win']:>7.0%}"
          f"{b['best'][1] + str(round(b['best'][0], 1)) + '%':>16}"
          f"{b['worst'][1] + str(round(b['worst'][0], 1)) + '%':>16}")
    print(f"{'A组':<4}{a_['mean']:>+9.2f}%{a_['mean_ew']:>+9.2f}%{a_['win']:>7.0%}"
          f"{a_['best'][1] + str(round(a_['best'][0], 1)) + '%':>16}"
          f"{a_['worst'][1] + str(round(a_['worst'][0], 1)) + '%':>16}")
    diff = b["mean"] - a_["mean"]
    print("-" * 66)
    print(f"  B-A 相对差(加权): {diff:+.2f}pp  "
          f"{'✅ B组领先' if diff > 1e-9 else ('🤝 持平' if abs(diff) <= 1e-9 else '🔻 A组领先')}")

    # 落盘日志
    today = datetime.now().strftime("%Y%m%d")
    log = REPORT_DIR / f"COMBO_EXPERIMENT_TRACK_{today}.md"
    lines = [f"# A/B 试验追踪 {str(latest_date)[:8]}", "",
             f"- B组累计 {b['mean']:+.2f}%（胜率{b['win']:.0%}） | A组累计 {a_['mean']:+.2f}%（胜率{a_['win']:.0%}）",
             f"- B-A: {diff:+.2f}pp | 基准日 {BASE_DATE} | 耗时 {time.time()-t0:.0f}s"]
    log.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✅ 已入库 combo_experiment + {log.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())