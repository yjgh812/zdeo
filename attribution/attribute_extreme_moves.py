#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场极端涨跌标准化归因引擎（2026-08-10）
对104只涨停股 + 16只大跌股进行五维归因拆解。

归因框架：
  涨停五维：技术形态 / 资金驱动 / 板块联动 / 基本面消息 / 特异收益
  大跌五维：技术破位 / 资金出逃 / 情绪退潮 / 基本面利空 / 特异亏损

输入: extreme_moves_20260810.json
输出: extreme_attribution_20260810.json + 控制台报告
"""
import json
import os
from collections import Counter, defaultdict

IN_PATH = r'D:\zcodesj\attribution\extreme_moves_20260810.json'
OUT_PATH = r'D:\zcodesj\attribution\extreme_attribution_20260810.json'

def safe(d, key, default=None):
    """安全取值"""
    v = d.get(key) if d else None
    return v if v is not None else default

# ============================================================
# 涨停归因引擎
# ============================================================

def attribute_limit_up(stock, sector_hotness):
    """
    对涨停股做五维归因。
    基于t-1期因子，输出各维度贡献度和可预判性评分。
    """
    f = stock.get('factors') or {}
    code = stock['code']
    sector = stock.get('sector', '未知')

    contributions = {
        'tech_form': 0,      # 技术形态
        'capital': 0,        # 资金驱动
        'sector_link': 0,    # 板块联动
        'fundamental': 0,    # 基本面消息
        'idiosyncratic': 0,  # 特异收益
    }

    tags = []
    sub_type = []
    predictability = 5  # 默认中等

    # --- 1. 技术形态驱动 ---
    trend = safe(f, 'trend_20d', 0)
    ret_5d = safe(f, 'return_5d_pct', 0)
    ret_20d = safe(f, 'return_20d_pct', 0)
    vol_ratio = safe(f, 'volume_ratio_prev', 1)
    boards = safe(f, 'consecutive_boards', 0)
    sealed = stock.get('sealed', False)

    # 连板接力
    if boards >= 2:
        contributions['tech_form'] += 30
        sub_type.append('连板情绪接力')
        tags.append(f'{boards}连板')
        predictability = max(predictability, 7)
    elif boards == 1:
        contributions['tech_form'] += 20
        sub_type.append('首板接力')
        tags.append('昨日涨停')

    # 趋势加速
    if trend and trend > 5:
        contributions['tech_form'] += 15
        sub_type.append('趋势加速')
        tags.append(f'20日趋势+{trend:.1f}%')
        predictability = max(predictability, 6)

    # 横盘突破（20日涨幅小但趋势转正+放量）
    if ret_20d is not None and abs(ret_20d) < 10 and trend and trend > 0 and vol_ratio and vol_ratio > 1.5:
        contributions['tech_form'] += 20
        sub_type.append('横盘箱体突破')
        tags.append('横盘突破')
        predictability = max(predictability, 7)

    # 超跌反弹
    if ret_20d is not None and ret_20d < -15:
        contributions['tech_form'] += 15
        sub_type.append('超跌反弹')
        tags.append(f'20日跌{ret_20d:.0f}%')
        predictability = max(predictability, 5)

    # --- 2. 资金驱动 ---
    if vol_ratio and vol_ratio > 2.0:
        contributions['capital'] += 25
        tags.append(f'放量{vol_ratio:.1f}倍')
        predictability = max(predictability, 6)
    elif vol_ratio and vol_ratio > 1.5:
        contributions['capital'] += 15
        tags.append('温和放量')

    # 竞价缺口大（资金抢筹信号）
    gap = safe(f, 'auction_gap_pct', 0)
    if gap and gap > 5:
        contributions['capital'] += 15
        sub_type.append('竞价抢筹')
        tags.append(f'高开{gap:.1f}%')
        predictability = max(predictability, 6)

    # --- 3. 板块联动 ---
    sec_rank = sector_hotness.get(sector, 99)
    sec_count = sector_hotness.get(f'{sector}_count', 0)
    if sec_rank <= 5 and sec_count >= 3:
        contributions['sector_link'] += 30
        sub_type.append('热点板块龙头/跟风')
        tags.append(f'{sector}Top{sec_rank}({sec_count}只涨停)')
        predictability = max(predictability, 7)
    elif sec_rank <= 10 and sec_count >= 2:
        contributions['sector_link'] += 15
        tags.append(f'{sector}板块联动')

    # --- 4. 基本面消息（不可预判，默认低权重）---
    # 无公告数据源，只能基于"无技术+无资金+无板块信号"反推
    has_other_signal = (contributions['tech_form'] + contributions['capital'] + contributions['sector_link']) > 20
    if not has_other_signal:
        contributions['fundamental'] += 30
        sub_type.append('疑似消息驱动')
        tags.append('无前置技术信号')
        predictability = min(predictability, 3)  # 消息不可预判

    # --- 5. 特异收益（归一化剩余）---
    total = sum(contributions.values())
    if total < 50:  # 各维度信号都很弱
        contributions['idiosyncratic'] = 50
        sub_type.append('特异收益')
        tags.append('随机涨停')
        predictability = min(predictability, 2)
    else:
        # 归一化到100
        scale = 100.0 / total if total > 0 else 1
        for k in contributions:
            contributions[k] = round(contributions[k] * scale)

    # 策略价值判定
    if predictability >= 7:
        value = '可复用涨停范式'
    elif predictability >= 5:
        value = '有条件参考'
    else:
        value = '无参考价值(随机/消息)'

    # 一级归因（取最大维度）
    primary = max(contributions, key=contributions.get)

    return {
        'code': code,
        'sector': sector,
        'pct': stock['pct'],
        'sealed': sealed,
        'primary_attribution': primary,
        'sub_types': sub_type,
        'contributions': contributions,
        'predictability': predictability,
        'tags': tags,
        'value_verdict': value,
        'factors_snapshot': {
            'trend_20d': trend,
            'return_5d': ret_5d,
            'return_20d': ret_20d,
            'vol_ratio': vol_ratio,
            'boards': boards,
            'gap': gap,
        }
    }

# ============================================================
# 大跌归因引擎
# ============================================================

def attribute_big_drop(stock, sector_hotness):
    """
    对大跌股做五维风险归因。
    """
    f = stock.get('factors') or {}
    code = stock['code']
    sector = stock.get('sector', '未知')

    contributions = {
        'tech_breakdown': 0,   # 技术破位
        'capital_flight': 0,   # 资金出逃
        'sentiment_retreat': 0, # 情绪退潮
        'fundamental_neg': 0,  # 基本面利空
        'idiosyncratic': 0,    # 特异亏损
    }

    tags = []
    sub_type = []
    avoidability = 5  # 可规避性

    trend = safe(f, 'trend_20d', 0)
    ret_5d = safe(f, 'return_5d_pct', 0)
    ret_20d = safe(f, 'return_20d_pct', 0)
    vol_ratio = safe(f, 'volume_ratio_prev', 1)
    boards = safe(f, 'consecutive_boards', 0)
    is_ld = stock.get('is_limit_down', False)

    # --- 1. 技术破位 ---
    # 前期大涨后高位回落
    if ret_20d is not None and ret_20d > 20:
        contributions['tech_breakdown'] += 30
        sub_type.append('高位放量出货')
        tags.append(f'20日涨{ret_20d:.0f}%后回落')
        avoidability = max(avoidability, 7)
    elif ret_5d is not None and ret_5d > 15:
        contributions['tech_breakdown'] += 20
        sub_type.append('短期暴涨回调')
        tags.append(f'5日涨{ret_5d:.0f}%')
        avoidability = max(avoidability, 6)

    # 趋势已走坏
    if trend is not None and trend < -3:
        contributions['tech_breakdown'] += 20
        sub_type.append('趋势破位加速')
        tags.append(f'20日趋势{trend:.1f}%')
        avoidability = max(avoidability, 7)

    # 昨日涨停今日大跌（炸板/天地板）
    if boards and boards >= 1:
        contributions['tech_breakdown'] += 25
        sub_type.append('昨日涨停今日大跌')
        tags.append('涨停后反转')
        avoidability = max(avoidability, 6)

    # --- 2. 资金出逃 ---
    if vol_ratio and vol_ratio > 2.0:
        contributions['capital_flight'] += 20
        tags.append(f'放量下跌{vol_ratio:.1f}倍')
        avoidability = max(avoidability, 5)

    # --- 3. 情绪退潮 ---
    sec_rank = sector_hotness.get(sector, 99)
    sec_drop = sector_hotness.get(f'{sector}_drop_count', 0)
    if sec_drop >= 2:
        contributions['sentiment_retreat'] += 25
        sub_type.append('板块集体回调')
        tags.append(f'{sector}板块{sec_drop}只大跌')
        avoidability = max(avoidability, 6)

    # --- 4. 基本面利空（无公告数据，反推）---
    has_signal = (contributions['tech_breakdown'] + contributions['capital_flight'] + contributions['sentiment_retreat']) > 20
    if not has_signal:
        contributions['fundamental_neg'] += 35
        sub_type.append('疑似突发利空')
        tags.append('无前置风险信号')
        avoidability = min(avoidability, 3)

    # --- 5. 特异亏损 ---
    total = sum(contributions.values())
    if total < 40:
        contributions['idiosyncratic'] = 50
        sub_type.append('特异亏损')
        tags.append('随机大跌')
        avoidability = min(avoidability, 2)
    else:
        scale = 100.0 / total if total > 0 else 1
        for k in contributions:
            contributions[k] = round(contributions[k] * scale)

    # 策略价值
    if avoidability >= 7:
        value = '可规避风险点'
    elif avoidability >= 5:
        value = '有条件规避'
    else:
        value = '不可预判风险'

    primary = max(contributions, key=contributions.get)

    return {
        'code': code,
        'sector': sector,
        'pct': stock['pct'],
        'is_limit_down': is_ld,
        'primary_attribution': primary,
        'sub_types': sub_type,
        'contributions': contributions,
        'avoidability': avoidability,
        'tags': tags,
        'value_verdict': value,
        'factors_snapshot': {
            'trend_20d': trend,
            'return_5d': ret_5d,
            'return_20d': ret_20d,
            'vol_ratio': vol_ratio,
            'boards': boards,
        }
    }

# ============================================================
# 主程序
# ============================================================

def main():
    with open(IN_PATH, 'r', encoding='utf-8') as fh:
        data = json.load(fh)

    limit_ups = data['limit_ups']
    big_drops = data['big_drops']
    sector_dist = data['sector_distribution']

    # 计算板块热度排名（涨停数多的板块热度高）
    lu_sector = sector_dist['limit_up']
    bd_sector = sector_dist['big_drop']

    # 板块热度map: sector -> rank, sector_count, drop_count
    sector_hotness = {}
    sorted_lu = sorted(lu_sector.items(), key=lambda x: -x[1])
    for rank, (sec, cnt) in enumerate(sorted_lu, 1):
        sector_hotness[sec] = rank
        sector_hotness[f'{sec}_count'] = cnt
    for sec, cnt in bd_sector.items():
        sector_hotness[f'{sec}_drop_count'] = cnt

    # === 涨停归因 ===
    print("=" * 70)
    print("涨停股五维归因")
    print("=" * 70)

    lu_results = []
    for stock in limit_ups:
        result = attribute_limit_up(stock, sector_hotness)
        lu_results.append(result)

    # 统计
    lu_primary = Counter(r['primary_attribution'] for r in lu_results)
    lu_pred = Counter()
    for r in lu_results:
        if r['predictability'] >= 7:
            lu_pred['可预判(>=7)'] += 1
        elif r['predictability'] >= 5:
            lu_pred['中等(5-6)'] += 1
        else:
            lu_pred['不可预判(<5)'] += 1

    lu_value = Counter(r['value_verdict'] for r in lu_results)

    print(f"\n总样本: {len(lu_results)}")
    print(f"\n一级归因分布:")
    label_map = {
        'tech_form': '技术形态', 'capital': '资金驱动',
        'sector_link': '板块联动', 'fundamental': '基本面消息',
        'idiosyncratic': '特异收益'
    }
    for k, v in lu_primary.most_common():
        print(f"  {label_map.get(k,k):12s}: {v:3d} ({v/len(lu_results)*100:.1f}%)")

    print(f"\n可预判性分布:")
    for k, v in lu_pred.most_common():
        print(f"  {k:15s}: {v:3d} ({v/len(lu_results)*100:.1f}%)")

    print(f"\n策略价值分布:")
    for k, v in lu_value.most_common():
        print(f"  {k:20s}: {v:3d}")

    # 高预判性涨停（可复用范式）
    high_pred = [r for r in lu_results if r['predictability'] >= 7]
    print(f"\n可复用涨停范式 ({len(high_pred)}只):")
    for r in sorted(high_pred, key=lambda x: -x['predictability'])[:15]:
        print(f"  {r['code']} | {r['sector']:8s} | {label_map.get(r['primary_attribution'],''):8s} "
              f"| 预判={r['predictability']} | {', '.join(r['tags'][:3])}")

    # === 大跌归因 ===
    print("\n" + "=" * 70)
    print("大跌股五维风险归因")
    print("=" * 70)

    bd_results = []
    for stock in big_drops:
        result = attribute_big_drop(stock, sector_hotness)
        bd_results.append(result)

    bd_primary = Counter(r['primary_attribution'] for r in bd_results)
    bd_avoid = Counter()
    for r in bd_results:
        if r['avoidability'] >= 7:
            bd_avoid['可规避(>=7)'] += 1
        elif r['avoidability'] >= 5:
            bd_avoid['中等(5-6)'] += 1
        else:
            bd_avoid['不可预判(<5)'] += 1

    bd_value = Counter(r['value_verdict'] for r in bd_results)

    print(f"\n总样本: {len(bd_results)}")
    label_map_bd = {
        'tech_breakdown': '技术破位', 'capital_flight': '资金出逃',
        'sentiment_retreat': '情绪退潮', 'fundamental_neg': '基本面利空',
        'idiosyncratic': '特异亏损'
    }

    print(f"\n一级归因分布:")
    for k, v in bd_primary.most_common():
        print(f"  {label_map_bd.get(k,k):12s}: {v:3d} ({v/len(bd_results)*100:.1f}%)")

    print(f"\n可规避性分布:")
    for k, v in bd_avoid.most_common():
        print(f"  {k:15s}: {v:3d} ({v/len(bd_results)*100:.1f}%)")

    print(f"\n全部大跌股明细:")
    for r in bd_results:
        print(f"  {r['code']} | {r['sector']:10s} | pct={r['pct']*100:+.1f}% "
              f"| {label_map_bd.get(r['primary_attribution'],''):10s} "
              f"| 可规避={r['avoidability']} | {'跌停' if r['is_limit_down'] else '大跌'} "
              f"| {', '.join(r['tags'][:3])}")

    # === 输出 ===
    output = {
        'meta': data['meta'],
        'limit_up_attribution': lu_results,
        'big_drop_attribution': bd_results,
        'summary': {
            'lu_primary_dist': dict(lu_primary.most_common()),
            'lu_predictability_dist': dict(lu_pred.most_common()),
            'lu_value_dist': dict(lu_value.most_common()),
            'bd_primary_dist': dict(bd_primary.most_common()),
            'bd_avoidability_dist': dict(bd_avoid.most_common()),
            'bd_value_dist': dict(bd_value.most_common()),
        }
    }

    with open(OUT_PATH, 'w', encoding='utf-8') as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] 归因结果: {OUT_PATH}")

if __name__ == '__main__':
    main()
