#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AB候选封板时间分层回测 v3
修复：限定2026年（分钟数据完整期），避免稀疏数据稀释
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
        return 'T1_早盘_930-1000'
    elif seal_time <= 1130:
        return 'T2_上午_1000-1130'
    elif seal_time <= 1400:
        return 'T3_午后_1300-1400'
    else:
        return 'T4_尾盘_1400-1500'

def main():
    db = sqlite3.connect(DB)
    cur = db.cursor()

    # Step 1: 加载2026年日线数据
    print("Step 1: 加载2026年日线数据...")
    cur.execute('SELECT code, date, open, close, change_pct FROM stock_daily_meta WHERE date >= 20260101 AND date <= 20260809 ORDER BY code, date')

    code_data = defaultdict(list)
    for code, date, o, c, chg in cur.fetchall():
        if is_excluded(code):
            continue
        if o is None or c is None or chg is None:
            continue
        code_data[code].append((date, o, c, chg))
    print(f"  主板code数: {len(code_data)}")

    # Step 2: 找AB候选且当日涨停
    print("Step 2: 筛选AB候选涨停日...")
    ab_zt_days = []  # (code, date, zt_price, open, close)

    for code, days in code_data.items():
        if len(days) < 2:
            continue
        for i in range(1, len(days)):
            prev_date, prev_o, prev_c, prev_chg = days[i-1]
            today_date, today_o, today_c, today_chg = days[i]

            # 前日涨停 (change_pct >= 9.7)
            if prev_chg < 9.7:
                continue
            # 今日竞价高开4-7%
            if prev_c <= 0:
                continue
            gap = (today_o / prev_c - 1) * 100
            if gap < 4 or gap > 7:
                continue
            # 今日涨停
            if today_chg < 9.7:
                continue

            zt_price = round(prev_c * 1.1, 2)
            ab_zt_days.append((code, today_date, zt_price, today_o, today_c))

    print(f"  AB候选涨停日: {len(ab_zt_days)}")

    if len(ab_zt_days) == 0:
        print("  无候选，退出")
        db.close()
        return

    # Step 3: 批量查5分钟线，找首次封板时间
    print("Step 3: 查询5分钟线封板时间...")

    # 预加载所有候选的5分钟线
    tier_data = defaultdict(list)  # tier -> list of (code, date, seal_time, next_date, next_open_ret, next_close_ret)
    matched = 0
    not_matched = 0

    # 分批查询
    batch_size = 200
    for batch_start in range(0, len(ab_zt_days), batch_size):
        batch = ab_zt_days[batch_start:batch_start+batch_size]
        # 构建查询条件
        conditions = []
        params = []
        for code, date, zt_price, _, _ in batch:
            conditions.append('(code=? AND date=?)')
            params.extend([code, date])

        where_clause = ' OR '.join(conditions)
        params_tuple = tuple(params)

        # 查5分钟线
        try:
            cur.execute(f'SELECT code, date, time, high FROM stock_minute_5 WHERE {where_clause} ORDER BY code, date, time', params_tuple)
        except Exception as e:
            print(f"  查询异常: {e}")
            continue

        # 组织: (code, date) -> list of (time, high)
        minute_data = defaultdict(list)
        for code, date, time_val, high in cur.fetchall():
            if high is None:
                continue
            minute_data[(code, date)].append((time_val, high))

        # 处理每个候选
        for code, date, zt_price, today_o, today_c in batch:
            key = (code, date)
            bars = minute_data.get(key, [])

            if len(bars) == 0:
                not_matched += 1
                continue

            # 找首次封板: high >= zt_price * 0.999
            seal_time = None
            threshold = zt_price * 0.999
            for time_val, high in bars:
                if high >= threshold:
                    seal_time = time_val
                    break

            if seal_time is None:
                # 当日虽涨停（日K change_pct>=9.7），但5分钟线中high未触及涨停价
                # 可能是数据精度问题或一字板（open就在涨停价）
                # 检查是否一字板：open >= zt_price * 0.999
                if today_o >= threshold:
                    seal_time = 930  # 开盘即涨停
                else:
                    not_matched += 1
                    continue

            tier = get_tier(seal_time)
            matched += 1

            # 查次日表现
            # 在code_data中找next_date
            days_list = code_data.get(code, [])
            next_info = None
            for j in range(len(days_list)):
                if days_list[j][0] == date and j + 1 < len(days_list):
                    next_info = days_list[j + 1]
                    break

            if next_info is None:
                # 无次日数据（可能是最后一天）
                tier_data[tier].append((code, date, seal_time, None, None, False))
                continue

            next_date, next_o, next_c, next_chg = next_info
            next_open_ret = (next_o / today_c - 1) * 100 if today_c > 0 else None
            next_close_ret = (next_c / today_c - 1) * 100 if today_c > 0 else None
            next_zt = next_chg >= 9.7 if next_chg is not None else False

            tier_data[tier].append((code, date, seal_time, next_open_ret, next_close_ret, next_zt))

        if (batch_start // batch_size) % 5 == 0:
            print(f"  已处理 {batch_start + len(batch)}/{len(ab_zt_days)}...")

    print(f"\n匹配结果: 成功={matched}, 未匹配={not_matched}, 匹配率={matched/(matched+not_matched)*100:.1f}%")

    # Step 4: 分层统计
    print("\n" + "="*90)
    print(f"{'分层':<24} {'样本':>6} {'次日开盘均收益%':>16} {'次日收盘均收益%':>16} {'连板率%':>8} {'正收益占比%':>12}")
    print("="*90)

    all_results = []
    for tier in ['T1_早盘_930-1000', 'T2_上午_1000-1130', 'T3_午后_1300-1400', 'T4_尾盘_1400-1500']:
        samples = tier_data[tier]
        if len(samples) == 0:
            print(f"{tier:<24} {0:>6}")
            continue

        valid_open = [s[3] for s in samples if s[3] is not None]
        valid_close = [s[4] for s in samples if s[4] is not None]
        zt_count = sum(1 for s in samples if s[5])
        pos_count = sum(1 for r in valid_close if r > 0)

        avg_open = sum(valid_open) / len(valid_open) if valid_open else 0
        avg_close = sum(valid_close) / len(valid_close) if valid_close else 0
        zt_rate = zt_count / len(samples) * 100
        pos_rate = pos_count / len(valid_close) * 100 if valid_close else 0

        all_results.extend(valid_close)
        print(f"{tier:<24} {len(samples):>6} {avg_open:>16.2f} {avg_close:>16.2f} {zt_rate:>8.1f} {pos_rate:>12.1f}")

    print("-"*90)
    if all_results:
        avg_all = sum(all_results) / len(all_results)
        pos_all = sum(1 for r in all_results if r > 0) / len(all_results) * 100
        print(f"{'总计':<24} {len(all_results):>6} {'':>16} {avg_all:>16.2f} {'':>8} {pos_all:>12.1f}")

    # 额外: 开盘高开统计
    print("\n--- 次日开盘高开分布 ---")
    all_opens = []
    for tier_samples in tier_data.values():
        for s in tier_samples:
            if s[3] is not None:
                all_opens.append(s[3])

    if all_opens:
        ge3 = sum(1 for r in all_opens if r >= 3)
        ge5 = sum(1 for r in all_opens if r >= 5)
        neg = sum(1 for r in all_opens if r < 0)
        print(f"  开盘>=+3%: {ge3}/{len(all_opens)} = {ge3/len(all_opens)*100:.1f}%")
        print(f"  开盘>=+5%: {ge5}/{len(all_opens)} = {ge5/len(all_opens)*100:.1f}%")
        print(f"  开盘<0%: {neg}/{len(all_opens)} = {neg/len(all_opens)*100:.1f}%")

    db.close()
    print("\n完成。")

if __name__ == '__main__':
    main()
