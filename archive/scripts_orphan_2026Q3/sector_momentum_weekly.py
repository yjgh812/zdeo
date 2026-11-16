# -*- coding: utf-8 -*-
"""行业动量周报(sector_momentum_weekly)·只读观测件·主线核对工具

数据: data/sector_momentum.db(sector_momentum.py --build 全历史 20 日 RS)。
用途: 每周五/每日复盘核对"当前主线在哪"——31 申万L1 行业 20 日相对强度排名。
     P2-B 已证伪"用 RS 过滤名单"(勿复活), 本条仅作主线观测, 不进入任何选股/闸门。
输出: reports/sector_momentum_weekly_YYYYMMDD.md(可贴复盘) + json。
用法: python sector_momentum_weekly.py [--days 10]
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from datetime import datetime

BASE = r"D:\zcodesj"
MOM_DB = os.path.join(BASE, "data", "sector_momentum.db")
OUT = os.path.join(BASE, "reports")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{MOM_DB}?mode=ro", uri=True, timeout=10)
    rows = conn.execute("SELECT date, sector, rs_20 FROM sector_mom "
                        "ORDER BY date DESC LIMIT 2000").fetchall()
    conn.close()
    if not rows:
        print("[FAIL] sector_momentum.db 空(先跑 sector_momentum.py --build)")
        return 2
    # date -> {sector: rs}
    by_date = {}
    for d, s, r in rows:
        by_date.setdefault(d, {})[s] = r
    dates = sorted(by_date)
    recent = dates[-a.days:]

    lines = [f"# 行业动量周报(20日RS) · {recent[0]}~{recent[-1]}", "",
             "> 主线核对观测件:RS=行业成分等权20日收益-市场等权20日收益(pp)。仅观测, 不进入选股/闸门"
             "(P2-B 行业动量过滤已证伪禁复活, 见 P2_BC_FALSIFICATION)。", ""]

    # 最新一日排名
    last = recent[-1]
    ranked = sorted(by_date[last].items(), key=lambda kv: kv[1], reverse=True)
    lines.append(f"## 最新({last}) 31行业RS排名")
    lines.append("| 排名 | 行业 | RS(pp) | 强度 |")
    lines.append("|---|---|---|---|")
    for i, (s, v) in enumerate(ranked, 1):
        tag = "主线候选" if i <= 5 else ("弱势" if i >= 27 else "")
        lines.append(f"| {i} | {s} | {v:+.1f} | {tag} |")
    lines.append("")

    # 周演变: 每行业近N个采样日的RS(取每2个点), 或展示首末
    lines.append(f"## 周演变({len(recent)}个交易日, 首末RS)")
    evo = []
    for s in sorted(by_date[last]):
        first, lastv = by_date[recent[0]].get(s), by_date[last].get(s)
        if first is not None and lastv is not None:
            evo.append((s, first, lastv, lastv - first))
    evo.sort(key=lambda x: x[3], reverse=True)
    lines.append("| 行业 | 期初 | 期末 | 变化 |")
    lines.append("|---|---|---|---|")
    for s, f0, f1, dlt in evo:
        lines.append(f"| {s} | {f0:+.1f} | {f1:+.1f} | {dlt:+.1f} |")
    lines.append("")
    lines.append(f"## 采样日: {', '.join(recent)}")

    out_md = os.path.join(OUT, f"sector_momentum_weekly_{last}.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[:22]))
    print(f"\n产物: {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
