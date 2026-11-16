"""
T1 封板真实回测（Bug 修复 + pos 扫描 + 分层重评 + 尾部风险）
=============================================================
🔧 修复两个方法论 bug：
  Bug 1: ret 分母 close0 → open1（消除溢价高估）
  Bug 2: ny 年化基准统一全样本跨度（消除稀疏子集天文级放大）

四组输出：
  1. bug_fix_compare.json  — 旧bug vs 修复后对比
  2. pos_sweep.json        — pos 多档扫描真实年化
  3. layers_fixed.json     — 修复后分层重评
  4. tail_risk.json        — 未触价尾部风险

作者: 知微
日期: 2026-08-12
"""
from __future__ import annotations
import sys
import json
import time
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

# 路径
T1_DIR = Path(r'D:\zcodesj\t1_research')
sys.path.insert(0, str(T1_DIR))

import tdx_common as tc

OUT_DIR = T1_DIR
TP_DEFAULT = 0.03  # +3% 条件单止盈（记忆最优档）
COST_DEFAULT = 0.003  # 0.3% 双边成本


# ============================================================
# 1. Bug 修复对比（旧 vs 新）
# ============================================================
def bug_fix_compare(ev_filtered: pd.DataFrame, full_span_years: float) -> Dict:
    """
    对比旧 bug（close0 分母 + 子集 ny）vs 修复后（open1 分母 + 全样本 ny）
    """
    print(f"\n{'='*60}")
    print(f"[1] Bug 修复对比（tp=+{TP_DEFAULT*100:.0f}% cost={COST_DEFAULT*100:.1f}%）")
    print(f"{'='*60}")

    # 旧 bug 版本
    d_old = tc.compute_tp_returns(ev_filtered, tp=TP_DEFAULT, cost=COST_DEFAULT,
                                   use_open1_denominator=False)
    r_old = tc.run_backtest_full(d_old, pos=1.0, ny_override=None, verbose=False)

    # 修复后版本
    d_new = tc.compute_tp_returns(ev_filtered, tp=TP_DEFAULT, cost=COST_DEFAULT,
                                   use_open1_denominator=True)
    r_new = tc.run_backtest_full(d_new, pos=1.0, ny_override=full_span_years, verbose=False)

    # 单笔统计
    print(f"\n  单笔统计（满仓 pos=1.0）：")
    print(f"    平均溢价 open1/close0-1 = {(d_new['open1']/d_new['close0']-1).mean()*100:.2f}%")
    print(f"    旧 bug avg_ret = {r_old['avg_ret']}%")
    print(f"    修复后 avg_ret = {r_new['avg_ret']}%")
    print(f"    差异 = {r_new['avg_ret'] - r_old['avg_ret']:+.3f}%/笔（应为负，消除高估）")

    print(f"\n  组合层（满仓 pos=1.0, 全样本 {full_span_years:.2f} 年）：")
    print(f"    {'指标':<12} {'旧bug':>12} {'修复后':>12} {'差异':>12}")
    for k in ['ann', 'monthly', 'mdd', 'win', 'cum']:
        old_v = r_old[k]
        new_v = r_new[k]
        diff = new_v - old_v
        print(f"    {k:<12} {old_v:>12.1f} {new_v:>12.1f} {diff:>+12.1f}")

    return {
        'tp': TP_DEFAULT, 'cost': COST_DEFAULT,
        'full_span_years': full_span_years,
        'n_trades': r_new['n'],
        'avg_premium_open_vs_close_pct': round(float((d_new['open1']/d_new['close0']-1).mean()*100), 2),
        'old_bug': r_old,
        'fixed': r_new,
        'per_year_fixed': r_new['per_year'],
    }


# ============================================================
# 2. pos 多档扫描
# ============================================================
def pos_sweep(d_new: pd.DataFrame, full_span_years: float) -> Dict:
    """
    pos 多档扫描，找年化/MDD 最优仓位
    """
    print(f"\n{'='*60}")
    print(f"[2] pos 多档扫描（tp=+{TP_DEFAULT*100:.0f}%）")
    print(f"{'='*60}")

    positions = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00]
    results = {}
    print(f"\n  {'pos':<8} {'年化%':>10} {'月化%':>10} {'MDD%':>10} {'胜率%':>8} {'avg%':>8} {'终值':>12}")
    for pos in positions:
        r = tc.run_backtest_full(d_new, pos=pos, ny_override=full_span_years, verbose=False)
        # 终值（10万本金）
        final_nav = tc.INIT_CAP * (1 + r['cum'] / 100)
        results[f'pos_{pos:.2f}'] = r
        print(f"  {pos:<8.2f} {r['ann']:>10.1f} {r['monthly']:>10.1f} {r['mdd']:>10.1f} "
              f"{r['win']:>8.1f} {r['avg_ret']:>8.3f} {final_nav:>12,.0f}")

    return results


