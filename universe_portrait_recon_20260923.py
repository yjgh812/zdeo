#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""侦察级：宇宙资格池 × 画像高分 × tier1.0x 合取前向收益（2026-09-23，v2 加稳健性）

缘起：依顿电子(603328)在宇宙白名单躺9日(9/14~9/23)期间 13.5→涨停15.20，
但白名单只作 WARN 对照不发单。本脚本检验"若给资格池配轻量触发通道"是否有alpha，
仅侦察（非 prereg、非生产），样本起点受画像标注日期限制(2026-09-10 起)。

口径：
  信号日 D = 白名单 CSV 日期（盘前由 T-1 数据生成）
  入场 = D+1 开盘（次日开盘可执行口径，与 house 惯例一致）
  出场 = 入场日起 +5/+10/+20 交易日收盘
  剔除：D+1 停牌（无开盘）；D+1 开盘涨停(主板>=9.7%/创板>=19.5% 一字买不进)
  去重：每只票每臂只取首次出现（避免同一票连续多日重复计数）
臂：
  A  假设臂   source=universe & tier=1.0x & pa_short_pct>=95
  B3 同期对照 9/10 起（标注期）tier=1.0x & 画像<95/缺（与 A 同期，排除批次混杂）
稳健性：
  S1 阈值敏感性 pct>=90/95/98（边际是否单调）
  S2 A 臂按主线标注拆分（note 含 主线[..]/次主线[..] vs 不含）——原假设第三条件
  S3 chg20 高位动量占比（R32 一致性：chg20>30% 曾证 n=15 全负，若 A 臂堆积高位票=冲突）
