#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qfq 前复权增量更新脚本 (2026-08-08 ~ 2026-08-10)
==================================================
从腾讯 fqkline 接口拉取近 5 个交易日的 qfq 数据，
追加/更新到 dragon_quant_qfq.db / stock_daily_meta_qfq 表。

注意：前复权在新的除权除息事件发生时，历史价格会被重新基准化。
因此本脚本拉取近 5 天数据，并用 INSERT OR REPLACE 覆盖。

作者: 知微
日期: 2026-08-10
"""

import sqlite3
import json
import urllib.request
import time
import sys
from datetime import datetime, timedelta

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'

# 拉取最近 N 个交易日的数据（多拉几天覆盖除权除息重算）
FETCH_DAYS = 10


def get_tencent_qfq(code: str, days: int = 10) -> list:
    """从腾讯 fqkline 接口获取前复权日线"""
    # 确定 sh/sz 前缀
    if code.startswith('6') or code.startswith('9'):
        prefix = 'sh'
    else:
        # 深市(0/3开头) 和 北交所(8/4开头) 都用 sz 前缀
        # 实测腾讯对北交所 920xxx 系列用 sh 前缀
        if code.startswith('920'):
            prefix = 'sh'
        else:
            prefix = 'sz'

    url = (
        f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={prefix}{code},day,,,40,qfq"
    )

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
        qfq_day = stock_data.get('qfqday', stock_data.get('day', []))

        results = []
        for item in qfq_day:
            if isinstance(item, list) and len(item) >= 6:
                # [date, open, close, high, low, volume]
                date_str = item[0].replace('-', '')  # YYYYMMDD
                open_p = float(item[1]) if item[1] else 0
                close_p = float(item[2]) if item[2] else 0
                high_p = float(item[3]) if item[3] else 0
                low_p = float(item[4]) if item[4] else 0
                vol = float(item[5]) if item[5] else 0

                results.append({
                    'code': code,
                    'date': date_str,
                    'open': open_p,
                    'high': high_p,
                    'low': low_p,
                    'close': close_p,
                    'volume': vol,
                    'amount': 0,  # 腾讯不提供
                })

        return results

    except Exception as e:
        return []


def main():
    print(f"\n{'#'*60}")
    print(f"# qfq 前复权增量更新 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")

    conn = sqlite3.connect(QFQ_DB)
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA busy_timeout = 60000')
    conn.execute('PRAGMA synchronous=NORMAL')

    cur = conn.cursor()

    # 当前最新日期
    cur.execute('SELECT MAX(date) FROM stock_daily_meta_qfq')
    current_max = cur.fetchone()[0]
    print(f"当前最新日期: {current_max}")

    # 获取所有需更新的股票
    cur.execute('SELECT DISTINCT code FROM stock_daily_meta_qfq ORDER BY code')
    all_codes = [r[0] for r in cur.fetchall()]
    print(f"总股票数: {len(all_codes)}")

    # 只处理有需要的（最后日期 < 20260811）
    cutoff = '20260809'
    cur.execute("""
        SELECT DISTINCT code FROM stock_daily_meta_qfq
        WHERE code NOT IN (
            SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date >= ?
        )
    """, (cutoff,))
    need_codes = [r[0] for r in cur.fetchall()]
    print(f"需更新股票数: {len(need_codes)}")

    if not need_codes:
        print("所有股票已是最新，无需更新")
        conn.close()
        return

    total_updated = 0
    total_errors = 0
    batch_count = 0
    start_time = time.time()

    # 计算最近 FETCH_DAYS 天的截止日期
    today_str = datetime.now().strftime('%Y%m%d')

    for i, code in enumerate(need_codes):
        rows = get_tencent_qfq(code, FETCH_DAYS)

        if not rows:
            total_errors += 1
            if total_errors <= 5:
                print(f"  WARNING {code}: 无数据返回")
        else:
            # 计算 change_pct
            prev_close = None
            insert_rows = []
            for row in rows:
                # 获取前一日收盘
                if prev_close is None:
                    # 从库里取前一日
                    prev = cur.execute(
                        "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date < ? ORDER BY date DESC LIMIT 1",
                        (code, row['date'])
                    ).fetchone()
                    if prev:
                        prev_close = prev[0]

                if prev_close and prev_close > 0:
                    change_pct = (row['close'] - prev_close) / prev_close * 100
                    # 数据质量门禁: |change_pct| > 21 设为 NULL
                    if abs(change_pct) > 21:
                        change_pct = None
                else:
                    change_pct = None

                insert_rows.append((
                    row['code'], row['date'], row['open'], row['high'],
                    row['low'], row['close'], row['volume'], row['amount'],
                    change_pct
                ))
                prev_close = row['close']

            # INSERT OR REPLACE
            cur.executemany("""
                INSERT OR REPLACE INTO stock_daily_meta_qfq
                (code, date, open, high, low, close, volume, amount, change_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, insert_rows)
            total_updated += len(insert_rows)

        batch_count += 1

        if batch_count % 200 == 0:
            conn.commit()
            elapsed = time.time() - start_time
            rate = batch_count / elapsed if elapsed > 0 else 0
            eta = (len(need_codes) - batch_count) / rate if rate > 0 else 0
            print(f"  进度: {batch_count}/{len(need_codes)} "
                  f"({batch_count*100//len(need_codes)}%) "
                  f"已更新 {total_updated} 行, "
                  f"速率 {rate:.1f}/s, "
                  f"ETA {eta/60:.1f}min", flush=True)

        time.sleep(0.05)  # 限速

    conn.commit()

    # 验证
    cur.execute('SELECT MAX(date) FROM stock_daily_meta_qfq')
    new_max = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE date >= "20260809"')
    new_rows = cur.fetchone()[0]
    cur.execute('SELECT COUNT(DISTINCT code) FROM stock_daily_meta_qfq WHERE date = "20260811"')
    codes_0811 = cur.fetchone()[0]

    print(f"\n{'='*60}")
    print(f"更新完成!")
    print(f"  原最新日期: {current_max}")
    print(f"  现最新日期: {new_max}")
    print(f"  新增/更新行: {total_updated}")
    print(f"  0809后行数: {new_rows}")
    print(f"  0811覆盖股票: {codes_0811}")
    print(f"  错误数: {total_errors}")
    print(f"  耗时: {(time.time()-start_time)/60:.1f} 分钟")
    print(f"{'='*60}")

    conn.close()


if __name__ == '__main__':
    main()
