#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
优化效果验证回测脚本
对比3条优化规则（R6/R1/R8）在历史数据中的表现差异。

数据源: dragon_quant_meta.db / stock_daily_meta（2024-08 ~ 2026-08）
方法: 不依赖Qlib框架，直接用SQLite+Pandas模拟AB策略的三组变体，
      确保逻辑透明可复现。

三条验证规则：
  R6: AB竞价确认路径（前日涨停 + 今日竞价高开4-7%）
      → 对比基线：前日涨停 + 今日任意高开（0-10%）
  R1: 机械化止损（持仓次日跌幅达-8%即卖出）
      → 对比基线：不止损，持有至收盘
  R8: 主线板块限定（仅买入当日Top5强势板块内的标的）
      → 对比基线：不限制板块

输出: D:\zcodesj\attribution\回测验证数据.json + 控制台摘要
"""
import sqlite3
import json
import os
from collections import defaultdict
from datetime import datetime

DB_PATH = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
OUT_PATH = r'D:\zcodesj\attribution\回测验证数据.json'

# ============================================================
# 工具函数
# ============================================================

def is_st_or_excluded(code):
    """排除ST/科创/北交所（R约束）"""
    if code.startswith('688') or code.startswith('8') or code.startswith('4'):
        return True
    return False

def get_zt_threshold(code):
    """涨停阈值：创业板20%，主板10%"""
    if code.startswith('300') or code.startswith('301'):
        return 19.0
    return 9.5

def check_zt(pct_chg, code):
    """判断是否涨停"""
    if pct_chg is None:
        return False
    return pct_chg >= get_zt_threshold(code)

# ============================================================
# 数据加载
# ============================================================

def load_daily_data(start_date=20240801, end_date=20260810):
    """加载日线数据，返回按code分组的字典"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT code, date, open, high, low, close, volume, amount, change_pct
        FROM stock_daily_meta
        WHERE date >= ? AND date <= ?
        ORDER BY code, date
    """, (start_date, end_date))

    rows = cursor.fetchall()
    conn.close()

    by_code = defaultdict(list)
    for r in rows:
        code, date, o, h, l, c, vol, amt, pct = r
        if is_st_or_excluded(code):
            continue
        if o is None or c is None or vol is None or vol == 0:
            continue
        by_code[code].append({
            'date': date, 'open': o, 'high': h, 'low': l,
            'close': c, 'volume': vol, 'amount': amt, 'pct': pct
        })

    return by_code


def load_sector_data():
    """加载板块归属数据，key统一为纯6位代码"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT code, sector_name FROM stock_sector_sw1")
    sectors = {}
    for r in cursor.fetchall():
        raw_code = r[0]
        # 统一为纯6位代码（去掉sz/sh/bj前缀）
        code = raw_code[-6:] if len(raw_code) > 6 else raw_code
        sectors[code] = r[1]
    conn.close()
    return sectors

# ============================================================
# 回测1: R6 AB竞价确认路径验证
# ============================================================

