#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
龙虎榜数据全量回填脚本 (2026-01-17 ~ 2026-08-10)
==================================================
数据源: akshare stock_lhb_detail_em (已验证本机可用)

回填三个目标:
  1. fundamental_data.db / longhubang_daily     (个股龙虎榜明细汇总)
  2. fundamental_data.db / feature_longhubang   (个股×日龙虎榜次数)
  3. fundamental_data.db / dragon_tiger          (龙虎榜净买卖额)
  4. dragon_quant.db / lhb_data                 (席位级买卖前5, 需逐股调用)
  5. dragon_quant.db / lhb_stat                 (龙虎榜热度统计)

作者: 知微
日期: 2026-08-10
"""

import sqlite3
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import time
import sys
import warnings
warnings.filterwarnings('ignore')

FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
MAIN_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'

# 回填区间
START_DATE = '2026-01-17'   # lhb_data 最后一天之后
END_DATE   = '2026-08-10'   # 今天


def get_trade_dates(start: str, end: str) -> list:
    """生成交易日列表 (排除周末, 无法精确排除节假日但 akshare 会返回空)"""
    dates = []
    d = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.strptime(end, '%Y-%m-%d')
    while d <= e:
        if d.weekday() < 5:  # 周一到周五
            dates.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)
    return dates


def get_db_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA busy_timeout = 30000')
    return conn


# ============================================================
# Part 1: longhubang_daily + dragon_tiger (批量按区间拉取)
# ============================================================

def backfill_lhb_summary(fund_conn, start_date: str, end_date: str):
    """回填 longhubang_daily 和 dragon_tiger (按区间一次拉取)"""
    print(f"\n{'='*60}")
    print(f"[1] 回填 longhubang_daily + dragon_tiger: {start_date} ~ {end_date}")
    print(f"{'='*60}")

    try:
        df = ak.stock_lhb_detail_em(start_date=start_date.replace('-', ''),
                                     end_date=end_date.replace('-', ''))
    except Exception as e:
        print(f"  ERROR: {e}")
        return 0, 0

    if len(df) == 0:
        print("  无数据返回")
        return 0, 0

    print(f"  拉取到 {len(df)} 行, 日期范围: {df['上榜日'].min()} ~ {df['上榜日'].max()}")

    # ---- 写 longhubang_daily ----
    df['trade_date'] = df['上榜日']

    cur = fund_conn.cursor()
    # 删除区间内旧数据避免重复
    cur.execute("DELETE FROM longhubang_daily WHERE 上榜日 >= ? AND 上榜日 <= ?",
                (start_date, end_date))
    deleted = cur.rowcount
    print(f"  longhubang_daily: 删除旧数据 {deleted} 行")

    df.to_sql('longhubang_daily', fund_conn, if_exists='append', index=False)
    fund_conn.commit()

    cur.execute("SELECT COUNT(*), MIN(上榜日), MAX(上榜日) FROM longhubang_daily")
    total, mn, mx = cur.fetchone()
    print(f"  longhubang_daily: 现有 {total} 行, 范围 {mn} ~ {mx}")

    # ---- 写 dragon_tiger ----
    # 转换格式: 代码→ts_code, 龙虎榜净买额→net_amount 等
    dt_rows = []
    for _, row in df.iterrows():
        code = str(row['代码']).zfill(6)
        # 判断交易所后缀
        if code.startswith(('6', '9')):
            ts_code = f"{code}.SH"
        elif code.startswith('8') or code.startswith('4'):
            ts_code = f"{code}.BJ"
        else:
            ts_code = f"{code}.SZ"

        dt_rows.append({
            'trade_date': str(row['上榜日']).replace('-', ''),
            'ts_code': ts_code,
            'net_buy': row.get('龙虎榜净买额'),
            'reason': row.get('上榜原因', ''),
            'abnormal_type': None,
            'name': row.get('名称', ''),
            'close': str(row.get('收盘价', '')),
            'pct_change': str(row.get('涨跌幅', '')),
            'turnover_rate': str(row.get('换手率', '')),
            'amount': float(row.get('市场总成交额', 0) or 0),
            'l_sell': float(row.get('龙虎榜卖出额', 0) or 0),
            'l_buy': float(row.get('龙虎榜买入额', 0) or 0),
            'l_amount': float(row.get('龙虎榜成交额', 0) or 0),
            'net_amount': float(row.get('龙虎榜净买额', 0) or 0),
            'net_rate': float(row.get('净买额占总成交比', 0) or 0),
            'amount_rate': float(row.get('成交额占总成交比', 0) or 0),
            'float_values': float(row.get('流通市值', 0) or 0),
        })

    dt_df = pd.DataFrame(dt_rows)
    start_yyyymmdd = start_date.replace('-', '')
    end_yyyymmdd = end_date.replace('-', '')

    cur.execute("DELETE FROM dragon_tiger WHERE trade_date >= ? AND trade_date <= ?",
                (start_yyyymmdd, end_yyyymmdd))
    deleted_dt = cur.rowcount
    print(f"  dragon_tiger: 删除旧数据 {deleted_dt} 行")

    dt_df.to_sql('dragon_tiger', fund_conn, if_exists='append', index=False)
    fund_conn.commit()

    cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM dragon_tiger")
    total_dt, mn_dt, mx_dt = cur.fetchone()
    print(f"  dragon_tiger: 现有 {total_dt} 行, 范围 {mn_dt} ~ {mx_dt}")

    return len(df), len(dt_rows)


# ============================================================
# Part 2: feature_longhubang (从 longhubang_daily 重建)
# ============================================================

def rebuild_feature_longhubang(fund_conn):
    """从 longhubang_daily 完整重建 feature_longhubang"""
    print(f"\n{'='*60}")
    print(f"[2] 重建 feature_longhubang")
    print(f"{'='*60}")

    df_counts = pd.read_sql_query("""
        SELECT 上榜日 as trade_date, 代码 as ts_code_simple, COUNT(*) as lhb_count
        FROM longhubang_daily
        GROUP BY 上榜日, 代码
    """, fund_conn)

    if len(df_counts) == 0:
        print("  longhubang_daily 为空, 跳过")
        return 0

    df_counts['trade_date'] = pd.to_datetime(df_counts['trade_date']).dt.strftime('%Y-%m-%d 00:00:00')

    cur = fund_conn.cursor()
    cur.execute("DELETE FROM feature_longhubang")
    print(f"  删除旧数据 {cur.rowcount} 行")

    df_counts.to_sql('feature_longhubang', fund_conn, if_exists='append', index=False)
    fund_conn.commit()

    cur.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM feature_longhubang")
    total, mn, mx = cur.fetchone()
    print(f"  feature_longhubang: 现有 {total} 行, 范围 {mn} ~ {mx}")
    return len(df_counts)


# ============================================================
# Part 3: lhb_data (席位级明细, 逐股调用)
# ============================================================

def code_to_ths_suffix(code: str) -> str:
    """纯代码→jqdata格式 (lhb_data 使用的格式)"""
    code = str(code).zfill(6)
    if code.startswith(('6', '9')):
        return f"{code}.XSHG"
    elif code.startswith('8') or code.startswith('4'):
        return f"{code}.BJSE"
    else:
        return f"{code}.XSHE"


def backfill_lhb_seat_detail(main_conn, fund_conn, start_date: str, end_date: str):
    """回填 lhb_data (席位级前5买卖明细)"""
    print(f"\n{'='*60}")
    print(f"[3] 回填 lhb_data (席位明细): {start_date} ~ {end_date}")
    print(f"{'='*60}")

    # 从 longhubang_daily 获取所有 (日期, 代码) 对
    pairs = pd.read_sql_query("""
        SELECT DISTINCT 上榜日, 代码 FROM longhubang_daily
        WHERE 上榜日 >= ? AND 上榜日 <= ?
        ORDER BY 上榜日, 代码
    """, fund_conn, params=(start_date, end_date))

    print(f"  需处理 {len(pairs)} 个 (日期, 代码) 组合")

    cur = main_conn.cursor()
    # 清空旧数据 (lhb_data 仅5天数据, 直接全删重建)
    cur.execute("DELETE FROM lhb_data")
    print(f"  清空 lhb_data 旧数据 {cur.rowcount} 行")

    total_inserted = 0
    errors = 0
    batch = 0

    for _, row in pairs.iterrows():
        trade_day = str(row['上榜日'])
        code = str(row['代码']).zfill(6)
        date_int = trade_day.replace('-', '')

        # 获取买入和卖出席位
        for direction, flag in [('BUY', '买入'), ('SELL', '卖出')]:
            try:
                df_seat = ak.stock_lhb_stock_detail_em(
                    symbol=code, date=date_int, flag=flag
                )
                if df_seat is None or len(df_seat) == 0:
                    continue

                # 取前5名
                df_top5 = df_seat.head(5)

                for rank, (_, seat_row) in enumerate(df_top5.iterrows(), 1):
                    # 获取异常类型 (从上榜原因映射)
                    reason = str(seat_row.get('类型', ''))
                    # 简化 abnormal_code: 用哈希代替
                    abnormal_code = '106001' if '涨幅' in reason else ('106002' if '跌幅' in reason else '106000')

                    cur.execute("""
                        INSERT INTO lhb_data (code, day, direction, rank, abnormal_code, abnormal_name)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        code_to_ths_suffix(code),
                        trade_day,
                        direction,
                        rank,
                        abnormal_code,
                        reason
                    ))
                    total_inserted += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  WARNING {code} {trade_day} {direction}: {e}")
                continue

        batch += 1
        if batch % 200 == 0:
            main_conn.commit()
            print(f"  进度: {batch}/{len(pairs)} ({batch*100//len(pairs)}%), 已插入 {total_inserted} 行")

        # 避免请求过快
        time.sleep(0.1)

    main_conn.commit()

    cur.execute("SELECT COUNT(*), MIN(day), MAX(day), COUNT(DISTINCT day) FROM lhb_data")
    total, mn, mx, days = cur.fetchone()
    print(f"\n  lhb_data 完成: {total} 行, {days} 个交易日, 范围 {mn} ~ {mx}")
    print(f"  错误数: {errors}")
    return total_inserted


