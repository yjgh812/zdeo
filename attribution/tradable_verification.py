#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可交易性验证：解决M3的regime未来信息问题

M3的regime分层用"当日涨停数"判断——但实际交易时t日开盘买入前不知道t日涨停数。
本脚本验证：
  1. t-1涨停数→t日regime的预测准确率
  2. t-1涨停数判定regime后，因子策略的实际T+1收益
  3. 因子筛选×regime过滤的联合可交易收益

V1: qlib_bin PASS
V3: 严格用t-1及更早信息，无未来信息
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
OUT = r'D:\zcodesj\attribution\tradable_verification.json'

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

def calc_stats(returns):
    n = len(returns)
    if n == 0: return {'n': 0}
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else None
    return {
        'n': n,
        'win_rate': round(len(wins) / n * 100, 1),
        'avg_return_pct': round(sum(returns) / n * 100, 2),
        'pf': round(pf, 2) if pf else None,
    }

def main():
    print("=== V1 数据预检 ===")
    check = precheck_data('qlib_bin')
    assert check['verdict'] == 'PASS'
    print(f"✅ PASS")

    calendars = load_calendar()
    features_dir = os.path.join(QLIB, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])

    print(f"\n加载{len(all_stocks)}只...")
    stock_data = {}
    for i, stock in enumerate(all_stocks):
        closes = load_field_safe(stock, 'close')
        opens = load_field_safe(stock, 'open')
        if closes and opens and len(closes) > 60:
            stock_data[stock] = {'close': closes, 'open': opens}
        if (i+1) % 2000 == 0: print(f"  {i+1}/{len(all_stocks)}")
    print(f"成功: {len(stock_data)}只")

    start_idx = next(i for i, d in enumerate(calendars) if d >= '2020-01-01')
    end_idx = len(calendars) - 1

    # ============================================================
    # 预计算：每天t-1因子 + t-1涨停数 + t日T+1收益 + t日涨停数
    # ============================================================
    print(f"\n预计算（严格时序）...")

    daily = {}  # t -> {records, prev_zt_count, today_zt_count}

    for t in range(start_idx + 21, end_idx):
        date_str = calendars[t]
        records = []
        prev_zt = 0
        today_zt = 0

        for stock, data in stock_data.items():
            closes = data['close']
            opens = data['open']
            if t >= len(closes) or t < 21:
                continue

            # t-1日涨停（用t-2/t-1的close判断）
            if t >= 2 and closes[t-2] > 0 and closes[t-1] > 0:
                prev_pct = closes[t-1] / closes[t-2] - 1
                if prev_pct >= get_zt_threshold(stock):
                    prev_zt += 1

            # t日涨停（用于后续验证，不用于决策）
            if closes[t-1] > 0 and closes[t] > 0:
                today_pct = closes[t] / closes[t-1] - 1
                if today_pct >= get_zt_threshold(stock):
                    today_zt += 1

            # t-1日因子
            def mom(days):
                s = t - days
                if s < 0 or closes[s] <= 0 or closes[t-1] <= 0: return None
                return closes[t-1] / closes[s] - 1

            mom5 = mom(5)
            mom20 = mom(20)
            if mom5 is None or mom20 is None:
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

            # t日T+1收益
            today_open = opens[t]
            today_close = closes[t]
            if today_open <= 0 or today_close <= 0:
                continue
            t1_ret = today_close / today_open - 1

            records.append({
                'mom5': mom5, 'mom20': mom20, 'rv20': rv20, 't1_ret': t1_ret,
            })

        if len(records) >= 100:
            daily[t] = {
                'records': records,
                'prev_zt': prev_zt,     # t-1日涨停数（可交易信息）
                'today_zt': today_zt,   # t日涨停数（用于验证，未来信息）
                'date': date_str,
            }

        if (t - start_idx) % 300 == 0:
            print(f"  {date_str} ({t-start_idx}/{end_idx-start_idx})")

    print(f"有效交易日: {len(daily)}")

    # ============================================================
    # Q1: t-1涨停数 → t日regime 预测准确率
    # ============================================================
    print(f"\n{'='*70}")
    print("Q1: t-1涨停数预测t日regime的准确率")
    print(f"{'='*70}")

    # 用t-1涨停数预测t日属于哪个regime
    # 预测规则：t-1涨停>100→预测t日热烈；50-100→正常；<50→低迷
    correct = 0
    total = 0
    confusion = defaultdict(lambda: defaultdict(int))

    for t in daily:
        prev_z = daily[t]['prev_zt']
        today_z = daily[t]['today_zt']

        # 预测
        if prev_z > 100:
            pred = '热烈'
        elif prev_z > 50:
            pred = '正常'
        else:
            pred = '低迷'

        # 实际
        if today_z > 100:
            actual = '热烈'
        elif today_z > 50:
            actual = '正常'
        else:
            actual = '低迷'

        confusion[pred][actual] += 1
        if pred == actual:
            correct += 1
        total += 1

    accuracy = correct / total * 100 if total > 0 else 0
    print(f"\n总准确率: {correct}/{total} = {accuracy:.1f}%")

    print(f"\n混淆矩阵（行=预测, 列=实际）:")
    print(f"{'':>8s} | {'实际热烈':>8s} | {'实际正常':>8s} | {'实际低迷':>8s} | {'合计':>6s}")
    for pred in ['热烈', '正常', '低迷']:
        row = confusion[pred]
        row_total = sum(row.values())
        print(f"{'预测'+pred:>8s} | {row['热烈']:8d} | {row['正常']:8d} | {row['低迷']:8d} | {row_total:6d}")

    # 关键指标：预测"低迷"时实际确实是低迷的比例（precision）
    # 和预测"非低迷"时实际也非低迷的比例
    low_precision = confusion['低迷']['低迷'] / max(sum(confusion['低迷'].values()), 1) * 100
    non_low_recall = (confusion['热烈']['热烈'] + confusion['正常']['正常']) / \
                     max(confusion['热烈']['热烈'] + confusion['正常']['热烈'] + confusion['正常']['正常'] + confusion['热烈']['正常'], 1) * 100

    print(f"\n退潮期precision（预测低迷→实际低迷）: {low_precision:.1f}%")
    print(f"非退潮期recall（实际非低迷→预测非低迷）: {non_low_recall:.1f}%")

    # ============================================================
    # Q2: 用t-1涨停数判定regime的T+1收益（严格可交易）
    # ============================================================
    print(f"\n{'='*70}")
    print("Q2: t-1涨停数regime × 因子筛选的T+1收益（严格可交易）")
    print(f"{'='*70}")

    # 策略矩阵：
    # regime(t-1涨停数判定) × 因子筛选条件 → T+1收益
    regime_rets = defaultdict(lambda: defaultdict(list))

    for t in daily:
        prev_z = daily[t]['prev_zt']
        records = daily[t]['records']

        if prev_z > 100:
            regime = '热烈(t-1>100)'
        elif prev_z > 50:
            regime = '正常(t-1:50-100)'
        else:
            regime = '低迷(t-1<50)'

        # 当日rv20 90分位阈值
        rvs = sorted([r['rv20'] for r in records])
        rv90 = rvs[int(len(rvs)*0.9)] if len(rvs) > 10 else 999

        for r in records:
            # 全部
            regime_rets[regime]['全部'].append(r['t1_ret'])
            # 因子筛选：mom5>0 + rv20 Top10%
            if r['mom5'] > 0 and r['rv20'] > rv90:
                regime_rets[regime]['因子筛选'].append(r['t1_ret'])
            # 仅高波
            if r['rv20'] > rv90:
                regime_rets[regime]['仅高波'].append(r['t1_ret'])

    q2_results = {}
    print(f"\n{'regime(t-1判定)':>18s} | {'策略':>8s} | {'N':>8s} | {'均收':>8s} | {'胜率':>6s} | {'PF':>5s}")
    print("-" * 65)
    for regime in ['热烈(t-1>100)', '正常(t-1:50-100)', '低迷(t-1<50)']:
        for strategy in ['全部', '仅高波', '因子筛选']:
            rets = regime_rets[regime][strategy]
            if len(rets) > 50:
                s = calc_stats(rets)
                print(f"{regime:>18s} | {strategy:>8s} | {s['n']:8d} | {s['avg_return_pct']:+7.2f}% | {s['win_rate']:5.1f}% | {str(s.get('pf','?')):>5s}")
                q2_results[f'{regime}_{strategy}'] = s

    # ============================================================
    # Q3: 最终可交易策略（regime过滤 + 因子筛选）
    # ============================================================
    print(f"\n{'='*70}")
    print("Q3: 最终可交易策略")
    print(f"{'='*70}")

    # 策略：t-1涨停>50时用因子筛选买入，<50时空仓
    strategy_rets = []
    no_trade_days = 0
    trade_days = 0

    for t in daily:
        prev_z = daily[t]['prev_zt']

        if prev_z < 50:
            no_trade_days += 1
            continue  # 空仓

        trade_days += 1
        records = daily[t]['records']
        rvs = sorted([r['rv20'] for r in records])
        rv90 = rvs[int(len(rvs)*0.9)] if len(rvs) > 10 else 999

        # 买入因子筛选的股票，等权
        selected = [r for r in records if r['mom5'] > 0 and r['rv20'] > rv90]
        if selected:
            avg_ret = sum(r['t1_ret'] for r in selected) / len(selected)
            strategy_rets.append(avg_ret)

    if strategy_rets:
        s = calc_stats(strategy_rets)
        dsr = require_dsr(strategy_rets, n_trials=5)
        label, allowed = verdict_label(dsr['dsr'], direction='positive')

        print(f"\n策略: t-1涨停>50时买入(mom5>0+rv Top10%等权), <50空仓")
        print(f"  交易日: {trade_days}天交易 + {no_trade_days}天空仓 = {trade_days+no_trade_days}天")
        print(f"  交易占比: {trade_days/(trade_days+no_trade_days)*100:.1f}%")
        print(f"  N(每日组合): {s['n']}")
        print(f"  日均收益: {s['avg_return_pct']:+.2f}%")
        print(f"  胜率(日): {s['win_rate']:.1f}%")
        print(f"  PF: {s.get('pf', '?')}")
        print(f"  DSR: {dsr['dsr']} → {label}")

        # 年化（假设交易日252天）
        daily_avg = s['avg_return_pct'] / 100
        annual = ((1 + daily_avg) ** 252 - 1) * 100
        print(f"  粗估年化: {annual:+.1f}%（含复利，未扣手续费）")

    # ============================================================
    # Q4: 与一直持有的对比
    # ============================================================
    print(f"\n--- 对照组：一直持有因子筛选（不做regime过滤）---")
    all_strategy_rets = []
    for t in daily:
        records = daily[t]['records']
        rvs = sorted([r['rv20'] for r in records])
        rv90 = rvs[int(len(rvs)*0.9)] if len(rvs) > 10 else 999
        selected = [r for r in records if r['mom5'] > 0 and r['rv20'] > rv90]
        if selected:
            avg_ret = sum(r['t1_ret'] for r in selected) / len(selected)
            all_strategy_rets.append(avg_ret)

    if all_strategy_rets:
        s2 = calc_stats(all_strategy_rets)
        daily_avg2 = s2['avg_return_pct'] / 100
        annual2 = ((1 + daily_avg2) ** 252 - 1) * 100
        print(f"  N: {s2['n']}, 日均: {s2['avg_return_pct']:+.2f}%, PF: {s2.get('pf')}, 年化: {annual2:+.1f}%")

    # ============================================================
    # 输出
    # ============================================================
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'data_source': 'qlib_bin (V1 PASS, NaN filtered)',
            'valid_days': len(daily),
            'timing': 'strict: t-1 info → t-day trade',
        },
        'q1_regime_prediction': {
            'accuracy_pct': round(accuracy, 1),
            'confusion_matrix': {p: dict(a) for p, a in confusion.items()},
            'low_precision_pct': round(low_precision, 1),
            'non_low_recall_pct': round(non_low_recall, 1),
        },
        'q2_regime_factor_t1': q2_results,
        'q3_final_strategy': {
            'trade_days': trade_days,
            'no_trade_days': no_trade_days,
            'stats': s if strategy_rets else None,
            'dsr': dsr['dsr'] if strategy_rets else None,
            'annual_est_pct': round(annual, 1) if strategy_rets else None,
        },
        'q4_no_filter_compare': {
            'stats': s2 if all_strategy_rets else None,
            'annual_est_pct': round(annual2, 1) if all_strategy_rets else None,
        },
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] {OUT}")

if __name__ == '__main__':
    main()