def backtest_r6_ab_gap(by_code):
    """
    对比：AB竞价确认（4-7%高开） vs 宽口径高开（0-10%）
    买入逻辑：前日涨停 + 今日竞价高开X% → 开盘价买入
    卖出逻辑：今日收盘价卖出（T+1）
    """
    results = {
        'ab_4_7': {'trades': [], 'label': 'AB竞价确认(4-7%)'},
        'wide_0_10': {'trades': [], 'label': '宽口径高开(0-10%)'},
        'low_2_4': {'trades': [], 'label': '低开承接(2-4%)'},
        'high_7_10': {'trades': [], 'label': '高开过高(7-10%)'},
    }

    for code, days in by_code.items():
        if len(days) < 2:
            continue

        for i in range(1, len(days)):
            d = days[i]
            d_prev = days[i - 1]

            prev_pct = d_prev['pct']
            if prev_pct is None:
                continue

            # 前日涨停
            if not check_zt(prev_pct, code):
                continue

            prev_close = d_prev['close']
            open_p = d['open']

            if prev_close <= 0 or open_p <= 0:
                continue

            gap = open_p / prev_close - 1

            # T+1收益：开盘买入，收盘卖出
            close_p = d['close']
            ret = close_p / open_p - 1

            trade = {
                'code': code, 'date': d['date'], 'gap': gap,
                'ret': ret, 'pct': d['pct']
            }

            # 分类
            if 0.04 <= gap <= 0.07:
                results['ab_4_7']['trades'].append(trade)
            if 0.00 <= gap <= 0.10:
                results['wide_0_10']['trades'].append(trade)
            if 0.02 <= gap < 0.04:
                results['low_2_4']['trades'].append(trade)
            if 0.07 < gap <= 0.10:
                results['high_7_10']['trades'].append(trade)

    # 统计
    summary = {}
    for key, val in results.items():
        trades = val['trades']
        n = len(trades)
        if n == 0:
            summary[key] = {'label': val['label'], 'n': 0}
            continue

        rets = [t['ret'] for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        zts = [t for t in trades if check_zt(t['pct'], t['code'])]

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        avg_ret = sum(rets) / n

        summary[key] = {
            'label': val['label'],
            'n': n,
            'win_rate': len(wins) / n * 100,
            'avg_return_pct': avg_ret * 100,
            'avg_win_pct': avg_win * 100,
            'avg_loss_pct': avg_loss * 100,
            'profit_factor': abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else None,
            'zt_rate': len(zts) / n * 100,
            'median_return_pct': sorted(rets)[n // 2] * 100,
        }

    return summary

# ============================================================
# 回测2: R1 机械化止损验证
# ============================================================

def backtest_r1_stoploss(by_code, stop_pct=-0.08):
    """
    对比：有止损 vs 无止损
    买入逻辑：前日涨停 + 今日AB高开(4-7%) → 开盘买入
    卖出逻辑：
      有止损版：若日内最低价相对买入价跌幅达stop_pct，按止损价卖出；否则收盘卖出
      无止损版：直接收盘卖出
    """
    results = {'with_stop': [], 'without_stop': []}

    for code, days in by_code.items():
        if len(days) < 3:
            continue

        for i in range(1, len(days) - 1):
            d = days[i]       # 买入日
            d_next = days[i + 1]  # 持仓日

            prev_pct = days[i - 1]['pct'] if i >= 1 else None
            if prev_pct is None:
                continue

            if not check_zt(prev_pct, code):
                continue

            prev_close = days[i - 1]['close']
            open_p = d['open']

            if prev_close <= 0 or open_p <= 0:
                continue

            gap = open_p / prev_close - 1
            if not (0.04 <= gap <= 0.07):
                continue

            # 买入价 = 买入日开盘价
            buy_price = open_p

            # === 持仓日（d_next）的T+1表现 ===
            next_low = d_next['low']
            next_close = d_next['close']

            if next_low is None or next_close is None:
                continue

            # 无止损：收盘卖出
            ret_no_stop = next_close / buy_price - 1
            results['without_stop'].append({
                'code': code, 'date': d['date'],
                'ret': ret_no_stop
            })

            # 有止损：检查日内最低价是否触发止损
            stop_price = buy_price * (1 + stop_pct)
            if next_low <= stop_price:
                # 触发止损，按止损价卖出
                ret_stop = stop_pct
            else:
                # 未触发，收盘卖出
                ret_stop = next_close / buy_price - 1

            results['with_stop'].append({
                'code': code, 'date': d['date'],
                'ret': ret_stop, 'stopped': next_low <= stop_price
            })

    # 统计
    summary = {}
    for key in ['with_stop', 'without_stop']:
        trades = results[key]
        n = len(trades)
        if n == 0:
            summary[key] = {'n': 0}
            continue

        rets = [t['ret'] for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        summary[key] = {
            'n': n,
            'win_rate': len(wins) / n * 100,
            'avg_return_pct': sum(rets) / n * 100,
            'avg_win_pct': avg_win * 100,
            'avg_loss_pct': avg_loss * 100,
            'profit_factor': abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else None,
            'max_loss_pct': min(rets) * 100,
            'median_return_pct': sorted(rets)[n // 2] * 100,
        }

    if results['with_stop']:
        stop_rate = sum(1 for t in results['with_stop'] if t.get('stopped')) / len(results['with_stop']) * 100
        summary['with_stop']['stop_trigger_rate'] = stop_rate

    return summary

# ============================================================
# 回测3: R8 主线板块限定验证
# ============================================================

def backtest_r8_sector_filter(by_code, sectors):
    """
    对比：限定强势板块 vs 不限定板块
    买入逻辑：前日涨停 + 今日AB高开(4-7%)
    R8版：仅买入当日全市场涨幅Top5板块的标的
    基线：不限制板块

    注意：板块当日涨幅需要用"当日所有个股平均涨幅"代理（板块实时数据不可得）。
    此处用申万一级板块，按板块内个股当日均值涨跌幅排名。
    """
    # 先计算每个交易日各板块的平均涨跌幅
    daily_sector_returns = defaultdict(lambda: defaultdict(list))

    for code, days in by_code.items():
        sector = sectors.get(code)
        if not sector:
            continue
        for d in days:
            if d['pct'] is not None:
                daily_sector_returns[d['date']][sector].append(d['pct'])

    # 每日Top5板块
    daily_top5_sectors = {}
    for date, sector_rets in daily_sector_returns.items():
        avg_rets = {s: sum(rts) / len(rts) for s, rts in sector_rets.items() if len(rts) >= 3}
        top5 = set(sorted(avg_rets, key=avg_rets.get, reverse=True)[:5])
        daily_top5_sectors[date] = top5

    # 回测
    results = {'r8_top5': [], 'baseline_all': []}

    for code, days in by_code.items():
        if len(days) < 2:
            continue
        sector = sectors.get(code)
        if not sector:
            continue

        for i in range(1, len(days)):
            d = days[i]
            d_prev = days[i - 1]

            prev_pct = d_prev['pct']
            if prev_pct is None:
                continue
            if not check_zt(prev_pct, code):
                continue

            prev_close = d_prev['close']
            open_p = d['open']
            if prev_close <= 0 or open_p <= 0:
                continue

            gap = open_p / prev_close - 1
            if not (0.04 <= gap <= 0.07):
                continue

            close_p = d['close']
            ret = close_p / open_p - 1

            trade = {'code': code, 'date': d['date'], 'ret': ret, 'sector': sector}

            results['baseline_all'].append(trade)

            if sector in daily_top5_sectors.get(d['date'], set()):
                results['r8_top5'].append(trade)

    summary = {}
    for key in ['r8_top5', 'baseline_all']:
        trades = results[key]
        n = len(trades)
        if n == 0:
            summary[key] = {'n': 0}
            continue

        rets = [t['ret'] for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        zts = [t for t in trades if check_zt(t.get('pct', 0), t['code'])]

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        summary[key] = {
            'n': n,
            'win_rate': len(wins) / n * 100,
            'avg_return_pct': sum(rets) / n * 100,
            'avg_win_pct': avg_win * 100,
            'avg_loss_pct': avg_loss * 100,
            'profit_factor': abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else None,
            'median_return_pct': sorted(rets)[n // 2] * 100,
        }

    return summary

# ============================================================
# 主程序
# ============================================================

def main():
    print("=" * 70)
    print("优化效果验证回测（R6/R1/R8）")
    print(f"数据区间: 2024-08-01 ~ 2026-08-10")
    print(f"数据源: {DB_PATH}")
    print("=" * 70)

    # 加载数据
    print("\n[1/4] 加载日线数据...")
    by_code = load_daily_data()
    total_bars = sum(len(v) for v in by_code.values())
    print(f"  股票数: {len(by_code)}, 总K线: {total_bars}")

    print("\n[2/4] 加载板块数据...")
    sectors = load_sector_data()
    print(f"  有板块归属的股票: {len(sectors)}")

    # 回测1: R6
    print("\n[3/4] 回测 R6: AB竞价确认路径...")
    r6_result = backtest_r6_ab_gap(by_code)
    print(f"\n  --- R6 结果 ---")
    for key, s in r6_result.items():
        if s['n'] > 0:
            pf = s.get('profit_factor')
            pf_str = f"{pf:.2f}" if pf else "N/A"
            print(f"  {s['label']:25s} | N={s['n']:5d} | 胜率={s['win_rate']:.1f}% "
                  f"| 均收={s['avg_return_pct']:+.2f}% | PF={pf_str} | 涨停率={s.get('zt_rate',0):.1f}%")

    # 回测2: R1
    print("\n[4/4] 回测 R1: 机械化止损...")
    r1_result = backtest_r1_stoploss(by_code)
    print(f"\n  --- R1 结果 ---")
    for key, s in r1_result.items():
        if s['n'] > 0:
            pf = s.get('profit_factor')
            pf_str = f"{pf:.2f}" if pf else "N/A"
            extra = f" | 止损触发率={s.get('stop_trigger_rate',0):.1f}%" if key == 'with_stop' else ""
            print(f"  {key:15s} | N={s['n']:5d} | 胜率={s['win_rate']:.1f}% "
                  f"| 均收={s['avg_return_pct']:+.2f}% | PF={pf_str} | 最大亏={s['max_loss_pct']:.1f}%{extra}")

    # 回测3: R8
    print("\n  回测 R8: 主线板块限定...")
    r8_result = backtest_r8_sector_filter(by_code, sectors)
    print(f"\n  --- R8 结果 ---")
    for key, s in r8_result.items():
        if s['n'] > 0:
            pf = s.get('profit_factor')
            pf_str = f"{pf:.2f}" if pf else "N/A"
            print(f"  {key:15s} | N={s['n']:5d} | 胜率={s['win_rate']:.1f}% "
                  f"| 均收={s['avg_return_pct']:+.2f}% | PF={pf_str}")

    # 汇总输出
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'data_range': '2024-08-01 ~ 2026-08-10',
            'db_path': DB_PATH,
            'total_stocks': len(by_code),
            'total_bars': total_bars,
        },
        'r6_ab_gap': r6_result,
        'r1_stoploss': r1_result,
        'r8_sector_filter': r8_result,
    }

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 结果已保存: {OUT_PATH}")

if __name__ == '__main__':
    main()
