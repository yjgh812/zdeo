# -*- coding: utf-8 -*-
"""
龙虎榜涨幅偏离类 × 次日开盘幅度分层交叉验证（2026-08-10，知微）
=================================================
验证：主人的过滤器（涨幅偏离类×合规过滤）叠加"次日低开承接"是否正期望，
并做止损敏感性（-3/-5/-7%）。窗口扩到 2025-01-01 ~ 2026-08-07。

数据：
  - longhubang_daily（东财龙虎榜，D:\\workbuddyspace\\databases\\fundamental_data.db）
  - stock_daily_meta（日K open/high/low/close，D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_meta.db）
口径：
  - 涨幅偏离类：日涨幅偏离7% / 3日累计20% / 3日累计30% / 日涨幅15%
  - 合规过滤：剔 ST/科创/北交/低价(<2)/±30% 异常
  - 次日开盘买入：buy_ret = close1/open1 - 1；gap = open1/close0 - 1
  - 止损模拟：low1 <= open1×(1-s) 则按 -s 出场，否则收盘出场
输出：D:\\zcodesj\\t1_research\\lhb_dip_cross_result.json
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

FUND_DB = Path(r'D:\workbuddyspace\databases\fundamental_data.db')
META_DB = Path(r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db')
WIN0, WIN1 = '20250101', '20260807'
OUT = Path(r'D:\zcodesj\t1_research\lhb_dip_cross_result.json')

UP_REASONS = ['日涨幅偏离值达到7%', '日收盘价格涨幅偏离值达到7%',
              '涨幅偏离值累计达到20%', '非ST、*ST和S证券连续三个交易日内收盘价格涨幅偏离值累计达到20%',
              '涨幅偏离值累计达到30%', '日涨幅达到15%', '日收盘价格涨幅达到15%']


def load():
    con = sqlite3.connect(FUND_DB)
    lhb = pd.read_sql("SELECT 代码, 名称, 上榜日, 上榜原因, 收盘价 FROM longhubang_daily", con)
    con.close()

    lhb['上榜日'] = pd.to_datetime(lhb['上榜日'].astype(str), format='%Y-%m-%d', errors='coerce')
    lhb = lhb.dropna(subset=['上榜日'])
    lhb['d'] = lhb['上榜日'].dt.strftime('%Y%m%d')
    lhb = lhb[(lhb['d'] >= WIN0) & (lhb['d'] <= WIN1)].copy()
    lhb['code'] = lhb['代码'].astype(str).str.zfill(6)
    lhb = lhb.drop_duplicates(['code', 'd', '上榜原因'])

    con2 = sqlite3.connect(META_DB)
    k = pd.read_sql(
        "SELECT code, date, open, high, low, close FROM stock_daily_meta "
        "WHERE date >= 20241201", con2)
    con2.close()
    k['date'] = k['date'].astype(str)
    k = k.sort_values(['code', 'date']).drop_duplicates(['code', 'date'])
    return lhb, k


def main():
    lhb, k = load()
    print(f"[info] 龙虎榜窗口 {WIN0}~{WIN1} 行数: {len(lhb)}")

    # 事件日 close + 次日 open/high/low/close（MIN 语义）
    k['d'] = k['date']
    k['next_open'] = k.groupby('code')['open'].shift(-1)
    k['next_high'] = k.groupby('code')['high'].shift(-1)
    k['next_low'] = k.groupby('code')['low'].shift(-1)
    k['next_close'] = k.groupby('code')['close'].shift(-1)
    k['prev_close'] = k.groupby('code')['close'].shift(1)

    m = lhb.merge(k[['code', 'd', 'close', 'next_open', 'next_high', 'next_low', 'next_close']],
                  on=['code', 'd'], how='left')
    m['ret1'] = m['next_close'] / m['close'] - 1

    # 合规过滤
    m['is_st'] = m['名称'].astype(str).str.upper().str.contains('ST', na=False)
    m['is_kc_bj'] = m['code'].str.startswith(('688', '689', '4', '8', '92'))
    ok = (~m['is_st']) & (~m['is_kc_bj']) & (m['close'] >= 2) & \
         (m['ret1'].notna()) & (m['ret1'] >= -0.3) & (m['ret1'] <= 0.3) & \
         (m['next_open'] > 0)
    m = m[ok].copy()

    m['is_up'] = m['上榜原因'].astype(str).str.contains('|'.join(UP_REASONS), regex=True, na=False)

    print(f"[info] 合规过滤后: {len(m)} 行（涨幅偏离类 {int(m['is_up'].sum())}）")

    m['gap'] = m['next_open'] / m['close'] - 1
    m['buy_ret'] = m['next_close'] / m['next_open'] - 1
    m['year'] = m['d'].str[:4]
    # 止损模拟（low 相对买入价）
    for s in (0.03, 0.05, 0.07):
        m[f'stop{s}'] = np.where(m['next_low'] / m['next_open'] - 1 <= -s, -s, m['buy_ret'])

    out = {}

    def block(df, tag):
        n = len(df)
        if n == 0:
            print(f'[{tag}] 无样本')
            return
        ge3 = (df['buy_ret'] >= 0.03).mean() * 100
        avg = df['buy_ret'].mean() * 100
        med = df['buy_ret'].median() * 100
        print(f'[{tag}] n={n:>5} 开盘买入≥3%={ge3:5.2f}% 平均={avg:6.3f}% 中位={med:6.3f}%')
        row = {'n': int(n), 'ge3_pct': round(float(ge3), 2), 'avg_ret_pct': round(float(avg), 3),
               'med_ret_pct': round(float(med), 3)}
        for s in (0.03, 0.05, 0.07):
            g = (df[f'stop{s}'] >= 0.03).mean() * 100
            a = df[f'stop{s}'].mean() * 100
            row[f'stop{s}_ge3'] = round(float(g), 2)
            row[f'stop{s}_avg'] = round(float(a), 3)
        out[tag] = row

    print('\n===== 涨幅偏离类：次日开盘幅度分层（开盘买入→收盘） =====')
    up = m[m['is_up']]
    gaps = [(-1, -0.03, '低开<-3%'), (-0.03, 0, '-3~0%'), (0, 0.03, '0~3%'),
            (0.03, 0.07, '3~7%'), (0.07, 1, '高开>7%')]
    for lo, hi, lab in gaps:
        block(up[(up['gap'] > lo) & (up['gap'] <= hi)], f'涨幅偏离×{lab}')

    print('\n===== 涨幅偏离类 低开<-3%：按年分组（稳定性） =====')
    dip = up[up['gap'] <= -0.03]
    for y, g in dip.groupby('year'):
        block(g, f'{y} 低开<-3%')

    print('\n===== 涨幅偏离类 低开<-3%：止损敏感性（-3/-5/-7%） =====')
    block(dip, '涨幅偏离×低开<-3% 汇总')

    print('\n===== 对照：全部龙虎榜（非涨幅类）低开<-3% =====')
    block(m[~m['is_up'] & (m['gap'] <= -0.03)], '非涨幅类×低开<-3%')

    print('\n===== 对照：涨幅偏离类 全 gap（无分层） =====')
    block(up, '涨幅偏离类全部')

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n[saved] {OUT}')


if __name__ == '__main__':
    main()
