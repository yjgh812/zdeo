#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T+1收益验证 + 联合因子最优化 + Regime动态策略

核心问题：
  D3发现"高波+高动量=3.12倍涨停率"，但涨停率高≠能赚钱
  必须验证T+1实际收益（开盘买→收盘卖）

三个模块：
  M1: T+1收益验证（各组因子条件下的实际盈亏）
  M2: 联合因子最优化（5日动量×RESVOL最佳阈值组合）
  M3: Regime动态策略（主升期高波 vs 退潮期低波的T+1收益对比）

V1: qlib_bin PASS
V3: 仅用t-1因子，t日开盘买t日收盘卖
V2: 每组计算DSR
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
OUT = r'D:\zcodesj\attribution\t1_profit_verification.json'

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
        'median_pct': round(sorted(returns)[n//2] * 100, 2),
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
    # 预计算：每天的t-1因子值 + t日T+1收益（开盘买→收盘卖）
    # ============================================================
    print(f"\n预计算 ({calendars[start_idx]} ~ {calendars[end_idx-1]})...")

    # daily_records[t] = [{stock, mom5, mom20, resvol5, resvol20, t1_ret, is_zt, market_zt_count}]
    daily_records = {}

    for t in range(start_idx + 21, end_idx):
        date_str = calendars[t]
        records = []
        market_zt = 0

        for stock, data in stock_data.items():
            closes = data['close']
            opens = data['open']
            if t >= len(closes) or t < 21:
                continue

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

            rv5 = resvol(5)
            rv20 = resvol(20)
            if rv20 is None:
                continue

            # t日T+1收益（V3：用t-1因子预测t日，开盘买收盘卖）
            today_open = opens[t]
            today_close = closes[t]
            if today_open <= 0 or today_close <= 0:
                continue
            t1_ret = today_close / today_open - 1

            # t日涨停
            prev_close = closes[t-1]
            threshold = get_zt_threshold(stock)
            is_zt = today_close / prev_close - 1 >= threshold if prev_close > 0 else False
            if is_zt:
                market_zt += 1

            records.append({
                'mom5': mom5, 'mom20': mom20,
                'rv5': rv5, 'rv20': rv20,
                't1_ret': t1_ret, 'is_zt': is_zt,
            })

        if len(records) >= 100:
            daily_records[t] = {'records': records, 'market_zt': market_zt, 'date': date_str}

        if (t - start_idx) % 300 == 0:
            print(f"  {date_str} ({t-start_idx}/{end_idx-start_idx})")

    print(f"有效交易日: {len(daily_records)}")

    # ============================================================
    # M1: T+1收益验证（核心！）
    # ============================================================
    print(f"\n{'='*70}")
    print("M1: T+1收益验证（开盘买→收盘卖）")
    print(f"{'='*70}")

    # 定义筛选条件组
    conditions = {
        '全市场基线': lambda r: True,
        '高波Top30%': None,  # 动态阈值，下面处理
        '高动量Top30%(mom5)': None,
        '高波+高mom5': None,
        '高波+高mom20': None,
        'mom5>5%': lambda r: r['mom5'] > 0.05,
        'mom20>20%': lambda r: r['mom20'] > 0.20,
        'mom20>50%': lambda r: r['mom20'] > 0.50,
        'mom5>5%+高波': None,
    }

    m1_results = {}

    # 基线
    all_rets = [r['t1_ret'] for t in daily_records for r in daily_records[t]['records']]
    s = calc_stats(all_rets)
    dsr_base = require_dsr(all_rets, n_trials=10)
    print(f"\n{'条件':>22s} | {'N':>8s} | {'胜率':>6s} | {'均收':>8s} | {'PF':>5s} | {'中位':>8s} | {'DSR':>6s} | 判定")
    print("-" * 90)
    print(f"{'全市场基线':>22s} | {s['n']:8d} | {s['win_rate']:5.1f}% | {s['avg_return_pct']:+7.2f}% | {str(s.get('pf','?')):>5s} | {s['median_pct']:+7.2f}% | {dsr_base['dsr']:5.2f} | {dsr_base['verdict']}")
    m1_results['全市场基线'] = {**s, 'dsr': dsr_base['dsr'], 'verdict': dsr_base['verdict']}

    # 动态阈值组
    for cond_name in ['高波Top30%', '高动量Top30%(mom5)', '高波+高mom5', '高波+高mom20', 'mom5>5%+高波']:
        rets = []
        for t in daily_records:
            records = daily_records[t]['records']
            # 计算当日70分位阈值
            rvs = sorted([r['rv20'] for r in records])
            rv_threshold = rvs[int(len(rvs)*0.7)] if len(rvs) > 10 else 999

            moms5 = sorted([r['mom5'] for r in records])
            mom5_threshold = moms5[int(len(moms5)*0.7)] if len(moms5) > 10 else 999

            for r in records:
                if cond_name == '高波Top30%' and r['rv20'] > rv_threshold:
                    rets.append(r['t1_ret'])
                elif cond_name == '高动量Top30%(mom5)' and r['mom5'] > mom5_threshold:
                    rets.append(r['t1_ret'])
                elif cond_name == '高波+高mom5' and r['rv20'] > rv_threshold and r['mom5'] > mom5_threshold:
                    rets.append(r['t1_ret'])
                elif cond_name == '高波+高mom20' and r['rv20'] > rv_threshold and r['mom20'] > 0.05:
                    rets.append(r['t1_ret'])
                elif cond_name == 'mom5>5%+高波' and r['mom5'] > 0.05 and r['rv20'] > rv_threshold:
                    rets.append(r['t1_ret'])

        if len(rets) > 100:
            s = calc_stats(rets)
            dsr = require_dsr(rets, n_trials=10)
            label, _ = verdict_label(dsr['dsr'], direction='positive')
            print(f"{cond_name:>22s} | {s['n']:8d} | {s['win_rate']:5.1f}% | {s['avg_return_pct']:+7.2f}% | {str(s.get('pf','?')):>5s} | {s['median_pct']:+7.2f}% | {dsr['dsr']:5.2f} | {label}")
            m1_results[cond_name] = {**s, 'dsr': dsr['dsr'], 'verdict': label}

    # 固定阈值组
    for cond_name, cond_func in conditions.items():
        if cond_func is None:
            continue
        rets = [r['t1_ret'] for t in daily_records for r in daily_records[t]['records'] if cond_func(r)]
        if len(rets) > 100:
            s = calc_stats(rets)
            dsr = require_dsr(rets, n_trials=10)
            label, _ = verdict_label(dsr['dsr'], direction='positive')
            print(f"{cond_name:>22s} | {s['n']:8d} | {s['win_rate']:5.1f}% | {s['avg_return_pct']:+7.2f}% | {str(s.get('pf','?')):>5s} | {s['median_pct']:+7.2f}% | {dsr['dsr']:5.2f} | {label}")
            m1_results[cond_name] = {**s, 'dsr': dsr['dsr'], 'verdict': label}

    # ============================================================
    # M2: 联合因子最优化（5日动量阈值 × RESVOL阈值 网格搜索）
    # ============================================================
    print(f"\n{'='*70}")
    print("M2: 联合因子最优化（mom5 × rv20分位 网格搜索）")
    print(f"{'='*70}")

    mom5_thresholds = [0, 0.02, 0.05, 0.10]
    rv_percentiles = [0, 50, 70, 80, 90]  # 0=全部, 50=Top50%, 70=Top30%, 80=Top20%, 90=Top10%

    print(f"{'mom5阈值':>10s} | {'rv分位':>8s} | {'N':>8s} | {'均收':>8s} | {'胜率':>6s} | {'PF':>5s}")
    print("-" * 60)

    m2_grid = []
    best_pf = 0
    best_combo = None

    for mt in mom5_thresholds:
        for rp in rv_percentiles:
            rets = []
            for t in daily_records:
                records = daily_records[t]['records']
                rvs = sorted([r['rv20'] for r in records])
                rv_thresh = rvs[int(len(rvs)*rp/100)] if rp > 0 else 0

                for r in records:
                    if r['mom5'] > mt and (rp == 0 or r['rv20'] > rv_thresh):
                        rets.append(r['t1_ret'])

            if len(rets) > 200:
                s = calc_stats(rets)
                pf_val = s.get('pf', 0) or 0
                m2_grid.append({
                    'mom5_thresh': mt, 'rv_percentile': rp,
                    **s
                })
                mt_label = f'>{mt*100:.0f}%' if mt > 0 else '全部'
                rp_label = f'Top{100-rp}%' if rp > 0 else '全部'
                print(f"{mt_label:>10s} | {rp_label:>8s} | {s['n']:8d} | {s['avg_return_pct']:+7.2f}% | {s['win_rate']:5.1f}% | {str(s.get('pf','?')):>5s}")

                if pf_val > best_pf and s['avg_return_pct'] > 0:
                    best_pf = pf_val
                    best_combo = (mt, rp, s)

    if best_combo:
        print(f"\n⭐ 最佳组合: mom5>{best_combo[0]*100:.0f}% + rv20 Top{100-best_combo[1]}%")
        print(f"   N={best_combo[2]['n']}, 均收={best_combo[2]['avg_return_pct']:+.2f}%, PF={best_combo[2].get('pf','?')}")

    # ============================================================
    # M3: Regime动态策略
    # ============================================================
    print(f"\n{'='*70}")
    print("M3: Regime动态策略T+1收益")
    print(f"{'='*70}")

    # 分regime
    regime_rets = {
        '热烈(>100涨停)': {'high_rv': [], 'low_rv': [], 'all': []},
        '正常(50-100)': {'high_rv': [], 'low_rv': [], 'all': []},
        '低迷(<50)': {'high_rv': [], 'low_rv': [], 'all': []},
    }

    for t in daily_records:
        records = daily_records[t]['records']
        mzt = daily_records[t]['market_zt']

        if mzt > 100: regime = '热烈(>100涨停)'
        elif mzt > 50: regime = '正常(50-100)'
        else: regime = '低迷(<50)'

        rvs = sorted([r['rv20'] for r in records])
        rv_median = rvs[len(rvs)//2] if len(rvs) > 10 else 0

        for r in records:
            regime_rets[regime]['all'].append(r['t1_ret'])
            if r['rv20'] > rv_median:
                regime_rets[regime]['high_rv'].append(r['t1_ret'])
            else:
                regime_rets[regime]['low_rv'].append(r['t1_ret'])

    m3_results = {}
    print(f"{'Regime':>15s} | {'组别':>8s} | {'N':>8s} | {'均收':>8s} | {'胜率':>6s} | {'PF':>5s}")
    print("-" * 60)
    for regime, groups in regime_rets.items():
        for grp_name, rets in groups.items():
            if len(rets) > 100:
                s = calc_stats(rets)
                print(f"{regime:>15s} | {grp_name:>8s} | {s['n']:8d} | {s['avg_return_pct']:+7.2f}% | {s['win_rate']:5.1f}% | {str(s.get('pf','?')):>5s}")
                m3_results[f'{regime}_{grp_name}'] = s

    # Regime策略收益对比：主升期选高波 vs 退潮期选低波
    print(f"\n--- Regime动态策略对比 ---")
    hot_high = regime_rets['热烈(>100涨停)']['high_rv']
    cold_low = regime_rets['低迷(<50)']['low_rv']
    if hot_high and cold_low:
        s_hot = calc_stats(hot_high)
        s_cold = calc_stats(cold_low)
        print(f"主升期+高波: N={s_hot['n']}, 均收={s_hot['avg_return_pct']:+.2f}%, PF={s_hot.get('pf')}")
        print(f"退潮期+低波: N={s_cold['n']}, 均收={s_cold['avg_return_pct']:+.2f}%, PF={s_cold.get('pf')}")

    # ============================================================
    # 输出
    # ============================================================
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'data_source': 'qlib_bin (V1 PASS, NaN filtered)',
            'valid_days': len(daily_records),
            'note': 'T+1收益=开盘买→收盘卖',
        },
        'm1_t1_profit': m1_results,
        'm2_optimization_grid': m2_grid,
        'm2_best_combo': {
            'mom5_thresh': best_combo[0],
            'rv_percentile': best_combo[1],
            'stats': best_combo[2],
        } if best_combo else None,
        'm3_regime_strategy': m3_results,
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] {OUT}")

if __name__ == '__main__':
    main()
