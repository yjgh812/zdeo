#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tulong_chip_snapshot.py — 批量筹码快照（研究工具，接复盘/盯盘）
================================================================
输入: 股票列表(逗号分隔) 或 读 reports/ml_ridge_top20_latest.csv 的Top20
输出: 控制台表格 + reports/chip_snapshot_<date>.csv / .md
用法:
  python tulong_chip_snapshot.py 600148,000717,600917
  python tulong_chip_snapshot.py --from-ml            # ML清单Top20
  python tulong_chip_snapshot.py --codes-file xxx.txt # 每行一个代码
定位: 盘口研究(套牢盘压力/成本支撑/集中度), 非交易信号——IC检验27月不显著(见MYHHUB_STOCK_ASSESS)
"""
import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import require_name  # noqa: E402
from tulong_chip_distribution import load_kdata, CYQCalculator  # noqa: E402

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
OUT_DIR = r"D:\zcodesj\reports"
ML_CSV = os.path.join(OUT_DIR, "ml_ridge_top20_latest.csv")
CHIP_DAYS = 210


def latest_date() -> int:
    con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=30)
    try:
        return con.execute("SELECT MAX(date) FROM stock_daily_meta").fetchone()[0]
    finally:
        con.close()


def chip_of(code: str, date: int) -> dict | None:
    kd = load_kdata(code, date, CHIP_DAYS)
    if len(kd) < 30:
        return None
    cal = CYQCalculator(kd, cyq_days=CHIP_DAYS)
    return cal.calc(len(kd) - 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="批量筹码快照")
    ap.add_argument("codes", nargs="*", help="股票代码(空格分隔)或逗号分隔")
    ap.add_argument("--from-ml", action="store_true", help="读ML清单Top20")
    ap.add_argument("--codes-file", default="", help="文本文件每行一代码")
    ap.add_argument("--date", type=int, default=0)
    a = ap.parse_args()

    codes: list[str] = []
    for c in a.codes:
        codes += [x.strip() for x in c.replace("，", ",").split(",") if x.strip()]
    if a.from_ml and os.path.exists(ML_CSV):
        with open(ML_CSV, encoding="utf-8-sig") as f:
            codes += [r["code"] for r in csv.DictReader(f)]
    if a.codes_file and os.path.exists(a.codes_file):
        with open(a.codes_file, encoding="utf-8") as f:
            codes += [ln.strip() for ln in f if ln.strip()]
    codes = list(dict.fromkeys(codes))
    if not codes:
        print("[拒绝] 无股票代码输入")
        return 1

    date = a.date or latest_date()
    rows = []
    for code in codes:
        r = chip_of(code, date)
        if r is None:
            print(f"  [skip] {code} 数据不足")
            continue
        p90 = r["percent_90"]
        pos = ("获利盘" if r["benefit_part"] > 0.6 else
               "套牢盘" if r["benefit_part"] < 0.4 else "均衡")
        rows.append({"code": code, "name": require_name(code), "close": r["current_price"],
                     "benefit": f"{r['benefit_part']:.0%}", "avg_cost": r["avg_cost"],
                     "pos": pos, "conc90": f"{p90['concentration']:.3f}",
                     "range90": f"[{p90['price_range'][0]},{p90['price_range'][1]}]"})

    if not rows:
        print("[拒绝] 全部计算失败")
        return 1

    print(f"\n=== 筹码快照 {date} ({len(rows)}只) ===")
    print(f"{'代码':<8}{'名称':<10}{'现价':>7}{'获利':>6}{'均成本':>8}{'状态':<5}{'集中度':>7}  90%成本区间")
    for r in rows:
        print(f"{r['code']:<8}{r['name']:<10}{r['close']:>7}{r['benefit']:>6}{r['avg_cost']:>8}"
              f"{r['pos']:<5}{r['conc90']:>7}  {r['range90']}")

    os.makedirs(OUT_DIR, exist_ok=True)
    md = os.path.join(OUT_DIR, f"chip_snapshot_{date}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(f"# 筹码快照 {date}（{len(rows)}只，210日成本分布）\n\n")
        f.write("> 研究工具: 套牢盘压力/成本支撑/集中度判断；非交易信号(因子IC 27月不显著)\n\n")
        f.write("| 代码 | 名称 | 现价 | 获利比例 | 平均成本 | 状态 | 集中度 | 90%成本区间 |\n")
        f.write("|---|---|---:|---:|---:|:-:|---:|---|\n")
        for r in rows:
            f.write(f"| {r['code']} | {r['name']} | {r['close']} | {r['benefit']} | "
                    f"{r['avg_cost']} | {r['pos']} | {r['conc90']} | {r['range90']} |\n")
        f.write(f"\n生成: {datetime.now().isoformat(timespec='seconds')}\n")
    print(f"\n[输出] {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
