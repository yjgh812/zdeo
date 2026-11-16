#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
momvol_enhancer_signal — 卫星增强层信号器 (mom20+vol_low@T20)
================================================================
成色(backtrader全约束双引擎终验, 2018-2026): CAGR +4.70% / Sharpe 0.69 / MDD -12.9%
口径: qfq复权+清洁只杀上行 | 原始价≤30元 | 20日均成交额>2000万 | 月度调仓
用法:
    python momvol_enhancer_signal.py            # 生成最新信号(截至最近月末)
输出:
    D:\\zcodesj\\multifactor\\reports\\momvol_top20_YYYYMMDD.csv (带日期)
    D:\\zcodesj\\multifactor\\reports\\momvol_top20_latest.csv  (最新)
    控制台: 本月新进/退出清单(与上期对比)
每月最后一个交易日后运行一次(建议每月1日 09:00 cron)。
"""
import os
import sqlite3
import sys
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd

META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
QFQ_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
DRAGON_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
OUT_DIR = r'D:\zcodesj\multifactor\reports'
TOPN = 20
PRE_DAYS = 200          # 预留动量/滚动窗口的自然日


def log(m):
    print('[%s] %s' % (datetime.now().strftime('%H:%M:%S'), m), flush=True)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    end = int(datetime.now().strftime('%Y%m%d'))
    start = int((pd.Timestamp(str(end)) - pd.Timedelta(days=365 * 9)).strftime('%Y%m%d'))
    pre = int((pd.Timestamp(str(end)) - pd.Timedelta(days=PRE_DAYS)).strftime('%Y%m%d'))

    log('加载数据...')
    con = sqlite3.connect(META_DB)
    dates = [r[0] for r in con.execute(
        'SELECT DISTINCT date FROM stock_daily_meta WHERE date BETWEEN ? AND ? ORDER BY date',
        (start, end))]
    df = pd.DataFrame({'d': dates})
    df['ym'] = df['d'].astype(str).str[:6]
    cur_ym = datetime.now().strftime('%Y%m')
    df = df[df['ym'] < cur_ym]          # 只用已完成月份(月末口径=回测验证口径)
    me_dates = [int(x) for x in df.groupby('ym')['d'].max().tolist()]
    keep = set(me_dates)
    raw = pd.read_sql_query(
        'SELECT code, date, close, amount FROM stock_daily_meta '
        'WHERE date BETWEEN ? AND ? AND close > 0', con, params=(pre, end))
    ind_map = {}
    for c2, sec in con.execute('SELECT code, sector_name FROM stock_sector_sw1'):
        ind_map[c2[-6:]] = sec
    con.close()
    con = sqlite3.connect('file:%s?mode=ro' % QFQ_DB, uri=True)
    qfq = pd.read_sql_query(
        'SELECT code, date, close FROM stock_daily_meta_qfq '
        'WHERE date BETWEEN ? AND ? AND close > 0', con, params=(pre, end))
    con.close()
    raw['date'] = raw['date'].astype(int)
    qfq['date'] = qfq['date'].astype(int)

    log('因子(qfq清洁只杀上行)...')
    close_p = qfq.pivot(index='date', columns='code', values='close').sort_index()
    med = close_p.rolling(21, min_periods=5).median()
    bad = (close_p / med > 3) | (close_p.pct_change() > 0.21)
    close_p = close_p.mask(bad).ffill()
    mom20 = close_p.pct_change(20)
    vol20 = close_p.pct_change().rolling(20).std()
    amt20 = (raw.pivot(index='date', columns='code', values='amount').sort_index()
             .rolling(20).mean())
    combo = (mom20.rank(axis=1, pct=True) - vol20.rank(axis=1, pct=True)) / 2

    log('生成Top%d(截至最近月末)...' % TOPN)
    results = {}
    for d in me_dates[-13:]:        # 最近13个月末, 供对比
        if d not in combo.index:
            continue
        f = combo.loc[d].dropna()
        day_raw = raw[raw['date'] == d].set_index('code')['close']
        liq = amt20.loc[d] if d in amt20.index else None
        f = f[[c for c in f.index if c in day_raw.index
               and day_raw[c] == day_raw[c] and 0 < day_raw[c] <= 30
               and (liq is None or (c in liq.index and liq[c] == liq[c]
                                     and liq[c] > 2e7))]]
        if len(f) >= TOPN * 3:
            results[int(d)] = f.nlargest(TOPN).index.tolist()
    if not results:
        print('❌ 无有效信号'); sys.exit(1)
    sig_dates = sorted(results)
    cur_d, prev_d = sig_dates[-1], sig_dates[-2] if len(sig_dates) > 1 else None

    # 名称
    con = sqlite3.connect('file:%s?mode=ro' % DRAGON_DB.replace('\\', '/'), uri=True)
    names = dict(con.execute('SELECT code, name FROM stock_info').fetchall())
    con.close()

    cur = results[cur_d]
    prev = set(results[prev_d]) if prev_d else set()
    rows = []
    for i, c in enumerate(cur, 1):
        rows.append({'rank': i, 'code': c, 'name': names.get(c, '?'),
                     'industry': ind_map.get(c, ''),
                     'close_raw': round(float(
                         raw[raw['date'] == cur_d].set_index('code')['close'].get(c, 0)), 2),
                     'action': '新进' if c not in prev else '保留',
                     'signal_date': cur_d})
    out_df = pd.DataFrame(rows)
    dated = os.path.join(OUT_DIR, 'momvol_top20_%d.csv' % cur_d)
    latest = os.path.join(OUT_DIR, 'momvol_top20_latest.csv')
    out_df.to_csv(dated, index=False, encoding='utf-8-sig')
    out_df.to_csv(latest, index=False, encoding='utf-8-sig')

    print('\n=== 卫星增强层信号 (%d, mom20+vol_low@T%d) ===' % (cur_d, TOPN))
    for _, r in out_df.iterrows():
        print('  %2d %s %s %-8s %6.2f元 %s'
              % (r['rank'], r['code'], r['name'], r['industry'],
                 r['close_raw'], r['action']))
    if prev_d:
        exit_l = sorted(prev - set(cur))
        print('\n本月退出(%d只): %s' % (
            len(exit_l),
            ', '.join('%s %s' % (c, names.get(c, '?')) for c in exit_l) or '无'))
    print('\n输出: %s | %s' % (dated, latest))
    print('提示: 增强层为卫星仓(全约束终验+4.70%/年,MDD-12.9%), '
          '与趋势跟踪主线互补; 建议等权, 主人裁决后经闸门执行。')


if __name__ == '__main__':
    main()