# ============================================================
# 3. 分层重评（修复 ann bug）
# ============================================================
def layers_reeval(ev_filtered: pd.DataFrame, full_span_years: float) -> Dict:
    """
    修复 ann bug 后的分层重评
    分层维度：连板数 + 情绪 regime（复刻 t1_tp_layers.py 逻辑）
    """
    print(f"\n{'='*60}")
    print(f"[3] 分层重评（修复 ann bug，统一 ny={full_span_years:.2f}）")
    print(f"{'='*60}")

    d = tc.compute_tp_returns(ev_filtered, tp=TP_DEFAULT, cost=COST_DEFAULT,
                               use_open1_denominator=True).copy()
    if d.empty:
        return {}

    # 计算 streak（连板数）—— 复刻 t1_tp_layers.py:80-85
    d = d.sort_values(['code', 'date']).reset_index(drop=True)
    streak = np.zeros(len(d), dtype=int)
    # 需要每只股票的封板历史（含未触发条件的，简化：用事件本身的连续性）
    last_code = None
    last_date = None
    s = 0
    for i in range(len(d)):
        code = d.iloc[i]['code']
        date = d.iloc[i]['date']
        if code != last_code:
            s = 1
        else:
            # 同股连续事件（简化：日期间隔<=3 视为连板递增）
            try:
                d_prev = pd.Timestamp(last_date)
                d_cur = pd.Timestamp(date)
                if (d_cur - d_prev).days <= 5:
                    s += 1
                else:
                    s = 1
            except Exception:
                s = 1
        streak[i] = s
        last_code = code
        last_date = date
    d['streak'] = streak

    # 情绪 regime（全市场当日封板事件数中位数）
    zt_per_day = d.groupby('date')['code'].count()
    zt_median = zt_per_day.median()
    high_days = set(zt_per_day[zt_per_day >= zt_median].index)
    d['regime'] = np.where(d['date'].isin(high_days), 'high', 'low')

    # 分层定义
    groups = [
        ('全部', np.ones(len(d), dtype=bool)),
        ('连板1', d['streak'] == 1),
        ('连板2', d['streak'] == 2),
        ('连板3', d['streak'] == 3),
        ('连板4+', d['streak'] >= 4),
        ('情绪高', d['regime'] == 'high'),
        ('情绪低', d['regime'] == 'low'),
        ('连板2×情绪高', (d['streak'] == 2) & (d['regime'] == 'high')),
        ('连板1×情绪低', (d['streak'] == 1) & (d['regime'] == 'low')),
    ]

    results = {}
    print(f"\n  {'分层':<16} {'n':>6} {'胜率%':>8} {'年化%':>10} {'月化%':>10} {'MDD%':>8}")
    for label, mask in groups:
        sub = d[mask.values if hasattr(mask, 'values') else mask]
        if len(sub) == 0:
            continue
        r = tc.run_backtest_full(sub, pos=1.0, ny_override=full_span_years, verbose=False)
        results[label] = r
        print(f"  {label:<16} {r['n']:>6} {r['win']:>8.1f} {r['ann']:>10.1f} "
              f"{r['monthly']:>10.1f} {r['mdd']:>8.1f}")

    return results


