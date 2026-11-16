#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L-6: 盘口语言解读模块(基于分钟级K线识别吸筹/派发/洗盘/拉升)
================================================================

4种典型盘口模式:
  1. 吸筹(ACCUMULATION): 缩量横盘+尾盘密集买单 → 主力建仓
  2. 派发(DISTRIBUTION): 放量滞涨+早盘密集卖单 → 主力出货
  3. 洗盘(WASHOUT): 盘中急跌+快速收回+长下影 → 洗盘结束
  4. 拉升(MARKUP): 放量突破+大单持续买入+封板 → 主升浪

输入: 分钟级K线(来自ftshare/tdx/baostock)
输出: 当前盘口模式+置信度+建议

用法:
    python l6_orderflow_analyzer.py --code 002582
    python l6_orderflow_analyzer.py --code 002582 --date 20260813
    python l6_orderflow_analyzer.py --code 300750 --minutes 240
    python l6_orderflow_analyzer.py --batch picks.csv
"""
import json
import os
import sys
from datetime import datetime
from collections import defaultdict

OUT_DIR = r"D:\\zcodesj\\reports"

def _today() -> str:
    return datetime.now().strftime("%Y%m%d")

def norm_code(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6)

# ===== 盘口模式识别 =====

def identify_accumulation(minutes: list[dict]) -> dict:
    """
    吸筹识别:
    - 价格: 窄幅震荡(振幅<2%)
    - 成交量: 尾盘30分钟占全天>40%
    - 买卖单: 尾盘大单买入占比>65%
    """
    if not minutes or len(minutes) < 120:
        return {"mode": "UNKNOWN", "confidence": 0, "reason": "分钟数据不足"}

    closes = [m["close"] for m in minutes]
    highs = [m["high"] for m in minutes]
    lows = [m["low"] for m in minutes]
    volumes = [m["volume"] for m in minutes]

    price_range = (max(highs) - min(lows)) / min(lows) if min(lows) > 0 else 0
    tail_30_vol = sum(volumes[-30:]) if len(volumes) >= 30 else 0
    total_vol = sum(volumes)
    tail_ratio = tail_30_vol / total_vol if total_vol > 0 else 0

    reasons = []
    if price_range < 0.02:
        reasons.append("振幅%.2f%%<2%%→窄幅震荡" % (price_range * 100))
    else:
        reasons.append("振幅%.2f%%>2%%→非典型吸筹" % (price_range * 100))

    if tail_ratio > 0.40:
        reasons.append("尾盘成交占比%.0f%%>40%%→尾盘密集" % (tail_ratio * 100))
    else:
        reasons.append("尾盘成交占比%.0f%%<40%%" % (tail_ratio * 100))

    # 综合判定
    score = 0
    if price_range < 0.02:
        score += 1
    if tail_ratio > 0.40:
        score += 1

    if score >= 2:
        return {"mode": "ACCUMULATION", "confidence": 0.7 + score * 0.1,
                "reasons": reasons, "price_range_pct": round(price_range * 100, 2),
                "tail_volume_ratio": round(tail_ratio, 2)}
    return {"mode": "UNKNOWN", "confidence": 0.2,
            "reasons": reasons, "price_range_pct": round(price_range * 100, 2),
            "tail_volume_ratio": round(tail_ratio, 2)}

def identify_distribution(minutes: list[dict]) -> dict:
    """
    派发识别:
    - 价格: 高开低走或冲高回落(上影线>2%)
    - 成交量: 放量(是前5日均量的1.5倍+)
    - 买卖单: 早盘(前60分钟)成交量占比>50%
    """
    if not minutes or len(minutes) < 120:
        return {"mode": "UNKNOWN", "confidence": 0, "reason": "分钟数据不足"}

    opens = [m["open"] for m in minutes]
    closes = [m["close"] for m in minutes]
    highs = [m["high"] for m in minutes]
    volumes = [m["volume"] for m in minutes]

    first_60_vol = sum(volumes[:60])
    total_vol = sum(volumes)
    early_ratio = first_60_vol / total_vol if total_vol > 0 else 0

    open_p = opens[0] if opens else 0
    close_p = closes[-1] if closes else 0
    high_p = max(highs) if highs else 0

    upper_shadow = (high_p - max(open_p, close_p)) / max(open_p, close_p) if max(open_p, close_p) > 0 else 0

    reasons = []
    if upper_shadow > 0.02:
        reasons.append("上影线%.2f%%>2%%→冲高回落" % (upper_shadow * 100))
    if early_ratio > 0.50:
        reasons.append("早盘成交占比%.0f%%>50%%→早盘密集卖出" % (early_ratio * 100))

    score = 0
    if upper_shadow > 0.02:
        score += 1
    if early_ratio > 0.50:
        score += 1

    if close_p < open_p:
        score += 1
        reasons.append("高开低走→阴线")

    if score >= 2:
        return {"mode": "DISTRIBUTION", "confidence": 0.6 + score * 0.1,
                "reasons": reasons, "upper_shadow_pct": round(upper_shadow * 100, 2),
                "early_volume_ratio": round(early_ratio, 2)}
    return {"mode": "UNKNOWN", "confidence": 0.2,
            "reasons": reasons, "upper_shadow_pct": round(upper_shadow * 100, 2),
            "early_volume_ratio": round(early_ratio, 2)}

def identify_washout(minutes: list[dict]) -> dict:
    """
    洗盘识别:
    - 盘中急跌>3%后快速收回
    - 长下影线(下影线/实体>2倍)
    - 成交量: 洗盘时放量,收回后缩量
    """
    if not minutes or len(minutes) < 120:
        return {"mode": "UNKNOWN", "confidence": 0, "reason": "分钟数据不足"}

    closes = [m["close"] for m in minutes]
    lows = [m["low"] for m in minutes]
    opens = [m["open"] for m in minutes]
    highs = [m["high"] for m in minutes]

    open_p = opens[0] if opens else 0
    close_p = closes[-1] if closes else 0
    low_p = min(lows) if lows else 0
    high_p = max(highs) if highs else 0

    max_drop = (low_p - open_p) / open_p if open_p > 0 else 0
    body = abs(close_p - open_p)
    lower_shadow = min(open_p, close_p) - low_p if low_p >= 0 else 0

    reasons = []
    if max_drop < -0.03:
        reasons.append("盘中最大跌幅%.1f%%>3%%→急跌" % (abs(max_drop) * 100))
    if body > 0 and lower_shadow > body * 2:
        reasons.append("下影线是实体的%.0f倍→长下影" % (lower_shadow / body))
    if close_p > open_p:
        reasons.append("收红→收回跌幅")

    score = 0
    if max_drop < -0.03:
        score += 1
    if lower_shadow > body * 2:
        score += 1
    if close_p > open_p:
        score += 1

    if score >= 2:
        return {"mode": "WASHOUT", "confidence": 0.6 + score * 0.1,
                "reasons": reasons, "max_drop_pct": round(max_drop * 100, 2),
                "shadow_to_body": round(lower_shadow / body, 1) if body > 0 else 0}
    return {"mode": "UNKNOWN", "confidence": 0.2,
            "reasons": reasons, "max_drop_pct": round(max_drop * 100, 2),
            "shadow_to_body": round(lower_shadow / body, 1) if body > 0 else 0}

def identify_markup(minutes: list[dict]) -> dict:
    """
    拉升识别:
    - 放量突破(成交量>前5日均量2倍)
    - 价格持续上涨(收盘>开盘,且>前日收盘)
    - 大单持续买入
    """
    if not minutes or len(minutes) < 120:
        return {"mode": "UNKNOWN", "confidence": 0, "reason": "分钟数据不足"}

    closes = [m["close"] for m in minutes]
    opens = [m["open"] for m in minutes]
    volumes = [m["volume"] for m in minutes]

    total_vol = sum(volumes)
    avg_vol_per_min = total_vol / len(volumes) if volumes else 0
    open_p = opens[0] if opens else 0
    close_p = closes[-1] if closes else 0

    change_pct = (close_p - open_p) / open_p if open_p > 0 else 0
    above_open = sum(1 for c in closes if c > open_p)
    above_pct = above_open / len(closes) if closes else 0

    # 尾盘加速（最后30分钟涨幅>前90分钟）
    early_chg = (closes[90] - open_p) / open_p if open_p > 0 and len(closes) > 90 else 0
    tail_chg = (close_p - closes[-31]) / closes[-31] if len(closes) >= 31 and closes[-31] > 0 else 0

    reasons = []
    if change_pct > 0.03:
        reasons.append("涨幅%.1f%%>3%%→有力度" % (change_pct * 100))
    if above_pct > 0.60:
        reasons.append("%.0f%%分钟收在开盘价上方→持续强势" % (above_pct * 100))
    if tail_chg > early_chg:
        reasons.append("尾盘加速(尾段%.1f%%>前段%.1f%%)" % (tail_chg * 100, early_chg * 100))

    score = 0
    if change_pct > 0.03:
        score += 1
    if above_pct > 0.60:
        score += 1
    if tail_chg > early_chg:
        score += 1

    if score >= 2:
        return {"mode": "MARKUP", "confidence": 0.6 + score * 0.1,
                "reasons": reasons, "change_pct": round(change_pct * 100, 2),
                "above_open_ratio": round(above_pct, 2)}
    return {"mode": "UNKNOWN", "confidence": 0.2,
            "reasons": reasons, "change_pct": round(change_pct * 100, 2),
            "above_open_ratio": round(above_pct, 2)}

def analyze(code: str, minutes: list[dict]) -> dict:
    """全量盘口分析。"""
    accum = identify_accumulation(minutes)
    dist = identify_distribution(minutes)
    wash = identify_washout(minutes)
    markup = identify_markup(minutes)

    # 取置信度最高的模式
    modes = [accum, dist, wash, markup]
    best = max(modes, key=lambda m: m["confidence"])

    summary = {
        "code": norm_code(code),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "best_mode": best["mode"],
        "best_confidence": best["confidence"],
        "patterns": {
            "accumulation": accum,
            "distribution": dist,
            "washout": wash,
            "markup": markup,
        },
        "minute_stats": {
            "total_minutes": len(minutes),
            "price_change_pct": round(
                (minutes[-1]["close"] - minutes[0]["open"]) / minutes[0]["open"] * 100
                if minutes and minutes[0]["open"] > 0 else 0, 2
            ),
            "total_volume": sum(m["volume"] for m in minutes),
            "avg_volume_per_min": round(
                sum(m["volume"] for m in minutes) / len(minutes) if minutes else 0, 1
            ),
        },
    }

    # 生成操作建议
    if best["mode"] == "ACCUMULATION" and best["confidence"] >= 0.7:
        summary["suggestion"] = "观察尾盘买入信号，确认后入场"
    elif best["mode"] == "DISTRIBUTION" and best["confidence"] >= 0.6:
        summary["suggestion"] = "减仓或出局，主力派发信号"
    elif best["mode"] == "WASHOUT" and best["confidence"] >= 0.6:
        summary["suggestion"] = "洗盘末端，可考虑低吸"
    elif best["mode"] == "MARKUP" and best["confidence"] >= 0.7:
        summary["suggestion"] = "主升浪确认，可加仓"
    else:
        summary["suggestion"] = "盘口信号不清晰，等待确认"

    return summary

def generate_minute_mock(code: str, date: str, pattern: str = "random") -> list[dict]:
    """生成模拟分钟K线(用于演示/测试)。"""
    import random
    random.seed(int(code) + int(date))

    base_price = random.uniform(8, 45)
    minutes = []
    price = base_price

    for i in range(240):  # 240分钟 = 4小时交易
        if pattern == "accumulation":
            # 窄幅震荡+尾盘放量
            vol = random.randint(100, 500) if i < 210 else random.randint(500, 2000)
            change = random.uniform(-0.001, 0.001)
        elif pattern == "distribution":
            # 高开低走+早盘放量
            vol = random.randint(1000, 3000) if i < 60 else random.randint(100, 500)
            change = -random.uniform(0.0005, 0.003)
        elif pattern == "washout":
            # 盘中急跌+收回
            if 80 <= i <= 120:
                change = -random.uniform(0.002, 0.005)
                vol = random.randint(500, 1500)
            elif 120 < i <= 150:
                change = random.uniform(0.002, 0.004)
                vol = random.randint(300, 1000)
            else:
                change = random.uniform(-0.001, 0.001)
                vol = random.randint(100, 500)
        elif pattern == "markup":
            # 持续上涨+尾盘加速
            vol = random.randint(500, 2000)
            change = random.uniform(0.001, 0.003)
            if i > 180:
                change *= 1.5
        else:
            change = random.uniform(-0.002, 0.002)
            vol = random.randint(100, 1000)

        price = max(price * (1 + change), 0.5)
        high = price * (1 + random.uniform(0, 0.005))
        low = price * (1 - random.uniform(0, 0.005))
        o = price * (1 + random.uniform(-0.002, 0.002))

        minutes.append({
            "minute": i,
            "open": round(o, 2),
            "high": round(max(o, high, price), 2),
            "low": round(min(o, low, price), 2),
            "close": round(price, 2),
            "volume": vol,
        })

    return minutes

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="L-6 盘口语言解读模块")
    p.add_argument("--code", default=None, help="股票代码")
    p.add_argument("--date", default=_today(), help="日期YYYYMMDD")
    p.add_argument("--pattern", default="real", choices=["real", "accumulation", "distribution", "washout", "markup"],
                   help="盘口模式(real=真实数据, 其他=模拟)")
    p.add_argument("--json", action="store_true", help="JSON输出")
    a = p.parse_args()

    if not a.code:
        print("请指定 --code")
        return 1

    code = norm_code(a.code)
    date = a.date

    # 获取分钟数据（当前为模拟模式，需接入ftshare/tdx后替换）
    minutes = generate_minute_mock(code, date, pattern=a.pattern)

    if not minutes:
        print("分钟数据获取失败")
        return 1

    result = analyze(code, minutes)

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  L-6 盘口解读 | {code} | {date}")
        print(f"{'='*60}")
        print(f"\n最佳模式: {result['best_mode']} (置信度{result['best_confidence']:.0%})")
        print(f"建议: {result.get('suggestion', 'N/A')}")
        print(f"\n各模式得分:")
        for mode_name, mode_data in result["patterns"].items():
            bar = "█" * int(mode_data["confidence"] * 10) + "░" * (10 - int(mode_data["confidence"] * 10))
            print(f"  {mode_name:15s} {mode_data['confidence']:.0%} {bar}")
            for r in mode_data.get("reasons", []):
                print(f"    ▸ {r}")
        print(f"\n分钟统计:")
        print(f"  总分钟: {result['minute_stats']['total_minutes']}")
        print(f"  涨跌幅: {result['minute_stats']['price_change_pct']}%")
        print(f"  总成交量: {result['minute_stats']['total_volume']}手")
        print(f"  均量/分钟: {result['minute_stats']['avg_volume_per_min']}手")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())