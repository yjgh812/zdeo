#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AB候选封板时间分层回测
====================
目标：验证交易计划v3中封板时间评分权重是否与实证一致
评分体系：09:30-10:00=+2 / 10-11:30=+1 / 13-14:00=0 / 14:00后=-1~-2

数据源：dragon_quant_meta.db
  - stock_daily_meta: 日K（找AB候选+次日表现）
  - stock_minute_5: 5分钟线（找首次封板时间）

分层：
  T1 早盘封板: 09:30-10:00 (time 930-1000)
  T2 上午封板: 10:00-11:30 (time 1005-1130)
  T3 午后封板: 13:00-14:00 (time 1300-1400)
  T4 尾盘封板: 14:00-15:00 (time 1405-1500)
"""
import sqlite3
from datetime import datetime

DB_PATH = 'D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db'

def is_excluded(code):
    """排除科创板(688)、北交所(8/4开头)、ST等"""
    if not code or len(code) < 6:
        return True
    if code.startswith('688') or code.startswith('8') or code.startswith('4'):
        return True
    if code.startswith('300'):  # 创业板20%涨停，样本量少先排除
        return True
    return False

def zt_threshold(code):
    """涨停阈值"""
    if code.startswith('300') or code.startswith('688'):
        return 19.5  # 20%涨停，留0.5%容差
    return 9.7  # 10%涨停，留0.3%容差

def main():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()

    print("=" * 80)
    print("AB候选封板时间分层回测")
    print(f"数据库: {DB_PATH}")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # ========== Step 1: 找AB候选（前日涨停+今日竞价缺口4-7%）==========
    print("\n[Step 1] 扫描AB候选...")

    cur.execute("""
        SELECT code, date, open, high, low, close, change_pct
        FROM stock_daily_meta
        WHERE date >= 20210101 AND date <= 20260807
          AND change_pct IS NOT NULL
        ORDER BY code, date
    """)
    all_rows = cur.fetchall()
    print(f"  日K总行数: {len(all_rows):,}")

    # 按code分组
    from collections import defaultdict
    by_code = defaultdict(list)
    for row in all_rows:
        code = row[0]
        if is_excluded(code):
            continue
        by_code[code].append(row)

    # 找AB候选
    ab_candidates = []
    for code, rows in by_code.items():
        rows.sort(key=lambda x: x[1])  # 按date排序
        zt = zt_threshold(code)

        for i in range(1, len(rows)):
            prev = rows[i-1]
            curr = rows[i]

            prev_pct = prev[6]  # change_pct
            if prev_pct is None:
                continue

            # 前日涨停
            if prev_pct < zt:
                continue

            prev_close = prev[5]  # 前日收盘
            curr_open = curr[2]   # 今日开盘
            if prev_close is None or prev_close <= 0 or curr_open is None:
                continue

            gap = (curr_open / prev_close - 1) * 100

            # 竞价缺口4-7%
            if gap < 4.0 or gap > 7.0:
                continue

            # 今日是否涨停
            curr_pct = curr[6]
            if curr_pct is None:
                continue

            is_zt_today = curr_pct >= zt

            # 次日数据
            next_day = None
            if i + 1 < len(rows):
                nd = rows[i + 1]
                if nd[5] is not None and nd[2] is not None and curr[5] is not None:
                    next_day = nd

            ab_candidates.append({
                'code': code,
                'date': curr[1],
                'open': curr_open,
                'close': curr[5],
                'change_pct': curr_pct,
                'is_zt': is_zt_today,
                'gap_pct': gap,
                'next': next_day,
            })

    total_ab = len(ab_candidates)
    zt_ab = [c for c in ab_candidates if c['is_zt']]
    print(f"  AB候选总数: {total_ab:,}")
    print(f"  其中当日涨停: {len(zt_ab):,} ({len(zt_ab)/total_ab*100:.1f}%)")

    # ========== Step 2: 对涨停候选找首次封板时间 ==========
    print("\n[Step 2] 查找5分钟线封板时间...")

    # 批量查询5分钟线
    seal_times = {}  # (code, date) -> first seal time

    batch_size = 500
    for batch_start in range(0, len(zt_ab), batch_size):
        batch = zt_ab[batch_start:batch_start + batch_size]
        placeholders = ','.join(['(?,?)'] * len(batch))
        params = []
        for c in batch:
            params.extend([c['code'], c['date']])

        cur.execute(f"""
            SELECT code, date, time, high, low, close
            FROM stock_minute_5
            WHERE (code, date) IN ({placeholders})
            ORDER BY code, date, time
        """, params)

        # 按(code,date)分组
        minute_data = defaultdict(list)
        for r in cur.fetchall():
            minute_data[(r[0], r[1])].append(r)

        for c in batch:
            key = (c['code'], c['date'])
            bars = minute_data.get(key, [])
            if not bars:
                continue

            # 涨停价 = 前日收盘 * (1 + 涨停幅度)
            # 用日K的change_pct反推不够精确，改用5分钟线最高价≈涨停价来判断
            # 更好的方法：日K的close如果≈涨停，则找5分钟线中close首次达到日K close的时间
            zt_price = c['close']  # 如果涨停了，收盘价就是涨停价
            tolerance = zt_price * 0.001  # 0.1%容差

            for bar in bars:
                bar_close = bar[5]
                if bar_close is not None and abs(bar_close - zt_price) <= tolerance:
                    seal_times[key] = bar[2]  # time
                    break

        if (batch_start // batch_size) % 5 == 0:
            found = sum(1 for c in batch if (c['code'], c['date']) in seal_times)
            print(f"  进度: {batch_start + len(batch)}/{len(zt_ab)} (本批找到封板时间: {found}/{len(batch)})")

    found_count = len(seal_times)
    print(f"  找到封板时间: {found_count}/{len(zt_ab)} ({found_count/len(zt_ab)*100:.1f}%)")

    # ========== Step 3: 分层统计 ==========
    print("\n[Step 3] 封板时间分层统计")

    def get_tier(seal_time):
        """返回分层标签"""
        if seal_time <= 1000:
            return 'T1_早盘(09:30-10:00)'
        elif seal_time <= 1130:
            return 'T2_上午(10:00-11:30)'
        elif seal_time <= 1400:
            return 'T3_午后(13:00-14:00)'
        else:
            return 'T4_尾盘(14:00-15:00)'

    tiers = defaultdict(lambda: {'count': 0, 'next_open_ret': [], 'next_close_ret': [], 'ge3pct': 0})

    for c in zt_ab:
        key = (c['code'], c['date'])
        if key not in seal_times:
            continue
        if c['next'] is None:
            continue

        seal_t = seal_times[key]
        tier = get_tier(seal_t)

        curr_close = c['close']
        next_open = c['next'][2]
        next_close = c['next'][5]

        if curr_close <= 0:
            continue

        open_ret = (next_open / curr_close - 1) * 100
        close_ret = (next_close / curr_close - 1) * 100

        tiers[tier]['count'] += 1
        tiers[tier]['next_open_ret'].append(open_ret)
        tiers[tier]['next_close_ret'].append(close_ret)
        if open_ret >= 3.0:
            tiers[tier]['ge3pct'] += 1

    # 打印结果
    print("\n" + "=" * 80)
    print("封板时间分层回测结果")
    print("=" * 80)
    print(f"\n{'分层':<28} {'样本数':>6} {'次日开盘均收益%':>14} {'次日收盘均收益%':>14} {'开盘≥3%命中率':>12}")
    print("-" * 80)

    tier_order = ['T1_早盘(09:30-10:00)', 'T2_上午(10:00-11:30)', 'T3_午后(13:00-14:00)', 'T4_尾盘(14:00-15:00)']
    all_open_rets = []
    all_close_rets = []

    for tier in tier_order:
        d = tiers[tier]
        if d['count'] == 0:
            print(f"{tier:<28} {'0':>6} {'N/A':>14} {'N/A':>14} {'N/A':>12}")
            continue

        avg_open = sum(d['next_open_ret']) / d['count']
        avg_close = sum(d['next_close_ret']) / d['count']
        hit_rate = d['ge3pct'] / d['count'] * 100

        print(f"{tier:<28} {d['count']:>6} {avg_open:>14.2f} {avg_close:>14.2f} {hit_rate:>11.1f}%")

        all_open_rets.extend(d['next_open_ret'])
        all_close_rets.extend(d['next_close_ret'])

    print("-" * 80)
    total_n = len(all_open_rets)
    if total_n > 0:
        print(f"{'合计':<28} {total_n:>6} {sum(all_open_rets)/total_n:>14.2f} {sum(all_close_rets)/total_n:>14.2f}")

    # ========== Step 4: 中位数和分布 ==========
    print("\n" + "=" * 80)
    print("各分层中位数与分布")
    print("=" * 80)
    print(f"\n{'分层':<28} {'开盘收益中位数%':>14} {'正收益占比':>10} {'亏损>3%占比':>10}")
    print("-" * 80)

    for tier in tier_order:
        d = tiers[tier]
        if d['count'] == 0:
            continue
        sorted_rets = sorted(d['next_open_ret'])
        median = sorted_rets[len(sorted_rets)//2]
        positive = sum(1 for r in d['next_open_ret'] if r > 0) / d['count'] * 100
        big_loss = sum(1 for r in d['next_open_ret'] if r < -3) / d['count'] * 100
        print(f"{tier:<28} {median:>14.2f} {positive:>9.1f}% {big_loss:>9.1f}%")

    # ========== Step 5: 验证评分体系 ==========
    print("\n" + "=" * 80)
    print("评分体系验证")
    print("=" * 80)

    # 当前评分：T1=+2, T2=+1, T3=0, T4=-1~-2
    # 如果实证显示 T1 >> T2 >> T3 > T4，则评分合理
    score_map = {'T1_早盘(09:30-10:00)': 2, 'T2_上午(10:00-11:30)': 1, 'T3_午后(13:00-14:00)': 0, 'T4_尾盘(14:00-15:00)': -2}

    print("\n封板时间 vs 次日开盘收益排序检查：")
    tier_avg = {}
    for tier in tier_order:
        d = tiers[tier]
        if d['count'] >= 10:  # 至少10个样本
            tier_avg[tier] = sum(d['next_open_ret']) / d['count']

    sorted_tiers = sorted(tier_avg.items(), key=lambda x: -x[1])
    for i, (tier, avg) in enumerate(sorted_tiers):
        score = score_map.get(tier, '?')
        print(f"  第{i+1}名: {tier} → 次日开盘均收益 {avg:.2f}% → 当前评分 {score}")

    # 检查是否单调递减
    scores_by_rank = [score_map.get(t[0], 0) for t in sorted_tiers]
    is_monotonic = all(scores_by_rank[i] >= scores_by_rank[i+1] for i in range(len(scores_by_rank)-1))
    print(f"\n  评分单调性检验: {'✅ 通过（收益递减→评分递减）' if is_monotonic else '❌ 未通过（评分与收益不匹配）'}")

    # ========== 结论 ==========
    print("\n" + "=" * 80)
    print("结论与建议")
    print("=" * 80)

    t1 = tiers.get('T1_早盘(09:30-10:00)', {'count': 0})
    t4 = tiers.get('T4_尾盘(14:00-15:00)', {'count': 0})

    if t1['count'] >= 10 and t4['count'] >= 10:
        t1_avg = sum(t1['next_open_ret']) / t1['count']
        t4_avg = sum(t4['next_open_ret']) / t4['count']
        diff = t1_avg - t4_avg
        print(f"\n  早盘 vs 尾盘 次日开盘收益差: {diff:.2f}%")
        if diff > 1.0:
            print(f"  → 早盘封板显著优于尾盘，当前评分体系(T1=+2, T4=-2)合理")
        elif diff > 0:
            print(f"  → 早盘略优于尾盘，但差异不大，建议维持现有评分")
        else:
            print(f"  → ⚠️ 尾盘封板反而更优，需重新审视评分体系")

    db.close()
    print(f"\n回测完成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == '__main__':
    main()
