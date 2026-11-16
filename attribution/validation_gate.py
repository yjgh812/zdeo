#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [A7 SSOT] 研究副本：生产主 = D:\workbuddyspace\validation_gate.py（8/11 更新，4 个 wb 调用方）。
# 改动须与生产主双写同步；本副本仅供 zcodesj 研究引用。
"""
验证纪律工具包（validation_gate.py）
提供3个强制预检函数，任何回测/分析脚本应在开头调用。

用法：
  from validation_gate import precheck_data, require_dsr, verdict_label
  
  # 铁律V1：回测前必须预检数据
  valid_dates = precheck_data(source='qlib_bin')
  
  # 铁律V2：结论必须标注DSR
  dsr = require_dsr(returns, n_trials=12)
  
  # 铁律V4：结论措辞约束
  label = verdict_label(dsr, direction='positive')
  print(f"结论: {label}")  # "🟡 待验证" or "⚠️ 存疑" etc.
"""
import os
import struct
import math
import json
from datetime import datetime

# ============================================================
# 铁律V1：数据质量预检
# ============================================================

def precheck_data(source='qlib_bin', start_date=None, end_date=None):
    """
    回测前强制运行的数据质量预检。
    
    参数:
      source: 'qlib_bin' 或 'meta_db'
      start_date/end_date: 可选日期过滤
    
    返回:
      {
        'verdict': 'PASS' | 'FAIL',
        'valid_dates': [...],  # 可信日期清单
        'reason': str,         # 如果FAIL，原因
        'stats': {...}
      }
    """
    if source == 'qlib_bin':
        return _precheck_qlib(start_date, end_date)
    elif source == 'meta_db':
        return _precheck_meta_db(start_date, end_date)
    else:
        return {'verdict': 'FAIL', 'reason': f'未知数据源: {source}'}

def _precheck_qlib(start_date=None, end_date=None):
    """qlib bin预检"""
    qlib_path = r'D:\workbuddyspace\qlib_data\qlib_bin'
    
    # 读日历
    cal_file = os.path.join(qlib_path, 'calendars', 'day.txt')
    if not os.path.exists(cal_file):
        return {'verdict': 'FAIL', 'reason': f'日历不存在: {cal_file}'}
    
    with open(cal_file, 'r') as f:
        all_dates = [line.strip() for line in f if line.strip()]
    
    # 日期过滤
    valid_dates = all_dates
    if start_date:
        valid_dates = [d for d in valid_dates if d >= start_date]
    if end_date:
        valid_dates = [d for d in valid_dates if d <= end_date]
    
    # 抽样检查3只股票
    features_dir = os.path.join(qlib_path, 'features')
    sample_stocks = ['sh600000', 'sz000001', 'sh600519']
    issues = []
    
    for stock in sample_stocks:
        close_file = os.path.join(features_dir, stock, 'close.day.bin')
        if not os.path.exists(close_file):
            issues.append(f'{stock} close.bin不存在')
            continue
        with open(close_file, 'rb') as f:
            n = os.path.getsize(close_file) // 4
            vals = struct.unpack(f'{n}f', f.read())
        zeros = sum(1 for v in vals if v == 0)
        zero_pct = zeros / n * 100 if n > 0 else 100
        if zero_pct > 5:
            issues.append(f'{stock} 零值率{zero_pct:.1f}%超标')
    
    if issues:
        return {
            'verdict': 'FAIL',
            'reason': '抽样检查失败: ' + '; '.join(issues),
            'valid_dates': valid_dates,
        }
    
    return {
        'verdict': 'PASS',
        'valid_dates': valid_dates,
        'total_days': len(valid_dates),
        'date_range': f'{valid_dates[0]} ~ {valid_dates[-1]}' if valid_dates else 'N/A',
        'source': 'qlib_bin',
    }