输出：reports/universe_portrait_recon_20260923.md + stdout 摘要
"""
import bisect
import csv
import glob
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"D:\zcodesj"
WL_DIR = os.path.join(BASE, "reports")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
DATA_END = "20260922"  # qfq 库最新完整截面
OUT_MD = os.path.join(WL_DIR, "universe_portrait_recon_20260923.md")
MAINLINE_RE = re.compile(r"(次)?主线\[")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_whitelist():
    rows = []
    for p in sorted(glob.glob(os.path.join(WL_DIR, "buy_whitelist_*.csv"))):
        date = os.path.basename(p)[len("buy_whitelist_"):-4]
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("source") != "universe":
                    continue
                note = r.get("note") or ""
                m = MAINLINE_RE.search(note)
                rows.append({
                    "date": date, "code": (r.get("code") or "")[-6:],
                    "name": r.get("name") or "", "tier": (r.get("tier") or "").strip(),
                    "pct": _f(r.get("pa_short_pct")), "note": note,
                    "mainline": m.group(0) if m else "",
                })
    return rows


def load_qfq():
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=30)
    bars = defaultdict(list)  # code -> [(date, open, close)] 升序
    for code, d, o, c in con.execute(
            "select code,date,open,close from stock_daily_meta_qfq where date<=? order by code,date",
            (DATA_END,)):
        c6 = code[-6:]
        if c6.isdigit() and o and c:
            bars[c6].append((str(d), float(o), float(c)))
    con.close()
    return bars


def simulate(bars, sig_date, code, holds=(5, 10, 20)):
    seq = bars.get(code)
    if not seq:
        return None
    dates = [b[0] for b in seq]
    i = bisect.bisect_left(dates, sig_date)
    if i >= len(seq) or i + 1 >= len(seq):
        return None
    prev_close, open1 = seq[i][2], seq[i + 1][1]
    if not open1 or not prev_close:
        return None
    lim = 0.195 if code.startswith(("300", "301")) else 0.097
    if open1 / prev_close - 1 >= lim:
        return None  # 次日开盘涨停买不进
    j = i + 1
    out = {}
    for h in holds:
        k = j + h
        out[h] = (seq[k][2] / open1 - 1) if (k < len(seq) and seq[k][2]) else None
    return out


def chg20(bars, sig_date, code):
    """R32 口径：T-1 收盘 / 20 交易日前收盘 - 1（数据不足返回 None）。"""
    seq = bars.get(code)
    if not seq:
        return None
    dates = [b[0] for b in seq]
    i = bisect.bisect_left(dates, sig_date)
    if i < 21:
        return None
    base = seq[i - 21][2] if seq[i - 21][2] else None
    prev = seq[i - 1][2]
    if not base or not prev or base <= 0:
        return None
    return prev / base - 1


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    s = sorted(vals)
    n = len(vals)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"n": n, "mean": sum(vals) / n, "med": med, "win": sum(1 for v in vals if v > 0) / n}


def collect(rows, bars, cond, holds=(5, 10, 20)):
    seen, out = set(), []
    for r in rows:
        if cond(r) and r["code"] not in seen:
            seen.add(r["code"])
            sim = simulate(bars, r["date"], r["code"], holds)
            if sim is not None:
                out.append({**r, **sim})
    return out


def fmt_row(key, lst):
    st5, st10 = stats([x[5] for x in lst]), stats([x[10] for x in lst])

    def f(s):
        return f"{s['mean']*100:+.2f}/{s['med']*100:+.2f}/{s['win']*100:.0f}%" if s else "-"
    return f"| {key} | {st5['n'] if st5 else 0} | {f(st5)} | {f(st10)} |"


def main():
    rows = load_whitelist()
    bars = load_qfq()
    A = collect(rows, bars, lambda r: r["tier"] == "1.0x" and r["pct"] is not None and r["pct"] >= 95)
    B3 = collect(rows, bars, lambda r: r["tier"] == "1.0x" and (r["pct"] is None or r["pct"] < 95) and r["date"] >= "20260910")
    A90 = collect(rows, bars, lambda r: r["tier"] == "1.0x" and r["pct"] is not None and r["pct"] >= 90 and r["date"] >= "20260910")
    A98 = collect(rows, bars, lambda r: r["tier"] == "1.0x" and r["pct"] is not None and r["pct"] >= 98 and r["date"] >= "20260910")
    A_ml = [x for x in A if x["mainline"]]
    A_noml = [x for x in A if not x["mainline"]]

    def s5(lst):
        return stats([x[5] for x in lst])

    a5, b5 = s5(A), s5(B3)
    a_vals = [x[5] for x in A if x[5] is not None]
    b_vals = [x[5] for x in B3 if x[5] is not None]
    va = sum((v - a5["mean"]) ** 2 for v in a_vals) / (len(a_vals) - 1)
    vb = sum((v - b5["mean"]) ** 2 for v in b_vals) / (len(b_vals) - 1)
    t = (a5["mean"] - b5["mean"]) / math.sqrt(va / len(a_vals) + vb / len(b_vals))

    def f5(lst):
        s = s5(lst)
        return f"{s['mean']*100:+.2f}%/{s['win']*100:.0f}% (n={s['n']})" if s else "-"

    chgA = [chg20(bars, x["date"], x["code"]) for x in A]
    chgB = [chg20(bars, x["date"], x["code"]) for x in B3]
    hiA = sum(1 for v in chgA if v is not None and v > 0.30)
    knA = sum(1 for v in chgA if v is not None)
    hiB = sum(1 for v in chgB if v is not None and v > 0.30)
    knB = sum(1 for v in chgB if v is not None)
    mean_chgA = (sum(v for v in chgA if v is not None) / knA) if knA else None
    mean_chgB = (sum(v for v in chgB if v is not None) / knB) if knB else None

    lines = [
        "# 宇宙资格池×画像高分×tier1.0x 合取侦察 v2（2026-09-23）", "",
        "- 白名单: buy_whitelist_20260813~20260923，仅 source=universe（9/8 起）；画像标注 9/10 起",
        "- 入场=信号日D+1开盘（剔除一字板/停牌），出场=D+5/10收盘；每票每臂首现去重；qfq 截至 20260922", "",
        "## 主表", "",
        "| 臂 | n | +5d 均/中位/胜率 | +10d 均/中位/胜率 |", "|---|---|---|---|",
        fmt_row("A 假设(pct>=95,tier1.0x)", A),
        fmt_row("B3 同期对照(9/10起,画像<95/缺)", B3),
        fmt_row("C 参考(universe全部)", collect(rows, bars, lambda r: True)),
        "", f"- A vs B3: diff={((a5['mean']-b5['mean'])*100):+.2f}pp, Welch t={t:.2f}（naive，截面重叠高估）", "",
        "## S1 阈值敏感性（同期，tier1.0x）", "",
        "| 阈值 | +5d 均/胜率 |", "|---|---|",
        f"| pct>=90 | {f5(A90)} |", f"| pct>=95 | {f5(A)} |", f"| pct>=98 | {f5(A98)} |",
        f"| 对照<95/缺 | {f5(B3)} |", "",
        "## S2 A臂按主线标注拆分（原假设第三条件）", "",
        "| 子臂 | +5d 均/胜率 |", "|---|---|",
        f"| A∩主线/次主线标注 | {f5(A_ml)} |", f"| A 无主线标注 | {f5(A_noml)} |", "",
        "## S3 chg20 高位动量占比（R32 一致性检查）", "",
        f"- A 臂: chg20>30% 占 {hiA}/{knA}（可判样本），chg20 均值 {mean_chgA*100:+.1f}%",
        f"- B3 臂: chg20>30% 占 {hiB}/{knB}，chg20 均值 {mean_chgB*100:+.1f}%",
        f"- R32 在库证据: chg20>30% 组 n=15 全负（白名单层剔除）——若 A 臂高位占比显著高，需标注冲突", "",
        "## A臂明细", "", "| 日期 | 代码 | 名称 | 主线标注 | +5d | +10d |", "|---|---|---|---|---|---|",
    ]
    for x in sorted(A, key=lambda x: x["date"]):
        f5v = f"{x[5]*100:+.2f}%" if x[5] is not None else "-"
        f10 = f"{x[10]*100:+.2f}%" if x[10] is not None else "-"
        lines.append(f"| {x['date']} | {x['code']} | {x['name']} | {x['mainline'] or '-'} | {f5v} | {f10} |")
    lines += ["", "## 声明", "",
              "- 侦察级（非 prereg/非生产）：仅 +5d 可评估（3 个信号日），单一两周窗口，多重挖掘；",
              "- B 对照臂(全期)被 9/8~9/9 无标注批次主导，同期结论以 B3 为准；",
              "- +10d/+20d 随数据自然成熟；画像 A/B 前向首验排期 2026-09-30（portrait_ab_track）。",
              ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[:34]))
    print(f"[done] -> {OUT_MD}")


if __name__ == "__main__":
    main()
