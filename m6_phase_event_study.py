#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m6_phase_event_study.py — 情绪相位消费判据事件研究（M6，2026-09-29）。

背景：emotion_phase_v2 台账 419 交易日相位标签至今无生产消费端（sentiment_cycle v1
已按主人 9/20 裁决停用，替代判据悬空）。本件=预注册式事件研究，检验相位标签对
**次日**市场结构是否有判别力；产出仅供 PD/方向层参考位裁决，不自动接线。

🔴 预注册判据（跑前 LOCKED，不做多维扫描防挖后显著）：
  H1(方向)  ebbing(退潮) 相位的次日全市场等权收益 < healing(修复) 相位的次日收益。
            判据=方向正确 且 Mann-Whitney 单侧 p<0.05 且 分年(2025/2026)方向一致。
  H2(结构)  ebbing 的次日炸板率 br > healing 的次日 br（同判据形式）。
  其余相位（euphoric/diverging/bottom）只做描述性统计呈报，不进入判据（防挑报）。
  附加呈报（非判据）：各相位次日涨停数/最高板/涨跌家数比，供裁决参考。

数据源（全本地只读）：
  相位   data/emotion_phase_v2_ledger.jsonl（phase_date, lu, br, mb, phase）
  次日收益 fundamental? no → meta.daily_market_stats.avg_change_pct（全市场平均涨跌幅,
         小数口径; 勘误注记 9/29: 该表 9/28 行 avg_change_pct=-0.0224 对应 -2.24%）
用法: python m6_phase_event_study.py
输出: reports/phase_event_study_<run>.md + stdout 判据行
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime

ROOT = r"D:\zcodesj"
LEDGER = os.path.join(ROOT, "data", "emotion_phase_v2_ledger.jsonl")
META_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"