def _precheck_meta_db(start_date=None, end_date=None):
    """meta db预检——预期会FAIL（大部分日期不可信）"""
    import sqlite3
    db_path = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
    
    if not os.path.exists(db_path):
        return {'verdict': 'FAIL', 'reason': f'DB不存在: {db_path}'}
    
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    sd = int(start_date.replace('-','')) if start_date else 20240101
    ed = int(end_date.replace('-','')) if end_date else 99991231
    
    # 找可信日期
    c.execute('''
        SELECT date FROM (
            SELECT date,
                   SUM(CASE WHEN change_pct = 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as zero_pct,
                   SUM(CASE WHEN change_pct > 0.5 OR change_pct < -0.5 THEN 1 ELSE 0 END) as anomaly
            FROM stock_daily_meta
            WHERE date >= ? AND date <= ?
            GROUP BY date
            HAVING zero_pct < 10 AND anomaly < 100
        ) ORDER BY date
    ''', (sd, ed))
    valid_dates = [str(r[0]) for r in c.fetchall()]
    conn.close()
    
    if not valid_dates:
        return {
            'verdict': 'FAIL',
            'reason': '该区间内无可信交易日（0%占比>10%或异常值过多）',
            'valid_dates': [],
            'recommendation': '改用qlib_bin数据源',
        }
    
    # 即使有可信日，也标注警告
    return {
        'verdict': 'WARN',
        'reason': f'meta db仅{len(valid_dates)}个可信日，建议改用qlib_bin',
        'valid_dates': valid_dates,
        'recommendation': 'qlib_bin',
    }

# ============================================================
# 铁律V2：DSR统计检验
# ============================================================

