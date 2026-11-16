#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zt_sentiment_m6_bridge.py — 炸板率刻度 × M-6情绪周期复合指数 统一情绪卡片（对接器）
======================================================================================
只读对接：调用 zt_sentiment_calibrator.py 的最新刻度值 + attribution/emotion_index.py
（M-6）的现有输入与算法，输出统一情绪卡片。**不修改两脚本本体、不写它们的产物文件**。

输入源（均为只读）：
  A) zt_sentiment_calibrator（8/15建）
     - dragon_quant.db.stock_daily_hsj 自查炸板，price_limits 唯一口径涨停价
     - 产出：炸板率/晋级率/最高连板 + 研报刻度档位
       炸板率刻度: <8% 情绪强(亢奋) | 8-30% 健康强势 | 30-50% 分歧 | >50% 退潮
       晋级率刻度: ≥30% 强接力 | 15-30% 中性 | <15% 退潮信号
  B) M-6 attribution/emotion_index.py
     - dragon_quant_meta.db.stock_daily_meta 五维复合指数(0-100)：
       涨停活跃30% + 连板强度25% + 炸板质量20% + 跌停压力15% + 涨跌比10%
     - 阶段: 冰点/冷/常温/热/沸点 → 仓位上限 30/50/70/90/100%

日期对齐：默认取两库共同最新交易日（--date 可指定）。

退化检测（实事求是关键项）：M-6 若当日 change_pct 全空（涨停=炸板=涨跌家数全0，
例如 2026-08-14 的 meta 库 change_pct 整列缺失），自动回退到最近一个有该列的交易日，
并在卡片显式标注"数据回退"，绝不静默用坏数据冒充情绪读数。

统一规则（两套刻度并排 + 冲突取保守）：
  各自映射 5 档热度（1冰点/2冷/3常温/4热/5沸点）：
    炸板率: <8%→5  8-30%→4  30-50%→3  >50%→1
    晋级率: ≥30%→4  15-30%→3  <15%→1（晋级为接力确认维度，从保守）
    校准器综合档 = min(炸板档, 晋级档)
    M-6 档 = 冰点1/冷2/常温3/热4/沸点5
  统一档 = min(校准器档, M-6档) —— 冲突时以更保守方为准
  仓位上限沿用 M-6 阶段表: 1→≤30% 2→≤50% 3→≤70% 4→≤90% 5→≤100%
  两系统档位差 ≥2 时在卡片标注"显著冲突"提示人工复核。

用法：
  python zt_sentiment_m6_bridge.py                     # 最新共同交易日
  python zt_sentiment_m6_bridge.py --date 20260814
  python zt_sentiment_m6_bridge.py --json card.json    # 机器可读输出
  python zt_sentiment_m6_bridge.py --lookback 20260601 # 校准器序列起始(算5日均值)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
import zt_sentiment_calibrator as zsc            # noqa: E402
sys.path.insert(0, r"D:\zcodesj\attribution")
import emotion_index as m6                       # noqa: E402

HEAT_NAMES = {1: "冰点", 2: "冷", 3: "常温", 4: "热", 5: "沸点"}
POS_CAP = {1: 30, 2: 50, 3: 70, 4: 90, 5: 100}


def heat_from_broken_rate(br: float) -> int:
    if pd.isna(br):
        return 3  # 无值不给方向性，中性
    if br < 0.08:
        return 5
    if br <= 0.30:
        return 4
    if br <= 0.50:
        return 3
    return 1


def heat_from_promotion(pr: float) -> int:
    if pd.isna(pr):
        return 3
    if pr >= 0.30:
        return 4
    if pr >= 0.15:
        return 3
    return 1


def heat_from_m6(stage_en: str) -> int:
    return {"ICE": 1, "COLD": 2, "NORMAL": 3, "HOT": 4, "OVERHEAT": 5}.get(stage_en, 3)


def latest_date_meta() -> int:
    con = sqlite3.connect(f"file:{m6.DB_PATH}?mode=ro", uri=True)
    d = con.execute("SELECT MAX(date) FROM stock_daily_meta").fetchone()[0]
    con.close()
    return int(d)


def prev_dates_meta(d: int, n: int = 10) -> list[int]:
    con = sqlite3.connect(f"file:{m6.DB_PATH}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta WHERE date<? ORDER BY date DESC LIMIT ?",
        (d, n)).fetchall()
    con.close()
    return [int(r[0]) for r in rows]


