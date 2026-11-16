#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Barra风格因子计算引擎（简化版）
基于qlib bin数据计算Barra CNE5的10个风格因子的简化版。

因子列表（简化实现）：
  1. SIZE      - 市值（用成交额代理流动性市值）
  2. BETA      - 贝塔（个股收益 vs 全市场均收的回归系数）
  3. MOMENTUM  - 动量（近20日累计收益）
  4. RESVOL    - 残差波动率（近20日收益标准差）
  5. BTOP      - 账面市值比（无财务数据，用价格倒数代理）
  6. LIQUIDITY - 流动性（近20日均量比）

输出每只股票的因子暴露，可用于归因分解。
"""
import os
import struct
import math
import json
from collections import defaultdict

QLIB_PATH = r'D:\workbuddyspace\qlib_data\qlib_bin'
OUT_PATH = r'D:\zcodesj\attribution\barra_factors.json'

def load_calendar():
    with open(os.path.join(QLIB_PATH, 'calendars', 'day.txt'), 'r') as f:
        return [line.strip() for line in f if line.strip()]

def load_field(stock, field):
    filepath = os.path.join(QLIB_PATH, 'features', stock, f'{field}.day.bin')
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'rb') as f:
        n = os.path.getsize(filepath) // 4
        return list(struct.unpack(f'{n}f', f.read()))

def calc_barra_factors(stock, closes, volumes, amounts, calendars, target_idx):
    """
    计算单只股票在target_idx处的Barra风格因子
    """
    if target_idx >= len(closes) or target_idx < 20:
        return None

    # 取近20日窗口
    window_closes = closes[max(0, target_idx-20):target_idx+1]
    window_vols = volumes[max(0, target_idx-20):target_idx+1] if volumes else None

    if len(window_closes) < 20:
        return None

    # 过滤异常值
    valid_closes = [c for c in window_closes if c is not None and c > 0]
    if len(valid_closes) < 15:
        return None

    # 1. MOMENTUM: 近20日累计收益
    if len(window_closes) >= 21 and window_closes[-21] > 0:
        momentum = window_closes[-1] / window_closes[-21] - 1
    else:
        momentum = None

    # 2. RESVOL: 近20日收益标准差
    returns = []
    for i in range(1, len(window_closes)):
        if window_closes[i-1] and window_closes[i-1] > 0 and window_closes[i]:
            returns.append(window_closes[i] / window_closes[i-1] - 1)
    if len(returns) >= 10:
        mean_ret = sum(returns) / len(returns)
        var_ret = sum((r - mean_ret)**2 for r in returns) / len(returns)
        resvol = math.sqrt(var_ret)
    else:
        return None

    # 3. LIQUIDITY: 近20日均量 / 价格（流动性代理）
    if window_vols:
        valid_vols = [v for v in window_vols if v is not None and v > 0]
        if valid_vols:
            avg_vol = sum(valid_vols) / len(valid_vols)
            current_price = window_closes[-1]
            liquidity = avg_vol / current_price if current_price > 0 else None
        else:
            liquidity = None
    else:
        liquidity = None

    # 4. SIZE: 对数成交额代理
    if amounts and target_idx < len(amounts):
        recent_amt = amounts[target_idx]
        size = math.log(recent_amt) if recent_amt and recent_amt > 0 else None
    elif window_vols:
        valid_vols = [v for v in window_vols if v is not None and v > 0]
        size = math.log(sum(valid_vols)) if valid_vols else None
    else:
        size = None

    # 5. BETA: 简化为近20日收益/标准差比（近似Sharpe = 方向性）
    beta = mean_ret / resvol if resvol > 0 else None

    # 6. BTOP: 价格倒数（低价股=高BTOP代理）
    btop = 1.0 / window_closes[-1] if window_closes[-1] > 0 else None

    # 标准化标记（后续需跨截面标准化）
    return {
        'stock': stock,
        'momentum': round(momentum * 100, 2) if momentum else None,  # 百分比
        'resvol': round(resvol * 100, 2),  # 百分比
        'liquidity': round(liquidity, 2) if liquidity else None,
        'size': round(size, 2) if size else None,
        'beta_proxy': round(beta * 100, 4) if beta else None,  # 近似Sharpe
        'btop_proxy': round(btop, 6) if btop else None,
        'close': round(window_closes[-1], 2),
    }

def cross_sectional_normalize(factors_list):
    """
    对因子做横截面标准化（z-score）
    """
    if not factors_list:
        return factors_list

    for field in ['momentum', 'resvol', 'liquidity', 'size', 'beta_proxy', 'btop_proxy']:
        vals = [f[field] for f in factors_list if f.get(field) is not None]
        if len(vals) < 10:
            continue
        mean_v = sum(vals) / len(vals)
        var_v = sum((v - mean_v)**2 for v in vals) / len(vals)
        std_v = math.sqrt(var_v) if var_v > 0 else 1

        for f in factors_list:
            if f.get(field) is not None:
                f[f'{field}_z'] = round((f[field] - mean_v) / std_v, 2)

    return factors_list

def main():
    print("=" * 70)
    print("Barra风格因子计算（简化版）")
    print("=" * 70)

    calendars = load_calendar()
    # 用最新交易日
    target_date = calendars[-1]
    target_idx = len(calendars) - 1
    print(f"目标日期: {target_date} (idx={target_idx})")

    features_dir = os.path.join(QLIB_PATH, 'features')
    all_stocks = sorted(os.listdir(features_dir))

    print(f"计算{len(all_stocks)}只股票的因子...")
    all_factors = []

    for i, stock in enumerate(all_stocks):
        closes = load_field(stock, 'close')
        volumes = load_field(stock, 'volume')
        amounts = load_field(stock, 'amount')

        if closes is None:
            continue

        factors = calc_barra_factors(stock, closes, volumes, amounts, calendars, target_idx)
        if factors:
            all_factors.append(factors)

        if (i + 1) % 1000 == 0:
            print(f"  进度: {i+1}/{len(all_stocks)}")

    print(f"\n成功计算: {len(all_factors)}只")

    # 横截面标准化
    all_factors = cross_sectional_normalize(all_factors)

    # 输出统计
    print(f"\n因子横截面统计:")
    for field in ['momentum', 'resvol', 'liquidity', 'size']:
        vals = [f[field] for f in all_factors if f.get(field) is not None]
        if vals:
            print(f"  {field:12s}: mean={sum(vals)/len(vals):.2f}, "
                  f"min={min(vals):.2f}, max={max(vals):.2f}, N={len(vals)}")

    # 按动量排序，输出Top10（最强势）
    by_momentum = sorted([f for f in all_factors if f.get('momentum') is not None],
                         key=lambda x: x['momentum'], reverse=True)
    print(f"\n动量Top10（近20日涨幅最大）:")
    for f in by_momentum[:10]:
        print(f"  {f['stock']}: momentum={f['momentum']:.1f}%, resvol={f['resvol']:.2f}%, "
              f"close={f['close']}, size={f.get('size','?')}")

    # 输出
    output = {
        'meta': {
            'target_date': target_date,
            'total_stocks': len(all_factors),
            'method': 'Barra CNE5 simplified (6/10 factors)',
        },
        'factors': all_factors,
    }

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 因子数据保存: {OUT_PATH}")

    print(f"\n[说明] Barra因子可用于:")
    print(f"  1. 持仓归因：将持仓收益分解到各风格因子暴露")
    print(f"  2. 选股过滤：动量_z>0 + resvol_z<0 = 强势低波组合")
    print(f"  3. 与batch_attribution.py联动：归因时标注因子暴露")

if __name__ == '__main__':
    main()