# ============================================================
# Part 4: lhb_stat (龙虎榜热度统计)
# ============================================================

def rebuild_lhb_stat(main_conn, fund_conn):
    """从 longhubang_daily 重建 lhb_stat (最近上榜单股统计)"""
    print(f"\n{'='*60}")
    print(f"[4] 重建 lhb_stat")
    print(f"{'='*60}")

    # 取最近30天的龙虎榜个股
    df = pd.read_sql_query("""
        SELECT 代码 as code, 名称 as name, 上榜日 as day,
               收盘价 as close, 涨跌幅 as change_pct,
               COUNT(*) OVER (PARTITION BY 代码) as cnt
        FROM longhubang_daily
        WHERE 上榜日 >= date('now', '-60 days')
        ORDER BY cnt DESC, 上榜日 DESC
    """, fund_conn)

    if len(df) == 0:
        print("  无数据")
        return 0

    # 取每只股票最近一次上榜
    df_latest = df.sort_values('day', ascending=False).drop_duplicates(subset='code', keep='first')
    df_latest = df_latest.head(500)  # 最多500只
    df_latest.insert(0, 'seq', range(1, len(df_latest) + 1))

    cur = main_conn.cursor()
    cur.execute("DELETE FROM lhb_stat")
    print(f"  删除旧数据 {cur.rowcount} 行")

    for _, row in df_latest.iterrows():
        cur.execute("""
            INSERT INTO lhb_stat (seq, code, name, last_date, close, change_pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (int(row['seq']), str(row['code']).zfill(6), row['name'],
              row['day'], float(row['close'] or 0), float(row['change_pct'] or 0)))

    main_conn.commit()
    print(f"  lhb_stat: 写入 {len(df_latest)} 行")
    return len(df_latest)


# ============================================================
# Main
# ============================================================

def main():
    print(f"\n{'#'*60}")
    print(f"# 龙虎榜数据全量回填 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"# 区间: {START_DATE} ~ {END_DATE}")
    print(f"{'#'*60}")

    fund_conn = get_db_conn(FUND_DB)
    main_conn = get_db_conn(MAIN_DB)

    try:
        # Part 1: longhubang_daily + dragon_tiger
        n1, n2 = backfill_lhb_summary(fund_conn, START_DATE, END_DATE)

        # Part 2: feature_longhubang
        rebuild_feature_longhubang(fund_conn)

        # Part 3: lhb_data (逐股, 耗时较长)
        n3 = backfill_lhb_seat_detail(main_conn, fund_conn, START_DATE, END_DATE)

        # Part 4: lhb_stat
        rebuild_lhb_stat(main_conn, fund_conn)

        print(f"\n{'#'*60}")
        print(f"# 回填完成!")
        print(f"#   longhubang_daily 新增: {n1}")
        print(f"#   dragon_tiger 新增: {n2}")
        print(f"#   lhb_data 总计: {n3}")
        print(f"{'#'*60}")

    finally:
        fund_conn.close()
        main_conn.close()


if __name__ == '__main__':
    main()