def latest_date_hsj() -> int:
    con = sqlite3.connect(f"file:{zsc.MAIN_DB}?mode=ro", uri=True)
    d = con.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0]
    con.close()
    return int(d)


def m6_is_degenerate(ind: dict) -> bool:
    """change_pct 整列缺失的特征：涨停=炸板=0 且 上涨=下跌=0 且 total_valid>1000"""
    return (ind["limit_up_count"] == 0 and ind["broken_count"] == 0
            and ind["up_count"] == 0 and ind["down_count"] == 0
            and ind["total_valid"] > 1000)


def get_m6(date_want: int) -> tuple[dict, dict, int, list[int]]:
    """返回 (indicators, emotion, 实际使用日期, 回退经过的日期列表)"""
    fallback_path: list[int] = []
    d = date_want
    for _ in range(10):
        ind = m6.compute_indicators(d)
        emo = m6.compute_emotion_index(ind)
        if not m6_is_degenerate(ind):
            return ind, emo, d, fallback_path
        fallback_path.append(d)
        prevs = prev_dates_meta(d, 1)
        if not prevs:
            break
        d = prevs[0]
    return ind, emo, d, fallback_path  # type: ignore


def get_calibrator(date_want: int, lookback: int) -> tuple[pd.Series, pd.DataFrame]:
    """返回 (当日行, 日频序列截尾)。start 取 lookback 与 date_want 的较早者。"""
    start = min(lookback, date_want)
    buf = int((pd.Timestamp(str(start)) - pd.Timedelta(days=15)).strftime("%Y%m%d"))
    raw = zsc.load_hsj(buf, date_want)
    daily = zsc.compute_broken_board(raw, start)
    daily = daily[daily["n_touched"] >= 5].reset_index(drop=True)
    row = daily[daily["date"] == date_want]
    if len(row) == 0:
        # 目标日被残缺日守卫剔除 → 用序列内最新
        row = daily.tail(1)
    return row.iloc[-1], daily