def mw_u_one_sided_p(a: list[float], b: list[float]) -> tuple[float, float]:
    """Mann-Whitney U（单侧，备择=a 中位 < b 中位）。返回 (U, p_normal_approx)。"""
    n1, n2 = len(a), len(b)
    if n1 < 8 or n2 < 8:
        return float("nan"), float("nan")
    allv = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks = {}
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    r1 = sum(ranks[k] for k, (v, g) in enumerate(allv) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    z = (u1 - mu) / sigma if sigma else float("nan")
    p = 0.5 * math.erfc(z / math.sqrt(2))  # 单侧 P(U <= u1)（备择 a<b）
    return u1, p


def main() -> int:
    rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
    rows.sort(key=lambda r: str(r["phase_date"]))

    mcon = sqlite3.connect(META_DB, uri=True)
    # 主判据收益源=上证 000001 日收益(1990起全史干净); 2026-09-29 勘误:
    # daily_market_stats.avg_change_pct 仅 20260309 起 141 日且为百分数口径+含极端脏值,
    # 降级为附加呈报(清洗 |x|>11% 剔除)。
    sh_rows = mcon.execute(
        "SELECT date, close FROM index_daily_meta WHERE code='000001' ORDER BY date").fetchall()
    sh_ret = {}
    for j in range(1, len(sh_rows)):
        d, c = str(sh_rows[j][0]), float(sh_rows[j][1] or 0)
        pc = float(sh_rows[j - 1][1] or 0)
        if c > 0 and pc > 0:
            sh_ret[d] = c / pc - 1
    ew_ret = {}
    for d, v in mcon.execute("SELECT date, avg_change_pct FROM daily_market_stats"):
        try:
            x = float(v) / 100.0
        except (TypeError, ValueError):
            continue
        if abs(x) <= 0.11:
            ew_ret[str(d)] = x
    mcon.close()

    samples: dict[str, dict[str, list[float]]] = {}
    for i, r in enumerate(rows[:-1]):
        nxt = rows[i + 1]
        if str(nxt["phase_date"]) == str(r["phase_date"]):  # 同日重复才跳过; 台账行序=交易日序
            continue
        ph = str(r["phase"])
        ret = sh_ret.get(str(nxt["phase_date"]))
        if ret is None:
            continue
        d = samples.setdefault(ph, {"ret": [], "ew": [], "br": [], "lu": [], "mb": []})
        d["ret"].append(ret)
        ew = ew_ret.get(str(nxt["phase_date"]))
        if ew is not None:
            d["ew"].append(ew)
        if isinstance(nxt.get("br"), (int, float)):
            d["br"].append(float(nxt["br"]))
        if isinstance(nxt.get("lu"), (int, float)):
            d["lu"].append(float(nxt["lu"]))
        if isinstance(nxt.get("mb"), (int, float)):
            d["mb"].append(float(nxt["mb"]))

    def desc(v: list[float], scale: float = 100) -> str:
        if not v:
            return "—"
        s = sorted(v)
        win = sum(1 for x in v if x > 0)
        return f"均值{sum(v) / len(v) * scale:+.2f} 中位{s[len(s) // 2] * scale:+.2f} 胜率{win / len(v) * 100:.0f}% n={len(v)}"

    lines = [f"# 情绪相位→次日结构 事件研究（{datetime.now().strftime('%Y-%m-%d %H:%M')}）", "",
             "- 预注册判据跑前 LOCKED：H1=ebbing次日收益<healing 且 MW单侧p<0.05 且分年同向；"
             "H2=ebbing次日炸板率>healing。其余相位仅描述性呈报，不做多维扫描。", "",
             "## 各相位次日结构（描述性）", "",
             "| 相位 | 次日上证收益(主判据) | 次日等权收益(附加,覆盖期2026/3起) | 次日炸板率 | 次日涨停数 | 次日最高板 |",
             "|---|---|---|---|---|---|"]
    for ph in ("bottom", "healing", "euphoric", "diverging", "ebbing"):
        d = samples.get(ph, {})
        lines.append(f"| {ph} | {desc(d.get('ret', []))} | {desc(d.get('ew', []))} "
                     f"| {desc(d.get('br', []))} "
                     f"| {desc(d.get('lu', []), 1)} | {desc(d.get('mb', []), 1)} |")

    verdict = {}
    for tag, key, bigger_is in (("H1方向", "ret", False), ("H2结构(炸板率)", "br", True)):
        a = samples.get("ebbing", {}).get(key, [])
        b = samples.get("healing", {}).get(key, [])
        u, p = mw_u_one_sided_p(a, b)
        ma = sum(a) / len(a) if a else float("nan")
        mb_ = sum(b) / len(b) if b else float("nan")
        dir_ok = (ma < mb_) if not bigger_is else (ma > mb_)
        lines += ["", f"## {tag} 检验", "",
                  f"- ebbing vs healing：均值 {ma:+.4f} vs {mb_:+.4f}（方向{'正确' if dir_ok else '错误'}）"
                  f"｜MW-U={u:.0f} 单侧p={p:.4f}（样本不足=nan）"]
        if math.isnan(p):
            verdict[tag] = "FAIL(样本不足)"
        else:
            verdict[tag] = ("PASS" if (dir_ok and p < 0.05) else "FAIL")
            lines.append(f"- 判据：{'✅ PASS' if verdict[tag] == 'PASS' else '❌ FAIL'}"
                         "（方向 ∧ p<0.05）")

    # 分年稳健性（呈报项，非判据门槛；不一致则结论降置信度）
    lines += ["", "## 分年稳健性（呈报项，主判据收益口径）", "", "| 年 | ebbing次日上证收益 | healing次日上证收益 |", "|---|---|---|"]
    for yr in ("2025", "2026"):
        a = [sh_ret[str(rows[i + 1]["phase_date"])] for i, r in enumerate(rows[:-1])
             if str(r["phase"]) == "ebbing" and str(r["phase_date"]).startswith(yr)
             and str(rows[i + 1]["phase_date"]) != str(r["phase_date"])
             and str(rows[i + 1]["phase_date"]) in sh_ret]
        b = [sh_ret[str(rows[i + 1]["phase_date"])] for i, r in enumerate(rows[:-1])
             if str(r["phase"]) == "healing" and str(r["phase_date"]).startswith(yr)
             and str(rows[i + 1]["phase_date"]) != str(r["phase_date"])
             and str(rows[i + 1]["phase_date"]) in sh_ret]
        lines.append(f"| {yr} | {desc(a)} | {desc(b)} |")

    lines += ["", "## 结论", "",
              f"- H1(方向判别): **{verdict['H1方向']}** ｜ H2(结构判别): **{verdict['H2结构(炸板率)']}**",
              "- PASS→可提请方向层参考位 prereg（零交易权，呈报主人裁决）；FAIL→相位标签降级为复盘描述性维度。",
              "- 🔴 本事件研究为全史描述+两预注册判据，无后验挑选；引用须附本行声明。"]
    out = os.path.join(ROOT, "reports", f"phase_event_study_{datetime.now().strftime('%Y%m%d')}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[M6] H1={verdict['H1方向']} H2={verdict['H2结构(炸板率)']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
