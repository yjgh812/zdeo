#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Barra因子多维度深度验证

5个维度并行测试：
  D1: 多窗口动量(5/10/20/60日)IC对比
  D2: RESVOL按市场regime分层IC
  D3: 分层筛选条件概率(剔除低分位→涨停率提升?)
  D4: 动量×AB信号交互效应
  D5: 非线性二值化(高动量=1/0 → 涨停率差异)

V1: qlib_bin PASS
V3: 仅用t-1因子预测t日
V4: 每个维度独立标注置信度
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
from validation_gate import precheck_data, verdict_label

QLIB = r'D:\workbuddyspace\qlib_data\qlib_bin'
OUT = r'D:\zcodesj\attribution\factor_multidim_result.json'

def load_calendar():
    with open(os.path.join(QLIB, 'calendars', 'day.txt'), 'r') as f:
        return [l.strip() for l in f if l.strip()]

def load_field_safe(stock, field):
    """带NaN过滤的数据加载（第4个qlib坑）"""
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

def spearman_ic(x, y):
    try:
        ic, _ = sp_stats.spearmanr(x, y)
        return ic if math.isfinite(ic) else float('nan')
    except Exception:
        return float('nan')

def pearson_ic(x, y):
    try:
        ic, _ = sp_stats.pearsonr(x, y)
        return ic if math.isfinite(ic) else float('nan')
    except Exception:
        return float('nan')

