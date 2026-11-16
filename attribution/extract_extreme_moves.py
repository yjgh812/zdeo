#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场极端涨跌样本提取（2026-08-10）
从 stock_daily_meta 提取涨停股 + 大跌股(跌幅>=8%)，
并拉取每只股票 t-1日（2026-08-07）的可观测因子数据。
所有文件输出到 D:/zcodesj/attribution/
"""
import sqlite3
import json
import os

DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
OUT = r'D:\zcodesj\attribution\extreme_moves_20260810.json'

def is_excluded(code):
    """排除科创/北交所（688/8/4开头）"""
    if code.startswith('688') or code.startswith('8') or code.startswith('4'):
        return True
    return False

def get_zt_threshold(code):
    """涨停阈值（小数比例）：创业板/科创板20%，主板10%"""
    if code.startswith('300') or code.startswith('301') or code.startswith('688'):
        return 0.195  # 留余量
    return 0.095

def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    EVENT_DATE = 20260810  # 事件日
    PREV_DATE = 20260807   # t-1日（上一交易日）

    # === 1. 提取事件日全市场数据 ===
    c.execute("""
        SELECT code, date, open, high, low, close, volume, amount, change_pct
        FROM stock_daily_meta
        WHERE date = ?
    """, (EVENT_DATE,))
    event_rows = c.fetchall()

    # === 2. 提取t-1日全市场数据 ===
    c.execute("""
        SELECT code, date, open, high, low, close, volume, amount, change_pct
        FROM stock_daily_meta
        WHERE date = ?
    """, (PREV_DATE,))
    prev_rows = c.fetchall()

    # === 3. 提取t-2日数据（用于计算t-1日的20日趋势等，先取近30日） ===
    c.execute("""
        SELECT code, date, open, high, low, close, volume, amount, change_pct
        FROM stock_daily_meta
        WHERE date >= 20260706 AND date <= ?
        ORDER BY code, date
    """, (PREV_DATE,))
    history_rows = c.fetchall()

    # === 4. 加载板块 ===
    c.execute("SELECT code, sector_name FROM stock_sector_sw1")
    sector_map = {}
    for r in c.fetchall():
        code = r[0][-6:] if len(r[0]) > 6 else r[0]
        sector_map[code] = r[1]

    conn.close()

    print(f"事件日({EVENT_DATE})记录: {len(event_rows)}")
    print(f"t-1日({PREV_DATE})记录: {len(prev_rows)}")
    print(f"近30日历史记录: {len(history_rows)}")
    print(f"板块归属: {len(sector_map)}")

    # 组织数据
    event_data = {}
    for r in event_rows:
        code = r[0]
        if is_excluded(code):
            continue
        if r[3] is None or r[6] is None or r[6] == 0:  # 排除无数据/零成交
            continue
        event_data[code] = {
            'open': r[2], 'high': r[3], 'low': r[4], 'close': r[5],
            'volume': r[6], 'amount': r[7], 'pct': r[8],
            'sector': sector_map.get(code, '未知')
        }

    prev_data = {}
    for r in prev_rows:
        code = r[0]
        if is_excluded(code):
            continue
        prev_data[code] = {
            'open': r[2], 'high': r[3], 'low': r[4], 'close': r[5],
            'volume': r[6], 'amount': r[7], 'pct': r[8]
        }

    # 历史数据按code分组
    hist_by_code = {}
    for r in history_rows:
        code = r[0]
        if is_excluded(code):
            continue
        if code not in hist_by_code:
            hist_by_code[code] = []
        hist_by_code[code].append({
            'date': r[1], 'open': r[2], 'high': r[3], 'low': r[4],
            'close': r[5], 'volume': r[6], 'amount': r[7], 'pct': r[8]
        })

    # === 5. 筛选涨停 + 大跌 ===
    limit_ups = []
    big_drops = []

    for code, ed in event_data.items():
        pct = ed['pct']
        if pct is None:
            continue

        threshold = get_zt_threshold(code)

        # 涨停（含封板和炸板后收盘仍达阈值）
        if pct >= threshold:
            ed['code'] = code
            ed['event_type'] = 'limit_up'
            ed['sealed'] = (ed['close'] >= ed['high'] * 0.999)  # 收盘=最高≈封死
            limit_ups.append(ed)

        # 大跌（跌幅>=8%，小数比例）
        elif pct <= -0.08:
            ed['code'] = code
            ed['event_type'] = 'big_drop'
            ed['is_limit_down'] = pct <= -get_zt_threshold(code)  # 是否跌停
            big_drops.append(ed)

    print(f"\n涨停股: {len(limit_ups)} 只")
    print(f"大跌股(≥8%): {len(big_drops)} 只")

    # === 6. 计算t-1期因子 ===
    def calc_factors(code, ed, pd, hist):
        """基于t-1日及更早数据计算前置因子"""
        factors = {}

        if not pd or not hist:
            return None

        # 近期收盘价序列
        closes = [h['close'] for h in hist if h['close'] is not None]
        vols = [h['volume'] for h in hist if h['volume'] is not None]

        if len(closes) < 5:
            return None

        # 1. 20日趋势（MA5 vs MA20近似）
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        factors['ma5'] = round(ma5, 2) if ma5 else None
        factors['ma20'] = round(ma20, 2) if ma20 else None
        factors['trend_20d'] = round((ma5 / ma20 - 1) * 100, 2) if ma5 and ma20 and ma20 > 0 else None

        # 2. 前期累计涨幅
        if len(closes) >= 21 and closes[-21] > 0:
            factors['return_20d_pct'] = round((closes[-1] / closes[-21] - 1) * 100, 2)
        if len(closes) >= 6 and closes[-6] > 0:
            factors['return_5d_pct'] = round((closes[-1] / closes[-6] - 1) * 100, 2)

        # 3. t-1日涨停？
        factors['prev_zt'] = pd['pct'] is not None and pd['pct'] >= get_zt_threshold(code)

        # 4. 量比（t-1日成交量 / 近20日均量）
        if len(vols) >= 20 and vols[-1] > 0:
            avg_vol_20 = sum(vols[-20:]) / 20
            factors['volume_ratio_prev'] = round(vols[-1] / avg_vol_20, 2) if avg_vol_20 > 0 else None

        # 5. t-1日换手代理（成交额/收盘价，相对值）
        factors['prev_amount'] = pd.get('amount')

        # 6. 波动率（近10日涨跌幅标准差）
        pcts = [h['pct'] for h in hist if h['pct'] is not None]
        if len(pcts) >= 10:
            recent_pcts = pcts[-10:]
            mean_pct = sum(recent_pcts) / len(recent_pcts)
            var_pct = sum((p - mean_pct) ** 2 for p in recent_pcts) / len(recent_pcts)
            factors['volatility_10d'] = round(var_pct ** 0.5, 2)

        # 7. 振幅（t-1日）
        if pd['high'] and pd['low'] and pd['close'] and pd['close'] > 0:
            factors['prev_amplitude'] = round((pd['high'] - pd['low']) / pd['close'] * 100, 2)

        # 8. 竞价缺口（事件日）
        if pd['close'] and ed['open'] and pd['close'] > 0:
            factors['auction_gap_pct'] = round((ed['open'] / pd['close'] - 1) * 100, 2)

        # 9. 连板天数（从t-1往前数连续涨停）
        streak = 0
        for h in reversed(hist):
            if h['pct'] is not None and h['pct'] >= get_zt_threshold(code):
                streak += 1
            else:
                break
        factors['consecutive_boards'] = streak

        return factors

    # 为涨停股和大跌股计算因子
    for stock in limit_ups:
        code = stock['code']
        stock['factors'] = calc_factors(code, stock, prev_data.get(code, {}), hist_by_code.get(code, []))

    for stock in big_drops:
        code = stock['code']
        stock['factors'] = calc_factors(code, stock, prev_data.get(code, {}), hist_by_code.get(code, []))

    # 统计封板情况
    sealed = sum(1 for s in limit_ups if s.get('sealed'))
    broken = len(limit_ups) - sealed
    print(f"  封死涨停: {sealed} 只, 炸板回落: {broken} 只")
    limit_down_count = sum(1 for s in big_drops if s.get('is_limit_down'))
    print(f"  跌停: {limit_down_count} 只, 非跌停大跌: {len(big_drops) - limit_down_count} 只")

    # 板块分布
    lu_sectors = {}
    for s in limit_ups:
        sec = s.get('sector', '未知')
        lu_sectors[sec] = lu_sectors.get(sec, 0) + 1

    bd_sectors = {}
    for s in big_drops:
        sec = s.get('sector', '未知')
        bd_sectors[sec] = bd_sectors.get(sec, 0) + 1

    print(f"\n涨停板块TOP5: {sorted(lu_sectors.items(), key=lambda x: -x[1])[:5]}")
    print(f"大跌板块TOP5: {sorted(bd_sectors.items(), key=lambda x: -x[1])[:5]}")

    # 输出
    output = {
        'meta': {
            'event_date': str(EVENT_DATE),
            'prev_date': str(PREV_DATE),
            'total_event_stocks': len(event_data),
            'limit_up_count': len(limit_ups),
            'big_drop_count': len(big_drops),
            'sealed_count': sealed,
            'broken_count': broken,
            'limit_down_count': limit_down_count,
        },
        'limit_ups': limit_ups,
        'big_drops': big_drops,
        'sector_distribution': {
            'limit_up': dict(sorted(lu_sectors.items(), key=lambda x: -x[1])),
            'big_drop': dict(sorted(bd_sectors.items(), key=lambda x: -x[1])),
        }
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[完成] 数据保存: {OUT}")

if __name__ == '__main__':
    main()
