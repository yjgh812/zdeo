#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market_rules_config — A股回测制度参数唯一配置（2026-11-07 修复包②）。

单一配置源：所有回测引擎读同一份制度参数，杜绝“两个引擎两套参数”。
含日期分段：
  涨跌停幅度 = 板块 × 日期（创业板 2020-08-24 起 20%、此前 10%；科创板开市即 20%；
             北交所/精选层 30% 不分段；ST 5% 由调用方按代码覆盖传入）
  印花税 = 日期分段（2023-08-28 减半：此前 0.1%，此后 0.05%，均仅卖出）
注：本配置只服务回测层；实盘涨跌停价以交易所每日公布值为准，不经本配置。
"""
from __future__ import annotations

# ── 日期分段锚点 ──
CHI_NEXT_REG_DATE = 20200824   # 创业板注册制改革日：存量一并适用 20% 涨跌停
STAMP_CUT_DATE = 20230828      # 印花税减半日
STAR_OPEN_DATE = 20190722      # 科创板开市日（开市前无科创板股票，无需更早分段）

# ── 印花税分段（仅卖出，降序匹配；条目=(生效日下限, 税率)）──
STAMP_SEGMENTS = (
    (STAMP_CUT_DATE, 0.0005),  # >= 20230828: 0.05%
    (0, 0.001),                # 更早: 0.1%
)

# 板块前缀 → 幅度
_PCT_30 = 0.30   # 北交所/精选层（8/4/92 开头）
_PCT_20 = 0.20
_PCT_10 = 0.10


def _norm_date(d) -> int:
    """统一日期为 int YYYYMMDD；None → 0。"""
    if d is None:
        return 0
    if isinstance(d, (int, float)):
        return int(d)
    s = str(d)
    for sep in ('-', '/', '.', ' '):
        s = s.replace(sep, '')
    head = s[:8]
    return int(head) if head.isdigit() else 0


def limit_pct(code, trade_date=None) -> float:
    """按代码前缀×日期返回涨跌停幅度。
    trade_date：YYYYMMDD int/str、'YYYY-MM-DD' 均可；None=按最新规则。"""
    digits = ''.join(ch for ch in str(code) if ch.isdigit())[:6]
    if not digits:
        return _PCT_10
    if digits.startswith('68'):
        return _PCT_20   # 科创板开市即 20%
    if digits.startswith('30'):
        td = _norm_date(trade_date)
        # td=0（未提供日期）按最新规则 20%
        return _PCT_10 if (td and td < CHI_NEXT_REG_DATE) else _PCT_20
    if digits.startswith(('8', '4', '92')):
        return _PCT_30
    return _PCT_10


def stamp_duty_rate(trade_date=None) -> float:
    """按日期返回卖出印花税率。None=按最新规则（0.05%）。"""
    if trade_date is None:
        return STAMP_SEGMENTS[0][1]
    td = _norm_date(trade_date)
    for cut, rate in STAMP_SEGMENTS:
        if td >= cut:
            return rate  # 段为降序，首个命中即返回
    return STAMP_SEGMENTS[-1][1]


# ── 执行摩擦系数（2026-11-07 重演对账实测，259 笔平仓，带符号均值）──
# 买入追价 = 纸面成交价高于当日开盘 +0.69pp；出场偏移 = 纸面出场价低于当日开盘 -1.13pp。
# 出场含盘中止损机制；恒定系数只校正系统性偏置——个股级残余中位 ≈0.99pp（见重演对账报告），
# 即摩擦不是常数：回测注入本系数校正期望值，逐笔噪声靠组合分散消化或分钟级精化消除。
EXECUTION_FRICTION = {
    "buy_chase_pp": 0.69,      # 回测买入成交假设更贴近实际: 开盘价×(1+0.0069)
    "exit_off_open_pp": -1.13, # 回测卖出成交假设: 开盘价×(1-0.0113)
    "measured_at": "2026-11-07",
    "sample_n": 259,
    "residual_med_pp": 0.99,
}