def main():
    print("=== V1 数据预检 ===")
    check = precheck_data('qlib_bin')
    assert check['verdict'] == 'PASS'
    print(f"✅ PASS")

    calendars = load_calendar()
    features_dir = os.path.join(QLIB, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])

    # 加载数据
    print(f"\n加载{len(all_stocks)}只股票...")
    stock_data = {}
    for i, stock in enumerate(all_stocks):
        closes = load_field_safe(stock, 'close')
        opens = load_field_safe(stock, 'open')
        if closes and opens and len(closes) > 60:
            stock_data[stock] = {'close': closes, 'open': opens}
        if (i+1) % 2000 == 0:
            print(f"  {i+1}/{len(all_stocks)}")
    print(f"成功: {len(stock_data)}只")

    start_idx = next(i for i, d in enumerate(calendars) if d >= '2020-01-01')
    end_idx = len(calendars) - 1

    # ============================================================
    # 预计算每天的个股因子值和涨跌状态
    # ============================================================
    print(f"\n预计算因子 ({calendars[start_idx]} ~ {calendars[end_idx-1]})...")

    # daily_factors[t] = [{stock, mom5, mom10, mom20, mom60, resvol5, resvol20, today_ret, is_zt, gap, prev_zt}]
    daily_factors = {}

    for t in range(start_idx + 60, end_idx):  # +60因为需要60日窗口
        date_str = calendars[t]
        factors_today = []

        for stock, data in stock_data.items():
            closes = data['close']
            opens = data['open']
            if t >= len(closes) or t < 60:
                continue

            # 多窗口动量
            def calc_mom(window_days):
                start = t - window_days
                if start < 0: return None
                c_start = closes[start]
                c_end = closes[t-1]  # t-1日的close（不含t日=V3防泄露）
                if c_start > 0 and c_end > 0:
                    return c_end / c_start - 1
                return None

            mom5 = calc_mom(5)
            mom10 = calc_mom(10)
            mom20 = calc_mom(20)
            mom60 = calc_mom(60)

            if mom20 is None:
                continue

            # 多窗口波动率
            def calc_resvol(window_days):
                start = max(0, t - window_days)
                window = closes[start:t]
                rets = []
                for j in range(1, len(window)):
                    if window[j-1] > 0 and window[j] > 0:
                        rets.append(window[j] / window[j-1] - 1)
                if len(rets) < 5:
                    return None
                mean_r = sum(rets) / len(rets)
                var_r = sum((r - mean_r)**2 for r in rets) / len(rets)
                return math.sqrt(var_r) if var_r > 0 else 0

            resvol5 = calc_resvol(5)
            resvol20 = calc_resvol(20)

            if resvol20 is None:
                continue

            # t日状态
            today_close = closes[t]
            today_open = opens[t]
            prev_close = closes[t-1]

            if not today_close or not prev_close or prev_close <= 0:
                continue

            today_ret = today_close / prev_close - 1
            threshold = get_zt_threshold(stock)
            is_zt = today_ret >= threshold

            # AB信号：前日涨停+竞价缺口
            prev_pct = prev_close / closes[t-2] - 1 if t >= 2 and closes[t-2] > 0 else 0
            prev_zt = prev_pct >= threshold
            gap = today_open / prev_close - 1 if today_open > 0 and prev_close > 0 else 0
            is_ab = prev_zt and (0.04 <= gap <= 0.07)

            factors_today.append({
                'stock': stock,
                'mom5': mom5, 'mom10': mom10, 'mom20': mom20, 'mom60': mom60,
                'resvol5': resvol5, 'resvol20': resvol20,
                'today_ret': today_ret, 'is_zt': is_zt,
                'prev_zt': prev_zt, 'gap': gap, 'is_ab': is_ab,
            })

        if len(factors_today) >= 100:
            daily_factors[t] = factors_today

        if (t - start_idx) % 300 == 0:
            print(f"  {date_str} ({t-start_idx}/{end_idx-start_idx})")

    print(f"有效交易日: {len(daily_factors)}天")

    # ============================================================
    # D1: 多窗口动量IC对比
    # ============================================================
    print(f"\n{'='*70}")
    print("D1: 多窗口动量IC对比")
    print(f"{'='*70}")

    mom_windows = ['mom5', 'mom10', 'mom20', 'mom60']
    d1_results = {}

    print(f"{'窗口':>8s} | {'IC均值':>8s} | {'ICIR':>8s} | {'t值':>7s} | {'p值':>10s} | {'IC>0%':>6s} | 判定")
    print("-" * 75)

    for win in mom_windows:
        ics = []
        for t, factors in daily_factors.items():
            vals = [(f[win], f['today_ret']) for f in factors if f[win] is not None]
            if len(vals) < 100:
                continue
            x = [v[0] for v in vals]
            y = [v[1] for v in vals]
            ic = pearson_ic(x, y)
            if not math.isnan(ic):
                ics.append(ic)

        if len(ics) < 30:
            continue

        mean_ic = sum(ics) / len(ics)
        std_ic = math.sqrt(sum((ic - mean_ic)**2 for ic in ics) / len(ics)) or 0.001
        icir = mean_ic / std_ic
        t_stat, p_val = sp_stats.ttest_1samp(ics, 0)
        pos_pct = sum(1 for ic in ics if ic > 0) / len(ics) * 100

        sig = '✅' if p_val < 0.01 else ('🟡' if p_val < 0.05 else '⚠️')
        print(f"{win:>8s} | {mean_ic:8.4f} | {icir:8.4f} | {t_stat:7.3f} | {p_val:10.6f} | {pos_pct:5.1f}% | {sig}")

        d1_results[win] = {
            'n_days': len(ics), 'ic_mean': round(mean_ic, 4),
            'icir': round(icir, 4), 't': round(t_stat, 3),
            'p': round(p_val, 6), 'pos_pct': round(pos_pct, 1),
            'significant': p_val < 0.05,
        }

    # ============================================================
    # D2: RESVOL按市场regime分层IC
    # ============================================================
    print(f"\n{'='*70}")
    print("D2: RESVOL按市场regime分层IC")
    print(f"{'='*70}")

    # 定义regime：当日全市场涨停数
    # >100 = 情绪热烈, 50-100 = 正常, <50 = 低迷
    regime_ic = {'热烈(>100涨停)': [], '正常(50-100)': [], '低迷(<50)': []}

    for t, factors in daily_factors.items():
        zt_count = sum(1 for f in factors if f['is_zt'])
        rv_vals = [f['resvol20'] for f in factors]
        ret_vals = [f['today_ret'] for f in factors]
        ic = pearson_ic(rv_vals, ret_vals)

        if math.isnan(ic):
            continue

        if zt_count > 100:
            regime_ic['热烈(>100涨停)'].append(ic)
        elif zt_count > 50:
            regime_ic['正常(50-100)'].append(ic)
        else:
            regime_ic['低迷(<50)'].append(ic)

    d2_results = {}
    print(f"{'Regime':>20s} | {'天数':>5s} | {'IC均值':>8s} | {'ICIR':>8s} | {'p值':>10s} | 判定")
    print("-" * 75)
    for regime, ics in regime_ic.items():
        if len(ics) < 10:
            print(f"{regime:>20s} | {len(ics):5d} | 样本不足")
            continue
        mean_ic = sum(ics) / len(ics)
        std_ic = math.sqrt(sum((ic - mean_ic)**2 for ic in ics) / len(ics)) or 0.001
        icir = mean_ic / std_ic
        t_stat, p_val = sp_stats.ttest_1samp(ics, 0)
        sig = '✅' if p_val < 0.05 else ('🟡' if p_val < 0.10 else '⚠️')
        print(f"{regime:>20s} | {len(ics):5d} | {mean_ic:8.4f} | {icir:8.4f} | {p_val:10.4f} | {sig}")
        d2_results[regime] = {
            'n_days': len(ics), 'ic_mean': round(mean_ic, 4),
            'icir': round(icir, 4), 'p': round(p_val, 4),
            'significant': p_val < 0.05,
        }

    # ============================================================
    # D3: 分层筛选条件概率（剔除低分位→涨停率提升?）
    # ============================================================
    print(f"\n{'='*70}")
    print("D3: 分层筛选条件概率")
    print(f"{'='*70}")

    # 策略：剔除RESVOL最低的N个分位，看剩余涨停率是否提升
    d3_results = {}

    # 全市场基线
    all_zt = sum(1 for t, factors in daily_factors.items() for f in factors if f['is_zt'])
    all_total = sum(len(factors) for factors in daily_factors.values())
    base_rate = all_zt / all_total * 100

    print(f"全市场涨停率基线: {base_rate:.2f}% (N={all_total})")
    print(f"\n{'筛选条件':>25s} | {'样本':>8s} | {'涨停率':>8s} | {'vs基线':>8s} | {'提升倍数':>8s}")
    print("-" * 70)

    # 按日计算十分位阈值，然后筛选
    for filter_desc, filter_func in [
        ('全市场(无筛选)', lambda f, t10, t50: True),
        ('剔除RESVOL最低10%', lambda f, t10, t50: f['resvol20'] > t10),
        ('剔除RESVOL最低30%', lambda f, t10, t50: f['resvol20'] > t50),  # 近似
        ('仅高波Top30%', lambda f, t10, t50: f['resvol20'] > t50),
        ('仅高动量Top30%', lambda f, t10, t50: f['mom20'] is not None and f['mom20'] > 0.05),
        ('高波+高动量', lambda f, t10, t50: f['resvol20'] > t50 and f['mom20'] is not None and f['mom20'] > 0.05),
    ]:
        zt_count = 0
        total = 0

        for t, factors in daily_factors.items():
            # 计算当日十分位阈值
            rvs = sorted([f['resvol20'] for f in factors])
            t10_val = rvs[len(rvs)//10] if len(rvs) > 10 else 0
            t50_val = rvs[len(rvs)*7//10] if len(rvs) > 10 else 0  # 70分位≈Top30%

            for f in factors:
                if filter_func(f, t10_val, t50_val):
                    total += 1
                    if f['is_zt']:
                        zt_count += 1

        if total > 0:
            rate = zt_count / total * 100
            vs_base = rate - base_rate
            multiplier = rate / base_rate if base_rate > 0 else 0
            print(f"{filter_desc:>25s} | {total:8d} | {rate:7.2f}% | {vs_base:+7.2f}pp | {multiplier:7.2f}x")
            d3_results[filter_desc] = {
                'total': total, 'zt': zt_count, 'zt_rate': round(rate, 2),
                'vs_baseline': round(vs_base, 2), 'multiplier': round(multiplier, 2),
            }

    # ============================================================
    # D4: 动量×AB信号交互效应
    # ============================================================
    print(f"\n{'='*70}")
    print("D4: 动量×AB信号交互效应")
    print(f"{'='*70}")

    d4_results = {}

    # 分组：AB候选 vs 非AB候选 × 高动量 vs 低动量
    groups = {
        'AB+高动量': {'total': 0, 'zt': 0, 'rets': []},
        'AB+低动量': {'total': 0, 'zt': 0, 'rets': []},
        '非AB+高动量': {'total': 0, 'zt': 0, 'rets': []},
        '非AB+低动量': {'total': 0, 'zt': 0, 'rets': []},
    }

    for t, factors in daily_factors.items():
        # 当日动量中位数
        moms = [f['mom20'] for f in factors if f['mom20'] is not None]
        if not moms:
            continue
        mom_median = sorted(moms)[len(moms)//2]

        for f in factors:
            is_high_mom = f['mom20'] is not None and f['mom20'] > mom_median
            is_ab = f['is_ab']

            if is_ab and is_high_mom:
                key = 'AB+高动量'
            elif is_ab and not is_high_mom:
                key = 'AB+低动量'
            elif not is_ab and is_high_mom:
                key = '非AB+高动量'
            else:
                key = '非AB+低动量'

            groups[key]['total'] += 1
            groups[key]['rets'].append(f['today_ret'])
            if f['is_zt']:
                groups[key]['zt'] += 1

    print(f"{'分组':>15s} | {'样本':>8s} | {'涨停率':>8s} | {'均收':>8s} | {'胜率':>6s}")
    print("-" * 60)
    for name, g in groups.items():
        if g['total'] > 0:
            rate = g['zt'] / g['total'] * 100
            avg_ret = sum(g['rets']) / len(g['rets']) * 100
            win_rate = sum(1 for r in g['rets'] if r > 0) / len(g['rets']) * 100
            print(f"{name:>15s} | {g['total']:8d} | {rate:7.2f}% | {avg_ret:+7.2f}% | {win_rate:5.1f}%")
            d4_results[name] = {
                'total': g['total'], 'zt': g['zt'],
                'zt_rate': round(rate, 2), 'avg_return': round(avg_ret, 2),
                'win_rate': round(win_rate, 1),
            }

    # AB内部动量差异显著性
    ab_high = groups['AB+高动量']['rets']
    ab_low = groups['AB+低动量']['rets']
    if len(ab_high) > 30 and len(ab_low) > 30:
        t_stat, p_val = sp_stats.ttest_ind(ab_high, ab_low, equal_var=False)
        print(f"\nAB内部 高动量 vs 低动量 Welch t检验: t={t_stat:.3f}, p={p_val:.4f}")
        if p_val < 0.05:
            print(f"✅ 动量在AB候选内有显著增量")
        else:
            print(f"⚠️ 动量在AB候选内无显著增量")
        d4_results['ab_momentum_ttest'] = {'t': round(t_stat,3), 'p': round(p_val,4)}

    # ============================================================
    # D5: 非线性二值化
    # ============================================================
    print(f"\n{'='*70}")
    print("D5: 非线性二值化（动量>阈值=1）")
    print(f"{'='*70}")

    d5_results = {}

    # 动量阈值二值化
    print(f"{'条件':>20s} | {'样本':>8s} | {'涨停率':>8s} | {'vs基线':>8s} | {'t检验p':>10s}")
    print("-" * 65)

    for threshold_desc, threshold_val in [
        ('mom20 > 0(正动量)', 0.0),
        ('mom20 > 5%', 0.05),
        ('mom20 > 10%', 0.10),
        ('mom20 > 20%', 0.20),
        ('mom20 > 50%', 0.50),
    ]:
        zt_hi = 0; total_hi = 0
        zt_lo = 0; total_lo = 0
        hi_rets = []; lo_rets = []

        for t, factors in daily_factors.items():
            for f in factors:
                if f['mom20'] is None: continue
                if f['mom20'] > threshold_val:
                    total_hi += 1
                    hi_rets.append(f['today_ret'])
                    if f['is_zt']: zt_hi += 1
                else:
                    total_lo += 1
                    lo_rets.append(f['today_ret'])
                    if f['is_zt']: zt_lo += 1

        if total_hi > 100 and total_lo > 100:
            rate_hi = zt_hi / total_hi * 100
            rate_lo = zt_lo / total_lo * 100
            # 涨停率差异的卡方检验
            contingency = [[zt_hi, total_hi - zt_hi], [zt_lo, total_lo - zt_lo]]
            chi2, p_chi, _, _ = sp_stats.chi2_contingency(contingency)
            sig = '✅' if p_chi < 0.01 else ('🟡' if p_chi < 0.05 else '⚠️')
            print(f"{threshold_desc:>20s} | {total_hi:8d} | {rate_hi:7.2f}% | {rate_hi-base_rate:+7.2f}pp | {p_chi:10.6f} {sig}")
            d5_results[threshold_desc] = {
                'total': total_hi, 'zt': zt_hi, 'zt_rate': round(rate_hi, 2),
                'vs_baseline': round(rate_hi - base_rate, 2),
                'lo_rate': round(rate_lo, 2), 'chi2_p': round(p_chi, 6),
                'significant': p_chi < 0.05,
            }

    # ============================================================
    # 汇总输出
    # ============================================================
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'data_source': 'qlib_bin (V1 PASS, NaN filtered)',
            'date_range': f'{calendars[start_idx]} ~ {calendars[end_idx-1]}',
            'valid_days': len(daily_factors),
            'total_observations': all_total,
            'baseline_zt_rate': round(base_rate, 2),
        },
        'd1_multi_window_momentum': d1_results,
        'd2_resvol_by_regime': d2_results,
        'd3_layered_screening': d3_results,
        'd4_momentum_ab_interaction': d4_results,
        'd5_nonlinear_binarization': d5_results,
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] {OUT}")

if __name__ == '__main__':
    main()
