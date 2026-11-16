#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tech_score_screener.py — 选股技术面评分器（TA-Lib 五维评分）

对候选股票进行五维技术评分（0-100分），叠加在主线选股器输出之上，
帮助区分同等基本面/题材下谁的技术面更优。

五维评分体系（屠龙术语标准量化版）:
  1. 趋势分 (30分): MA排列 + ADX趋势强度 + 唐奇安位置
  2. 动量分 (25分): MACD零轴位置+方向 + RSI区间
  3. 波动率分 (20分): ATR标准化 + 布林带位置
  4. 成交量分 (15分): OBV趋势 + 量比
  5. 形态分 (10分): K线反转形态识别

用法:
    python tech_score_screener.py --codes 600519,000858,300750
    python tech_score_screener.py --codes 600519 --detail    # 单票详细评分
    python tech_score_screener.py --from-screener             # 从主线选股器取候选
    python tech_score_screener.py --from-screener --top 10    # 只看前10
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import numpy as np
import talib

# ── MCP 数据源 ──
try:
    from trend_checker import get_kline
except ImportError:
    import urllib.request
    def get_kline(code, limit=90):
        URL = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
        TOKEN = "0a04c746006600da402983c037dae94424751e880d4eb6b3e334a41ae6abd95d"
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "data_kline", "arguments": {"codes": code, "ktype": "day", "limit": limit}},
        }
        req = urllib.request.Request(
            URL, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        nodes = data.get("result", {}).get("content", [{}])[0].get("text", "")
        nodes = json.loads(nodes).get("data", {}).get("nodes", [])
        return nodes[::-1]


def score_stock(code: str, name: str = "") -> dict:
    """对单只股票进行五维技术评分"""
    nodes = get_kline(code, limit=120)
    if not nodes or len(nodes) < 35:
        return {"code": code, "name": name, "error": "K线不足(需≥35日)", "total_score": 0}

    closes = np.array([n["last"] for n in nodes], dtype=float)
    highs = np.array([n["high"] for n in nodes], dtype=float)
    lows = np.array([n["low"] for n in nodes], dtype=float)
    volumes = np.array([n.get("volume", 0) or 0 for n in nodes], dtype=float)
    opens = np.array([n.get("open", n["last"]) for n in nodes], dtype=float)
    c = closes[-1]

    metrics = {}  # 各维度明细
    reasons = []  # 评分理由

    # ══════════════════════════════════════════════
    #  维度1: 趋势分 (30分)
    # ══════════════════════════════════════════════
    trend_score = 0

    # 1a. MA排列 (10分)
    ma5 = talib.SMA(closes, 5)[-1]
    ma10 = talib.SMA(closes, 10)[-1]
    ma20 = talib.SMA(closes, 20)[-1]
    ma60 = talib.SMA(closes, 60)[-1] if len(closes) >= 60 else None

    mas_ok = sum(1 for x in [ma5, ma10, ma20] if not np.isnan(x))
    if mas_ok == 3:
        if c > ma5 > ma10 > ma20:
            trend_score += 10
            reasons.append("MA多头排列(+10)")
        elif ma5 > ma10 > ma20 > c:
            trend_score -= 5
            reasons.append("MA空头排列(-5)")
        elif ma5 > c > ma20:
            trend_score += 3
            reasons.append("MA缠绕偏多(+3)")
    if ma60 is not None and not np.isnan(ma60) and c > ma60:
        trend_score += 3
        reasons.append("站上60日线(+3)")

    # 1b. ADX趋势强度 (10分)
    adx = talib.ADX(highs, lows, closes, timeperiod=14)
    pdi = talib.PLUS_DI(highs, lows, closes, timeperiod=14)
    mdi = talib.MINUS_DI(highs, lows, closes, timeperiod=14)

    if not np.isnan(adx[-1]):
        if adx[-1] > 25 and not np.isnan(pdi[-1]) and not np.isnan(mdi[-1]) and pdi[-1] > mdi[-1]:
            trend_score += 10
            reasons.append(f"上升趋势ADX={adx[-1]:.1f}(+10)")
        elif adx[-1] > 25 and pdi[-1] < mdi[-1]:
            trend_score -= 5
            reasons.append(f"下降趋势ADX={adx[-1]:.1f}(-5)")

    # 1c. 唐奇安位置 (10分)
    prior_high20 = max(highs[-21:-1]) if len(highs) > 21 else max(highs[:-1])
    prior_low20 = min(lows[-21:-1]) if len(lows) > 21 else min(lows[:-1])
    pos_donchian = (c - prior_low20) / (prior_high20 - prior_low20) * 100 if prior_high20 > prior_low20 else 50

    if pos_donchian > 80:
        trend_score += 8
        reasons.append(f"唐奇安高位{pos_donchian:.0f}%(+8)")
    elif pos_donchian > 50:
        trend_score += 4
        reasons.append(f"唐奇安中上{pos_donchian:.0f}%(+4)")
    elif pos_donchian < 20:
        trend_score -= 3
        reasons.append(f"唐奇安低位{pos_donchian:.0f}%(-3)")

    metrics["trend"] = {"score": trend_score, "ma5": round(ma5, 2), "ma10": round(ma10, 2),
                        "ma20": round(ma20, 2), "adx": round(adx[-1], 1) if not np.isnan(adx[-1]) else None,
                        "donchian_pos": round(pos_donchian, 1)}

    # ══════════════════════════════════════════════
    #  维度2: 动量分 (25分)
    # ══════════════════════════════════════════════
    momentum_score = 0

    # 2a. MACD (15分)
    macd, macd_sig, macd_hist = talib.MACD(closes, fastperiod=12, slowperiod=26, signalperiod=9)
    if not np.isnan(macd[-1]):
        if macd[-1] > 0 and macd[-1] > macd_sig[-1]:
            momentum_score += 15
            reasons.append(f"MACD多头金叉(DIF={macd[-1]:+.3f})(+15)")
        elif macd[-1] > 0:
            momentum_score += 8
            reasons.append(f"MACD零轴上方(DIF={macd[-1]:+.3f})(+8)")
        elif macd[-1] > macd_sig[-1]:
            momentum_score += 3
            reasons.append("MACD空头金叉(底背离+3)")

    # 2b. RSI (10分)
    rsi14 = talib.RSI(closes, timeperiod=14)
    if not np.isnan(rsi14[-1]):
        if 40 <= rsi14[-1] <= 60:
            momentum_score += 8
            reasons.append(f"RSI中性{rsi14[-1]:.0f}(+8)")
        elif 30 <= rsi14[-1] < 40:
            momentum_score += 5
            reasons.append(f"RSI偏弱{rsi14[-1]:.0f},超卖边缘(+5)")
        elif 60 < rsi14[-1] <= 70:
            momentum_score += 5
            reasons.append(f"RSI偏强{rsi14[-1]:.0f}(+5)")
        elif rsi14[-1] > 70:
            momentum_score += 2
            reasons.append(f"RSI超买{rsi14[-1]:.0f}(+2,注意回调)")
        elif rsi14[-1] < 30:
            momentum_score += 3
            reasons.append(f"RSI超卖{rsi14[-1]:.0f}(+3,反弹机会)")

    metrics["momentum"] = {"score": momentum_score,
                           "macd": round(macd[-1], 4) if not np.isnan(macd[-1]) else None,
                           "macd_signal": round(macd_sig[-1], 4) if not np.isnan(macd_sig[-1]) else None,
                           "rsi14": round(rsi14[-1], 1) if not np.isnan(rsi14[-1]) else None}

    # ══════════════════════════════════════════════
    #  维度3: 波动率分 (20分)
    # ══════════════════════════════════════════════
    vol_score = 0

    # 3a. ATR 标准化波幅 (10分)
    atr = talib.ATR(highs, lows, closes, timeperiod=14)
    if not np.isnan(atr[-1]) and c > 0:
        atr_pct = atr[-1] / c * 100
        if 1 <= atr_pct <= 4:
            vol_score += 10
            reasons.append(f"ATR波幅适中{atr_pct:.1f}%(+10)")
        elif atr_pct < 1:
            vol_score += 5
            reasons.append(f"ATR波幅偏低{atr_pct:.1f}%(+5)")
        elif atr_pct > 6:
            vol_score -= 3
            reasons.append(f"ATR波幅过高{atr_pct:.1f}%(-3)")

    # 3b. 布林带位置 (10分)
    upper, middle, lower = talib.BBANDS(closes, timeperiod=20, nbdevup=2, nbdevdn=2)
    if not np.isnan(upper[-1]) and not np.isnan(lower[-1]):
        bb_pos = (c - lower[-1]) / (upper[-1] - lower[-1]) * 100 if upper[-1] > lower[-1] else 50
        if 30 <= bb_pos <= 70:
            vol_score += 10
            reasons.append(f"布林带中轨区域({bb_pos:.0f}%)(+10)")
        elif 70 < bb_pos <= 90:
            vol_score += 5
            reasons.append(f"布林带偏上轨({bb_pos:.0f}%)(+5)")
        elif bb_pos > 90:
            vol_score -= 2
            reasons.append(f"布林带触上轨({bb_pos:.0f}%)(-2)")
        elif 10 <= bb_pos < 30:
            vol_score += 5
            reasons.append(f"布林带偏下轨({bb_pos:.0f}%)(+5)")
        elif bb_pos < 10:
            vol_score += 3
            reasons.append(f"布林带触下轨({bb_pos:.0f}%)(+3)")

    metrics["volatility"] = {"score": vol_score,
                             "atr_pct": round(atr[-1] / c * 100, 2) if not np.isnan(atr[-1]) else None,
                             "bb_pos": round(bb_pos, 1)}

    # ══════════════════════════════════════════════
    #  维度4: 成交量分 (15分)
    # ══════════════════════════════════════════════
    volume_score = 0

    # 4a. OBV 趋势 (8分)
    obv = talib.OBV(closes, volumes)
    if len(obv) > 5 and not np.isnan(obv[-1]) and not np.isnan(obv[-5]):
        obv_trend = obv[-1] - obv[-5]
        if obv_trend > 0:
            volume_score += 8
            reasons.append("OBV上升量能配合(+8)")
        else:
            volume_score += 2
            reasons.append("OBV走平/下降量能不足(+2)")

    # 4b. 成交量相对位置 (7分)
    if len(volumes) > 20:
        vol_ma20 = np.mean(volumes[-20:])
        if vol_ma20 > 0:
            vol_ratio = volumes[-1] / vol_ma20
            if vol_ratio > 1.5:
                volume_score += 7
                reasons.append(f"放量{vol_ratio:.1f}x(+7)")
            elif vol_ratio > 1.0:
                volume_score += 4
                reasons.append(f"量能正常{vol_ratio:.1f}x(+4)")
            else:
                volume_score += 1
                reasons.append(f"缩量{vol_ratio:.1f}x(+1)")

    metrics["volume"] = {"score": volume_score,
                         "obv": float(obv[-1]) if not np.isnan(obv[-1]) else None}

    # ══════════════════════════════════════════════
    #  维度5: 形态分 (10分)
    # ══════════════════════════════════════════════
    pattern_score = 0
    patterns_found = []

    cdl1 = talib.CDLENGULFING(opens, highs, lows, closes)
    cdl2 = talib.CDLHAMMER(opens, highs, lows, closes)
    cdl3 = talib.CDLDOJISTAR(opens, highs, lows, closes)
    cdl4 = talib.CDL3WHITESOLDIERS(opens, highs, lows, closes)
    cdl5 = talib.CDL3BLACKCROWS(opens, highs, lows, closes)
    cdl6 = talib.CDLMORNINGSTAR(opens, highs, lows, closes, penetration=0.3)
    cdl7 = talib.CDLEVENINGSTAR(opens, highs, lows, closes, penetration=0.3)
    cdl8 = talib.CDLPIERCING(opens, highs, lows, closes)
    cdl9 = talib.CDLDARKCLOUDCOVER(opens, highs, lows, closes)

    if cdl1[-1] > 0:
        pattern_score += 4; patterns_found.append("多头吞没(+4)")
    elif cdl1[-1] < 0:
        pattern_score -= 3; patterns_found.append("空头吞没(-3)")
    if cdl2[-1] != 0:
        pattern_score += 3; patterns_found.append("锤子线(+3)")
    if cdl3[-1] > 0:
        pattern_score += 2; patterns_found.append("十字星(+2)")
    if cdl4[-1] != 0:
        pattern_score += 5; patterns_found.append("三白兵(+5)")
    if cdl5[-1] != 0:
        pattern_score -= 4; patterns_found.append("三乌鸦(-4)")
    if cdl6[-1] != 0:
        pattern_score += 5; patterns_found.append("晨星(+5)")
    if cdl7[-1] != 0:
        pattern_score -= 4; patterns_found.append("暮星(-4)")
    if cdl8[-1] != 0:
        pattern_score += 3; patterns_found.append("刺透形态(+3)")
    if cdl9[-1] != 0:
        pattern_score -= 3; patterns_found.append("乌云盖顶(-3)")

    # 形态分不能超过10也不能低于-10
    pattern_score = max(-10, min(10, pattern_score))
    if patterns_found:
        reasons.extend(patterns_found)

    metrics["pattern"] = {"score": pattern_score, "patterns": patterns_found}

    # ══════════════════════════════════════════════
    #  总分计算
    # ══════════════════════════════════════════════
    total_score = max(0, min(100, trend_score + momentum_score + vol_score + volume_score + pattern_score))

    # 评分等级
    if total_score >= 80:
        grade = "S级（极优）"
    elif total_score >= 65:
        grade = "A级（优秀）"
    elif total_score >= 50:
        grade = "B级（良好）"
    elif total_score >= 35:
        grade = "C级（一般）"
    else:
        grade = "D级（差）"

    return {
        "code": code,
        "name": name,
        "close": round(c, 2),
        "total_score": total_score,
        "grade": grade,
        "dimensions": {
            "trend": {**metrics["trend"], "max": 30},
            "momentum": {**metrics["momentum"], "max": 25},
            "volatility": {**metrics["volatility"], "max": 20},
            "volume": {**metrics["volume"], "max": 15},
            "pattern": {**metrics["pattern"], "max": 10},
        },
        "reasons": reasons[:10],  # 最多10条理由
    }


def get_from_screener() -> list[tuple[str, str]]:
    """从 mainline_candidate_screener 获取候选列表"""
    try:
        import sqlite3
        db = r"D:\zcodesj\attribution\attribution.db"
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM limit_up_patterns")
        max_date = cur.fetchone()[0]
        if not max_date:
            return []
        rows = cur.execute(
            "SELECT DISTINCT code, name FROM limit_up_patterns WHERE trade_date=? "
            "AND (pattern_type IS NULL OR pattern_type NOT IN ('开板','烂板')) "
            "ORDER BY strategy_value DESC LIMIT 50",
            (max_date,)
        ).fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        print(f"⚠️ 无法从归因库取候选: {e}", file=sys.stderr)
        return []


def print_scores(results: list[dict], detail: bool = False, top: int = 0):
    """输出评分结果"""
    valid = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]

    # 按总分降序
    valid.sort(key=lambda x: -x["total_score"])

    if top > 0:
        valid = valid[:top]

    print("=" * 70)
    print(f"📊 技术面评分结果 — 共{len(valid)}只有效 | 错误{len(errors)}只")
    print("=" * 70)

    # 等级分布
    grades = {}
    for r in valid:
        g = r["grade"].split("（")[0]
        grades[g] = grades.get(g, 0) + 1
    grade_dist = ", ".join(f"{g}: {c}只" for g, c in sorted(grades.items()))
    print(f"等级分布: {grade_dist}")
    print()

    # 头部评分明细
    print(f"{'排名':<4} {'代码':<10} {'名称':<8} {'总分':<6} {'等级':<10} {'趋势':<6} {'动量':<6} {'波动':<6} {'量能':<6} {'形态':<6}")
    print("-" * 70)
    for i, r in enumerate(valid, 1):
        d = r["dimensions"]
        print(f"{i:<4} {r['code']:<10} {r['name'] or '':<8} "
              f"{r['total_score']:<6} {r['grade'].split('（')[0]:<10} "
              f"{d['trend']['score']:<6} {d['momentum']['score']:<6} "
              f"{d['volatility']['score']:<6} {d['volume']['score']:<6} {d['pattern']['score']:<6}")

    if detail:
        print("\n" + "=" * 70)
        print("📋 详细评分理由")
        print("=" * 70)
        for r in valid[:10]:
            print(f"\n{r['code']} {r['name']} — {r['grade']} ({r['total_score']}分)")
            if r.get("close"):
                print(f"  收盘: {r['close']}")
            for reason in r.get("reasons", []):
                print(f"  • {reason}")

    if errors:
        print(f"\n⚠️ 数据不足: {len(errors)}只")
        for r in errors[:5]:
            print(f"  {r['code']} {r['name']}: {r['error']}")

    print("\n" + "=" * 70)
    print("*评分器: tech_score_screener.py (TA-Lib 五维)")


def main() -> int:
    ap = argparse.ArgumentParser(description="选股技术面评分器（TA-Lib 五维评分）")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--codes", type=str, help="代码列表（逗号分隔）")
    group.add_argument("--from-screener", action="store_true", help="从主线选股器取候选")
    ap.add_argument("--detail", action="store_true", help="显示详细评分理由")
    ap.add_argument("--top", type=int, default=0, help="只显示前N只")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()

    codes = []
    if a.from_screener:
        codes = get_from_screener()
    elif a.codes:
        codes = [(c.strip(), "") for c in a.codes.split(",")]
    else:
        ap.print_help()
        return 1

    results = []
    for code, name in codes:
        r = score_stock(code, name)
        results.append(r)
        time.sleep(0.1)

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_scores(results, detail=a.detail, top=a.top)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())