#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R8主线板块限定——无数据泄露版回测
修复前期版本的核心错误：用t-1日板块排名（而非当日板块涨幅）选股。

V1数据预检: ✅ qlib_bin PASS
V3防泄露: ✅ 仅用t-1及更早信息

数据流:
  1. 从meta_db/stock_sector_sw1获取静态板块归属（不涉及daily数据质量问题）
  2. 从qlib bin获取个股日K（干净数据）
  3. 用t-1日个股涨跌计算板块均涨跌→排名Top5
  4. t日仅从t-1日Top5板块中选AB候选
  5. 对比：Top5限定 vs 全市场基线
"""
import os
import struct
import math
import json
import sqlite3
from collections import defaultdict
from datetime import datetime

import sys
sys.path.insert(0, r'D:\zcodesj\attribution')
from validation_gate import precheck_data, require_dsr, verdict_label

QLIB = r'D:\workbuddyspace\qlib_data\qlib_bin'
META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
OUT = r'D:\zcodesj\attribution\r8_no_leak_result.json'

def load_calendar():
    with open(os.path.join(QLIB, 'calendars', 'day.txt'), 'r') as f:
        return [l.strip() for l in f if l.strip()]

def load_field(stock, field):
    fp = os.path.join(QLIB, 'features', stock, f'{field}.day.bin')
    if not os.path.exists(fp): return None
    with open(fp, 'rb') as f:
        n = os.path.getsize(fp) // 4
        return list(struct.unpack(f'{n}f', f.read()))

def load_sectors():
    """从meta_db获取板块归属（静态表，无数据质量问题）"""
    conn = sqlite3.connect(META_DB)
    c = conn.cursor()
    c.execute('SELECT code, sector_name FROM stock_sector_sw1')
    sectors = {}
    for r in c.fetchall():
        # bj920000 -> bj920000 不转，直接按qlib格式映射
        # sector表code带前缀(sh/sz/bj)，qlib也是sh/sz/bj
        sectors[r[0]] = r[1]
    conn.close()
    return sectors

def is_excluded(stock):
    code = stock[2:]
    return code.startswith('688') or code.startswith('8') or code.startswith('4')

def get_zt_threshold(stock):
    code = stock[2:]
    return 0.195 if (code.startswith('300') or code.startswith('301')) else 0.095

def main():
    # === V1 数据预检 ===
    print("=== V1 数据预检 ===")
    check = precheck_data('qlib_bin')
    assert check['verdict'] == 'PASS', f"数据预检失败: {check['reason']}"
    print(f"✅ qlib_bin PASS, {check['total_days']}天")

    calendars = load_calendar()
    sectors = load_sectors()
    print(f"板块归属: {len(sectors)}只股票")

    # 加载所有股票close/open
    features_dir = os.path.join(QLIB, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])

    print(f"\n加载{len(all_stocks)}只股票数据...")
    stock_data = {}
    for i, stock in enumerate(all_stocks):
        closes = load_field(stock, 'close')
        opens = load_field(stock, 'open')
        if closes and opens and len(closes) == len(opens):
            stock_data[stock] = {'close': closes, 'open': opens}
        if (i+1) % 1000 == 0:
            print(f"  {i+1}/{len(all_stocks)}")
    print(f"成功加载: {len(stock_data)}只")

    # === 回测参数 ===
    # 用近3年数据（2023-08 ~ 2026-08）减少计算量
    start_idx = next(i for i, d in enumerate(calendars) if d >= '2023-08-01')
    end_idx = len(calendars) - 1

    # 预计算每天的个股涨跌幅
    print(f"\n预计算个股涨跌幅 ({calendars[start_idx]} ~ {calendars[end_idx]})...")

    # daily_returns[date_idx][stock] = pct
    daily_returns = defaultdict(dict)
    for stock, data in stock_data.items():
        closes = data['close']
        for t in range(start_idx, min(end_idx + 1, len(closes))):
            if t > 0 and closes[t] and closes[t-1] and closes[t-1] > 0:
                daily_returns[t][stock] = closes[t] / closes[t-1] - 1

    # === 核心回测 ===
    print(f"\n开始无泄露R8回测...")

    r8_trades = []      # R8 Top5限定
    baseline_trades = []  # 全市场基线

    for t in range(start_idx + 2, end_idx):
        date_str = calendars[t]

        # === Step1: 用t-1日板块涨幅计算Top5（无泄露！） ===
        prev_returns = daily_returns.get(t - 1, {})
        sector_returns = defaultdict(list)

        for stock, ret in prev_returns.items():
            sec = sectors.get(stock)
            if sec:
                sector_returns[sec].append(ret)

        # 计算各板块均涨跌，取Top5
        sector_avg = {}
        for sec, rets in sector_returns.items():
            if len(rets) >= 3:  # 至少3只股票才有代表性
                sector_avg[sec] = sum(rets) / len(rets)

        top5_sectors = set(sorted(sector_avg, key=sector_avg.get, reverse=True)[:5])

        # === Step2: 找t日的AB候选 ===
        for stock, data in stock_data.items():
            closes = data['close']
            opens = data['open']

            if t >= len(closes) or t < 2:
                continue

            # 前日（t-1）涨停？
            prev_close = closes[t - 1]
            prev2_close = closes[t - 2]
            today_open = opens[t]

            if not all([prev_close, prev2_close, today_open]):
                continue
            if prev2_close <= 0 or prev_close <= 0 or today_open <= 0:
                continue

            prev_pct = prev_close / prev2_close - 1
            threshold = get_zt_threshold(stock)
            if prev_pct < threshold:
                continue

            # AB竞价缺口4-7%
            gap = today_open / prev_close - 1
            if gap > 0.15 or gap < -0.10:  # 排除除权
                continue
            if not (0.04 <= gap <= 0.07):
                continue

            today_close = closes[t]
            if not today_close or today_close <= 0:
                continue

            ret = today_close / today_open - 1

            trade = {
                'stock': stock,
                'date': date_str,
                'gap': round(gap * 100, 2),
                'ret': ret,
                'sector': sectors.get(stock, '未知'),
            }

            # 基线（全市场AB候选）
            baseline_trades.append(trade)

            # R8限定（t-1日Top5板块）
            if trade['sector'] in top5_sectors:
                trade['top5_source'] = 't-1排名'
                r8_trades.append(trade)

        if (t - start_idx) % 200 == 0:
            print(f"  {date_str} ({t-start_idx}/{end_idx-start_idx}) | "
                  f"基线={len(baseline_trades)} R8={len(r8_trades)}")

    # === 统计 ===
    def stats(trades):
        n = len(trades)
        if n == 0: return {'n': 0}
        rets = [t['ret'] for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else None
        return {
            'n': n,
            'win_rate': round(len(wins) / n * 100, 1),
            'avg_return_pct': round(sum(rets) / n * 100, 2),
            'pf': round(pf, 2) if pf else None,
            'median_return_pct': round(sorted(rets)[n // 2] * 100, 2),
            'returns': rets,  # 保留完整序列供DSR计算
        }

    print(f"\n{'='*70}")
    print(f"R8无泄露回测结果（{calendars[start_idx]} ~ {calendars[end_idx-1]}）")
    print(f"{'='*70}")

    s_r8 = stats(r8_trades)
    s_base = stats(baseline_trades)

    for label, s in [('R8 Top5板块(t-1排名)', s_r8), ('基线(全市场AB)', s_base)]:
        if s['n'] > 0:
            print(f"\n{label}:")
            print(f"  N={s['n']}, 胜率={s['win_rate']}%, 均收={s['avg_return_pct']:+.2f}%, "
                  f"PF={s['pf']}, 中位={s['median_return_pct']:+.2f}%")

    # === V2 DSR检验 ===
    print(f"\n{'='*70}")
    print("V2 DSR统计检验")
    print(f"{'='*70}")

    # N=2个核心比较（R8 vs 基线），但隐含试验约8个
    N_TRIALS = 8

    if s_r8['n'] > 10 and 'returns' in s_r8:
        dsr_r8 = require_dsr(s_r8['returns'], n_trials=N_TRIALS)
        label_r8, allowed_r8 = verdict_label(dsr_r8['dsr'], direction='positive')
        print(f"\nR8 Top5板块:")
        print(f"  年化SR={dsr_r8['sr_annual']}, PSR0={dsr_r8['psr0']}")
        print(f"  DSR={dsr_r8['dsr']} (N_trials={N_TRIALS})")
        print(f"  判定: {dsr_r8['verdict']}")
        print(f"  措辞: {label_r8} → {allowed_r8}")

    if s_base['n'] > 10 and 'returns' in s_base:
        dsr_base = require_dsr(s_base['returns'], n_trials=N_TRIALS)
        print(f"\n基线:")
        print(f"  年化SR={dsr_base['sr_annual']}, DSR={dsr_base['dsr']}")
        print(f"  判定: {dsr_base['verdict']}")

    # === 差异显著性 ===
    if s_r8['n'] > 30 and s_base['n'] > 30:
        print(f"\n{'='*70}")
        print("R8 vs 基线 差异检验")
        print(f"{'='*70}")

        r8_rets = s_r8['returns']
        base_rets = s_base['returns']

        # 均值差异
        diff = s_r8['avg_return_pct'] - s_base['avg_return_pct']
        print(f"  均收差异: {s_r8['avg_return_pct']:.2f}% - {s_base['avg_return_pct']:.2f}% = {diff:+.2f}pp")

        # 简单t检验
        from scipy import stats as sp_stats
        t_stat, p_val = sp_stats.ttest_ind(r8_rets, base_rets, equal_var=False)
        print(f"  Welch t检验: t={t_stat:.3f}, p={p_val:.4f}")
        if p_val < 0.05:
            print(f"  ✅ 差异统计显著(p<0.05)——R8限定确实改善了收益")
        elif p_val < 0.10:
            print(f"  🟡 差异边际显著(0.05<p<0.10)")
        else:
            print(f"  ⚠️ 差异不显著(p≥0.10)——R8改善可能是噪音")

    # 输出
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'data_source': 'qlib_bin (V1预检PASS)',
            'date_range': f'{calendars[start_idx]} ~ {calendars[end_idx-1]}',
            'leakage_fixed': True,
            'sector_ranking_timing': 't-1 (无数据泄露)',
        },
        'r8_top5': {k: v for k, v in s_r8.items() if k != 'returns'},
        'baseline': {k: v for k, v in s_base.items() if k != 'returns'},
        'dsr_r8': dsr_r8 if s_r8['n'] > 10 else None,
        'dsr_baseline': dsr_base if s_base['n'] > 10 else None,
        'difference_test': {
            'mean_diff_pp': diff if s_r8['n'] > 30 else None,
            't_stat': round(t_stat, 3) if s_r8['n'] > 30 else None,
            'p_value': round(p_val, 4) if s_r8['n'] > 30 else None,
        } if s_r8['n'] > 30 else None,
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 结果保存: {OUT}")

if __name__ == '__main__':
    main()
