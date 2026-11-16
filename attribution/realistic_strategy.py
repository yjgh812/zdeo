#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易成本与流动性修正——对Q3策略的现实化调整

修正项：
  1. 手续费：双边0.1%（印花税0.05%卖出+佣金0.025%双边）= 约0.1%/轮
  2. 滑点：T+1策略开盘买入有竞价滑点，估计0.2%
  3. 流动性约束：限制每日持仓数（5/10/20只），而非全买Top10%(~500只)
  4. 成交约束：开盘涨停的股票买不到，需剔除

目标：给出扣除全部成本后的真实可交易年化收益估计。
"""
import os
import struct
import math
import json
from collections import defaultdict
from datetime import datetime
from scipy import stats as sp_stats

import sys
sys.path.insert(0, r'D:\zcodesj\attribution')
from validation_gate import precheck_data, require_dsr, verdict_label

QLIB = r'D:\workbuddyspace\qlib_data\qlib_bin'
OUT = r'D:\zcodesj\attribution\realistic_strategy.json'

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

    calendars = load_calendar()
    features_dir = os.path.join(QLIB, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])

    print(f"加载{len(all_stocks)}只...")
    stock_data = {}
    for i, stock in enumerate(all_stocks):
        closes = load_field_safe(stock, 'close')
        opens = load_field_safe(stock, 'open')
        highs = load_field_safe(stock, 'high')
        if closes and opens and len(closes) > 60:
            stock_data[stock] = {'close': closes, 'open': opens, 'high': highs}
        if (i+1) % 2000 == 0: print(f"  {i+1}/{len(all_stocks)}")
    print(f"成功: {len(stock_data)}只")

    start_idx = next(i for i, d in enumerate(calendars) if d >= '2020-01-01')
    end_idx = len(calendars) - 1

    # 预计算
    print(f"\n预计算...")
    daily = {}

    for t in range(start_idx + 21, end_idx):
        date_str = calendars[t]
        records = []
        prev_zt = 0

        for stock, data in stock_data.items():
            closes = data['close']
            opens = data['open']
            highs = data['high']
            if t >= len(closes) or t < 21:
                continue

            # t-1涨停数
            if t >= 2 and closes[t-2] > 0 and closes[t-1] > 0:
                if closes[t-1]/closes[t-2]-1 >= get_zt_threshold(stock):
                    prev_zt += 1

            # t-1因子
            def mom(days):
                s = t - days
                if s < 0 or closes[s] <= 0 or closes[t-1] <= 0: return None
                return closes[t-1] / closes[s] - 1

            mom5 = mom(5)
            if mom5 is None:
                continue

            def resvol(days):
                window = closes[max(0,t-days):t]
                rets = [window[j]/window[j-1]-1 for j in range(1,len(window)) if window[j-1]>0 and window[j]>0]
                if len(rets) < 5: return None
                m = sum(rets)/len(rets)
                v = sum((r-m)**2 for r in rets)/len(rets)
                return math.sqrt(v) if v > 0 else 0

            rv20 = resvol(20)
            if rv20 is None:
                continue

            # t日开盘/收盘/最高
            today_open = opens[t]
            today_close = closes[t]
            today_high = highs[t] if t < len(highs) else today_open

            if today_open <= 0 or today_close <= 0:
                continue

            # 涨停价（近似：prev_close × 1.1）
            prev_close = closes[t-1]
            zt_threshold = get_zt_threshold(stock)
            limit_up_price = prev_close * (1 + zt_threshold + 0.005)  # 留余量

            # 开盘是否涨停（买不到）
            open_at_limit = today_open >= limit_up_price

            # T+1收益
            t1_ret = today_close / today_open - 1

            records.append({
                'stock': stock,
                'mom5': mom5, 'rv20': rv20,
                't1_ret': t1_ret,
                'open_at_limit': open_at_limit,  # 开盘涨停=不可买
                'today_high': today_high,
                'today_open': today_open,
                'today_close': today_close,
            })

        if len(records) >= 100:
            daily[t] = {'records': records, 'prev_zt': prev_zt, 'date': date_str}

        if (t - start_idx) % 400 == 0:
            print(f"  {date_str}")

    print(f"有效交易日: {len(daily)}")

    # ============================================================
    # 现实化策略测试
    # ============================================================
    COST_PER_TRADE = 0.003  # 0.3%双边成本（手续费+滑点）
    SLIPPAGE_OPEN = 0.001   # 额外竞价滑点0.1%

    print(f"\n{'='*70}")
    print(f"现实化策略（成本={COST_PER_TRADE*100:.1f}%/轮 + 滑点{SLIPPAGE_OPEN*100:.1f}%）")
    print(f"{'='*70}")

    # 测试多种持仓数限制
    for max_holdings in [5, 10, 20, 50, 'all']:
        strategy_rets = []
        trade_days = 0

        for t in daily:
            records = daily[t]['records']

            # 因子筛选：mom5>0 + rv20 Top10%（按当日排序）
            rvs = sorted([r['rv20'] for r in records])
            rv90 = rvs[int(len(rvs)*0.9)] if len(rvs) > 10 else 999

            candidates = [r for r in records if r['mom5'] > 0 and r['rv20'] > rv90]

            # 流动性修正：剔除开盘涨停（买不到）
            candidates = [r for r in candidates if not r['open_at_limit']]

            if not candidates:
                continue

            # 限制持仓数：取rv20最高的N只
            if max_holdings != 'all':
                candidates = sorted(candidates, key=lambda x: x['rv20'], reverse=True)[:max_holdings]

            trade_days += 1
            # 等权组合收益 - 成本
            gross_ret = sum(r['t1_ret'] for r in candidates) / len(candidates)
            net_ret = gross_ret - COST_PER_TRADE - SLIPPAGE_OPEN
            strategy_rets.append(net_ret)

        if not strategy_rets:
            continue

        n = len(strategy_rets)
        wins = [r for r in strategy_rets if r > 0]
        losses = [r for r in strategy_rets if r <= 0]
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0
        avg_daily = sum(strategy_rets) / n
        win_rate = len(wins) / n * 100

        # 年化
        annual = ((1 + avg_daily) ** 252 - 1) * 100
        simple_annual = avg_daily * 252 * 100

        dsr = require_dsr(strategy_rets, n_trials=10)
        label, _ = verdict_label(dsr['dsr'], direction='positive')

        hold_label = f'{max_holdings}只' if max_holdings != 'all' else '全部(~500只)'
        print(f"\n  每日持仓{hold_label}:")
        print(f"    交易日={trade_days}, N={n}, 胜率={win_rate:.1f}%, PF={pf:.2f}")
        print(f"    日均净收益={avg_daily*100:+.3f}%")
        print(f"    复利年化={annual:+.1f}%, 简单年化={simple_annual:+.1f}%")
        print(f"    DSR={dsr['dsr']:.2f} → {label}")

    # ============================================================
    # Q2补充：regime过滤在成本修正后的效果
    # ============================================================
    print(f"\n{'='*70}")
    print("regime过滤策略（成本修正后）")
    print(f"{'='*70}")

    for regime_filter, regime_label in [(50, 't-1涨停>50交易'), (0, '不过滤')]:
        for max_hold in [10, 20]:
            rets = []
            for t in daily:
                if daily[t]['prev_zt'] < regime_filter:
                    continue

                records = daily[t]['records']
                rvs = sorted([r['rv20'] for r in records])
                rv90 = rvs[int(len(rvs)*0.9)] if len(rvs) > 10 else 999

                candidates = [r for r in records if r['mom5'] > 0 and r['rv20'] > rv90
                              and not r['open_at_limit']]
                candidates = sorted(candidates, key=lambda x: x['rv20'], reverse=True)[:max_hold]

                if candidates:
                    gross = sum(r['t1_ret'] for r in candidates) / len(candidates)
                    rets.append(gross - COST_PER_TRADE - SLIPPAGE_OPEN)

            if rets:
                n = len(rets)
                avg = sum(rets) / n
                annual = ((1 + avg) ** 252 - 1) * 100
                pf = abs(sum(r for r in rets if r > 0) / sum(r for r in rets if r <= 0)) if any(r <= 0 for r in rets) else 0
                print(f"  {regime_label} + 每日{max_hold}只: N={n}, 日均={avg*100:+.3f}%, PF={pf:.2f}, 年化={annual:+.1f}%")

    # 输出
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'cost_per_trade_pct': COST_PER_TRADE * 100,
            'slippage_pct': SLIPPAGE_OPEN * 100,
            'note': '含成本+滑点+流动性约束(剔除开盘涨停)+持仓数限制',
        },
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] {OUT}")

if __name__ == '__main__':
    main()