def build_card(date_arg: int | None, lookback: int, json_out: str = "") -> dict:
    t0 = time.time()
    if date_arg:
        common = date_arg
    else:
        common = min(latest_date_meta(), latest_date_hsj())

    ind, emo, m6_date, fb = get_m6(common)
    cal_row, daily = get_calibrator(common, lookback)
    cal_date = int(cal_row["date"])

    br = float(cal_row["broken_rate"])
    pr = float(cal_row["promotion_rate"]) if pd.notna(cal_row["promotion_rate"]) else None
    br5 = float(daily["broken_rate"].tail(5).mean())
    pr5 = float(daily["promotion_rate"].tail(5).mean()) \
        if daily["promotion_rate"].tail(5).notna().any() else None

    h_br = heat_from_broken_rate(br)
    h_pr = heat_from_promotion(pr)
    cal_heat = min(h_br, h_pr)
    m6_heat = heat_from_m6(emo["stage_en"])
    unified = min(cal_heat, m6_heat)
    conservative_src = ("zt校准器" if cal_heat <= m6_heat else "M-6") + (
        f"（含M-6回退{fb[-1]}）" if fb else "")
    delta = abs(cal_heat - m6_heat)

    card = {
        "date_common": common,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "zt_calibrator": {
            "date": cal_date,
            "n_sealed": int(cal_row["n_sealed"]),
            "n_broken": int(cal_row["n_broken"]),
            "broken_rate": round(br, 4),
            "broken_rate_5d": round(br5, 4),
            "grade_broken": cal_row["grade_broken"],
            "promotion_rate": None if pr is None else round(pr, 4),
            "promotion_rate_5d": None if pr5 is None else round(pr5, 4),
            "grade_promotion": cal_row["grade_promotion"],
            "max_streak": int(cal_row["max_streak"]),
            "heat_broken": h_br,
            "heat_promotion": h_pr,
            "heat": cal_heat,
            "source": "zt_sentiment_calibrator (stock_daily_hsj自查, price_limits唯一口径)",
        },
        "m6": {
            "date_used": m6_date,
            "fallback_path": fb,
            "composite_index": emo["composite_index"],
            "stage": emo["stage_en"],
            "advice": emo["advice"],
            "raw": {k: v["raw"] for k, v in emo["dimensions"].items()},
            "heat": m6_heat,
            "source": "attribution/emotion_index.py (dragon_quant_meta.stock_daily_meta)",
        },
        "unified": {
            "heat": unified,
            "stage": HEAT_NAMES[unified],
            "max_position_pct": POS_CAP[unified],
            "conservative_source": conservative_src,
            "conflict": delta >= 2,
            "heat_delta": delta,
            "rule": "统一档=min(校准器档, M-6档); 校准器档=min(炸板档,晋级档)",
        },
        "load_seconds": round(time.time() - t0, 1),
    }

    if json_out:
        p = Path(json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    return card


def print_card(card: dict):
    z, m, u = card["zt_calibrator"], card["m6"], card["unified"]
    w = 78
    print("=" * w)
    print(f" 屠龙统一情绪卡片 | 共同交易日 {card['date_common']} | 生成 {card['generated_at']}")
    print("=" * w)
    print("\n[A] 炸板率刻度（zt_sentiment_calibrator，price_limits唯一口径）")
    print(f"    数据日 {z['date']} | 封板 {z['n_sealed']} 只 / 炸板 {z['n_broken']} 只")
    print(f"    炸板率 {z['broken_rate']:.1%} → {z['grade_broken']}（热度档 {z['heat_broken']}/5）"
          f" | 近5日均值 {z['broken_rate_5d']:.1%}")
    pr = "n/a" if z["promotion_rate"] is None else f"{z['promotion_rate']:.1%}"
    pr5 = "n/a" if z["promotion_rate_5d"] is None else f"{z['promotion_rate_5d']:.1%}"
    print(f"    晋级率 {pr} → {z['grade_promotion']}（热度档 {z['heat_promotion']}/5）"
          f" | 近5日均值 {pr5}")
    print(f"    最高连板 {z['max_streak']} 板 | 校准器综合档 {z['heat']}/5（min炸板档,晋级档）")

    print("\n[B] M-6 情绪周期复合指数（attribution/emotion_index.py）")
    if m["fallback_path"]:
        print(f"    [数据回退] {m['fallback_path']} 当日 change_pct 缺失（涨停/炸板/涨跌家数全0），"
              f"已回退至 {m['date_used']} —— 请修 meta 管线")
    print(f"    数据日 {m['date_used']} | 复合指数 {m['composite_index']}/100"
          f" → {m['stage']}（热度档 {m['heat']}/5）")
    r = m["raw"]
    print(f"    维度原始值: 涨停{r['limit_up_score']} 连板{r['board_score']}板 "
          f"M-6炸板率{r['break_rate_score']}% 跌停{r['limit_down_score']} "
          f"上涨占比{r['up_ratio_score']}%")
    print(f"    M-6建议: {m['advice']}")

    print("\n[C] 统一判定（冲突时以更保守方为准）")
    print(f"    校准器档 {z['heat']}/5 vs M-6档 {m['heat']}/5"
          f"{'  → 显著冲突(差≥2档, 需人工复核)' if u['conflict'] else ''}")
    print(f"    统一档位: {u['heat']}/5（{u['stage']}） | 保守方: {u['conservative_source']}")
    print(f"    仓位上限: ≤{u['max_position_pct']}%")
    print(f"    规则: {u['rule']}")
    if z["broken_rate"] is not None and r["break_rate_score"]:
        print(f"\n    [口径提示] 两套炸板率并排: 校准器 {z['broken_rate']:.1%}（精确涨停价, 含ST/20cm）"
              f" vs M-6 {r['break_rate_score']:.1f}%（change_pct≥9.5%/19.5%阈值, 剔688）——"
              f"口径不同允许差异, 以校准器为准")
    print("\n" + "=" * w)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="炸板率刻度×M-6 统一情绪卡片（只读对接器）")
    ap.add_argument("--date", type=int, default=None, help="共同交易日 yyyymmdd（默认两库最新交集）")
    ap.add_argument("--json", default="", help="卡片 JSON 输出路径")
    ap.add_argument("--lookback", type=int, default=None, help="校准器序列起始 yyyymmdd（默认当日-120天）")
    a = ap.parse_args(argv)

    lookback = a.lookback or int((pd.Timestamp.today() - pd.Timedelta(days=120))
                                 .strftime("%Y%m%d"))
    card = build_card(a.date, lookback, a.json)
    print_card(card)
    if a.json:
        print(f"[save] {a.json}")
    print(f"[done] 数据加载 {card['load_seconds']}s | M-6本体未被修改（只读import）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
