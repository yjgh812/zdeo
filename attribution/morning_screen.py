#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘前选股脚本——竞价结束后运行(9:25后)
从westock实时行情获取竞价数据，结合qlib因子，输出各策略买入标的。

用法：python morning_screen.py
输出：控制台推荐 + DB写入verification_trades
"""
import sqlite3
import json
import os
import math
from datetime import datetime

DB_PATH = r'D:\zcodesj\attribution\attribution.db'

# 策略定义
STRATEGIES = {
    'V1': {
        'name': '低开承接',
        'hypothesis': '前日涨停+今日竞价低开2-4%→T+1接近盈亏平衡(PF=0.89)',
        'expected': 'T+1正收益或接近持平',
        'filter': lambda s: s.get('prev_zt') and s.get('gap_pct') is not None 
                            and 0.02 <= s.get('gap_pct', 99) <= 0.04,
        'max_picks': 3,
    },
    'V2': {
        'name': '5日高动量',
        'hypothesis': 'mom5>5%的股票涨停率高(IC=0.018 p=0)',
        'expected': '涨停率高于全市场',
        'filter': lambda s: s.get('mom5') is not None and s.get('mom5', 0) > 0.05,
        'max_picks': 2,
    },
    'V3': {
        'name': '高波+正动量',
        'hypothesis': 'rv20 Top10%+mom5>0→涨停率9.2倍',
        'expected': '涨停率显著高于基线',
        'filter': lambda s: s.get('rv20_rank') is not None and s.get('rv20_rank', 999) <= 0.10
                            and s.get('mom5', 0) > 0,
        'max_picks': 2,
    },
    'V5_low': {
        'name': 'AB低开(0-2%)',
        'hypothesis': '前日涨停+微幅低开0-2%测试',
        'expected': '与V1对比哪个低开区间更好',
        'filter': lambda s: s.get('prev_zt') and 0.0 <= s.get('gap_pct', 99) < 0.02,
        'max_picks': 2,
    },
    'V5_mid': {
        'name': 'AB中开(4-7%)',
        'hypothesis': '原R6的AB确认区间(已回测PF=0.65)',
        'expected': '验证回测结论是否在模拟盘成立',
        'filter': lambda s: s.get('prev_zt') and 0.04 <= s.get('gap_pct', 99) <= 0.07,
        'max_picks': 2,
    },
    'V6': {
        'name': '超跌反弹',
        'hypothesis': '60日跌>20%+近期企稳(mom5>0)→反转IC=-0.006',
        'expected': '反转收益',
        'filter': lambda s: s.get('mom60') is not None and s.get('mom60', 0) < -0.20
                            and s.get('mom5', 0) > 0,
        'max_picks': 2,
    },
}

def get_realtime_quotes(stock_codes):
    """从候选JSON获取最新因子（实际使用时替换为实时行情接口）"""
    candidates_path = r'D:\zcodesj\attribution\candidates_latest.json'
    if not os.path.exists(candidates_path):
        return {}

    with open(candidates_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    result = {}
    # 合并所有策略的候选
    all_stocks = {}
    for strategy, stocks in data.get('candidates', {}).items():
        for s in stocks:
            code = s['stock']
            if code not in all_stocks:
                all_stocks[code] = s

    # 计算rv20排名百分比
    if all_stocks:
        sorted_rv = sorted(all_stocks.values(), key=lambda x: x.get('rv20', 0), reverse=True)
        n = len(sorted_rv)
        for rank, s in enumerate(sorted_rv):
            code = s['stock']
            all_stocks[code]['rv20_rank'] = (rank + 1) / n

    return all_stocks

def run_screen():
    """执行盘前筛选"""
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"{'='*60}")
    print(f"盘前选股 {today}")
    print(f"{'='*60}")

    # 获取候选数据
    all_stocks = get_realtime_quotes(None)
    if not all_stocks:
        print("⚠️ 未找到候选数据。请先运行 init_verification_trades.py 生成候选池")
        print("   或在竞价结束后更新竞价缺口数据")
        return

    print(f"候选池: {len(all_stocks)}只\n")

    # 按策略筛选
    picks = {}
    for sid, strat in STRATEGIES.items():
        matched = []
        for code, s in all_stocks.items():
            try:
                if strat['filter'](s):
                    matched.append({**s, 'stock': code})
            except Exception:
                continue

        # 排序取最优
        if sid == 'V2':
            matched.sort(key=lambda x: x.get('mom5', 0), reverse=True)
        elif sid == 'V3':
            matched.sort(key=lambda x: x.get('rv20', 0), reverse=True)
        elif sid == 'V6':
            matched.sort(key=lambda x: x.get('mom60', 0))

        picks[sid] = matched[:strat['max_picks']]

    # 输出推荐
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    total_picks = 0

    for sid, strat in STRATEGIES.items():
        matched = picks[sid]
        if not matched:
            print(f"[{sid} {strat['name']}] 无候选")
            continue

        print(f"\n[{sid} {strat['name']}] {len(matched)}只")
        print(f"  假设: {strat['hypothesis']}")
        print(f"  预期: {strat['expected']}")

        for s in matched:
            code = s['stock']
            print(f"  → {code} close={s.get('close','?')} " +
                  f"mom5={s.get('mom5','?')}% rv20={s.get('rv20','?')}%")

            # 写入DB
            try:
                c.execute('''INSERT OR IGNORE INTO verification_trades
                    (trade_date, stock_code, strategy_id, hypothesis, expected_outcome,
                     signal_snapshot, notes, created_at)
                    VALUES (?,?,?,?,?,?,?,?)''', (
                    today, code, sid, strat['hypothesis'], strat['expected'],
                    json.dumps({k: v for k, v in s.items() if k != 'stock'}, ensure_ascii=False),
                    f'盘前推荐 close={s.get("close")}',
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))
                total_picks += 1
            except Exception as e:
                pass

    conn.commit()
    conn.close()

    print(f"\n{'='*60}")
    print(f"总计推荐: {total_picks}只 → verification_trades表")
    print(f"\n下一步:")
    print(f"  1. 竞价结束后(9:25)更新实际竞价缺口")
    print(f"  2. 确认买入后回填entry_price和volume")
    print(f"  3. 盘后运行 afternoon_fillback.py 回填收益")

def update_auction_gap(stock_code, gap_pct):
    """手动更新竞价缺口（竞价结束后调用）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')

    c.execute('''UPDATE verification_trades SET signal_snapshot = 
        json_set(COALESCE(signal_snapshot, '{}'), '$.auction_gap_pct', ?)
        WHERE stock_code = ? AND trade_date = ?''',
        (gap_pct, stock_code, today))

    conn.commit()
    conn.close()
    print(f"[OK] {stock_code} 竞价缺口更新为 {gap_pct*100:.2f}%")

