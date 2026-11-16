# -*- coding: utf-8 -*-
"""
emotion_radar.py — 市场情绪六维雷达 (移植自 tickflow-stock-panel)
====================================================================
来源: tickflow-stock-panel backend/app/services/market_overview_builder.py
      (radar 六维权重 + emotion_label 五档), 2026-08-15 移植适配屠龙系统。

六维 (0-100, 线性归一后按权重合成):
  指数 index     : 平均指数涨幅          [-2.5, +2.5]%
  赚钱 profit    : 上涨占比*0.45 + 平均涨幅*0.25 + 中位涨幅*0.20 + 强弱差*0.10
  量能 money     : 平均量比*0.70 + 高量比占比*0.30
  投机 speculation: 涨停数*0.25 + 封板率*0.35 + 最高连板*0.25 + 2板以上家数*0.15
  抗跌 resilience: 100 - (下跌占比*0.55 + 强跌占比*0.45)
  主线 mainline  : 主线平均涨幅*0.65 + 主线覆盖率*0.35

总分 = 六维简单平均 → 五档: >=70强势 / >=55偏暖 / >=45震荡 / >=30偏冷 / <30冰点

单位约定 (与TSP原版差异): 本模块所有涨幅一律百分数制 (1.5 = 1.5%)。
  TSP 原版 avg/median/mainline 用小数制区间(±0.02/-0.005~0.03), 已换算为
  ±2 / -0.5~3, 语义等价; 占比/封板率/量比等无单位差异项保持原样。

与屠龙 M-6 情绪周期复合指数的关系: 本模块是"市场截面情绪", M-6 偏"周期相位",
两者互补; 建议复盘 SOP 情绪环境一节同时输出两者对照。
"""
from __future__ import annotations

from typing import Optional


def _score(value: Optional[float], low: float, high: float) -> int:
    """线性归一到 0-100 后 clamp (TSP 同款)。"""
    if value is None:
        return 50  # 缺数据 → 中性
    if high <= low:
        return 50
    return max(0, min(100, round((value - low) / (high - low) * 100)))


EMOTION_LABELS = [(70, "强势"), (55, "偏暖"), (45, "震荡"), (30, "偏冷"), (0, "冰点")]


def emotion_label(score: int) -> str:
    for threshold, label in EMOTION_LABELS:
        if score >= threshold:
            return label
    return "冰点"


def compute_radar(m: dict) -> dict:
    """六维雷达评分。

    参数 m (键均可缺省, 缺省项记 50 中性并在 missing 中列出):
      index_avg_pct      平均指数涨幅 %                     (如 0.62)
      up_pct             上涨家数占比 %                     (如 55.3)
      avg_pct            全A平均涨幅 %                      (如 0.85)
      median_pct         全A涨幅中位数 %                    (如 0.31)
      strong_diff_pct    强势股占比-弱势股占比 % (±3%强度差) (如 +6.2)
      avg_vol_ratio      全A平均量比                        (如 1.12)
      high_vol_ratio_pct 量比>=1.5 家数占比 %                (如 8.4)
      limit_up_count     涨停家数                           (如 62)
      seal_rate          封板率 % = 涨停/(涨停+炸板)         (如 63.0)
      max_boards         最高连板数                          (如 5)
      tier2_count        2板及以上家数                       (如 18)
      down_pct           下跌家数占比 %                      (如 40.1)
      strong_down_pct    跌幅>3% 家数占比 %                  (如 5.2)
      mainline_avg_pct   最强主线平均涨幅 %                  (如 2.8)
      mainline_cover_pct 主线成份上涨覆盖率 %                 (如 8.5)
    返回: {radar: {六维}, score: 总分, label: 档位, missing: [缺省键]}
    """
    g = lambda k: m.get(k)
    radar = {
        "index": _score(g("index_avg_pct"), -2.5, 2.5),
        "profit": round(
            _score(g("up_pct"), 20, 80) * 0.45
            + _score(g("avg_pct"), -2, 2) * 0.25
            + _score(g("median_pct"), -2, 2) * 0.20
            + _score(g("strong_diff_pct"), -8, 8) * 0.10
        ),
        "money": round(
            _score(g("avg_vol_ratio"), 0.6, 1.8) * 0.70
            + _score(g("high_vol_ratio_pct"), 2, 12) * 0.30
        ),
        "speculation": round(
            _score(g("limit_up_count"), 5, 90) * 0.25
            + _score(g("seal_rate"), 30, 85) * 0.35
            + _score(g("max_boards"), 1, 8) * 0.25
            + _score(g("tier2_count"), 0, 30) * 0.15
        ),
        "resilience": 100 - round(
            _score(g("down_pct"), 20, 80) * 0.55
            + _score(g("strong_down_pct"), 1, 12) * 0.45
        ),
        "mainline": round(
            _score(g("mainline_avg_pct"), -0.5, 3) * 0.65
            + _score(g("mainline_cover_pct"), 1, 12) * 0.35
        ),
    }
    score = round(sum(radar.values()) / len(radar))
    return {
        "radar": radar,
        "score": score,
        "label": emotion_label(score),
        "missing": sorted(k for k, v in m.items() if v is None),
    }


# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ok, total = 0, 0

    def check(desc, got, want):
        global ok, total
        total += 1
        if got == want:
            ok += 1
            print(f"[PASS] {desc}: got={got}")
        else:
            print(f"[FAIL] {desc}: got={got} want={want}")

    # 1. 单元: _score 线性+clamp+缺省
    check("_score 中点", _score(0.0, -2.5, 2.5), 50)
    check("_score 上界clamp", _score(99, -2.5, 2.5), 100)
    check("_score 下界clamp", _score(-99, 20, 80), 0)
    check("_score 缺省中性", _score(None, 20, 80), 50)

    # 2. 五档标签
    check("标签70强势", emotion_label(70), "强势")
    check("标签69偏暖", emotion_label(69), "偏暖")
    check("标签55偏暖", emotion_label(55), "偏暖")
    check("标签45震荡", emotion_label(45), "震荡")
    check("标签30偏冷", emotion_label(30), "偏冷")
    check("标签29冰点", emotion_label(29), "冰点")

    # 3. 场景A: 8/13 实际盘面近似(震荡偏弱极端结构: 涨停62/炸板率37%/最高板5)
    #    seal_rate = 62/(62+36) ≈ 63.3
    r = compute_radar({
        "index_avg_pct": -0.3, "up_pct": 35, "avg_pct": -0.5, "median_pct": -0.6,
        "strong_diff_pct": -2.0, "avg_vol_ratio": 1.15, "high_vol_ratio_pct": 9,
        "limit_up_count": 62, "seal_rate": 63.3, "max_boards": 5, "tier2_count": 15,
        "down_pct": 60, "strong_down_pct": 6, "mainline_avg_pct": 2.5, "mainline_cover_pct": 7,
    })
    print("场景A(8/13震荡偏弱):", r["radar"], "总分", r["score"], r["label"])
    check("场景A 非强势", r["label"] in ("震荡", "偏冷", "偏暖"), True)
    check("场景A 投机维中等偏上(62家涨停5连板)", 55 <= r["radar"]["speculation"] <= 85, True)

    # 4. 场景B: 冰点(千股跌停式)
    r2 = compute_radar({
        "index_avg_pct": -3.0, "up_pct": 8, "avg_pct": -4.0, "median_pct": -4.2,
        "strong_diff_pct": -8, "avg_vol_ratio": 1.9, "high_vol_ratio_pct": 15,
        "limit_up_count": 6, "seal_rate": 30, "max_boards": 1, "tier2_count": 0,
        "down_pct": 90, "strong_down_pct": 20, "mainline_avg_pct": -1.0, "mainline_cover_pct": 1,
    })
    print("场景B(冰点):", r2["radar"], "总分", r2["score"], r2["label"])
    check("场景B 冰点", r2["label"] in ("冰点", "偏冷"), True)

    # 5. 场景C: 强势主升
    r3 = compute_radar({
        "index_avg_pct": 1.8, "up_pct": 85, "avg_pct": 2.2, "median_pct": 1.9,
        "strong_diff_pct": 8, "avg_vol_ratio": 1.5, "high_vol_ratio_pct": 12,
        "limit_up_count": 120, "seal_rate": 85, "max_boards": 8, "tier2_count": 30,
        "down_pct": 12, "strong_down_pct": 1, "mainline_avg_pct": 4.5, "mainline_cover_pct": 15,
    })
    print("场景C(强势):", r3["radar"], "总分", r3["score"], r3["label"])
    check("场景C 强势", r3["label"], "强势")

    # 6. 缺省键 → 中性50不崩
    r4 = compute_radar({})
    check("空输入总分中性50", r4["score"], 50)
    check("空输入标签震荡", r4["label"], "震荡")

    print(f"\n{ok}/{total} PASS" + ("" if ok == total else "  ← 有失败!"))
    raise SystemExit(0 if ok == total else 1)
