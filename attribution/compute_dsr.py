#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deflated Sharpe Ratio (DSR) 统计检验
对R6/R1/R8三条规则的回测结果做多重检验校正，
判断优化效果是否在统计上显著（而非multiple testing的虚假发现）。

方法：Bailey & López de Prado (2014)
  DSR = Z[(SR_hat - SR0)×√(T-1) / √(1 - γ₃×SR + (γ₄-1)/4×SR²)]
  SR0 = √V[SR_n]×[(1-γ)×Z⁻¹(1-1/N) + γ×Z⁻¹(1-1/(N×e))]

输出：D:/zcodesj/attribution/dsr_validation.json + 控制台报告
"""
import json
import math
import os
from datetime import datetime
from scipy import stats

OUT_PATH = r'D:\zcodesj\attribution\dsr_validation.json'

# Euler-Mascheroni常数
GAMMA_EM = 0.5772156649015329

# ============================================================
# DSR核心计算
# ============================================================

def norm_cdf(x):
    """标准正态CDF"""
    return stats.norm.cdf(x)

def norm_ppf(p):
    """标准正态分位数函数（逆CDF）"""
    return stats.norm.ppf(p)

def compute_sr0(n_trials, sr_variance):
    """
    计算N次零技能试验的期望最大夏普比率（SR0）
    SR0 ≈ √V[SR_n] × [(1-γ)×Z⁻¹(1-1/N) + γ×Z⁻¹(1-1/(N×e))]
    """
    if n_trials <= 1:
        return 0.0

    sqrt_v = math.sqrt(sr_variance) if sr_variance > 0 else 0

    # Z⁻¹(1-1/N)
    term1 = norm_ppf(1 - 1.0 / n_trials)

    # Z⁻¹(1-1/(N×e))
    term2 = norm_ppf(1 - 1.0 / (n_trials * math.e))

    sr0 = sqrt_v * ((1 - GAMMA_EM) * term1 + GAMMA_EM * term2)
    return sr0

def compute_dsr(sr_hat, sr0, T, skewness, kurtosis):
    """
    计算Deflated Sharpe Ratio
    DSR = Z[(SR_hat - SR0)×√(T-1) / √(1 - γ₃×SR + (γ₄-1)/4×SR²)]
    """
    if T <= 1:
        return 0.0

    numerator = (sr_hat - sr0) * math.sqrt(T - 1)

    # 分母：非正态调整项
    denom_sq = 1 - skewness * sr_hat + (kurtosis - 1) / 4.0 * sr_hat ** 2
    denom_sq = max(denom_sq, 0.01)  # 防止负值/零
    denominator = math.sqrt(denom_sq)

    z_score = numerator / denominator
    dsr = norm_cdf(z_score)
    return dsr

def compute_psr(sr_hat, sr_benchmark, T, skewness, kurtosis):
    """Probabilistic Sharpe Ratio（SR0=固定基准）"""
    return compute_dsr(sr_hat, sr_benchmark, T, skewness, kurtosis)

def annualized_to_daily_sr(annual_sr, trading_days=252):
    """年化夏普转日频"""
    return annual_sr / math.sqrt(trading_days)

# ============================================================
# 从回测结果提取收益序列统计量
# ============================================================

def load_backtest_returns():
    """
    从回测验证数据中提取各组策略的每笔交易收益率，
    用于计算夏普/偏度/峰度。
    """
    backtest_path = r'D:\zcodesj\attribution\回测验证数据.json'
    with open(backtest_path, 'r', encoding='utf-8') as f:
        bt = json.load(f)

    strategies = {}

    # R6各组：提取每笔T+1收益
    for key in ['ab_4_7', 'wide_0_10', 'low_2_4', 'high_7_10']:
        data = bt.get('r6_ab_gap', {}).get(key, {})
        trades = data.get('trades', [])
        # 注意：JSON里没存trades明细，只有统计量，需从meta推算
        # 改为直接用统计量
        n = data.get('n', 0)
        if n > 0:
            strategies[f'R6_{key}'] = {
                'n': n,
                'avg_return': data.get('avg_return_pct', 0) / 100,
                'win_rate': data.get('win_rate', 0) / 100,
                'label': data.get('label', key),
                'pf': data.get('profit_factor'),
                'median_return': data.get('median_return_pct', 0) / 100,
            }

    # R1各组
    for key in ['with_stop', 'without_stop']:
        data = bt.get('r1_stoploss', {}).get(key, {})
        n = data.get('n', 0)
        if n > 0:
            strategies[f'R1_{key}'] = {
                'n': n,
                'avg_return': data.get('avg_return_pct', 0) / 100,
                'win_rate': data.get('win_rate', 0) / 100,
                'pf': data.get('profit_factor'),
                'median_return': data.get('median_return_pct', 0) / 100,
            }

    # R8各组
    for key in ['r8_top5', 'baseline_all']:
        data = bt.get('r8_sector_filter', {}).get(key, {})
        n = data.get('n', 0)
        if n > 0:
            strategies[f'R8_{key}'] = {
                'n': n,
                'avg_return': data.get('avg_return_pct', 0) / 100,
                'win_rate': data.get('win_rate', 0) / 100,
                'pf': data.get('profit_factor'),
                'median_return': data.get('median_return_pct', 0) / 100,
            }

    return strategies

def estimate_sr_from_stats(avg_return, win_rate, n):
    """
    在没有完整收益序列的情况下，用胜率和均收估算夏普比率。
    假设收益服从二项+正态混合分布：
      胜则收益 ~ N(avg_win, σ_win)，败则 ~ N(avg_loss, σ_loss)
    简化：用胜率方差近似
    """
    # 二项分布方差：p(1-p)，每次收益的"信号"=avg_return
    # Sharpe ≈ mean / std，std用 win_rate*(1-win_rate) 代理波动
    if n < 2:
        return 0, 0, 0  # SR, skew, kurt

    # 用胜率推估偏度：胜率偏离0.5越多，偏度越大
    p = win_rate
    if abs(p - 0.5) < 0.001:
        skew = 0
    elif p > 0.5:
        skew = -2 * (1 - p)  # 右偏为负偏度
    else:
        skew = 2 * p  # 左偏为正偏度

    # 峰度：T+1策略通常fat-tail，设为4-6
    kurt = 4.0 + abs(skew)  # 粗略估计

    # 估算std：用胜率*均收推算
    if avg_return != 0 and p > 0 and p < 1:
        # 简化：std ≈ |avg_return| × √(p(1-p)) 的倒数关系
        # 更稳健：假设avg_win和avg_loss对称
        std_est = abs(avg_return) / max(abs(2 * p - 1), 0.1) if abs(2*p-1) > 0.01 else abs(avg_return) * 3
    else:
        std_est = 0.03  # 默认3%日波动

    std_est = max(std_est, 0.001)

    # 日频夏普
    sr_daily = avg_return / std_est if std_est > 0 else 0

    return sr_daily, skew, kurt

# ============================================================
# 主程序
# ============================================================

def main():
    print("=" * 70)
    print("Deflated Sharpe Ratio 统计检验")
    print("方法：Bailey & López de Prado (2014)")
    print("=" * 70)

    strategies = load_backtest_returns()

    # === 确定多重检验的N ===
    # 本次回测共测试的策略变体数（所有R6/R1/R8的组）
    # R6: 4组(AB/宽/低开/高开) + R1: 2组(有/无止损) + R8: 2组(Top5/全量) = 8组
    # 但实际进行"优化vs基线"比较的假设检验 = 3个核心假设(R6/R1/R8各1个)
    # 取保守N=12（含参数调试的隐含试验）
    N_TRIALS = 12

    print(f"\n策略数: {len(strategies)}")
    print(f"多重检验N（含隐含试验）: {N_TRIALS}")

    # === 计算各组夏普并收集 ===
    all_sr = []
    results = []

    for name, s in strategies.items():
        sr_daily, skew, kurt = estimate_sr_from_stats(
            s['avg_return'], s['win_rate'], s['n']
        )

        # 年化夏普
        sr_annual = sr_daily * math.sqrt(252)

        # T = 样本量（交易笔数）
        T = s['n']

        all_sr.append(sr_daily)
        results.append({
            'name': name,
            'label': s.get('label', name),
            'n': T,
            'avg_return_pct': s['avg_return'] * 100,
            'win_rate': s['win_rate'] * 100,
            'pf': s.get('pf'),
            'sr_daily': round(sr_daily, 4),
            'sr_annual': round(sr_annual, 2),
            'skew_est': round(skew, 2),
            'kurt_est': round(kurt, 2),
        })

    # === 计算SR0（多重检验通胀门槛） ===
    sr_mean = sum(all_sr) / len(all_sr) if all_sr else 0
    sr_var = sum((sr - sr_mean) ** 2 for sr in all_sr) / len(all_sr) if all_sr else 0
    sr0 = compute_sr0(N_TRIALS, sr_var)

    print(f"\n各策略日频夏普: {[round(s,4) for s in all_sr]}")
    print(f"夏普均值: {sr_mean:.4f}, 方差: {sr_var:.6f}")
    print(f"SR0（{N_TRIALS}次试验通胀门槛）: {sr0:.4f} (日频) = {sr0*math.sqrt(252):.2f} (年化)")

    # === 对每组计算DSR ===
    print(f"\n{'策略':20s} | {'N':>5s} | {'日SR':>7s} | {'年SR':>6s} | {'PSR(0)':>7s} | {'DSR':>7s} | {'判定':s}")
    print("-" * 85)

    for r in results:
        sr_d = r['sr_daily']
        T = r['n']
        skew = r['skew_est']
        kurt = r['kurt_est']

        # PSR（基准SR0=0，即"是否优于随机"）
        psr0 = compute_psr(sr_d, 0, T, skew, kurt)

        # DSR（多重检验校正后）
        dsr = compute_dsr(sr_d, sr0, T, skew, kurt)

        r['psr_0'] = round(psr0, 4)
        r['dsr'] = round(dsr, 4)

        # 判定
        if dsr > 0.95:
            verdict = '✅ 高度显著(经校正)'
        elif dsr > 0.90:
            verdict = '🟡 边际显著'
        elif dsr > 0.50:
            verdict = '⚠️ 不确定'
        else:
            verdict = '❌ 不显著(可能是multiple testing)'

        r['verdict'] = verdict

        print(f"{r['label']:20s} | {T:5d} | {sr_d:7.4f} | {r['sr_annual']:6.2f} | {psr0:7.4f} | {dsr:7.4f} | {verdict}")

    # === 核心结论 ===
    print("\n" + "=" * 70)
    print("核心结论")
    print("=" * 70)

    # 找出R8 r8_top5的结果（我们最关心的"最强规则"）
    r8 = next((r for r in results if 'r8_top5' in r['name']), None)
    r8_base = next((r for r in results if 'baseline_all' in r['name']), None)
    r6_ab = next((r for r in results if 'ab_4_7' in r['name']), None)
    r1_stop = next((r for r in results if 'with_stop' in r['name']), None)

    conclusions = []

    if r8:
        conclusions.append({
            'rule': 'R8主线板块限定',
            'dsr': r8['dsr'],
            'verdict': r8['verdict'],
            'interpretation': f"R8的DSR={r8['dsr']:.4f}，{'通过' if r8['dsr']>0.9 else '未通过'}多重检验校正。"
        })
    if r6_ab:
        conclusions.append({
            'rule': 'R6 AB竞价(4-7%)',
            'dsr': r6_ab['dsr'],
            'verdict': r6_ab['verdict'],
            'interpretation': f"R6的DSR={r6_ab['dsr']:.4f}。负收益策略的DSR自然低，证伪结论不受影响。"
        })
    if r1_stop:
        conclusions.append({
            'rule': 'R1机械化止损',
            'dsr': r1_stop['dsr'],
            'verdict': r1_stop['verdict'],
            'interpretation': f"R1的DSR={r1_stop['dsr']:.4f}。止损改善的是尾部风险而非夏普，DSR可能低估其价值。"
        })

    for c in conclusions:
        print(f"\n{c['rule']}:")
        print(f"  DSR = {c['dsr']:.4f} → {c['verdict']}")
        print(f"  {c['interpretation']}")

    # === 输出 ===
    output = {
        'meta': {
            'run_time': datetime.now().isoformat(),
            'method': 'Bailey & López de Prado (2014)',
            'n_trials': N_TRIALS,
            'sr0_daily': round(sr0, 6),
            'sr0_annual': round(sr0 * math.sqrt(252), 4),
            'sr_variance': round(sr_var, 6),
            'note': '偏度/峰度为估计值（无完整收益序列），结论为近似',
        },
        'strategies': results,
        'conclusions': conclusions,
    }

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 结果保存: {OUT_PATH}")

if __name__ == '__main__':
    main()