# ============================================================
# 4. 尾部风险细查
# ============================================================
def tail_risk_analysis(d_new: pd.DataFrame) -> Dict:
    """
    未触价交易（45%）的亏损分布 + 黑天鹅识别
    """
    print(f"\n{'='*60}")
    print(f"[4] 尾部风险细查（未触价交易亏损分布）")
    print(f"{'='*60}")

    d = d_new.copy()
    hit = d['hit']
    miss = ~hit
    n_hit = int(hit.sum())
    n_miss = int(miss.sum())
    print(f"\n  触价交易: {n_hit} ({n_hit/len(d)*100:.1f}%)")
    print(f"  未触价: {n_miss} ({n_miss/len(d)*100:.1f}%) → 收盘卖")

    if n_miss == 0:
        return {'n_hit': n_hit, 'n_miss': 0}

    miss_ret = d.loc[miss, 'trade_ret']
    hit_ret = d.loc[hit, 'trade_ret']

    # 分布统计
    def pct(s, p):
        return float(s.quantile(p) * 100)

    stats = {
        'n_hit': n_hit,
        'n_miss': n_miss,
        'miss_avg_ret_pct': round(float(miss_ret.mean() * 100), 3),
        'miss_median_ret_pct': round(float(miss_ret.median() * 100), 3),
        'miss_p5_pct': round(pct(miss_ret, 0.05), 2),
        'miss_p25_pct': round(pct(miss_ret, 0.25), 2),
        'miss_p75_pct': round(pct(miss_ret, 0.75), 2),
        'miss_p95_pct': round(pct(miss_ret, 0.95), 2),
        'hit_avg_ret_pct': round(float(hit_ret.mean() * 100), 3),
        'hit_median_ret_pct': round(float(hit_ret.median() * 100), 3),
    }

    print(f"\n  触价交易收益: avg={stats['hit_avg_ret_pct']}% "
          f"median={stats['hit_median_ret_pct']}%")
    print(f"  未触价收益: avg={stats['miss_avg_ret_pct']}% "
          f"median={stats['miss_median_ret_pct']}%")
    print(f"  未触价分布: P5={stats['miss_p5_pct']}% P25={stats['miss_p25_pct']}% "
          f"P75={stats['miss_p75_pct']}% P95={stats['miss_p95_pct']}%")

    # 黑天鹅识别（亏 >5%）
    black_swan = d[d['trade_ret'] <= -0.05]
    stats['n_blackswan_gt5pct'] = int(len(black_swan))
    stats['blackswan_ratio_pct'] = round(len(black_swan) / len(d) * 100, 2)
    stats['blackswan_avg_ret_pct'] = round(float(black_swan['trade_ret'].mean() * 100), 2) \
                                     if len(black_swan) > 0 else 0
    print(f"\n  黑天鹅（亏≥5%）: {stats['n_blackswan_gt5pct']} 笔 "
          f"({stats['blackswan_ratio_pct']}%), "
          f"平均={stats['blackswan_avg_ret_pct']}%")

    # 尾部止损测试（仅对亏>X%的提前止损）
    print(f"\n  尾部止损敏感性测试（假设亏>X%时按 X% 止损）：")
    for stop in [-0.03, -0.05, -0.07, -0.10]:
        ret_capped = d['trade_ret'].clip(lower=stop)
        avg = ret_capped.mean() * 100
        win = (ret_capped > 0).mean() * 100
        delta = avg - d['trade_ret'].mean() * 100
        print(f"    止损 {stop*100:+.0f}%: avg={avg:.3f}% win={win:.1f}% Δavg={delta:+.3f}%")
        stats[f'stop_{stop}'] = {'avg_ret_pct': round(avg, 3),
                                  'win_pct': round(win, 1),
                                  'delta_avg': round(delta, 3)}

    return stats


# ============================================================
# 5. 主入口
# ============================================================
def main():
    t0 = time.time()
    print(f"T1 封板真实回测（Bug 修复版）")
    print(f"区间: {tc.START} ~ {tc.END}")
    print(f"{'='*60}")

    # 1. 加载事件
    ev = tc.scan_all_codes(start=tc.START, kind='sealed')
    ev_filtered = tc.apply_filters(ev, kind='sealed')
    print(f"[info] 过滤后封板非一字事件: {len(ev_filtered)}")

    if ev_filtered.empty:
        print("[FAIL] 无事件")
        return

    # 全样本跨度
    full_span_years = tc.compute_full_span_years(ev_filtered)
    print(f"[info] 全样本跨度: {full_span_years:.2f} 年")

    # 2. 四组分析
    # (1) Bug 修复对比
    compare = bug_fix_compare(ev_filtered, full_span_years)
    (OUT_DIR / 't1_bug_fix_compare.json').write_text(
        json.dumps(compare, ensure_ascii=False, indent=2), encoding='utf-8')

    # 修复后的收益数据（后续复用）
    d_new = tc.compute_tp_returns(ev_filtered, tp=TP_DEFAULT, cost=COST_DEFAULT,
                                   use_open1_denominator=True)

    # (2) pos 扫描
    sweep = pos_sweep(d_new, full_span_years)
    (OUT_DIR / 't1_pos_sweep.json').write_text(
        json.dumps(sweep, ensure_ascii=False, indent=2), encoding='utf-8')

    # (3) 分层重评
    layers = layers_reeval(ev_filtered, full_span_years)
    (OUT_DIR / 't1_layers_fixed.json').write_text(
        json.dumps(layers, ensure_ascii=False, indent=2), encoding='utf-8')

    # (4) 尾部风险
    tail = tail_risk_analysis(d_new)
    (OUT_DIR / 't1_tail_risk.json').write_text(
        json.dumps(tail, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"全部完成 ({elapsed:.0f}s)")
    print(f"输出: {OUT_DIR}")
    print(f"  - t1_bug_fix_compare.json")
    print(f"  - t1_pos_sweep.json")
    print(f"  - t1_layers_fixed.json")
    print(f"  - t1_tail_risk.json")


if __name__ == '__main__':
    main()
