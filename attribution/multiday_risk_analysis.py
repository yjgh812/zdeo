#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多日持仓 + 风险调整分析

核心问题：
  T+1策略成本0.4%/天太高，但如果因子有效期>1天，多日持仓可摊薄成本。
  同时Top10策略年化194%如果回撤80%也没有意义。

验证：
  H1: 不同持仓期(1/2/3/5日)的因子alpha（全样本，非Top-N）
  H2: Top10策略的净值曲线/最大回撤/夏普/卡玛
  H3: 全样本因子的多日alpha（彻底检验因子有效性）
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
OUT = r'D:\zcodesj\attribution\multiday_risk_analysis.json'

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

    # ============================================================
    # 预计算：每天的因子值 + 后续N日收益
    # ============================================================
    print(f"\n预计算因子+多日收益...")

    daily = {}  # t -> {records: [{stock, mom5, rv20, ret_1d, ret_2d, ret_3d, ret_5d}]}

    for t in range(start_idx + 21, end_idx - 5):  # -5因为需要t+5日收益
        date_str = calendars[t]
        records = []

        for stock, data in stock_data.items():
            closes = data['close']
            opens = data['open']
            if t >= len(closes) - 5 or t < 21:
                continue

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

            # 前日收盘价（用于过滤异常跳空）
            prev_close = closes[t-1] if t >= 1 and closes[t-1] > 0 else 0

            # 多日收益（从t日开盘到t+N-1日收盘）
            # 1日 = t日开盘到t日收盘（日内）
            # 2日 = t日开盘到t+1日收盘（跨2天）
            # 5日 = t日开盘到t+4日收盘
            entry_price = opens[t]
            if entry_price <= 0:
                continue

            # 关键过滤：排除极端收益（新上市/停牌复牌等数据异常）
            # 如果t日开盘价相对前日收盘价偏离>20%，可能是数据异常
            if prev_close > 0:
                day_gap = abs(entry_price / prev_close - 1)
                if day_gap > 0.20:
                    continue

            rets = {}
            for n_days in [1, 2, 3, 5]:
                exit_idx = t + n_days - 1  # 修正：1日的exit是t日（同一天收盘）
                if exit_idx < len(closes) and closes[exit_idx] > 0:
                    r = closes[exit_idx] / entry_price - 1
                    # 排除极端收益（>50%或<-50%）
                    if abs(r) > 0.50:
                        rets[n_days] = None
                    else:
                        rets[n_days] = r
                else:
                    rets[n_days] = None

            if rets[1] is None:
                continue

            # 开盘涨停（流动性约束）
            prev_close = closes[t-1]
            limit_up = prev_close * (1 + get_zt_threshold(stock) + 0.005)
            open_at_limit = entry_price >= limit_up

            records.append({
                'stock': stock,
                'mom5': mom5, 'rv20': rv20,
                'ret_1d': rets[1],
                'ret_2d': rets[2],
                'ret_3d': rets[3],
                'ret_5d': rets[5],
                'open_at_limit': open_at_limit,
            })

        if len(records) >= 100:
            daily[t] = {'records': records, 'date': date_str}

        if (t - start_idx) % 400 == 0:
            print(f"  {date_str}")

    print(f"有效交易日: {len(daily)}")

    COST_PER_TRADE = 0.003
    SLIPPAGE = 0.001
    TOTAL_COST = COST_PER_TRADE + SLIPPAGE  # 单边0.4%

    # ============================================================
    # H1: 不同持仓期的因子alpha（全样本，不Top-N）
    # ============================================================
    print(f"\n{'='*70}")
    print("H1: 多日持仓因子alpha（全样本~500只/日）")
    print(f"{'='*70}")

    h1_results = {}

    # 对每个持仓期，计算因子筛选组 vs 全市场基线的收益
    for hold_days in [1, 2, 3, 5]:
        ret_key = f'ret_{hold_days}d'

        # 全市场基线
        base_rets = []
        factor_rets = []

        for t in daily:
            records = daily[t]['records']
            rvs = sorted([r['rv20'] for r in records])
            rv90 = rvs[int(len(rvs)*0.9)] if len(rvs) > 10 else 999

            for r in records:
                if r[ret_key] is not None:
                    base_rets.append(r[ret_key])
                    if r['mom5'] > 0 and r['rv20'] > rv90 and not r['open_at_limit']:
                        factor_rets.append(r[ret_key])

        # 扣成本：多日持仓只扣一次买入+一次卖出
        cost = TOTAL_COST * 2  # 买卖双边
        base_net = [r - cost for r in base_rets]
        factor_net = [r - cost for r in factor_rets]

        n_base = len(base_net)
        n_factor = len(factor_net)
        avg_base = sum(base_net) / n_base if n_base > 0 else 0
        avg_factor = sum(factor_net) / n_factor if n_factor > 0 else 0

        # DSR
        dsr_factor = require_dsr(factor_net, n_trials=10) if n_factor > 30 else None
        dsr_base = require_dsr(base_net, n_trials=10) if n_base > 30 else None

        # alpha = 因子组 - 基线
        alpha = avg_factor - avg_base

        # 年化（按持仓期换手）
        trades_per_year = 252 / hold_days
        annual_factor = ((1 + avg_factor) ** trades_per_year - 1) * 100 if avg_factor > -1 else -100
        annual_base = ((1 + avg_base) ** trades_per_year - 1) * 100 if avg_base > -1 else -100

        print(f"\n  持仓{hold_days}日:")
        print(f"    因子组: N={n_factor}, 均{hold_days}日收益={avg_factor*100:+.3f}%, 年化={annual_factor:+.1f}%, DSR={dsr_factor['dsr'] if dsr_factor else '?'}")
        print(f"    基线组: N={n_base}, 均{hold_days}日收益={avg_base*100:+.3f}%, 年化={annual_base:+.1f}%, DSR={dsr_base['dsr'] if dsr_base else '?'}")
        print(f"    Alpha(因子-基线): {alpha*100:+.3f}%/{hold_days}日")

        h1_results[hold_days] = {
            'factor': {'n': n_factor, 'avg_ret': round(avg_factor*100, 3),
                       'annual': round(annual_factor, 1),
                       'dsr': dsr_factor['dsr'] if dsr_factor else None},
            'baseline': {'n': n_base, 'avg_ret': round(avg_base*100, 3),
                         'annual': round(annual_base, 1),
                         'dsr': dsr_base['dsr'] if dsr_base else None},
            'alpha_per_period_pct': round(alpha * 100, 3),
        }

    # ============================================================
    # H2: Top10策略净值曲线 + 风险指标
    # ============================================================
    print(f"\n{'='*70}")
    print("H2: Top10策略净值曲线与风险指标")
    print(f"{'='*70}")

    # T+1 Top10策略净值
    daily_returns_top10 = []
    dates_used = []

    for t in sorted(daily.keys()):
        records = daily[t]['records']
        rvs = sorted([r['rv20'] for r in records])
        rv90 = rvs[int(len(rvs)*0.9)] if len(rvs) > 10 else 999

        candidates = [r for r in records if r['mom5'] > 0 and r['rv20'] > rv90
                      and not r['open_at_limit']]
        candidates = sorted(candidates, key=lambda x: x['rv20'], reverse=True)[:10]

        if candidates:
            gross = sum(r['ret_1d'] for r in candidates) / len(candidates)
            net = gross - TOTAL_COST * 2
            daily_returns_top10.append(net)
            dates_used.append(daily[t]['date'])

    # 计算净值曲线
    nav = [1.0]
    for r in daily_returns_top10:
        nav.append(nav[-1] * (1 + r))

    # 风险指标
    n = len(daily_returns_top10)
    mean_r = sum(daily_returns_top10) / n
    var_r = sum((r - mean_r) ** 2 for r in daily_returns_top10) / n
    std_r = math.sqrt(var_r)

    # 夏普（年化，无风险利率=0）
    sharpe = mean_r / std_r * math.sqrt(252) if std_r > 0 else 0

    # 最大回撤
    peak = nav[0]
    max_dd = 0
    max_dd_date_idx = 0
    for i, v in enumerate(nav):
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
            max_dd_date_idx = i

    # 卡玛比率
    annual_return = (nav[-1] ** (252 / n) - 1) if nav[-1] > 0 else -1
    calmar = annual_return / max_dd if max_dd > 0 else 0

    # 胜率
    wins = sum(1 for r in daily_returns_top10 if r > 0)
    win_rate = wins / n * 100

    # 月度收益
    monthly_returns = defaultdict(float)
    for i, r in enumerate(daily_returns_top10):
        month = dates_used[i][:7]
        monthly_returns[month] += r

    positive_months = sum(1 for v in monthly_returns.values() if v > 0)
    total_months = len(monthly_returns)

    print(f"\n  Top10 T+1策略净值分析:")
    print(f"    交易日: {n}")
    print(f"    最终净值: {nav[-1]:.2f} (7年)")
    print(f"    年化收益: {annual_return*100:+.1f}%")
    print(f"    夏普比率: {sharpe:.2f}")
    print(f"    最大回撤: {max_dd*100:.1f}%")
    print(f"    卡玛比率: {calmar:.2f}")
    print(f"    日胜率: {win_rate:.1f}%")
    print(f"    月胜率: {positive_months}/{total_months} = {positive_months/total_months*100:.1f}%")

    # 月度收益分布
    monthly_vals = list(monthly_returns.values())
    best_month = max(monthly_vals) * 100
    worst_month = min(monthly_vals) * 100
    median_month = sorted(monthly_vals)[len(monthly_vals)//2] * 100
    print(f"    最佳月: {best_month:+.1f}%, 最差月: {worst_month:+.1f}%, 中位月: {median_month:+.1f}%")

    # 年度收益
    yearly_returns = defaultdict(float)
    for i, r in enumerate(daily_returns_top10):
        year = dates_used[i][:4]
        yearly_returns[year] += r

    print(f"\n    年度收益:")
    for year in sorted(yearly_returns.keys()):
        yr = yearly_returns[year]
        # 转年化近似
        print(f"      {year}: {yr*100:+.1f}%")

    h2_results = {
        'nav_final': round(nav[-1], 2),
        'annual_return_pct': round(annual_return * 100, 1),
        'sharpe': round(sharpe, 2),
        'max_drawdown_pct': round(max_dd * 100, 1),
        'calmar': round(calmar, 2),
        'win_rate_daily_pct': round(win_rate, 1),
        'positive_months': positive_months,
        'total_months': total_months,
        'best_month_pct': round(best_month, 1),
        'worst_month_pct': round(worst_month, 1),
        'yearly_returns_pct': {y: round(v*100, 1) for y, v in yearly_returns.items()},
    }

    # ============================================================
    # 输出
    # ============================================================
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'cost_per_round_pct': TOTAL_COST * 2 * 100,
            'valid_days': len(daily),
        },
        'h1_multiday_alpha': h1_results,
        'h2_top10_risk_metrics': h2_results,
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] {OUT}")

if __name__ == '__main__':
    main()
