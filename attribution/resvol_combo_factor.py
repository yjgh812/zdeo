#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RESVOL因子IC验证 + 动量×波动率组合因子回测

V1: qlib_bin PASS
V3: 仅用t-1因子预测t日

研究问题：
  1. RESVOL的IC是否显著？（十分位单调≠IC显著）
  2. 动量×波动率组合能否提升预判力？
  3. 因子在不同年份/市场环境下是否稳定？
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
from validation_gate import precheck_data

QLIB = r'D:\workbuddyspace\qlib_data\qlib_bin'
OUT = r'D:\zcodesj\attribution\resvol_combo_factor.json'

def load_calendar():
    with open(os.path.join(QLIB, 'calendars', 'day.txt'), 'r') as f:
        return [l.strip() for l in f if l.strip()]

def load_field(stock, field):
    fp = os.path.join(QLIB, 'features', stock, f'{field}.day.bin')
    if not os.path.exists(fp): return None
    with open(fp, 'rb') as f:
        n = os.path.getsize(fp) // 4
        raw = list(struct.unpack(f'{n}f', f.read()))
    # 过滤NaN/inf（qlib bin中float32可能有NaN占位）
    import math as _m
    return [v if (_m.isfinite(v) and v > 0) else 0.0 for v in raw]

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
    print(f"✅ PASS")

    calendars = load_calendar()
    features_dir = os.path.join(QLIB, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])

    print(f"\n加载{len(all_stocks)}只...")
    stock_closes = {}
    for i, stock in enumerate(all_stocks):
        closes = load_field(stock, 'close')
        if closes and len(closes) > 60:
            if closes[0] == 0:
                closes = closes[1:]
            stock_closes[stock] = closes
        if (i+1) % 2000 == 0:
            print(f"  {i+1}/{len(all_stocks)}")
    print(f"成功: {len(stock_closes)}只")

    start_idx = next(i for i, d in enumerate(calendars) if d >= '2020-01-01')
    end_idx = len(calendars) - 1

    # === 逐日计算因子+IC ===
    resvol_ic_by_year = defaultdict(list)   # 按年分组的IC
    momentum_ic_by_year = defaultdict(list)
    combo_ic_by_year = defaultdict(list)    # 组合因子IC

    # 组合因子的十分位涨停统计
    combo_deciles = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'zt': 0}))

    for t in range(start_idx + 21, end_idx):
        date_str = calendars[t]
        year = date_str[:4]

        factors_today = []
        for stock, closes in stock_closes.items():
            if t >= len(closes) or t < 21:
                continue
            window = closes[max(0, t-21):t]
            valid = [c for c in window if c and c > 0]
            if len(valid) < 15:
                continue
            if len(window) < 21 or not window[0] or window[0] <= 0:
                continue
            momentum = window[-1] / window[0] - 1

            rets = []
            for j in range(1, len(valid)):
                if valid[j-1] > 0:
                    rets.append(valid[j] / valid[j-1] - 1)
            if len(rets) < 10:
                continue
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r)**2 for r in rets) / len(rets)
            resvol = math.sqrt(var_r) if var_r > 0 else 0

            today_close = closes[t]
            prev_close = closes[t-1]
            if not today_close or not prev_close or prev_close <= 0:
                continue
            today_ret = today_close / prev_close - 1
            threshold = get_zt_threshold(stock)

            factors_today.append({
                'stock': stock,
                'momentum': momentum,
                'resvol': resvol,
                'today_ret': today_ret,
                'is_zt': today_ret >= threshold,
            })

        if len(factors_today) < 100:
            continue

        # 准备因子值列表（IC计算用）
        mom_vals = [f['momentum'] for f in factors_today]
        rv_vals = [f['resvol'] for f in factors_today]
        ret_vals = [f['today_ret'] for f in factors_today]

        # IC计算（用Pearson作为主，Spearman作为辅，两者都存）
        # Pearson对连续值更稳健，不容易因ties产生NaN
        try:
            ic_mom_s, _ = sp_stats.spearmanr(mom_vals, ret_vals)
        except Exception:
            ic_mom_s = float('nan')
        try:
            ic_mom_p, _ = sp_stats.pearsonr(mom_vals, ret_vals)
        except Exception:
            ic_mom_p = float('nan')
        try:
            ic_rv_s, _ = sp_stats.spearmanr(rv_vals, ret_vals)
        except Exception:
            ic_rv_s = float('nan')
        try:
            ic_rv_p, _ = sp_stats.pearsonr(rv_vals, ret_vals)
        except Exception:
            ic_rv_p = float('nan')

        # 优先用Pearson（对ties更稳健）
        ic_mom = ic_mom_p if not math.isnan(ic_mom_p) else ic_mom_s
        ic_rv = ic_rv_p if not math.isnan(ic_rv_p) else ic_rv_s

        if not math.isnan(ic_mom):
            momentum_ic_by_year[year].append(ic_mom)
        if not math.isnan(ic_rv):
            resvol_ic_by_year[year].append(ic_rv)

        # 组合因子：高动量+高波动 = 涨停候选？
        # 标准化后相加
        n = len(factors_today)
        mom_mean = sum(mom_vals) / n
        mom_std = math.sqrt(sum((v-mom_mean)**2 for v in mom_vals)/n) or 0.001
        rv_mean = sum(rv_vals) / n
        rv_std = math.sqrt(sum((v-rv_mean)**2 for v in rv_vals)/n) or 0.001

        for f in factors_today:
            f['combo'] = (f['momentum'] - mom_mean) / mom_std + (f['resvol'] - rv_mean) / rv_std

        combo_vals = [f['combo'] for f in factors_today]
        try:
            ic_combo_p, _ = sp_stats.pearsonr(combo_vals, ret_vals)
        except Exception:
            ic_combo_p = float('nan')
        try:
            ic_combo_s, _ = sp_stats.spearmanr(combo_vals, ret_vals)
        except Exception:
            ic_combo_s = float('nan')
        ic_combo = ic_combo_p if not math.isnan(ic_combo_p) else ic_combo_s
        if not math.isnan(ic_combo):
            combo_ic_by_year[year].append(ic_combo)

        # 组合因子十分位
        sorted_combo = sorted(factors_today, key=lambda x: x['combo'])
        decile_size = len(sorted_combo) // 10
        for d in range(10):
            s = d * decile_size
            e = (d+1) * decile_size if d < 9 else len(sorted_combo)
            for item in sorted_combo[s:e]:
                combo_deciles[d][date_str]['total'] += 1
                if item['is_zt']:
                    combo_deciles[d][date_str]['zt'] += 1

        if (t - start_idx) % 300 == 0:
            print(f"  {date_str} ({t-start_idx}/{end_idx-start_idx})")

    # === 输出结果 ===
    print(f"\n{'='*70}")
    print("按年度IC统计")
    print(f"{'='*70}")

    def ic_stats(ic_list):
        if not ic_list:
            return None
        mean = sum(ic_list) / len(ic_list)
        std = math.sqrt(sum((ic-mean)**2 for ic in ic_list)/len(ic_list)) or 0.001
        icir = mean / std
        t_stat, p_val = sp_stats.ttest_1samp(ic_list, 0)
        pos_pct = sum(1 for ic in ic_list if ic > 0) / len(ic_list) * 100
        return {
            'n': len(ic_list), 'mean': mean, 'std': std, 'icir': icir,
            't': t_stat, 'p': p_val, 'pos_pct': pos_pct
        }

    print(f"\n{'年份':>6s} | {'因子':>8s} | {'N':>5s} | {'IC均值':>8s} | {'ICIR':>8s} | {'t值':>7s} | {'p值':>10s} | {'IC>0%':>6s}")
    print("-" * 75)

    all_years = sorted(set(list(resvol_ic_by_year.keys()) + list(momentum_ic_by_year.keys()) + list(combo_ic_by_year.keys())))

    yearly_results = {}
    for year in all_years:
        yearly_results[year] = {}
        for name, ic_dict in [('动量', momentum_ic_by_year), ('RESVOL', resvol_ic_by_year), ('组合', combo_ic_by_year)]:
            ics = ic_dict.get(year, [])
            s = ic_stats(ics)
            if s:
                sig = '✅' if s['p'] < 0.05 else ('🟡' if s['p'] < 0.10 else '⚠️')
                print(f"{year:>6s} | {name:>8s} | {s['n']:5d} | {s['mean']:8.4f} | {s['icir']:8.4f} | {s['t']:7.3f} | {s['p']:10.4f} | {s['pos_pct']:5.1f}% {sig}")
                yearly_results[year][name] = {k: round(v,4) if isinstance(v,float) else v for k,v in s.items()}

    # 全样本IC
    print(f"\n{'='*70}")
    print("全样本IC汇总")
    print(f"{'='*70}")

    all_results = {}
    for name, ic_dict in [('动量', momentum_ic_by_year), ('RESVOL', resvol_ic_by_year), ('组合', combo_ic_by_year)]:
        all_ics = []
        for year_ics in ic_dict.values():
            all_ics.extend(year_ics)
        s = ic_stats(all_ics)
        if s:
            sig = '✅' if s['p'] < 0.05 else ('🟡' if s['p'] < 0.10 else '⚠️')
            print(f"\n{name}:")
            print(f"  全样本IC均值={s['mean']:.4f}, ICIR={s['icir']:.4f}")
            print(f"  t={s['t']:.3f}, p={s['p']:.6f}, IC>0占比={s['pos_pct']:.1f}%")
            print(f"  判定: {sig}")

            # 结论标签
            if s['p'] < 0.01 and abs(s['icir']) > 0.3:
                verdict = '✅ 因子有效'
            elif s['p'] < 0.05:
                verdict = '🟡 边际有效'
            else:
                verdict = '⚠️ 统计不显著'
            print(f"  裁决: {verdict}")
            all_results[name] = {**{k: round(v,4) if isinstance(v,float) else v for k,v in s.items()}, 'verdict': verdict}

    # 组合因子十分位
    print(f"\n{'='*70}")
    print("组合因子（动量+波动率）十分位涨停率")
    print(f"{'='*70}")

    total_all = sum(sum(d['total'] for d in dates.values()) for dates in combo_deciles.values())
    zt_all = sum(sum(d['zt'] for d in dates.values()) for dates in combo_deciles.values())
    base_rate = zt_all / total_all * 100 if total_all > 0 else 0

    combo_decile_summary = []
    print(f"{'Decile':>8s} | {'样本':>8s} | {'涨停率':>8s} | {'vs基线':>8s}")
    for d in range(10):
        dates = combo_deciles[d]
        total = sum(v['total'] for v in dates.values())
        zt = sum(v['zt'] for v in dates.values())
        rate = zt/total*100 if total > 0 else 0
        vs = rate - base_rate
        label = '低动量低波' if d == 0 else ('高动量高波' if d == 9 else '')
        print(f"  D{d} {label:>8s} | {total:8d} | {rate:7.2f}% | {vs:+7.2f}pp")
        combo_decile_summary.append({'decile': d, 'total': total, 'zt': zt, 'zt_rate': round(rate,2), 'vs_base': round(vs,2)})

    spread = combo_decile_summary[9]['zt_rate'] - combo_decile_summary[0]['zt_rate']
    print(f"\nTop-Bottom价差: {spread:.2f}pp")

    # 输出
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'data_source': 'qlib_bin (V1 PASS)',
            'date_range': f'{calendars[start_idx]} ~ {calendars[end_idx-1]}',
        },
        'all_sample_ic': all_results,
        'yearly_ic': yearly_results,
        'combo_deciles': combo_decile_summary,
        'baseline_zt_rate': round(base_rate, 2),
        'combo_top_bottom_spread': round(spread, 2),
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] {OUT}")

if __name__ == '__main__':
    main()
