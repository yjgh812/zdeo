#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪周期量化复合指数 v1.0（8/13全维度自省 M-6）

========== 用途 ==========
在已有 sentiment_cycle.py 的基础上，输出标准化复合情绪指数（0-100），
供闸门系统、仓位管理、选股决策实时读取。

========== 复合指数构成 ==========
指数 = 涨停活跃度(30%) + 连板强度(25%) + 炸板质量(20%) + 跌停压力(15%) + 涨跌比(10%)

各维度归一化到 0-100，越低越冷，越高越热。

========== 情绪阶段量化映射 ==========
| 指数区间 | 阶段 | 仓位建议 | 操作原则 |
|----------|------|----------|----------|
| 0-20     | 冰点❄️ | ≤30% | 空仓/仅低吸防守 |
| 20-40    | 冷🥶  | ≤50% | 半仓试错，只做最强主线 |
| 40-60    | 常温🌤️ | ≤70% | 正常操作，严控回撤 |
| 60-80    | 热🔥  | ≤90% | 积极操作，主线重仓 |
| 80-100   | 沸点💥 | ≤100% | 满仓，但警惕退潮信号 |

========== 用法 ==========
    # 计算今日指数（自动取最新交易日）
    python emotion_index.py

    # 指定日期
    python emotion_index.py --date 20260813

    # 输出 JSON 供其它脚本消费
    python emotion_index.py --json

    # 集成到 daily_chain
    python emotion_index.py --to-db
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

DB_PATH = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
OUT_DIR = r'D:\zcodesj\attribution'
REPORT_DIR = r'D:\zcodesj\reports'


def is_excluded(code: str) -> bool:
    return code.startswith('688') or code.startswith('8') or code.startswith('4')


def get_zt_threshold(code: str) -> float:
    return 0.195 if code.startswith('300') or code.startswith('301') else 0.095


