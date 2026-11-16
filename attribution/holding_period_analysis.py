#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屠龙系统持仓周期成本-收益分析

核心问题：
  T+1策略双边成本0.8%/轮太高，因子alpha仅0.26%/日无法覆盖。
  但如果持仓N天，成本被摊薄为0.8%/N天=0.8/N %/天。
  需要回答：持仓多少天时，日均成本降到因子alpha(0.26%/日)可覆盖的水平？

同时：
  1. A股不同持仓周期的成本/收益盈亏平衡线
  2. 屠龙系统当前持仓周期的合理性评估
  3. 最优持仓周期的理论推导
"""
import os
import struct
import math
import json
from collections import defaultdict
from datetime import datetime

import sys
sys.path.insert(0, r'D:\zcodesj\attribution')
from validation_gate import precheck_data, require_dsr

QLIB = r'D:\workbuddyspace\qlib_data\qlib_bin'
OUT = r'D:\zcodesj\attribution\holding_period_analysis.json'

def load_calendar():
    with open(os.path.join(QLIB, 'calendars', 'day.txt'), 'r') as f:
        return [l.strip() for l in f if l.strip()]

def load_field_safe(stock, field):
    fp = os.path.join(QLIB, 'features', stock, f'{field}.day.bin')
    if not os.path.exists(fp): return None
    with open(fp, 'rb') as f:
        n = os.path.getsize(fp) // 4
        raw = list(struct.unpack(f'{n}f', f.read()))
    return [v if (math.isfinite(v) and v > 0) else 0.0 for v in raw]

def is_excluded(stock):
    code = stock[2:]
    return code.startswith('688') or code.startswith('8') or code.startswith('4')

def get_zt_threshold(stock):
    code = stock[2:]
    return 0.195 if (code.startswith('300') or code.startswith('301')) else 0.095

def main():
    print("=== V1 数据预检 ===")
    check = precheck_data('qlib_bin')
    assert check['verdict'] == 'PASS'

    # ============================================================
    # Part 1: 理论成本摊薄分析
    # ============================================================
    print(f"\n{'='*70}")
    print("Part 1: 持仓周期成本摊薄分析")
    print(f"{'='*70}")

    COST_ROUND = 0.008  # 双边0.8%
    FACTOR_ALPHA = 0.00262  # 因子1日alpha=0.262%

    print(f"\n假设：双边成本={COST_ROUND*100}%/轮, 因子alpha={FACTOR_ALPHA*100}%/日")
    print(f"\n{'持仓天数':>8s} | {'成本/轮':>8s} | {'日均成本':>8s} | {'日均alpha':>8s} | {'日均净alpha':>10s} | {'盈亏平衡':>8s}")
    print("-" * 70)

    breakeven_hold = None
    for hold_days in range(1, 21):
        daily_cost = COST_ROUND / hold_days
        daily_alpha = FACTOR_ALPHA  # 假设alpha每天持续（保守）
        net_alpha = daily_alpha - daily_cost
        is_breakeven = net_alpha > 0
        if is_breakeven and breakeven_hold is None:
            breakeven_hold = hold_days

        flag = '✅' if is_breakeven else '❌'
        print(f"{hold_days:8d} | {COST_ROUND*100:7.2f}% | {daily_cost*100:7.3f}% | {daily_alpha*100:7.3f}% | {net_alpha*100:+9.4f}% | {flag}")

    if breakeven_hold:
        print(f"\n→ 盈亏平衡持仓天数: ≥{breakeven_hold}日")
    else:
        print(f"\n→ 即使持仓20日，日均成本仍>alpha，无法盈亏平衡")

    # ============================================================
    # Part 2: 不同持仓周期的实际收益（全市场基线，无因子筛选）
    # 用qlib bin验证"持有N天"的真实日均收益
    # ============================================================
    print(f"\n{'='*70}")
    print("Part 2: 全市场不同持仓周期实际收益")
    print(f"{'='*70}")

    calendars = load_calendar()
    features_dir = os.path.join(QLIB, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])

    # 抽样1000只（减少计算量）
    import random
    random.seed(42)
    sample = random.sample(all_stocks, min(1000, len(all_stocks)))

    print(f"加载{len(sample)}只样本...")
    stock_data = {}
    for stock in sample:
        closes = load_field_safe(stock, 'close')
        opens = load_field_safe(stock, 'open')
        if closes and opens and len(closes) > 60:
            stock_data[stock] = {'close': closes, 'open': opens}
    print(f"成功: {len(stock_data)}只")

    start_idx = next(i for i, d in enumerate(calendars) if d >= '2020-01-01')
    end_idx = len(calendars) - 1

    # 对每个持仓周期，计算"随机买入持有N天"的日均收益
    hold_periods = [1, 2, 3, 5, 10, 15, 20]

    print(f"\n{'持仓':>6s} | {'N样本':>8s} | {'毛均收/轮':>10s} | {'日均毛收':>10s} | {'扣成本后':>10s} | {'日均净收':>10s} | {'年化净':>8s}")
    print("-" * 85)

    period_results = {}

    for hold in hold_periods:
        rets = []
        for stock, data in stock_data.items():
            closes = data['close']
            opens = data['open']

            # 每隔hold天采样一次（非重叠）
            for t in range(start_idx, min(end_idx - hold, len(closes) - hold), hold):
                if t + hold - 1 >= len(closes):
                    break
                entry = opens[t]
                exit_p = closes[t + hold - 1]

                if entry <= 0 or exit_p <= 0:
                    continue
                if closes[t-1] > 0 and abs(entry / closes[t-1] - 1) > 0.20:
                    continue  # 排除异常跳空

                r = exit_p / entry - 1
                if abs(r) > 0.50:
                    continue  # 排除极端值

                rets.append(r)

        if not rets:
            continue

        n = len(rets)
        avg_gross = sum(rets) / n  # 每轮毛收益
        daily_gross = avg_gross / hold  # 日均毛收益
        net_per_round = avg_gross - COST_ROUND  # 扣成本后每轮
        daily_net = net_per_round / hold  # 日均净收益

        # 年化（按换手频率）
        trades_per_year = 252 // hold
        annual_net = ((1 + net_per_round) ** trades_per_year - 1) * 100 if net_per_round > -1 else -100

        flag = '✅' if daily_net > 0 else '❌'
        print(f"{hold:5d}日 | {n:8d} | {avg_gross*100:+9.3f}% | {daily_gross*100:+9.4f}% | {net_per_round*100:+9.3f}% | {daily_net*100:+9.4f}% | {annual_net:+7.1f}% {flag}")

        period_results[hold] = {
            'n': n,
            'gross_per_round_pct': round(avg_gross * 100, 3),
            'daily_gross_pct': round(daily_gross * 100, 4),
            'net_per_round_pct': round(net_per_round * 100, 3),
            'daily_net_pct': round(daily_net * 100, 4),
            'annual_net_pct': round(annual_net, 1),
        }

    # ============================================================
    # Part 3: AB候选的不同持仓周期收益（屠龙系统实际路径）
    # ============================================================
    print(f"\n{'='*70}")
    print("Part 3: AB候选(前日涨停)不同持仓周期收益")
    print(f"{'='*70}")

    ab_period_results = {}

    for hold in hold_periods:
        rets = []
        for stock, data in stock_data.items():
            closes = data['close']
            opens = data['open']
            threshold = get_zt_threshold(stock)

            for t in range(start_idx + 1, min(end_idx - hold, len(closes) - hold)):
                # 前日涨停
                if t < 2 or closes[t-2] <= 0 or closes[t-1] <= 0:
                    continue
                if t + hold - 1 >= len(closes):
                    break
                prev_pct = closes[t-1] / closes[t-2] - 1
                if prev_pct < threshold:
                    continue

                entry = opens[t]
                exit_p = closes[t + hold - 1]

                if entry <= 0 or exit_p <= 0:
                    continue
                if abs(entry / closes[t-1] - 1) > 0.20:
                    continue

                r = exit_p / entry - 1
                if abs(r) > 0.50:
                    continue

                rets.append(r)

        if not rets:
            continue

        n = len(rets)
        avg_gross = sum(rets) / n
        daily_gross = avg_gross / hold
        net_per_round = avg_gross - COST_ROUND
        daily_net = net_per_round / hold
        trades_per_year = 252 // hold
        annual_net = ((1 + net_per_round) ** trades_per_year - 1) * 100 if net_per_round > -1 else -100

        flag = '✅' if daily_net > 0 else '❌'
        print(f"AB持仓{hold:2d}日 | N={n:6d} | 毛收={avg_gross*100:+.3f}%/轮 | 日毛={daily_gross*100:+.4f}% | 净={net_per_round*100:+.3f}%/轮 | 年化={annual_net:+.1f}% {flag}")

        ab_period_results[hold] = {
            'n': n,
            'gross_per_round_pct': round(avg_gross * 100, 3),
            'daily_gross_pct': round(daily_gross * 100, 4),
            'net_per_round_pct': round(net_per_round * 100, 3),
            'annual_net_pct': round(annual_net, 1),
        }

    # ============================================================
    # 总结
    # ============================================================
    print(f"\n{'='*70}")
    print("总结")
    print(f"{'='*70}")

    # 找全市场最优持仓期
    best_hold = None
    best_annual = -999
    for hold, data in period_results.items():
        if data['annual_net_pct'] > best_annual:
            best_annual = data['annual_net_pct']
            best_hold = hold

    # 找AB最优持仓期
    best_ab_hold = None
    best_ab_annual = -999
    for hold, data in ab_period_results.items():
        if data['annual_net_pct'] > best_ab_annual:
            best_ab_annual = data['annual_net_pct']
            best_ab_hold = hold

    print(f"\n全市场随机买入最优持仓: {best_hold}日, 年化净={best_annual:+.1f}%")
    print(f"AB候选最优持仓: {best_ab_hold}日, 年化净={best_ab_annual:+.1f}%")

    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'cost_per_round_pct': COST_ROUND * 100,
        },
        'part1_cost_amortization': {
            'breakeven_hold_days': breakeven_hold,
            'factor_alpha_daily': FACTOR_ALPHA * 100,
        },
        'part2_market_holding': period_results,
        'part3_ab_holding': ab_period_results,
        'best_market_hold': best_hold,
        'best_ab_hold': best_ab_hold,
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] {OUT}")

if __name__ == '__main__':
    main()
