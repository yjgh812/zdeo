# -*- coding: utf-8 -*-
"""K线形态计算模块(主人令「选股为什么没用上K线」=补缺失维度) 20261029 — 可复用模块。
从qfq库的open/high/low/close计算K线形态特征:
  1. 实体比例 = |close-open| / (high-low)  → 饱满度(越接近1越实)
  2. 上影线 = (high - max(open,close)) / (high-low)  → 上影线占比
  3. 下影线 = (min(open,close) - low) / (high-low)  → 下影线占比
  4. 阳线/阴线 = close > open
  5. 大阳线 = 阳线 + 实体比例>0.6 + 涨幅>2%
  6. 放量阳线 = 大阳线 + 量比>=1.5
  7. 上影线过长 = 上影线 > 实体的50% → 预警信号
  8. 十字星 = 实体比例 < 0.1
用法:
  作为模块导入: from analysis.kline_pattern_module import calc_kline_features
  或独立运行: python analysis/kline_pattern_module.py --code 000559 [--date 20261028]
输出: dict(K线特征) — 供 batch_battle_plan.py 等选股方法消费
"""
import argparse
import json
import sqlite3
import sys

import numpy as np
import pandas as pd


def calc_kline_features(open_, high, low, close, prev_close=None):
    """计算单根K线的形态特征. 输入为单日数据.
    返回: dict(实体比例/上影线/下影线/阳阴线/振幅/形态标签)
    """
    hl_range = high - low
    if hl_range <= 0:
        hl_range = 0.001  # 防除零
    body = abs(close - open_)
    upper_shadow = high - max(open_, close)
    lower_shadow = min(open_, close) - low
    is_yang = close > open_  # 阳线

    body_ratio = body / hl_range
    upper_ratio = upper_shadow / hl_range
    lower_ratio = lower_shadow / hl_range

    # 形态标签
    if body_ratio < 0.1:
        pattern = "十字星"
    elif is_yang and body_ratio > 0.6:
        pattern = "大阳线"
    elif is_yang:
        pattern = "阳线"
    elif body_ratio > 0.6:
        pattern = "大阴线"
    else:
        pattern = "阴线"

    # 上影线过长预警
    upper_warn = upper_ratio > body_ratio * 0.5 and upper_ratio > 0.2

    return {
        "body_ratio": round(body_ratio, 3),
        "upper_ratio": round(upper_ratio, 3),
        "lower_ratio": round(lower_ratio, 3),
        "is_yang": is_yang,
        "pattern": pattern,
        "upper_warn": upper_warn,
        "hl_range": round(hl_range, 3),
    }


def calc_volume_pattern(volume, prev_vol5):
    """量能特征: 量比+量能分级"""
    vol_ratio = volume / prev_vol5 if prev_vol5 > 0 else 0
    if vol_ratio >= 3.0:
        vol_tag = "巨量"
    elif vol_ratio >= 1.5:
        vol_tag = "放量"
    elif vol_ratio >= 0.8:
        vol_tag = "平量"
    else:
        vol_tag = "缩量"
    return {"vol_ratio": round(vol_ratio, 2), "vol_tag": vol_tag}


def scan_stock_kline(code, asof=None):
    """扫描单只股票最近5日的K线形态特征(从qfq库)."""
    con = sqlite3.connect(f"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    if asof:
        df = pd.read_sql_query(
            f"SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
            f"WHERE code LIKE '%{code[-6:]}' AND date<='{asof}' ORDER BY date DESC LIMIT 10", con)
    else:
        df = pd.read_sql_query(
            f"SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
            f"WHERE code LIKE '%{code[-6:]}' ORDER BY date DESC LIMIT 10", con)
    con.close()
    if len(df) < 5:
        return None
    df = df.sort_values("date", ascending=False).reset_index(drop=True)
    latest = df.iloc[0]
    prev_vol5 = float(df.iloc[1:6]["volume"].mean())

    kl = calc_kline_features(latest["open"], latest["high"], latest["low"], latest["close"])
    vp = calc_volume_pattern(latest["volume"], prev_vol5)
    chg1 = (latest["close"] / df.iloc[1]["close"] - 1) * 100 if len(df) > 1 else 0

    return {
        "code": code, "date": int(latest["date"]),
        "open": round(float(latest["open"]), 2), "high": round(float(latest["high"]), 2),
        "low": round(float(latest["low"]), 2), "close": round(float(latest["close"]), 2),
        "chg1": round(chg1, 2),
        **kl, **vp,
    }


def batch_scan(codes, asof=None):
    """批量扫描多只股票."""
    results = []
    for code in codes:
        r = scan_stock_kline(code, asof)
        if r:
            results.append(r)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", help="单只股票代码(6位)")
    ap.add_argument("--date", help="基准日期YYYYMMDD")
    ap.add_argument("--codes", help="多只股票代码(逗号分隔)")
    args = ap.parse_args()

    if args.code:
        r = scan_stock_kline(args.code, args.date)
        if r:
            print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.codes:
        results = batch_scan(args.codes.split(","), args.date)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("用法: python analysis/kline_pattern_module.py --code 000559 [--date 20261028]")
