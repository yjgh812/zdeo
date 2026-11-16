#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场扫描——基于8/10收盘数据，为8/11选股
1. 从qlib bin计算全市场所有股票的因子（mom5/mom20/mom60/rv20）
2. 排除ST/科创/北交所/已退市
3. 按各策略条件筛选完整候选
4. 标注8/10涨停股（V1/V5的AB候选）
"""
import os
import struct
import math
import json
import sqlite3
from collections import defaultdict
from datetime import datetime

QLIB = r'D:\workbuddyspace\qlib_data\qlib_bin'
META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
OUT = r'D:\zcodesj\attribution\full_scan_20260811.json'

def load_calendar():
    with open(os.path.join(QLIB, 'calendars', 'day.txt'), 'r') as f:
        return [l.strip() for l in f if l.strip()]

def load_field_safe(stock, field):
    fp = os.path.join(QLIB, 'features', stock, f'{field}.day.bin')
    if not os.path.exists(fp): return None
    with open(fp, 'rb') as f:
        n = os.path.getsize(fp) // 4
        raw = list(struct.unpack(f'{n}f', f.read()))
    return [v if (math.isfinite(v) and v > 0) else 0.0 for v in raw]

def get_st_stocks():
    """获取ST股名单"""
    conn = sqlite3.connect(META_DB)
    c = conn.cursor()
    # 尝试从namechange表获取ST标记
    st_set = set()
    try:
        c.execute("""SELECT code FROM stock_daily_meta 
                     WHERE date = (SELECT MAX(date) FROM stock_daily_meta) 
                     AND code LIKE '%%'""")
    except Exception:
        pass
    conn.close()
    return st_set

def is_excluded(stock, st_set=None):
    """增强版排除：科创/北交所/ST"""
    import sys
    sys.path.insert(0, r'D:\zcodesj\attribution')
    from st_filter import is_excluded_enhanced
    return is_excluded_enhanced(stock)

def get_zt_threshold(stock):
    code = stock[2:]
    return 0.195 if (code.startswith('300') or code.startswith('301')) else 0.095

def main():
    print("=" * 70)
    print("全市场扫描（基于8/10数据，为8/11选股）")
    print("=" * 70)

    calendars = load_calendar()
    t = len(calendars) - 1
    print(f"最新数据日: {calendars[t]} (idx={t})")

    features_dir = os.path.join(QLIB, 'features')
    all_stocks_raw = sorted(os.listdir(features_dir))

    # 从meta_db获取ST标记
    print("加载ST/名称信息...")
    conn = sqlite3.connect(META_DB)
    c = conn.cursor()
    # 用最新交易日的数据查名称（可能有ST标记）
    c.execute("SELECT MAX(date) FROM stock_daily_meta")
    latest_meta_date = c.fetchone()[0]

    # stock_sector_sw1有名称信息吗？没有，但有sector
    # 用namechange表？
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in c.fetchall()]
    conn.close()

    # 简化：先用已知ST代码段排除 + 后续用实时行情验证
    all_stocks = [s for s in all_stocks_raw if not is_excluded(s)]
    print(f"总股票: {len(all_stocks_raw)}, 排除科创/北交所后: {len(all_stocks)}")

    # 计算全市场因子
    print(f"\n计算因子...")
    all_factors = []
    stocks_zt_8_10 = []

    for i, stock in enumerate(all_stocks):
        closes = load_field_safe(stock, 'close')
        if not closes or t >= len(closes) or t < 60:
            continue

        # 前一日收盘
        prev_close = closes[t - 1] if t >= 1 else 0

        # mom5
        s5 = t - 5
        if s5 < 0 or closes[s5] <= 0 or closes[t] <= 0:
            continue
        mom5 = closes[t] / closes[s5] - 1

        # mom10
        s10 = t - 10
        mom10 = closes[t] / closes[s10] - 1 if s10 >= 0 and closes[s10] > 0 else None

        # mom20
        s20 = t - 20
        if s20 < 0 or closes[s20] <= 0:
            continue
        mom20 = closes[t] / closes[s20] - 1

        # mom60
        s60 = t - 60
        mom60 = closes[t] / closes[s60] - 1 if s60 >= 0 and closes[s60] > 0 else None

        # resvol20
        window = closes[max(0, t-20):t+1]
        rets = [window[j]/window[j-1]-1 for j in range(1, len(window))
                if window[j-1] > 0 and window[j] > 0]
        if len(rets) < 10:
            continue
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r)**2 for r in rets) / len(rets)
        rv20 = math.sqrt(var_r) if var_r > 0 else 0

        # 8/10涨停？
        if prev_close > 0:
            pct_8_10 = closes[t] / prev_close - 1
            is_zt = pct_8_10 >= get_zt_threshold(stock)
        else:
            is_zt = False
            pct_8_10 = 0

        # 振幅（近似：用近5日最高/最低）
        highs = load_field_safe(stock, 'high')
        lows = load_field_safe(stock, 'low')
        if highs and t < len(highs) and lows and t < len(lows):
            today_high = highs[t]
            today_low = lows[t]
            amplitude = (today_high - today_low) / prev_close * 100 if prev_close > 0 else 0
        else:
            amplitude = 0

        all_factors.append({
            'stock': stock,
            'close': round(closes[t], 2),
            'mom5': round(mom5 * 100, 1),
            'mom10': round(mom10 * 100, 1) if mom10 else None,
            'mom20': round(mom20 * 100, 1),
            'mom60': round(mom60 * 100, 1) if mom60 else None,
            'rv20': round(rv20 * 100, 2),
            'is_zt_8_10': is_zt,
            'pct_8_10': round(pct_8_10 * 100, 1),
            'amplitude': round(amplitude, 1),
        })

        if is_zt:
            stocks_zt_8_10.append(stock)

        if (i + 1) % 2000 == 0:
            print(f"  {i+1}/{len(all_stocks)}")

    print(f"成功计算: {len(all_factors)}只")
    print(f"8/10涨停: {len(stocks_zt_8_10)}只")

    # === 按策略筛选 ===
    print(f"\n{'='*70}")
    print("各策略候选")
    print(f"{'='*70}")

    # V1: 8/10涨停（明日AB竞价候选，竞价缺口需明日实时确定）
    v1 = [f for f in all_factors if f['is_zt_8_10']]
    # 排除极端高PE/已涨停封死的
    v1_sorted = sorted(v1, key=lambda x: x['mom5'], reverse=True)

    # V2: mom5>5%
    v2 = sorted([f for f in all_factors if f['mom5'] > 5], key=lambda x: x['mom5'], reverse=True)

    # V3: rv20 Top10% + mom5>0
    positive_mom = [f for f in all_factors if f['mom5'] > 0]
    v3_sorted = sorted(positive_mom, key=lambda x: x['rv20'], reverse=True)
    top10pct_n = max(len(v3_sorted) // 10, 10)
    v3 = v3_sorted[:top10pct_n]

    # V6: mom60<-20% + mom5>0（企稳）
    v6 = sorted([f for f in all_factors if f.get('mom60') is not None and f['mom60'] < -20 and f['mom5'] > 0],
                key=lambda x: x['mom60'])

    # V_extra: 高波低动量（情绪冰点反弹候选）
    v_extra = sorted([f for f in all_factors if f['mom5'] < -5 and f['rv20'] > 5],
                     key=lambda x: x['rv20'], reverse=True)[:20]

    # V_combo: mom5>5% + rv20 Top20%（动量+波动双重确认）
    v_combo_candidates = sorted([f for f in all_factors if f['mom5'] > 5], key=lambda x: x['rv20'], reverse=True)
    top20pct_n = max(len(v_combo_candidates) // 5, 10)
    v_combo = v_combo_candidates[:top20pct_n]

    strategies = {
        'V1_zt_candidates': v1_sorted,
        'V2_high_momentum': v2,
        'V3_high_vol_momentum': v3,
        'V6_oversold_bounce': v6,
        'V_combo_momentum_vol': v_combo,
        'V_extra_high_vol_low_mom': v_extra,
    }

    for sname, stocks in strategies.items():
        print(f"\n--- {sname} ({len(stocks)}只) ---")
        for s in stocks[:15]:
            zt_flag = '🔥涨停' if s['is_zt_8_10'] else ''
            print(f"  {s['stock']:10s} close={s['close']:8.2f} "
                  f"mom5={s['mom5']:+6.1f}% mom20={s['mom20']:+6.1f}% "
                  f"mom60={str(s.get('mom60','?')):>7s}% rv20={s['rv20']:5.2f}% "
                  f"amp={s['amplitude']:5.1f}% {zt_flag}")

    # 输出JSON
    output = {
        'scan_date': calendars[t],
        'target_date': '2026-08-11',
        'total_scanned': len(all_factors),
        'zt_count': len(stocks_zt_8_10),
        'strategies': {k: v[:30] for k, v in strategies.items()},  # 每策略保留Top30
        'all_factors_count': len(all_factors),
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] {OUT}")

if __name__ == '__main__':
    main()