def compute_indicators(target_date: str) -> dict:
    """计算情绪指标（复用 sentiment_cycle 核心逻辑但输出更标准化）。"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 取前5个交易日
    c.execute("""
        SELECT DISTINCT date FROM stock_daily_meta
        WHERE date < ? ORDER BY date DESC LIMIT 5
    """, (target_date,))
    prev_dates = [r[0] for r in c.fetchall()]

    # 加载当日数据
    c.execute("""
        SELECT code, open, high, low, close, volume, change_pct
        FROM stock_daily_meta WHERE date = ?
    """, (target_date,))
    today_rows = c.fetchall()

    # 加载历史数据（连板计算）
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
    hist_by_code: dict[str, dict] = defaultdict(dict)
    for r in hist_rows:
        code = r[0]
        if is_excluded(code):
            continue
        d = r[1]
        hist_by_code[code][d] = {
            'open': r[2], 'high': r[3], 'low': r[4],
            'close': r[5], 'volume': r[6], 'pct': r[7],
        }

    # ===== 计算各项指标 =====
    limit_ups, limit_downs, broken_boards, total_valid = [], [], [], 0
    up_count, down_count, flat_count = 0, 0, 0

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

        # 涨跌统计
        if pct > 0:
            up_count += 1
        elif pct < 0:
            down_count += 1
        else:
            flat_count += 1

        # 涨停/炸板/跌停
        if pct >= threshold:
            sealed = d['close'] >= d['high'] * 0.999
            if sealed:
                limit_ups.append(code)
            else:
                broken_boards.append(code)
        elif pct <= -threshold:
            limit_downs.append(code)

    # 连板计算
    max_consecutive = 0
    consecutive_distribution: dict[int, int] = defaultdict(int)
    for code in limit_ups:
        streak = 1
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

    limit_up_count = len(limit_ups)
    limit_down_count = len(limit_downs)
    broken_count = len(broken_boards)
    total_attempted = limit_up_count + broken_count
    broken_rate = broken_count / total_attempted * 100 if total_attempted > 0 else 0
    up_ratio = up_count / total_valid * 100 if total_valid > 0 else 0

    return {
        'date': target_date,
        'limit_up_count': limit_up_count,
        'limit_down_count': limit_down_count,
        'max_consecutive_boards': max_consecutive,
        'consecutive_distribution': dict(sorted(consecutive_distribution.items(), reverse=True)),
        'broken_count': broken_count,
        'sealed_count': limit_up_count - broken_count,
        'broken_rate_pct': round(broken_rate, 2),
        'up_count': up_count,
        'down_count': down_count,
        'flat_count': flat_count,
        'total_valid': total_valid,
        'up_ratio_pct': round(up_ratio, 1),
    }


def compute_emotion_index(indicators: dict) -> dict:
    """基于原始指标计算复合情绪指数 0-100。"""
    # ===== 维度1：涨停活跃度 (30%) =====
    lu = indicators['limit_up_count']
    # 基准：<20=0分, 20-40=25, 40-60=50, 60-80=75, >80=100
    if lu >= 80:
        lu_score = 100
    elif lu >= 60:
        lu_score = 75
    elif lu >= 40:
        lu_score = 50
    elif lu >= 20:
        lu_score = 25
    else:
        lu_score = 0

    # ===== 维度2：连板强度 (25%) =====
    mb = indicators['max_consecutive_boards']
    if mb >= 7:
        mb_score = 100
    elif mb >= 5:
        mb_score = 80
    elif mb >= 3:
        mb_score = 50
    elif mb >= 2:
        mb_score = 25
    else:
        mb_score = 0

    # ===== 维度3：炸板质量 (20%) =====
    br = indicators['broken_rate_pct']
    if br <= 15:
        br_score = 100
    elif br <= 25:
        br_score = 75
    elif br <= 35:
        br_score = 50
    elif br <= 50:
        br_score = 25
    else:
        br_score = 0

    # ===== 维度4：跌停压力 (15%) =====
    ld = indicators['limit_down_count']
    if ld <= 3:
        ld_score = 100
    elif ld <= 8:
        ld_score = 75
    elif ld <= 15:
        ld_score = 50
    elif ld <= 30:
        ld_score = 25
    else:
        ld_score = 0

    # ===== 维度5：涨跌比 (10%) =====
    ur = indicators['up_ratio_pct']
    if ur >= 70:
        ur_score = 100
    elif ur >= 50:
        ur_score = 75
    elif ur >= 30:
        ur_score = 50
    elif ur >= 15:
        ur_score = 25
    else:
        ur_score = 0

    # ===== 复合指数 (加权) =====
    composite = (
        lu_score * 0.30 +
        mb_score * 0.25 +
        br_score * 0.20 +
        ld_score * 0.15 +
        ur_score * 0.10
    )

    # ===== 情绪阶段判定 =====
    if composite >= 80:
        stage = '沸点💥'
        stage_en = 'OVERHEAT'
        advice = '满仓操作，但警惕退潮信号'
    elif composite >= 60:
        stage = '热🔥'
        stage_en = 'HOT'
        advice = '积极操作，主线重仓'
    elif composite >= 40:
        stage = '常温🌤️'
        stage_en = 'NORMAL'
        advice = '正常操作，严控回撤'
    elif composite >= 20:
        stage = '冷🥶'
        stage_en = 'COLD'
        advice = '半仓试错，只做最强主线'
    else:
        stage = '冰点❄️'
        stage_en = 'ICE'
        advice = '空仓或仅低吸防守'

    return {
        'composite_index': round(composite, 1),
        'stage': stage,
        'stage_en': stage_en,
        'advice': advice,
        'dimensions': {
            'limit_up_score': {'weight': 0.30, 'value': lu_score, 'raw': indicators['limit_up_count']},
            'board_score': {'weight': 0.25, 'value': mb_score, 'raw': indicators['max_consecutive_boards']},
            'break_rate_score': {'weight': 0.20, 'value': br_score, 'raw': indicators['broken_rate_pct']},
            'limit_down_score': {'weight': 0.15, 'value': ld_score, 'raw': indicators['limit_down_count']},
            'up_ratio_score': {'weight': 0.10, 'value': ur_score, 'raw': indicators['up_ratio_pct']},
        },
        'position_suggest': {
            'max_position_pct': min(100, int(composite + 10)),
            'description': f'情绪指数{composite:.0f} → 建议仓位上限{min(100, int(composite + 10))}%',
        },
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description='情绪周期量化复合指数 M-6')
    p.add_argument('--date', default=None, help='日期 YYYYMMDD（默认最新交易日）')
    p.add_argument('--json', action='store_true', help='仅输出 JSON')
    p.add_argument('--to-db', action='store_true', help='写入数据库（集成 daily_chain）')
    a = p.parse_args()

    # 确定目标日期
    target_date = a.date
    if target_date is None:
        conn = sqlite3.connect(DB_PATH)
        target_date = conn.execute("SELECT MAX(date) FROM stock_daily_meta").fetchone()[0]
        conn.close()

    # 计算
    indicators = compute_indicators(target_date)
    emotion = compute_emotion_index(indicators)

    # 组装输出
    output = {
        'timestamp': datetime.now().isoformat(),
        'date': target_date,
        'indicators': indicators,
        'emotion': emotion,
    }

    # 输出 JSON
    if a.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    # 写文件
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f'emotion_index_{target_date}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 写入 trade_state（供 R14-R18 读取）
    if a.to_db:
        try:
            sys.path.insert(0, r'D:\zcodesj')
            from trade_state import load_state, save_state
            state = load_state()
            state['emotion_index'] = emotion['composite_index']
            state['emotion_stage'] = emotion['stage_en']
            state['emotion_advice'] = emotion['advice']
            save_state(state)
            print(f"✅ 情绪指数已写入 trade_state: {emotion['composite_index']} ({emotion['stage']})")
        except Exception as e:
            print(f"⚠️ trade_state 写入失败: {e}", file=sys.stderr)

    # 控制台输出
    dashes = '=' * 60
    print(f"\n{dashes}")
    print(f"  情绪周期量化复合指数 M-6 | {target_date}")
    print(dashes)
    i = indicators
    print(f"\n📊 原始指标:")
    print(f"  涨停 {i['limit_up_count']} / 跌停 {i['limit_down_count']} / 炸板 {i['broken_count']}({i['broken_rate_pct']:.1f}%)")
    print(f"  最高连板 {i['max_consecutive_boards']}板 / 上涨占比 {i['up_ratio_pct']:.0f}%")
    print(f"\n🧮 复合指数: {emotion['composite_index']:.1f}/100")
    print(f"  阶段: {emotion['stage']} ({emotion['stage_en']})")
    print(f"  建议: {emotion['advice']}")
    print(f"\n📐 各维度得分:")
    for dim_name, dim_data in emotion['dimensions'].items():
        bar = '█' * int(dim_data['value'] / 10) + '░' * (10 - int(dim_data['value'] / 10))
        print(f"  {dim_name:20s} {dim_data['value']:3d}/100 {bar} (权重{dim_data['weight']:.0%})")
    print(f"\n📋 仓位建议: {emotion['position_suggest']['description']}")
    print(f"\n✅ 已保存: {path}")
    print(dashes)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())