def require_dsr(returns, n_trials, risk_free=0, annualization=252):
    """
    对收益序列计算DSR。
    
    参数:
      returns: 日收益率列表
      n_trials: 测试的策略总数（含隐含试验）
      risk_free: 无风险利率（日频）
      annualization: 年化天数
    
    返回:
      {
        'sr_daily': float,
        'sr_annual': float,
        'psr0': float,      # Probabilistic Sharpe Ratio (基准0)
        'dsr': float,       # Deflated Sharpe Ratio
        'n_trials': int,
        'verdict': str,
      }
    """
    from scipy import stats
    
    n = len(returns)
    if n < 2:
        return {'sr_daily': 0, 'dsr': 0, 'verdict': '样本不足'}
    
    excess = [r - risk_free for r in returns]
    mean_r = sum(excess) / n
    
    # 标准差
    var_r = sum((r - mean_r) ** 2 for r in excess) / (n - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 0.001
    
    sr_daily = mean_r / std_r if std_r > 0 else 0
    sr_annual = sr_daily * math.sqrt(annualization)
    
    # 偏度和峰度
    if n >= 4:
        skew = stats.skew(excess)
        kurt = stats.kurtosis(excess, fisher=True)  # 超额峰度
    else:
        skew, kurt = 0, 0
    
    # PSR(0)
    psr0 = _calc_psr(sr_daily, 0, n, skew, kurt)
    
    # SR0 (多重检验门槛)
    # 各试验SR方差估计：用样本SR的标准误平方 + 跨策略方差
    # 当只有一个策略时，用 sr_daily^2 的不确定性估计
    # SR的标准误 ≈ sqrt((1 - skew*SR + kurt/4*SR^2) / T)
    sr_se_sq = max(1 - skew * sr_daily + kurt / 4 * sr_daily ** 2, 0.01) / n
    # 跨策略方差 ≈ 单策略方差 × 策略多样性因子（保守取0.5）
    sr_var_est = sr_se_sq * 0.5
    sr0 = _calc_sr0(n_trials, sr_var_est)
    
    # DSR
    dsr = _calc_psr(sr_daily, sr0, n, skew, kurt)
    
    # 判定
    if dsr > 0.95:
        verdict = '✅ 高度显著'
    elif dsr > 0.90:
        verdict = '🟡 边际显著'
    elif dsr > 0.50:
        verdict = '⚠️ 待验证'
    else:
        verdict = '❌ 可能是multiple testing'
    
    return {
        'sr_daily': round(sr_daily, 4),
        'sr_annual': round(sr_annual, 2),
        'skew': round(skew, 2),
        'kurtosis': round(kurt, 2),
        'psr0': round(psr0, 4),
        'sr0_hurdle': round(sr0, 4),
        'dsr': round(dsr, 4),
        'n_trials': n_trials,
        'sample_size': n,
        'verdict': verdict,
    }

def _calc_psr(sr_hat, sr0, T, skew, kurt):
    """PSR/DSR核心计算"""
    if T <= 1:
        return 0
    from scipy import stats
    numer = (sr_hat - sr0) * math.sqrt(T - 1)
    denom_sq = 1 - skew * sr_hat + (kurt / 4) * sr_hat ** 2
    denom_sq = max(denom_sq, 0.01)
    z = numer / math.sqrt(denom_sq)
    return stats.norm.cdf(z)

def _calc_sr0(n_trials, sr_var):
    """计算N次试验的SR0门槛"""
    from scipy import stats
    if n_trials <= 1 or sr_var <= 0:
        return 0
    gamma_em = 0.5772
    sqrt_v = math.sqrt(sr_var)
    t1 = stats.norm.ppf(1 - 1.0 / n_trials)
    t2 = stats.norm.ppf(1 - 1.0 / (n_trials * math.e))
    return sqrt_v * ((1 - gamma_em) * t1 + gamma_em * t2)

# ============================================================
# 铁律V4：结论措辞约束
# ============================================================

def verdict_label(dsr, direction='positive', oos_confirmed=False):
    """
    根据DSR和方向输出结论标签。
    
    参数:
      dsr: DSR值（0-1）
      direction: 'positive'=策略有效方向 / 'negative'=证伪方向
      oos_confirmed: 是否有OOS确认
    
    返回: (标签, 允许的措辞)
    """
    if direction == 'negative':
        # 证伪方向不受DSR影响
        if dsr < 0.1:
            return ('❌ 证伪', '方向稳定为负，证伪可靠')
        else:
            return ('⚠️ 存疑', '负方向但不稳定，需更多样本')
    
    # 正向结论
    if oos_confirmed and dsr > 0.95:
        return ('✅ 确认', '可使用"确认/上线/有效"')
    elif dsr > 0.95:
        return ('🟡 待OOS', '可使用"初步通过，待OOS确认"')
    elif dsr > 0.50:
        return ('🟡 待验证', '只能使用"初步观察，待验证"')
    else:
        return ('⚠️ 存疑', '只能使用"可能是噪音/过拟合"')

# ============================================================
# 自检
# ============================================================

if __name__ == '__main__':
    print("=" * 60)
    print("验证纪律工具包自检")
    print("=" * 60)
    
    # V1测试
    print("\n[V1] qlib_bin预检:")
    r = precheck_data('qlib_bin')
    print(f"  verdict={r['verdict']}, days={r.get('total_days')}")
    
    print("\n[V1] meta_db预检:")
    r = precheck_data('meta_db')
    print(f"  verdict={r['verdict']}, reason={r.get('reason','')}")
    
    # V2测试
    print("\n[V2] DSR测试（模拟正收益序列）:")
    import random
    random.seed(42)
    good_returns = [random.gauss(0.002, 0.02) for _ in range(200)]  # 正期望
    r = require_dsr(good_returns, n_trials=12)
    print(f"  DSR={r['dsr']}, verdict={r['verdict']}")
    
    print("\n[V2] DSR测试（模拟随机序列）:")
    random_returns = [random.gauss(0, 0.02) for _ in range(200)]  # 零期望
    r = require_dsr(random_returns, n_trials=12)
    print(f"  DSR={r['dsr']}, verdict={r['verdict']}")
    
    # V4测试
    print("\n[V4] 措辞约束测试:")
    for dsr_val in [0.99, 0.92, 0.60, 0.20]:
        label, allowed = verdict_label(dsr_val, direction='positive')
        print(f"  DSR={dsr_val}: {label} → {allowed}")
    
    print("\n[完成] 工具包就绪，其他脚本可import使用")
