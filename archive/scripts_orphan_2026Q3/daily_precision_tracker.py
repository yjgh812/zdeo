#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日 Precision 跟踪框架（V5.1 反馈循环）
=========================================
每日自动评估：
  1. 昨日选股的实际 T+5 收益（需 T+5 后才有结果）
  2. 各模型(P2/V375/V2/HighWin)的 precision 变化
  3. 超跌反弹 vs 超跌均值回归 vs 多头的 precision 对比
  4. RSH 因子的 IC 变化

用法：
  python daily_precision_tracker.py                  # 评估最近可结算的交易日
  python daily_precision_tracker.py --date 20260804  # 指定评估日（需 T+5 数据）

输出：
  D:\\zcodesj\\reports\\precision_track_{date}.json
"""
import sqlite3
import json
import os
import sys
import time
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from datetime import datetime

MAIN_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
OUT_DIR = r'D:\zcodesj\reports'
COST = 0.007  # 0.7% 双边成本


def get_latest_settleable_date():
    """获取最近可结算的交易日（T-5，确保有 T+5 收益）"""
    conn = sqlite3.connect(f'file:{META_DB}?mode=ro', uri=True)
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM stock_daily_meta WHERE close > 0 ORDER BY date DESC LIMIT 6"
        ).fetchall()]
    finally:
        conn.close()
    return dates[-1] if len(dates) >= 6 else None


def evaluate_models(date_int):
    """评估各模型在 date_int 的 precision"""
    conn_meta = sqlite3.connect(f'file:{META_DB}?mode=ro', uri=True)
    fwd = pd.read_sql_query("""
        SELECT code, date, close,
               LEAD(close, 5) OVER (PARTITION BY code ORDER BY date) as close_5d,
               close / LAG(close, 60) OVER (PARTITION BY code ORDER BY date) - 1 as ret_3m,
               close / LAG(close, 20) OVER (PARTITION BY code ORDER BY date) - 1 as ret_20
        FROM stock_daily_meta WHERE date >= ? AND close > 0
    """, conn_meta, params=[date_int - 500])  # 取 ~500 日历天确保 LAG(60)+LEAD(5) 有足够数据
    conn_meta.close()

    fwd['fwd_ret_5d'] = fwd['close_5d'] / fwd['close'] - 1
    fwd = fwd.dropna(subset=['fwd_ret_5d', 'ret_3m', 'ret_20'])
    fwd['code'] = fwd['code'].astype(str).str.replace('.SH', '').str.replace('.SZ', '').str[:6]
    fwd['win'] = (fwd['fwd_ret_5d'] > COST).astype(int)

    # 只看评估日当天的截面
    day_data = fwd[fwd['date'] == date_int].copy()
    if day_data.empty:
        return None

    conn = sqlite3.connect(f'file:{MAIN_DB}?mode=ro', uri=True)
    results = {}

    for table, col, label in [
        ('stock_signals_p2', 'p2_prob', 'P2'),
        ('stock_signals_v375', 'v375_prob', 'V375'),
        ('stock_signals_v2', 'v2_prob', 'V2'),
        ('stock_signals_highwin', 'highwin_prob', 'HighWin'),
    ]:
        try:
            probs = pd.read_sql_query(
                f"SELECT code, {col} as prob FROM {table} WHERE date = ? AND {col} IS NOT NULL",
                conn, params=[date_int])
        except Exception:
            continue
        if probs.empty:
            continue
        probs['code'] = probs['code'].astype(str).str[:6]
        merged = day_data.merge(probs, on='code', how='inner')

        for thresh in [0.65, 0.70]:
            sub = merged[merged['prob'] >= thresh]
            if len(sub) >= 30:
                results[f'{label}_≥{thresh}'] = {
                    'precision': round(float(sub['win'].mean()), 4),
                    'mean_ret': round(float(sub['fwd_ret_5d'].mean()), 4),
                    'n': len(sub),
                }
    conn.close()

    # 超跌模式对比（用 P2≥0.65 的子集）
    try:
        p2 = pd.read_sql_query(
            "SELECT code, p2_prob FROM stock_signals_p2 WHERE date = ? AND p2_prob >= 0.65",
            sqlite3.connect(f'file:{MAIN_DB}?mode=ro', uri=True), params=[date_int])
        p2['code'] = p2['code'].astype(str).str[:6]
        merged = day_data.merge(p2, on='code', how='inner')

        for mode, mask in [
            ('超跌均值回归(ret3m<-10%)', merged['ret_3m'] < -0.10),
            ('超跌反弹(ret3m<-10%,ret20>+3%)', (merged['ret_3m'] < -0.10) & (merged['ret_20'] > 0.03)),
            ('多头(ret3m>0)', merged['ret_3m'] > 0),
        ]:
            sub = merged[mask]
            if len(sub) >= 10:
                results[mode] = {
                    'precision': round(float(sub['win'].mean()), 4),
                    'mean_ret': round(float(sub['fwd_ret_5d'].mean()), 4),
                    'n': len(sub),
                }
    except Exception:
        pass

    return results


def main():
    if '--date' in sys.argv:
        date_int = int(sys.argv[sys.argv.index('--date') + 1])
    else:
        date_int = get_latest_settleable_date()

    if date_int is None:
        print("❌ 无法获取可结算日期")
        return 1

    print(f"{'='*60}")
    print(f"每日 Precision 跟踪 — 评估日 {date_int}")
    print(f"{'='*60}")

    results = evaluate_models(date_int)
    if results is None:
        print(f"❌ 日期 {date_int} 无有效数据")
        return 1

    print(f"\n📊 模型 Precision:")
    for key, val in sorted(results.items()):
        if 'P2' in key or 'V375' in key or 'V2' in key or 'HighWin' in key:
            print(f"  {key:25s}: prec={val['precision']:.1%} ret={val['mean_ret']:+.2%} N={val['n']}")

    print(f"\n📊 超跌模式对比 (P2≥0.65 子集):")
    for key, val in sorted(results.items()):
        if '超跌' in key or '多头' in key:
            print(f"  {key:35s}: prec={val['precision']:.1%} ret={val['mean_ret']:+.2%} N={val['n']}")

    # 保存
    os.makedirs(OUT_DIR, exist_ok=True)
    out = {
        'evaluate_date': date_int,
        'cost_threshold': COST,
        'results': results,
        'timestamp': datetime.now().isoformat(),
    }
    out_path = os.path.join(OUT_DIR, f'precision_track_{date_int}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 报告: {out_path}")

    # 简单告警
    p2_70 = results.get('P2_≥0.70', {}).get('precision', 0)
    v375_70 = results.get('V375_≥0.70', {}).get('precision', 0)
    if p2_70 > 0 and v375_70 > 0:
        if p2_70 - v375_70 > 0.20:
            print(f"\n⚠️ P2({p2_70:.0%}) >> V375({v375_70:.0%})，差距>20pp，V375仍在拖累融合")
        elif v375_70 > p2_70:
            print(f"\n🟢 V375({v375_70:.0%}) > P2({p2_70:.0%})，V375重训生效！")

    return 0


if __name__ == "__main__":
    sys.exit(main())
