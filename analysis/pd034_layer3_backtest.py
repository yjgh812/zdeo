#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pd034_layer3_backtest.py — PD-034 layer3 立项回测：涨停家数档位 → 前向收益统计。

背景（2026-10-15 深夜主人「继续」令=批准立项推进）：择时档位自 8 月起为观测层，
"弱档禁买"始终未接执行闸（layer3 未激活）。本件用 qfq 库全史回填档位序列并
统计各档前向收益，为 layer3 是否值得上线提供历史证据。零交易权：只出统计，
不产生任何交易动作。

口径（写死，禁止跑完再调）：
  - 档位计数 = 全市场日「收盘涨幅≥9.5%」家数（PD-034 复核定谳 20261011 口径；
    创业板 20cm 涨停日亦≥9.5% 会计入，与原口径一致不加戏，如实注记）
  - 宇宙 = qfq 库全 A，剔 688/689(科创)/8xx/4xx/9xx(北交/老三板)；ST 无法 PIT 判
    名单，不剔（涨跌停计数双向影响小，注记局限）
  - 档位边界（与 timing_state 台账标签对齐）：弱<40 / 中档40-59 / 强60-79 / 超强≥80
  - 前向收益 = 全市场等权日收益（AVG(change_pct%)），T 档位 → T+1/T+5/T+20 收盘累计
  - 样本窗 = 20190101 起

判据（预注册，输出后不得回头改判据）：
  G1: 弱档 fwd5 中位 ≤ 全样本 fwd5 中位 − 0.5pp（弱档显著更差 → 支撑"弱档禁开仓"）
  G2: 超强档 fwd5 中位 ≥ 全样本 fwd5 中位（高位延续或至少不差 → 支撑档位单调性）
  G3: 弱档/超强档样本各 ≥ 100 日
  三门全过 → 呈请 layer3 上线裁决；任一不过 → 如实归档不再提（同一元规则先例）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
START = "20190101"
OUT = r"D:\zcodesj\reports\pd034_layer3_backtest_20261015.md"

SQL_ZT = """
SELECT date, SUM(CASE WHEN change_pct >= 9.5 THEN 1 ELSE 0 END) AS zt,
       COUNT(*) AS n, AVG(change_pct) AS ew
FROM stock_daily_meta_qfq
WHERE date >= ? AND LENGTH(code) = 6
  AND code NOT LIKE '688%' AND code NOT LIKE '689%'
  AND code NOT LIKE '8%' AND code NOT LIKE '4%' AND code NOT LIKE '9%'
GROUP BY date ORDER BY date
"""


def gear_of(zt: int) -> str:
    if zt < 40:
        return "弱"
    if zt < 60:
        return "中档"
    if zt < 80:
        return "强"
    return "超强"


def main() -> int:
    conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    rows = conn.execute(SQL_ZT, (START,)).fetchall()
    conn.close()
    if len(rows) < 300:
        print(f"❌ 样本不足: {len(rows)} 日", file=sys.stderr)
        return 2

    dates = [r[0] for r in rows]
    zts = [r[1] for r in rows]
    ew = [r[3] for r in rows]
    idx = {d: i for i, d in enumerate(dates)}

    def fwd(i: int, n: int) -> float | None:
        """T=i 收盘起 n 交易日等权累计收益%（逐日复利）。"""
        j = i + n
        if j >= len(dates):
            return None
        cum = 1.0
        for k in range(i + 1, j + 1):
            cum *= (1 + ew[k] / 100.0)
        return (cum - 1) * 100.0

    buckets: dict[str, dict] = {}
    all_f5 = []
    for i, d in enumerate(dates):
        g = gear_of(zts[i])
        b = buckets.setdefault(g, {"n": 0, "f1": [], "f5": [], "f20": []})
        b["n"] += 1
        for n, key in ((1, "f1"), (5, "f5"), (20, "f20")):
            v = fwd(i, n)
            if v is not None:
                b[key].append(v)
                if n == 5:
                    all_f5.append(v)

    def med(x):
        s = sorted(x)
        m = len(s)
        return None if m == 0 else s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2

    def mean(x):
        return None if not x else sum(x) / len(x)

    def win(x):
        return None if not x else sum(1 for v in x if v > 0) / len(x) * 100

    med_all_f5 = med(all_f5)
    lines = [
        "# PD-034 layer3 立项回测：涨停家数档位 → 前向收益（2026-10-15）",
        "",
        f"- 样本窗: {dates[0]}~{dates[-1]}（{len(dates)} 交易日）｜口径: qfq 自算收盘涨幅≥9.5%计数（PD-034 复核 20261011）",
        f"- 全样本 fwd5 中位: {med_all_f5:.3f}%｜注记: 创业板20cm计入/ST未PIT剔除",
        "",
        "| 档位 | 日数 | fwd1均% | fwd5均% | fwd5中位% | fwd5胜率% | fwd20中位% |",
        "|---|---|---|---|---|---|---|",
    ]
    med_f5_by_gear = {}
    for g in ("弱", "中档", "强", "超强"):
        b = buckets.get(g, {"n": 0, "f1": [], "f5": [], "f20": []})
        med_f5_by_gear[g] = med(b["f5"])
        lines.append(
            f"| {g}(<40/40-59/60-79/≥80) | {b['n']} | {mean(b['f1']):.3f} | "
            f"{mean(b['f5']):.3f} | {med(b['f5']):.3f} | {win(b['f5']):.1f} | {med(b['f20']):.3f} |")

    g1 = med_f5_by_gear.get("弱") is not None and med_all_f5 is not None and \
        med_f5_by_gear["弱"] <= med_all_f5 - 0.5
    g2 = med_f5_by_gear.get("超强") is not None and med_all_f5 is not None and \
        med_f5_by_gear["超强"] >= med_all_f5
    g3 = buckets.get("弱", {"n": 0})["n"] >= 100 and buckets.get("超强", {"n": 0})["n"] >= 100
    lines += ["", "## 预注册三门判读", "",
              f"- G1 弱档fwd5中位≤全样本−0.5pp: {med_f5_by_gear.get('弱')} vs {med_all_f5:.3f} → {'PASS' if g1 else 'FAIL'}",
              f"- G2 超强档fwd5中位≥全样本: {med_f5_by_gear.get('超强')} vs {med_all_f5:.3f} → {'PASS' if g2 else 'FAIL'}",
              f"- G3 弱/超强样本各≥100: {buckets.get('弱', {}).get('n')}/{buckets.get('超强', {}).get('n')} → {'PASS' if g3 else 'FAIL'}",
              "",
              f"**结论: {'三门全过 → 呈请 layer3 上线裁决（弱档禁开仓接闸）' if (g1 and g2 and g3) else '未全过 → 归档不再提（元规则先例）'}**",
              "",
              f"*生成: {datetime.now().isoformat(timespec='seconds')}｜零交易权统计件*"]

    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
