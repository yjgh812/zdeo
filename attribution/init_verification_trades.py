#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证型交易DB表初始化 + 明日选股预筛
"""
import sqlite3
import os
import struct
import math
import json
from datetime import datetime

DB_PATH = r'D:\zcodesj\attribution\attribution.db'
QLIB = r'D:\workbuddyspace\qlib_data\qlib_bin'

def init_table():
    """创建verification_trades表"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS verification_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            strategy_id TEXT NOT NULL,
            hypothesis TEXT,
            entry_price REAL,
            volume INTEGER,
            signal_snapshot TEXT,
            expected_outcome TEXT,
            actual_ret_t1 REAL,
            actual_ret_t3 REAL,
            actual_ret_t5 REAL,
            result TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    c.execute('CREATE INDEX IF NOT EXISTS idx_vt_strategy ON verification_trades(strategy_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_vt_date ON verification_trades(trade_date)')

    conn.commit()
    conn.close()
    print(f"[OK] verification_trades表已创建")

def load_field_safe(stock, field):
    fp = os.path.join(QLIB, 'features', stock, f'{field}.day.bin')
    if not os.path.exists(fp): return None
    with open(fp, 'rb') as f:
        n = os.path.getsize(fp) // 4
        raw = list(struct.unpack(f'{n}f', f.read()))
    return [v if (math.isfinite(v) and v > 0) else 0.0 for v in raw]

def is_excluded(stock):
    """增强版排除：科创/北交所/ST"""
    import sys as _sys
    _sys.path.insert(0, r'D:\zcodesj\attribution')
    from st_filter import is_excluded_enhanced
    return is_excluded_enhanced(stock)

def get_zt_threshold(stock):
    code = stock[2:]
    return 0.195 if (code.startswith('300') or code.startswith('301')) else 0.095

def generate_tomorrow_candidates():
    """用最新qlib数据（8/7）生成8/11候选池"""
    with open(os.path.join(QLIB, 'calendars', 'day.txt'), 'r') as f:
        cals = [l.strip() for l in f if l.strip()]

    t = len(cals) - 1  # 最新日=8/7
    print(f"基于最新数据日: {cals[t]} 生成候选")

    features_dir = os.path.join(QLIB, 'features')
    all_stocks = sorted([s for s in os.listdir(features_dir) if not is_excluded(s)])

    # 计算所有股票的因子
    candidates = {
        'V1_ab_low_open': [],      # 前日涨停（8/7涨停→8/11候选）
        'V2_high_momentum': [],     # mom5>5%
        'V3_high_vol_momentum': [], # rv20 Top10%+mom5>0
        'V6_oversold': [],          # 60日跌幅>20%
    }

    all_factors = []

    for stock in all_stocks:
        closes = load_field_safe(stock, 'close')
        if not closes or t >= len(closes) or t < 21:
            continue

        # mom5
        s5 = t - 5
        if s5 < 0 or closes[s5] <= 0 or closes[t] <= 0:
            continue
        mom5 = closes[t] / closes[s5] - 1

        # mom20
        s20 = t - 20
        if s20 < 0 or closes[s20] <= 0:
            continue
        mom20 = closes[t] / closes[s20] - 1

        # mom60
        s60 = t - 60
        mom60 = None
        if s60 >= 0 and closes[s60] > 0:
            mom60 = closes[t] / closes[s60] - 1

        # resvol20
        window = closes[max(0,t-20):t+1]
        rets = [window[j]/window[j-1]-1 for j in range(1,len(window)) if window[j-1]>0 and window[j]>0]
        if len(rets) < 10:
            continue
        mean_r = sum(rets)/len(rets)
        var_r = sum((r-mean_r)**2 for r in rets)/len(rets)
        rv20 = math.sqrt(var_r) if var_r > 0 else 0

        # 8/7涨停（V1/V7需要）
        if t >= 1 and closes[t-1] > 0:
            prev_pct = closes[t] / closes[t-1] - 1
            is_zt_8_7 = prev_pct >= get_zt_threshold(stock)
        else:
            is_zt_8_7 = False

        all_factors.append({
            'stock': stock,
            'close': closes[t],
            'mom5': mom5,
            'mom20': mom20,
            'mom60': mom60,
            'rv20': rv20,
            'is_zt': is_zt_8_7,
        })

        # V1: 8/7涨停的（8/11可做AB竞价）
        if is_zt_8_7:
            candidates['V1_ab_low_open'].append({
                'stock': stock, 'close': closes[t],
                'mom5': round(mom5*100, 1), 'rv20': round(rv20*100, 2),
            })

        # V2: mom5>5%
        if mom5 > 0.05:
            candidates['V2_high_momentum'].append({
                'stock': stock, 'close': closes[t],
                'mom5': round(mom5*100, 1), 'mom20': round(mom20*100, 1),
            })

        # V6: 60日跌幅>20%
        if mom60 is not None and mom60 < -0.20:
            candidates['V6_oversold'].append({
                'stock': stock, 'close': closes[t],
                'mom60': round(mom60*100, 1), 'mom5': round(mom5*100, 1),
            })

    # V3: rv20 Top10% + mom5>0
    sorted_by_rv = sorted([f for f in all_factors if f['mom5'] > 0], key=lambda x: x['rv20'], reverse=True)
    top10pct_count = max(len(sorted_by_rv) // 10, 10)
    for f in sorted_by_rv[:top10pct_count]:
        candidates['V3_high_vol_momentum'].append({
            'stock': f['stock'], 'close': f['close'],
            'mom5': round(f['mom5']*100, 1), 'rv20': round(f['rv20']*100, 2),
        })

    return candidates, cals[t]

def main():
    init_table()

    candidates, base_date = generate_tomorrow_candidates()

    print(f"\n{'='*60}")
    print(f"8/11(周二)候选池预筛（基于{base_date}数据）")
    print(f"{'='*60}")

    for strategy, stocks in candidates.items():
        print(f"\n--- {strategy} ({len(stocks)}只) ---")
        # 按相关因子排序取Top10
        if strategy == 'V2_high_momentum':
            stocks = sorted(stocks, key=lambda x: x['mom5'], reverse=True)[:10]
        elif strategy == 'V3_high_vol_momentum':
            stocks = sorted(stocks, key=lambda x: x['rv20'], reverse=True)[:10]
        elif strategy == 'V6_oversold':
            stocks = sorted(stocks, key=lambda x: x['mom60'])[:10]
        else:
            stocks = stocks[:10]

        for s in stocks:
            print(f"  {s['stock']:10s} close={s['close']:8.2f} " +
                  " ".join(f"{k}={v}" for k,v in s.items() if k not in ['stock','close']))

    # 输出JSON
    out_path = r'D:\zcodesj\attribution\candidates_20260811.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'base_date': base_date,
            'target_date': '2026-08-11',
            'candidates': candidates,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n[完成] 候选池: {out_path}")

if __name__ == '__main__':
    main()