def record_buy(stock_code, entry_price, volume, strategy_id=None):
    """记录实际买入"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')

    if strategy_id:
        c.execute('''UPDATE verification_trades 
            SET entry_price = ?, volume = ?
            WHERE stock_code = ? AND trade_date = ? AND strategy_id = ?''',
            (entry_price, volume, stock_code, today, strategy_id))
    else:
        c.execute('''UPDATE verification_trades 
            SET entry_price = ?, volume = ?
            WHERE stock_code = ? AND trade_date = ?''',
            (entry_price, volume, stock_code, today))

    affected = c.rowcount
    if affected == 0:
        # 新插入
        c.execute('''INSERT INTO verification_trades
            (trade_date, stock_code, strategy_id, entry_price, volume, created_at)
            VALUES (?,?,?,?,?,?)''',
            (today, stock_code, strategy_id or 'MANUAL', entry_price, volume,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    conn.commit()
    conn.close()
    print(f"[OK] {stock_code} 买入记录: @{entry_price} ×{volume}股")

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'buy':
        # python morning_screen.py buy 600272 12.50 1000 V1
        record_buy(sys.argv[2], float(sys.argv[3]), int(sys.argv[4]),
                   sys.argv[5] if len(sys.argv) > 5 else None)
    elif len(sys.argv) > 1 and sys.argv[1] == 'gap':
        # python morning_screen.py gap 600272 0.025
        update_auction_gap(sys.argv[2], float(sys.argv[3]))
    else:
        run_screen()
