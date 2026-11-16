#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
R6/R1/R8 回测验证（qlib bin数据源版）
用干净的qlib bin数据替代有质量问题的meta db。

数据源: D:\workbuddyspace\qlib_data\qlib_bin
区间: 2020-01-01 ~ 2026-08-07（6.5年）

核心修正：
  前期基于meta db的回测结论不可靠（数据质量问题）
  本脚本使用qlib bin数据重新验证R6/R1/R8三条规则
"""
import os
import struct
import math
import json
from collections import defaultdict
from datetime import datetime

QLIB_PATH = r'D:\workbuddyspace\qlib_data\qlib_bin'
OUT_PATH = r'D:\zcodesj\attribution\backtest_qlib_result.json'

# ============================================================
# qlib bin数据读取
# ============================================================

def load_calendar():
    """加载日历"""
    with open(os.path.join(QLIB_PATH, 'calendars', 'day.txt'), 'r') as f:
        return [line.strip() for line in f if line.strip()]

def load_stock_field(stock, field):
    """读取单只股票的单字段bin数据"""
    filepath = os.path.join(QLIB_PATH, 'features', stock, f'{field}.day.bin')
    if not os.path.exists(filepath):
        return None
    fsize = os.path.getsize(filepath)
    n = fsize // 4
    with open(filepath, 'rb') as f:
        return list(struct.unpack(f'{n}f', f.read()))

def load_all_stocks_fields(stocks, fields):
    """
    批量加载多只股票的多字段
    返回: {stock: {field: [values]}}
    """
    data = {}
    for i, stock in enumerate(stocks):
        stock_data = {}
        for field in fields:
            vals = load_stock_field(stock, field)
            if vals is not None:
                stock_data[field] = vals
        if len(stock_data) == len(fields):
            data[stock] = stock_data
        if (i + 1) % 1000 == 0:
            print(f"  加载进度: {i+1}/{len(stocks)}")
    return data

def is_excluded(stock):
    """排除科创/北交所"""
    code = stock[2:]  # sh600000 -> 600000
    if code.startswith('688') or code.startswith('8') or code.startswith('4'):
        return True
    return False

def get_zt_threshold(stock):
    """涨停阈值"""
    code = stock[2:]
    if code.startswith('300') or code.startswith('301'):
        return 0.195
    return 0.095

# ============================================================
# 回测核心
# ============================================================

def run_backtest(calendars, all_data, start_date='2020-01-01', end_date='2026-08-07'):
    """
    R6/R1/R8 联合回测
    """
    # 日期索引映射
    date_to_idx = {d: i for i, d in enumerate(calendars)}
    start_idx = next((i for i, d in enumerate(calendars) if d >= start_date), 0)
    end_idx = next((i for i, d in enumerate(calendars) if d >= end_date), len(calendars)-1)

    print(f"回测区间: {calendars[start_idx]} ~ {calendars[end_idx]} ({end_idx-start_idx+1}天)")

    # 结果容器
    results = {
        # R6: 竞价缺口分组
        'r6': {
            'ab_4_7': [],     # AB确认 4-7%
            'low_2_4': [],    # 低开承接 2-4%
            'high_7_10': [],  # 高开过高 7-10%
        },
        # R1: 止损对比（在AB 4-7%子集上）
        'r1': {'with_stop': [], 'without_stop': []},
        # R8: 板块限定（需要板块数据，先用全市场均收代理）
        'r8': {'top5_strong': [], 'baseline': []},
    }

    # 逐日遍历
    for t in range(start_idx + 1, min(end_idx, len(calendars))):
        date_str = calendars[t]
        prev_date = calendars[t-1]

        # === Step1: 找前日涨停的股票（AB候选） ===
        ab_candidates = []
        for stock, fields in all_data.items():
            closes = fields['close']
            if t >= len(closes) or t-1 < 0:
                continue

            prev_close = closes[t-1]
            today_open = fields['open'][t] if 'open' in fields else None

            if prev_close is None or prev_close <= 0 or today_open is None or today_open <= 0:
                continue

            # 前日涨跌幅
            if t >= 2:
                prev2_close = closes[t-2]
                if prev2_close is None or prev2_close <= 0:
                    continue
                prev_pct = prev_close / prev2_close - 1
            else:
                continue

            # 前日涨停？
            threshold = get_zt_threshold(stock)
            if prev_pct < threshold:
                continue

            # 今日竞价缺口
            gap = today_open / prev_close - 1

            # 过滤异常缺口（>15%或<-10%可能是数据错误/除权）
            if gap > 0.15 or gap < -0.10:
                continue

            today_close = closes[t]
            if today_close is None or today_close <= 0:
                continue

            # T+1收益（开盘买→收盘卖）
            ret = today_close / today_open - 1

            ab_candidates.append({
                'stock': stock,
                'date': date_str,
                'gap': gap,
                'ret': ret,
                't': t,
                'buy_price': today_open,
            })

        if not ab_candidates:
            continue

        # === R6: 分组统计 ===
        for cand in ab_candidates:
            g = cand['gap']
            if 0.04 <= g <= 0.07:
                results['r6']['ab_4_7'].append(cand)
            elif 0.02 <= g < 0.04:
                results['r6']['low_2_4'].append(cand)
            elif 0.07 < g <= 0.10:
                results['r6']['high_7_10'].append(cand)

        # === R1: 止损对比（AB 4-7%子集）===
        for cand in ab_candidates:
            if not (0.04 <= cand['gap'] <= 0.07):
                continue
            t_idx = cand['t']
            stock = cand['stock']
            fields = all_data[stock]
            lows = fields.get('low')

            # 无止损：T+1收盘卖（已经在ret中）
            results['r1']['without_stop'].append({
                'stock': stock, 'date': date_str, 'ret': cand['ret']
            })

            # 有止损：T+1日（下一个交易日）的表现
            if t_idx + 1 < len(fields['close']) and 'low' in fields:
                next_low = fields['low'][t_idx + 1]
                next_close = fields['close'][t_idx + 1]
                buy_price = cand['buy_price']

                if next_low is not None and next_close is not None and buy_price > 0:
                    stop_price = buy_price * 0.92  # -8%止损
                    if next_low <= stop_price:
                        ret_stop = -0.08
                    else:
                        ret_stop = next_close / buy_price - 1

                    results['r1']['with_stop'].append({
                        'stock': stock, 'date': date_str, 'ret': ret_stop,
                        'stopped': next_low <= stop_price
                    })

        # === R8: 板块限定（用当日全市场涨跌分布代理Top5）===
        # 计算当日全市场均收
        all_rets = [cand['ret'] for cand in ab_candidates]
        if all_rets:
            market_avg = sum(all_rets) / len(all_rets)
            # Top5强=收益>市场均值的标的（代理：无板块数据时的近似）
            # 注意：这不是真正的板块限定，只是用个股表现做近似
            # 真正的R8需要申万板块数据
            for cand in ab_candidates:
                if 0.04 <= cand['gap'] <= 0.07:
                    if cand['ret'] > market_avg:
                        results['r8']['top5_strong'].append(cand)
                    results['r8']['baseline'].append(cand)

        # 进度报告
        if (t - start_idx) % 200 == 0:
            print(f"  进度: {date_str} ({t-start_idx}/{end_idx-start_idx}天)")

    return results

def calc_stats(trades):
    """计算统计量"""
    n = len(trades)
    if n == 0:
        return {'n': 0}

    rets = [t['ret'] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    avg_ret = sum(rets) / n
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else None

    return {
        'n': n,
        'win_rate': round(len(wins) / n * 100, 1),
        'avg_return_pct': round(avg_ret * 100, 2),
        'avg_win_pct': round(avg_win * 100, 2),
        'avg_loss_pct': round(avg_loss * 100, 2),
        'profit_factor': round(pf, 2) if pf else None,
        'max_return_pct': round(max(rets) * 100, 2),
        'min_return_pct': round(min(rets) * 100, 2),
        'median_return_pct': round(sorted(rets)[n//2] * 100, 2),
    }

# ============================================================
# 主程序
# ============================================================

def main():
    print("=" * 70)
    print("R6/R1/R8 回测验证（qlib bin数据源版）")
    print("=" * 70)

    calendars = load_calendar()
    print(f"日历: {len(calendars)}天 ({calendars[0]} ~ {calendars[-1]})")

    # 加载所有股票数据（open/high/low/close/volume）
    features_dir = os.path.join(QLIB_PATH, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])
    print(f"待加载股票: {len(all_stocks)}只（已排除科创/北交所）")

    print("\n加载数据中（open/close/low/volume）...")
    all_data = load_all_stocks_fields(all_stocks, ['close', 'open', 'low', 'volume'])
    print(f"成功加载: {len(all_data)}只")

    # 运行回测
    print("\n开始回测...")
    results = run_backtest(calendars, all_data)

    # 统计
    print(f"\n{'='*70}")
    print("回测结果（qlib bin数据源）")
    print(f"{'='*70}")

    print("\n--- R6: 竞价缺口分组 ---")
    for key in ['ab_4_7', 'low_2_4', 'high_7_10']:
        s = calc_stats(results['r6'][key])
        if s['n'] > 0:
            print(f"  {key:12s} | N={s['n']:6d} | 胜率={s['win_rate']:.1f}% "
                  f"| 均收={s['avg_return_pct']:+.2f}% | PF={s.get('profit_factor','?')} "
                  f"| 最大亏={s['min_return_pct']:.1f}%")

    print("\n--- R1: 止损对比 ---")
    for key in ['with_stop', 'without_stop']:
        s = calc_stats(results['r1'][key])
        if s['n'] > 0:
            stop_rate = ''
            if key == 'with_stop':
                stopped = sum(1 for t in results['r1'][key] if t.get('stopped'))
                stop_rate = f" | 止损触发={stopped*100/s['n']:.1f}%"
            print(f"  {key:15s} | N={s['n']:6d} | 胜率={s['win_rate']:.1f}% "
                  f"| 均收={s['avg_return_pct']:+.2f}% | PF={s.get('profit_factor','?')} "
                  f"| 最大亏={s['min_return_pct']:.1f}%{stop_rate}")

    print("\n--- R8: 强势筛选代理 ---")
    print("  (注: 无板块数据，用个股表现>市场均值代理Top5)")
    for key in ['top5_strong', 'baseline']:
        s = calc_stats(results['r8'][key])
        if s['n'] > 0:
            print(f"  {key:15s} | N={s['n']:6d} | 胜率={s['win_rate']:.1f}% "
                  f"| 均收={s['avg_return_pct']:+.2f}% | PF={s.get('profit_factor','?')}")

    # 输出
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'data_source': 'qlib_bin',
            'date_range': '2020-01-04 ~ 2026-08-07',
            'total_stocks': len(all_data),
        },
        'r6': {k: calc_stats(v) for k, v in results['r6'].items()},
        'r1': {k: calc_stats(v) for k, v in results['r1'].items()},
        'r8': {k: calc_stats(v) for k, v in results['r8'].items()},
    }

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 结果保存: {OUT_PATH}")

if __name__ == '__main__':
    main()
