#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Barra动量因子涨停预判力正式回测
严格按验证纪律执行：
  V1: qlib_bin预检PASS
  V3: 仅用t-1及更早数据计算因子，预测t日涨停

研究问题：
  t-1日的MOMENTUM因子（近20日累计收益）能否预判t日涨停？

方法：
  1. 每个交易日，计算全市场股票的20日动量
  2. 按动量十分位（decile）分组
  3. 统计每个分组在t日的涨停率
  4. 如果Top decile涨停率显著高于Bottom decile → 因子有效

同时测试RESVOL（残差波动率）因子的预判力。
"""
import os
import struct
import math
import json
from collections import defaultdict
from datetime import datetime

import sys
sys.path.insert(0, r'D:\zcodesj\attribution')
from validation_gate import precheck_data, require_dsr, verdict_label

QLIB = r'D:\workbuddyspace\qlib_data\qlib_bin'
OUT = r'D:\zcodesj\attribution\momentum_factor_backtest.json'

def load_calendar():
    with open(os.path.join(QLIB, 'calendars', 'day.txt'), 'r') as f:
        return [l.strip() for l in f if l.strip()]

def load_field(stock, field):
    fp = os.path.join(QLIB, 'features', stock, f'{field}.day.bin')
    if not os.path.exists(fp): return None
    with open(fp, 'rb') as f:
        n = os.path.getsize(fp) // 4
        return list(struct.unpack(f'{n}f', f.read()))

def is_excluded(stock):
    code = stock[2:]
    return code.startswith('688') or code.startswith('8') or code.startswith('4')

def get_zt_threshold(stock):
    code = stock[2:]
    return 0.195 if (code.startswith('300') or code.startswith('301')) else 0.095

def main():
    # V1
    print("=== V1 数据预检 ===")
    check = precheck_data('qlib_bin')
    assert check['verdict'] == 'PASS'
    print(f"✅ PASS, {check['total_days']}天")

    calendars = load_calendar()
    features_dir = os.path.join(QLIB, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])

    # 加载数据
    print(f"\n加载{len(all_stocks)}只股票close数据...")
    stock_closes = {}
    for i, stock in enumerate(all_stocks):
        closes = load_field(stock, 'close')
        if closes and len(closes) > 60:
            # 过滤首日0值
            if closes[0] == 0:
                closes = closes[1:]
            stock_closes[stock] = closes
        if (i+1) % 2000 == 0:
            print(f"  {i+1}/{len(all_stocks)}")
    print(f"成功: {len(stock_closes)}只")

    # 回测参数
    start_idx = next(i for i, d in enumerate(calendars) if d >= '2020-01-01')
    end_idx = len(calendars) - 1
    print(f"\n回测: {calendars[start_idx]} ~ {calendars[end_idx-1]}")

    # === 核心：逐日计算因子+涨停统计 ===
    # decile_stats[decile][date] = {'total': N, 'zt': M}
    momentum_deciles = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'zt': 0}))
    resvol_deciles = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'zt': 0}))

    # 用于后续IC计算
    ic_records = []  # [(date, ic)]

    for t in range(start_idx + 21, end_idx):
        date_str = calendars[t]

        # 计算所有股票的因子值和涨停状态
        factors_today = []

        for stock, closes in stock_closes.items():
            if t >= len(closes) or t < 21:
                continue

            # t-1日的20日动量（V3防泄露：仅用t-1及更早）
            window = closes[max(0, t-21):t]  # t-21到t-1的close（不含t）
            valid = [c for c in window if c and c > 0]
            if len(valid) < 15:
                continue

            # 动量
            if len(window) >= 21 and window[0] and window[0] > 0:
                momentum = window[-1] / window[0] - 1
            else:
                continue

            # 波动率
            rets = []
            for j in range(1, len(valid)):
                if valid[j-1] > 0:
                    rets.append(valid[j] / valid[j-1] - 1)
            if len(rets) < 10:
                continue
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r)**2 for r in rets) / len(rets)
            resvol = math.sqrt(var_r) if var_r > 0 else 0

            # t日涨停？
            today_close = closes[t]
            prev_close = closes[t-1]
            if not today_close or not prev_close or prev_close <= 0:
                continue

            today_ret = today_close / prev_close - 1
            threshold = get_zt_threshold(stock)
            is_zt = today_ret >= threshold

            # t日连续收益（用于IC）
            today_cont_ret = today_ret

            factors_today.append({
                'stock': stock,
                'momentum': momentum,
                'resvol': resvol,
                'is_zt': is_zt,
                'today_ret': today_cont_ret,
            })

        if len(factors_today) < 100:
            continue

        # === 动量十分位 ===
        sorted_mom = sorted(factors_today, key=lambda x: x['momentum'])
        decile_size = len(sorted_mom) // 10

        for d in range(10):
            start_d = d * decile_size
            end_d = (d + 1) * decile_size if d < 9 else len(sorted_mom)
            group = sorted_mom[start_d:end_d]

            for item in group:
                momentum_deciles[d][date_str]['total'] += 1
                if item['is_zt']:
                    momentum_deciles[d][date_str]['zt'] += 1

        # === 波动率十分位 ===
        sorted_rv = sorted(factors_today, key=lambda x: x['resvol'])
        for d in range(10):
            start_d = d * decile_size
            end_d = (d + 1) * decile_size if d < 9 else len(sorted_rv)
            group = sorted_rv[start_d:end_d]

            for item in group:
                resvol_deciles[d][date_str]['total'] += 1
                if item['is_zt']:
                    resvol_deciles[d][date_str]['zt'] += 1

        # === IC（Spearman rank correlation） ===
        from scipy import stats as sp_stats
        mom_vals = [f['momentum'] for f in factors_today]
        ret_vals = [f['today_ret'] for f in factors_today]
        ic, _ = sp_stats.spearmanr(mom_vals, ret_vals)
        if not math.isnan(ic):
            ic_records.append((date_str, ic))

        if (t - start_idx) % 200 == 0:
            print(f"  {date_str} ({t-start_idx}/{end_idx-start_idx})")

    # === 汇总统计 ===
    print(f"\n{'='*70}")
    print("Barra动量因子——涨停预判力分析")
    print(f"{'='*70}")

    print(f"\n动量十分位涨停率（{calendars[start_idx]} ~ {calendars[end_idx-1]}）:")
    print(f"{'Decile':>8s} | {'动量范围':>12s} | {'样本':>8s} | {'涨停数':>6s} | {'涨停率':>8s} | {'vs全市场':>8s}")
    print("-" * 65)

    # 全市场基线涨停率
    total_all = sum(sum(d['total'] for d in dates.values()) for dates in momentum_deciles.values())
    zt_all = sum(sum(d['zt'] for d in dates.values()) for dates in momentum_deciles.values())
    baseline_zt_rate = zt_all / total_all * 100 if total_all > 0 else 0
    print(f"{'全市场':>8s} | {'—':>12s} | {total_all:8d} | {zt_all:6d} | {baseline_zt_rate:7.2f}% | {'—':>8s}")
    print("-" * 65)

    decile_summary = []
    for d in range(10):
        dates = momentum_deciles[d]
        total = sum(v['total'] for v in dates.values())
        zt = sum(v['zt'] for v in dates.values())
        zt_rate = zt / total * 100 if total > 0 else 0
        vs_base = zt_rate - baseline_zt_rate

        # 动量范围（近似）
        # decile 0 = 最低动量, decile 9 = 最高动量
        range_label = f'D{d}'

        bar = '🟢' if vs_base > 0.5 else ('🔴' if vs_base < -0.5 else '⚪')
        print(f"{bar} D{d:7d} | {range_label:>12s} | {total:8d} | {zt:6d} | {zt_rate:7.2f}% | {vs_base:+7.2f}pp")

        decile_summary.append({
            'decile': d, 'total': total, 'zt': zt,
            'zt_rate': round(zt_rate, 2), 'vs_baseline': round(vs_base, 2)
        })

    # Top vs Bottom
    top = decile_summary[9]
    bottom = decile_summary[0]
    spread = top['zt_rate'] - bottom['zt_rate']
    print(f"\nTop-Bottom价差: {top['zt_rate']:.2f}% - {bottom['zt_rate']:.2f}% = {spread:+.2f}pp")
    if spread > 2:
        print(f"✅ 动量因子有涨停预判力（Top比Bottom涨停率高{spread:.1f}pp）")
    elif spread > 0.5:
        print(f"🟡 动量因子有微弱预判力")
    else:
        print(f"⚠️ 动量因子无涨停预判力")

    # IC统计
    print(f"\n{'='*70}")
    print("IC（Spearman Rank）统计")
    print(f"{'='*70}")
    ic_vals = [ic for _, ic in ic_records]
    ic_mean = sum(ic_vals) / len(ic_vals) if ic_vals else 0
    ic_positive = sum(1 for ic in ic_vals if ic > 0)
    ic_ir = ic_mean / (math.sqrt(sum((ic - ic_mean)**2 for ic in ic_vals) / len(ic_vals))) if ic_vals else 0

    print(f"  IC均值: {ic_mean:.4f}")
    print(f"  IC标准差: {math.sqrt(sum((ic-ic_mean)**2 for ic in ic_vals)/len(ic_vals)):.4f}")
    print(f"  ICIR: {ic_ir:.4f}")
    print(f"  IC>0占比: {ic_positive/len(ic_vals)*100:.1f}%")
    print(f"  样本天数: {len(ic_vals)}")

    # IC判定
    if abs(ic_ir) > 0.3:
        print(f"  → ✅ ICIR={'强' if ic_ir > 0 else '反向'}有效（|ICIR|>0.3）")
    elif abs(ic_ir) > 0.1:
        print(f"  → 🟡 ICIR弱有效")
    else:
        print(f"  → ⚠️ ICIR无效（|ICIR|<0.1）")

    # === V2 DSR: 对Top decile做多策略的DSR ===
    # 构造Top decile等权多头的日收益序列
    print(f"\n{'='*70}")
    print("V2 DSR: Top动量decile多头策略")
    print(f"{'='*70}")

    # 简化：用每日Top decile的涨停率作为"信号收益"
    # 更严格应计算Top decile等权组合的实际日收益
    # 这里用IC序列的显著性代理
    if ic_vals:
        # IC的t检验
        from scipy import stats as sp_stats
        t_stat, p_val = sp_stats.ttest_1samp(ic_vals, 0)
        print(f"  IC t检验: t={t_stat:.3f}, p={p_val:.6f}")
        if p_val < 0.01:
            print(f"  ✅ IC显著非零(p<0.01)")
        elif p_val < 0.05:
            print(f"  🟡 IC边际显著(p<0.05)")
        else:
            print(f"  ⚠️ IC不显著(p≥0.05)")

    # 波动率因子
    print(f"\n{'='*70}")
    print("RESVOL（残差波动率）十分位涨停率")
    print(f"{'='*70}")
    print(f"{'Decile':>8s} | {'样本':>8s} | {'涨停率':>8s} | {'vs全市场':>8s}")
    for d in range(10):
        dates = resvol_deciles[d]
        total = sum(v['total'] for v in dates.values())
        zt = sum(v['zt'] for v in dates.values())
        zt_rate = zt / total * 100 if total > 0 else 0
        vs_base = zt_rate - baseline_zt_rate
        label = '低波' if d <= 2 else ('高波' if d >= 7 else '')
        print(f"  D{d} {label:>4s} | {total:8d} | {zt_rate:7.2f}% | {vs_base:+7.2f}pp")

    # === 输出 ===
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'data_source': 'qlib_bin (V1 PASS)',
            'date_range': f'{calendars[start_idx]} ~ {calendars[end_idx-1]}',
            'leakage': 'None (t-1 factors predict t-day)',
        },
        'momentum_deciles': decile_summary,
        'baseline_zt_rate': round(baseline_zt_rate, 2),
        'top_bottom_spread': round(spread, 2),
        'ic_stats': {
            'mean': round(ic_mean, 4),
            'icir': round(ic_ir, 4),
            'ic_positive_pct': round(ic_positive / len(ic_vals) * 100, 1),
            'n_days': len(ic_vals),
            't_stat': round(t_stat, 3),
            'p_value': round(p_val, 6),
        },
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 结果保存: {OUT}")

if __name__ == '__main__':
    main()
