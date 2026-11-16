# -*- coding: utf-8 -*-
"""
龙虎榜次日收益口径核对（2026-08-10，知微）
=================================================
目的：复现主人 2026-02~05 龙虎榜统计（全量次日≥3% 30.2%/涨停 11.9%/平均≈0；
涨幅偏离类 7%/20% 次日≥3% 38-42%/涨停 23-25%），逐层叠加过滤做归因，
定位 38-42% 与本地全量口径 27.5-30.7% 的差异来源。

数据：
  - longhubang_daily（东财龙虎榜，2026-02~05 共 6042 行）：代码/名称/上榜日/上榜原因/收盘价
  - daily_basic（收盘价/换手/市值）：算次日收益与过滤
口径：
  - 次日收益 = 下一交易日收盘 / 上榜日收盘 - 1（MIN(trade_date>d0) 取下一交易日）
  - 次日涨停 = 次日收盘 == round(上榜日收盘×(1+r),2)，r: 300/301→0.2, 688/689→0.2,
    名称含 ST→0.05, 其余→0.10
  - 过滤层级：L0 全量 → L1 剔±30%异常 → L2 剔ST → L3 剔科创/北交 → L4 剔低价(<2元)
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB = Path(r'D:\workbuddyspace\databases\fundamental_data.db')
WIN0, WIN1 = '20260201', '20260531'
OUT = Path(r'D:\zcodesj\t1_research\lhb_caliber_result.json')


def limit_ratio(code: str, name: str) -> float:
    """按板块返回涨跌幅限制。ST 优先（名称含 ST）。"""
    if 'ST' in name.upper():
        return 0.05
    if code.startswith(('300', '301', '688', '689')):
        return 0.20
    return 0.10


def is_black(code: str, name: str) -> bool:
    """红线剔除：科创(688/689)、北交(4/8/92 开头)、ST。"""
    if 'ST' in name.upper():
        return True
    if code.startswith(('688', '689')):
        return True
    if code.startswith(('4', '8', '92')) and code[0] in '48':
        return True
    return False


def load():
    con = sqlite3.connect(DB)
    lhb = pd.read_sql("SELECT 代码, 名称, 上榜日, 上榜原因, 收盘价, 涨跌幅 FROM longhubang_daily", con)
    db = pd.read_sql(
        "SELECT ts_code, trade_date, close, turnover_rate, circ_mv FROM daily_basic "
        "WHERE trade_date >= '20260101'", con)
    con.close()

    lhb['上榜日'] = pd.to_datetime(lhb['上榜日'].astype(str), format='%Y-%m-%d', errors='coerce')
    lhb = lhb.dropna(subset=['上榜日'])
    lhb['d'] = lhb['上榜日'].dt.strftime('%Y%m%d')
    lhb = lhb[(lhb['d'] >= WIN0) & (lhb['d'] <= WIN1)].copy()

    db['trade_date'] = db['trade_date'].astype(str)
    db['ts_code'] = db['ts_code'].astype(str)
    db['close'] = pd.to_numeric(db['close'], errors='coerce')
    db = db.sort_values(['ts_code', 'trade_date']).drop_duplicates(['ts_code', 'trade_date'])

    # 代码归一化：6 位 → xxxxxx.SH/SZ/BJ
    code_map = {}
    for full in db['ts_code'].unique():
        if '.' in full:
            code_map[full.split('.')[0]] = full
    lhb['ts_code'] = lhb['代码'].astype(str).str.zfill(6).map(code_map)
    lhb = lhb.dropna(subset=['ts_code'])

    # 下一交易日 close（MIN 语义）
    db['next_close'] = db.groupby('ts_code')['close'].shift(-1)
    db['prev_close'] = db.groupby('ts_code')['close'].shift(1)
    db['next_date'] = db.groupby('ts_code')['trade_date'].shift(-1)
    db['prev_date'] = db.groupby('ts_code')['trade_date'].shift(1)

    m = lhb.merge(db[['ts_code', 'trade_date', 'close', 'next_close', 'next_date', 'prev_close', 'prev_date',
                      'turnover_rate', 'circ_mv']], left_on=['ts_code', 'd'], right_on=['ts_code', 'trade_date'],
                  how='left', suffixes=('', '_d'))
    return lhb, m


def calc(df: pd.DataFrame) -> dict:
    """基础统计：次日收益/≥3%/涨停。"""
    df = df[df['next_close'].notna() & df['close'].notna() & (df['close'] > 0)].copy()
    df['ret1'] = df['next_close'] / df['close'] - 1
    df['lim1'] = (df['close'] * (1 + df['ratio'])).round(2)
    df['lu1'] = ((df['next_close'] - df['lim1']).abs() <= 0.001).astype(int)
    df['ge3'] = (df['ret1'] >= 0.03).astype(int)
    out = {
        'n': int(len(df)),
        'ge3_pct': round(float(df['ge3'].mean() * 100), 2),
        'lu1_pct': round(float(df['lu1'].mean() * 100), 2),
        'avg_ret_pct': round(float(df['ret1'].mean() * 100), 3),
    }
    return out, df


def main():
    lhb, m = load()
    m['ratio'] = [limit_ratio(c, n) for c, n in zip(m['ts_code'], m['名称'].astype(str))]
    m['black'] = [is_black(c, n) for c, n in zip(m['ts_code'], m['名称'].astype(str))]
    m['ret1_raw'] = pd.to_numeric(m['next_close'] / m['close'] - 1, errors='coerce')

    print(f"[info] longhubang_daily 窗口 {WIN0}~{WIN1} 行数: {len(m)}")
    print(f"[info] 可算次日收益: {m['ret1_raw'].notna().sum()}  (无次日数据: {m['ret1_raw'].isna().sum()})")

    # 过滤层级归因（逐层叠加）
    ratio_ok = m['ret1_raw'].isna() | ((m['ret1_raw'] >= -0.3) & (m['ret1_raw'] <= 0.3))
    not_st = ~m['名称'].astype(str).str.upper().str.contains('ST', na=False)
    not_kc_bj = ~m['ts_code'].str.startswith(('688', '689', '4', '8', '92'))
    not_low = m['close'] >= 2
    layers = {
        'L0 全量': m,
        'L1 剔±30%异常': m[ratio_ok],
        'L2 再剔ST': m[ratio_ok & not_st],
        'L3 再剔科创/北交': m[ratio_ok & not_st & not_kc_bj],
        'L4 再剔低价<2元': m[ratio_ok & not_st & not_kc_bj & not_low],
    }

    print('\n===== 逐层过滤归因（收盘对收盘口径） =====')
    results = {}
    for name, sub in layers.items():
        st, _ = calc(sub)
        results[name] = st
        print(f"{name:<18} n={st['n']:>5}  次日≥3%={st['ge3_pct']:>5.2f}%  涨停={st['lu1_pct']:>5.2f}%  平均={st['avg_ret_pct']:>6.3f}%")

    # reason 分类（L4 口径：剔异常+红线+低价）
    print('\n===== 按上榜原因分类（L4 口径：剔±30%/ST/科创北交/低价） =====')
    cats = {
        '涨幅偏离7%单日': ['日涨幅偏离值达到7%', '日收盘价格涨幅偏离值达到7%'],
        '3日累计20%': ['连续三个交易日内，涨幅偏离值累计达到20%', '非ST、*ST和S证券连续三个交易日内收盘价格涨幅偏离值累计达到20%'],
        '3日累计30%': ['连续三个交易日内，涨幅偏离值累计达到30%'],
        '涨幅15%': ['日涨幅达到15%', '日收盘价格涨幅达到15%'],
        '跌幅类': ['日跌幅偏离值达到7%', '日收盘价格跌幅偏离值达到7%', '跌幅偏离值累计达到20%', '收盘价格跌幅偏离值累计达到20%'],
        '换手类': ['换手率达到20%', '换手率达到30%'],
        '振幅类': ['振幅达到15%'],
    }
    base = m[ratio_ok & not_st & not_kc_bj & not_low]
    cat_results = {}
    for cat, kws in cats.items():
        mask = base['上榜原因'].astype(str).str.contains('|'.join(kws), regex=True, na=False)
        sub = base[mask]
        st, _ = calc(sub)
        cat_results[cat] = st
        print(f"{cat:<16} n={st['n']:>5}  次日≥3%={st['ge3_pct']:>5.2f}%  涨停={st['lu1_pct']:>5.2f}%  平均={st['avg_ret_pct']:>6.3f}%")
    # 涨幅大合并（7%+20%+30%+15%）
    upmask = base['上榜原因'].astype(str).str.contains(
        '涨幅偏离值达到7%|涨幅偏离值累计达到20%|涨幅偏离值累计达到30%|日涨幅达到15%|日收盘价格涨幅达到15%', regex=True, na=False)
    st, _ = calc(base[upmask])
    cat_results['涨幅类合并(7/20/30/15)'] = st
    print(f"{'涨幅类合并(7/20/30/15)':<14} n={st['n']:>5}  次日≥3%={st['ge3_pct']:>5.2f}%  涨停={st['lu1_pct']:>5.2f}%  平均={st['avg_ret_pct']:>6.3f}%")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'window': [WIN0, WIN1], 'layers': results, 'categories': cat_results},
                              ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n[saved] {OUT}')


if __name__ == '__main__':
    main()
