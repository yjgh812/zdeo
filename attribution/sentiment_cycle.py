#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股情绪周期日判定脚本
每日盘后自动计算4指标（涨停数/连板高度/炸板率/跌停数），
判定当前情绪周期阶段（启动/主升/分歧/退潮）。

⚠️ 2026-08-22 状态：已被 vibe_card.py v2 内置的 cycle_stage（同款 4 阶段判定）取代，
盘感卡片不再依赖本脚本的 JSON 产物（本脚本产物曾停更于 2026-08-10 且未入任何 cron）。
保留本文件仅因历史文档/归因标签引用，不删除；新逻辑一律走 vibe_card.context.cycle_stage。

阈值来源：实战量化体系（非主观判断）
  主升期：涨停>60 + 连板≥5 + 炸板率<30% + 跌停<10
  启动期：涨停30-60 + 连板2-3板为主 + 炸板率~30% + 跌停<10
  分歧期：涨停30-60回落 + 高位断板 + 炸板率30-50% + 跌停10-30
  退潮期：涨停<30 + 连板消失 + 炸板率>50% + 跌停>30

数据源：dragon_quant_meta.db / stock_daily_meta
输出：D:/zcodesj/attribution/sentiment_YYYYMMDD.json + 控制台
"""
import sqlite3
import json
import os
from datetime import datetime
from collections import defaultdict

DB_PATH = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
OUT_DIR = r'D:\zcodesj\attribution'

def is_excluded(code):
    if code.startswith('688') or code.startswith('8') or code.startswith('4'):
        return True
    return False

def get_zt_threshold(code):
    if code.startswith('300') or code.startswith('301'):
        return 0.195
    return 0.095

def main(target_date=None):
    """
    判定指定日期的情绪周期。target_date=None时取DB中最新交易日。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 确定目标日期
    if target_date is None:
        c.execute("SELECT MAX(date) FROM stock_daily_meta")
        target_date = c.fetchone()[0]

    target_prev = None
    # 取前5个交易日（用于连板计算）
    c.execute("""
        SELECT DISTINCT date FROM stock_daily_meta
        WHERE date < ? ORDER BY date DESC LIMIT 5
    """, (target_date,))
    prev_dates = [r[0] for r in c.fetchall()]
    if prev_dates:
        target_prev = prev_dates[0]

    print(f"目标日期: {target_date} (前交易日: {target_prev})")

    # === 加载目标日数据 ===
    c.execute("""
        SELECT code, date, open, high, low, close, volume, change_pct
        FROM stock_daily_meta WHERE date = ?
    """, (target_date,))
    rows = c.fetchall()

    # === 加载近5日数据（连板计算） ===
    date_set = set([target_date] + prev_dates)
    placeholders = ','.join('?' * len(date_set))
    c.execute(f"""
        SELECT code, date, open, high, low, close, volume, change_pct
        FROM stock_daily_meta WHERE date IN ({placeholders})
        ORDER BY code, date
    """, list(date_set))
    hist_rows = c.fetchall()

    conn.close()

    # 组织历史数据
    hist_by_code = defaultdict(dict)  # code -> {date: data}
    for r in hist_rows:
        code = r[0]
        if is_excluded(code):
            continue
        d = r[1]
        hist_by_code[code][d] = {
            'open': r[2], 'high': r[3], 'low': r[4],
            'close': r[5], 'volume': r[6], 'pct': r[7]
        }

    # === 指标1：涨停家数 ===
    limit_ups = []
    limit_downs = []
    broken_boards = []  # 炸板（日内触及涨停但收盘未封死）
    total_valid = 0

    for code, days in hist_by_code.items():
        if target_date not in days:
            continue
        d = days[target_date]
        if d['volume'] is None or d['volume'] == 0 or d['close'] is None:
            continue
        total_valid += 1

        pct = d['pct']
        if pct is None:
            continue

        threshold = get_zt_threshold(code)

        # 涨停（收盘达阈值）
        if pct >= threshold:
            # 封死 vs 炸板：收盘价=最高价≈封死
            sealed = d['close'] >= d['high'] * 0.999
            if sealed:
                limit_ups.append(code)
            else:
                broken_boards.append(code)  # 收盘涨停但非封死=炸板后回落到涨停

        # 跌停
        elif pct <= -threshold:
            limit_downs.append(code)

    limit_up_count = len(limit_ups)
    limit_down_count = len(limit_downs)
    broken_count = len(broken_boards)

    # === 指标2：炸板率 ===
    # 炸板率 = 炸板家数 / (涨停家数 + 炸板家数)
    # 注意：DB只有收盘价，无法精确判断"盘中触及涨停后炸开"
    # 近似：收盘涨停但非封死 = 炸板回落到涨停线
    # 更准确需要分钟线，此处用近似
    total_attempted_boards = limit_up_count + broken_count
    broken_rate = broken_count / total_attempted_boards * 100 if total_attempted_boards > 0 else 0

    # === 指标3：连板高度 ===
    max_consecutive = 0
    consecutive_distribution = defaultdict(int)

    for code in limit_ups:
        streak = 1  # 今日算1板
        # 从target_prev往前数连续涨停
        for pd in prev_dates:
            if pd in hist_by_code.get(code, {}):
                prev_pct = hist_by_code[code][pd].get('pct')
                if prev_pct is not None and prev_pct >= get_zt_threshold(code):
                    streak += 1
                else:
                    break
            else:
                break

        max_consecutive = max(max_consecutive, streak)
        consecutive_distribution[streak] += 1

    # === 指标4：涨跌家数比 ===
    up_count = 0
    down_count = 0
    flat_count = 0

    for code, days in hist_by_code.items():
        if target_date not in days:
            continue
        d = days[target_date]
        pct = d.get('pct')
        if pct is None or d['volume'] == 0:
            continue
        if pct > 0:
            up_count += 1
        elif pct < 0:
            down_count += 1
        else:
            flat_count += 1

    total_stocks = up_count + down_count + flat_count
    up_ratio = up_count / total_stocks * 100 if total_stocks > 0 else 0

    # === 周期判定 ===
    # 评分制：每个指标贡献阶段判定，最终取加权多数

    def classify(limit_ups, max_boards, broken_rate, limit_downs, up_ratio):
        """
        四指标综合判定情绪周期阶段。
        返回：(阶段, 置信度, 各指标判定)
        """
        scores = {'启动期': 0, '主升期': 0, '分歧期': 0, '退潮期': 0}
        details = {}

        # 涨停家数
        if limit_ups > 60:
            scores['主升期'] += 2
            details['涨停数'] = f'{limit_ups}→主升'
        elif limit_ups >= 30:
            scores['启动期'] += 1
            scores['分歧期'] += 1
            details['涨停数'] = f'{limit_ups}→启动/分歧'
        else:
            scores['退潮期'] += 2
            details['涨停数'] = f'{limit_ups}→退潮'

        # 连板高度
        if max_boards >= 5:
            scores['主升期'] += 2
            details['连板高度'] = f'{max_boards}板→主升'
        elif max_boards >= 3:
            scores['启动期'] += 1
            details['连板高度'] = f'{max_boards}板→启动'
        elif max_boards >= 2:
            scores['分歧期'] += 1
            details['连板高度'] = f'{max_boards}板→分歧'
        else:
            scores['退潮期'] += 1
            details['连板高度'] = f'{max_boards}板→退潮'

        # 炸板率
        if broken_rate < 30:
            scores['主升期'] += 1
            scores['启动期'] += 1
            details['炸板率'] = f'{broken_rate:.1f}%→主升/启动'
        elif broken_rate < 50:
            scores['分歧期'] += 2
            details['炸板率'] = f'{broken_rate:.1f}%→分歧'
        else:
            scores['退潮期'] += 2
            details['炸板率'] = f'{broken_rate:.1f}%→退潮'

        # 跌停数
        if limit_downs < 10:
            scores['主升期'] += 1
            scores['启动期'] += 1
            details['跌停数'] = f'{limit_downs}→安全'
        elif limit_downs <= 30:
            scores['分歧期'] += 2
            details['跌停数'] = f'{limit_downs}→分歧'
        else:
            scores['退潮期'] += 2
            details['跌停数'] = f'{limit_downs}→退潮'

        # 涨跌比（辅助）
        if up_ratio > 70:
            scores['主升期'] += 1
            details['涨跌比'] = f'{up_ratio:.0f}%→偏热'
        elif up_ratio < 30:
            scores['退潮期'] += 1
            details['涨跌比'] = f'{up_ratio:.0f}%→偏冷'

        # 取最高分
        best_stage = max(scores, key=scores.get)
        best_score = scores[best_stage]
        total_score = sum(scores.values())
        confidence = best_score / total_score * 100 if total_score > 0 else 0

        return best_stage, confidence, details, scores

    stage, confidence, details, scores = classify(
        limit_up_count, max_consecutive, broken_rate, limit_down_count, up_ratio
    )

    # === 输出 ===
    print(f"\n{'='*60}")
    print(f"情绪周期判定: {target_date}")
    print(f"{'='*60}")
    print(f"\n四大指标:")
    print(f"  涨停家数:   {limit_up_count}")
    print(f"  连板高度:   {max_consecutive}板 (分布: {dict(sorted(consecutive_distribution.items(), reverse=True))})")
    print(f"  炸板率:     {broken_rate:.1f}% (封死{limit_up_count-broken_count}+炸板{broken_count})")
    print(f"  跌停家数:   {limit_down_count}")
    print(f"\n辅助指标:")
    print(f"  涨跌比:     {up_count}涨/{down_count}跌/{flat_count}平 = {up_ratio:.1f}%上涨")
    print(f"\n各指标判定:")
    for k, v in details.items():
        print(f"  {k}: {v}")
    print(f"\n阶段评分: {scores}")
    print(f"\n{'='*60}")
    print(f"判定结果: 【{stage}】 置信度{confidence:.0f}%")
    print(f"{'='*60}")

    # 策略建议
    advice = {
        '主升期': '✅ 允许满仓，主线板块限定+AB竞价确认。情绪热烈，各手法均有盈利效应。',
        '启动期': '🟡 半仓试错，观察主线形成。板块轮动快，不宜重仓单一方向。',
        '分歧期': '⚠️ 降仓至50%以下，仅持有核心龙头。杂毛股下跌风险大，盈利效应集中。',
        '退潮期': '🔴 降仓至30%以下或空仓。流动性枯竭，任何操作均易亏损。',
    }
    print(f"\n策略建议: {advice.get(stage, '未知')}")

    # 输出JSON
    output = {
        'date': str(target_date),
        'indicators': {
            'limit_up_count': limit_up_count,
            'limit_down_count': limit_down_count,
            'max_consecutive_boards': max_consecutive,
            'consecutive_distribution': dict(sorted(consecutive_distribution.items(), reverse=True)),
            'broken_rate_pct': round(broken_rate, 2),
            'sealed_count': limit_up_count - broken_count,
            'broken_count': broken_count,
            'up_count': up_count,
            'down_count': down_count,
            'flat_count': flat_count,
            'up_ratio_pct': round(up_ratio, 1),
        },
        'stage': stage,
        'confidence_pct': round(confidence, 1),
        'scores': scores,
        'indicator_details': details,
        'advice': advice.get(stage, ''),
        'run_time': datetime.now().isoformat(),
    }

    out_path = os.path.join(OUT_DIR, f'sentiment_{target_date}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 结果保存: {out_path}")

    return output

if __name__ == '__main__':
    # 默认判定最新交易日，也可传参判定历史日
    main()
