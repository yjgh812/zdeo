#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AB候选封板时间分层回测 v2
修复：用 high >= 涨停价 检测首次封板时间
"""
import sqlite3
from collections import defaultdict

DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db'

def is_excluded(code):
    if code.startswith('688') or code.startswith('8') or code.startswith('4'):
        return True
    if code.startswith('300'):
        return True
    return False

def get_tier(seal_time):
    if seal_time <= 1000:
        return 'T1_早盘'
    elif seal_time <= 1130:
        return 'T2_上午'
    elif seal_time <= 1400:
        return 'T3_午后'
    else:
        return 'T4_尾盘'

def main():
    db = sqlite3.connect(DB)
    cur = db.cursor()

    # Step 1: 每个code的所有交易日 + close，按时间排序
    print("Step 1: 加载日线数据...")
    cur.execute('SELECT code, date, open, close, change_pct FROM stock_daily_meta WHERE date >= 20190101 ORDER BY code, date')

    # 组织: code -> list of (date, open, close, change_pct)
    code_data = defaultdict(list)
    for code, date, o, c, chg in cur.fetchall():
        if is_excluded(code):
            continue
        if o is None or c is None or chg is None:
            continue
        code_data[code].append((date, o, c, chg))
    print(f"  主板code数: {len(code_data)}")

    # Step 2: 找AB候选且当日涨停 (前日涨停 + 今日竞价高开4-7% + 今日涨停)
    print("\nStep 2: 筛选AB候选(前日涨停+今日高开4-7%+今日涨停)...")
    ab_zt = []  # (code, date, prev_close, zt_price)
    for code, dates in code_data.items():
        for i in range(1, len(dates)):
            prev_date, prev_open, prev_close, prev_chg = dates[i-1]
            today_date, today_open, today_close, today_chg = dates[i]

            # 前日涨停
            if prev_chg < 9.7:
                continue
            # 今日竞价高开4-7%（今日open相对前日close）
            gap = (today_open / prev_close - 1) * 100
            if gap < 4 or gap > 7:
                continue
            # 今日涨停
            if today_chg < 9.7:
                continue

            zt_price = round(prev_close * 1.1, 2)
            ab_zt.append((code, today_date, zt_price))

    print(f"  AB候选涨停日: {len(ab_zt)}")

    # Step 3: 批量查5分钟线，找首次 high >= zt_price
    print(f"\nStep 3: 查询5分钟线封板时间...")
    zt_map = {(c, d): zt for c, d, zt in ab_zt}
    seal_times = {}
    no_minute = 0

    batch_size = 500
    total = len(ab_zt)
    for start in range(0, total, batch_size):
        batch = ab_zt[start:start+batch_size]

        # 用 IN (code, date, code, date ...) 方式
        # SQLite 支持 (col1, col2) IN ((v1a,v1b),(v2a,v2b),...)
        parts = []
        params = []
        for c, d, zt in batch:
            parts.append("(code=? AND date=?)")
            params.extend([c, d])
        where_clause = " OR ".join(parts)

        cur.execute(f'''
            SELECT code, date, time, high
            FROM stock_minute_5
            WHERE {where_clause}
            ORDER BY code, date, time
        ''', params)

        # 按code+date分组
        minute_data = defaultdict(list)
        for code, date, t, h in cur.fetchall():
            minute_data[(code, date)].append((t, h))

        for c, d, zt_price in batch:
            bars = minute_data.get((c, d))
            if not bars:
                no_minute += 1
                continue
            threshold = zt_price * 0.999  # 0.1%容差
            for t, h in bars:
                if h >= threshold:
                    seal_times[(c, d)] = t
                    break

        if start % 5000 == 0:
            print(f"  进度: {start}/{total}, 已匹配: {len(seal_times)}, 无分钟数据: {no_minute}")

    print(f"\n  封板时间匹配: {len(seal_times)} / {total} = {len(seal_times)/total*100:.1f}%")
    print(f"  无分钟数据: {no_minute}")

    # Step 4: 分层统计 + 次日表现
    print(f"\nStep 4: 分层统计次日表现...")
    tier_stats = defaultdict(lambda: {'count': 0, 'next_open_ret': [], 'next_close_ret': [], 'next_zt': 0})

    # 需要次日数据：从code_data获取
    # 建 code -> {date: (open, close, chg)} 索引
    code_date_map = {}
    for code, dates in code_data.items():
        m = {d[0]: (d[1], d[2], d[3]) for d in dates}
        code_date_map[code] = m

    for (code, date), seal_time in seal_times.items():
        tier = get_tier(seal_time)
        dmap = code_date_map.get(code)
        if not dmap:
            continue

        # 找次日
        next_dates = sorted([d for d in dmap.keys() if d > date])
        if not next_dates:
            continue
        nd = next_dates[0]
        next_open, next_close, next_chg = dmap[nd]

        # 今日收盘价
        _, today_close, _ = dmap.get(date, (None, None, None))
        if today_close is None or today_close == 0:
            continue

        next_open_ret = (next_open / today_close - 1) * 100
        next_close_ret = (next_close / today_close - 1) * 100
        next_zt = 1 if next_chg >= 9.7 else 0

        tier_stats[tier]['count'] += 1
        tier_stats[tier]['next_open_ret'].append(next_open_ret)
        tier_stats[tier]['next_close_ret'].append(next_close_ret)
        tier_stats[tier]['next_zt'] += next_zt

    # 输出
    print(f"\n{'='*90}")
    print(f"{'分层':<12} {'样本':>6} {'次日开盘均收益%':>16} {'次日收盘均收益%':>16} {'连板率%':>10} {'正收益占比%':>12}")
    print(f"{'='*90}")

    for tier in ['T1_早盘', 'T2_上午', 'T3_午后', 'T4_尾盘']:
        s = tier_stats[tier]
        n = s['count']
        if n == 0:
            print(f"{tier:<12} {0:>6} {'N/A':>16} {'N/A':>16} {'N/A':>10} {'N/A':>12}")
            continue
        avg_open = sum(s['next_open_ret']) / n
        avg_close = sum(s['next_close_ret']) / n
        zt_rate = s['next_zt'] / n * 100
        pos_rate = sum(1 for r in s['next_close_ret'] if r > 0) / n * 100
        print(f"{tier:<12} {n:>6} {avg_open:>16.2f} {avg_close:>16.2f} {zt_rate:>10.1f} {pos_rate:>12.1f}")

    total_n = sum(s['count'] for s in tier_stats.values())
    if total_n > 0:
        all_open = []
        all_close = []
        all_zt = 0
        for s in tier_stats.values():
            all_open.extend(s['next_open_ret'])
            all_close.extend(s['next_close_ret'])
            all_zt += s['next_zt']
        pos_all = sum(1 for r in all_close if r > 0) / total_n * 100
        print(f"{'-'*90}")
        print(f"{'总计':<12} {total_n:>6} {sum(all_open)/total_n:>16.2f} {sum(all_close)/total_n:>16.2f} {all_zt/total_n*100:>10.1f} {pos_all:>12.1f}")

    print(f"\n{'='*90}")
    print("\n验证交易计划v3封板时间评分权重:")
    print("  v3: T1早盘=+2 / T2上午=+1 / T3午后=0 / T4尾盘=-1~-2")
    print("  若T1表现显著优于T4，则权重合理；若差异不大，需调整")

    db.close()

if __name__ == '__main__':
    main()
