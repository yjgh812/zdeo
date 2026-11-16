# -*- coding: utf-8 -*-
"""
IC 验证：SUE / RSH / Anomaly 三因子截面 IC 分析
抽样 12 个月末截面，计算 Spearman IC + 方向一致性
"""
import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
MAIN_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'


def get_month_end_dates(start='20250801', end='20260731'):
    """获取区间内每月最后一个交易日"""
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    try:
        dates = pd.read_sql_query(
            "SELECT DISTINCT date FROM stock_daily_meta WHERE date BETWEEN ? AND ? ORDER BY date",
            conn, params=[start, end]
        )['date'].tolist()
    finally:
        conn.close()
    # 每月最后一个交易日
    df = pd.DataFrame({'date': dates})
    df['ym'] = df['date'].astype(str).str[:6]
    month_ends = df.groupby('ym')['date'].last().tolist()
    return sorted(month_ends)


def calc_rsh_factor(date):
    """计算 RSH 因子截面"""
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query("""
            WITH ranked AS (
                SELECT code, date, close,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
                FROM stock_daily_meta
                WHERE date <= ? AND close > 0
            )
            SELECT code, date, close FROM ranked WHERE rn <= 21
        """, conn, params=[date])
    finally:
        conn.close()
    if df.empty: return pd.DataFrame()
    df['code'] = df['code'].astype(str).str.replace('.SH','').str.replace('.SZ','').str[:6]
    df = df.sort_values(['code','date'])
    results = []
    for code, grp in df.groupby('code'):
        if len(grp) >= 21:
            closes = grp['close'].values.astype(float)
            rets = np.diff(closes) / closes[:-1]
            ret_sign = (rets > 0).astype(float)
            order = np.argsort(ret_sign, kind='stable')
            high = rets[order[10:]]
            results.append({'code': code, 'rsh': float(np.mean(high)) if len(high) else 0})
    return pd.DataFrame(results)


def calc_anomaly_factor(date):
    """计算 Anomaly 因子截面（综合 4 因子 rank）"""
    conn = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query("""
            WITH ranked AS (
                SELECT code, report_date, revenue, net_profit, operating_cashflow, total_assets, total_equity,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY report_date DESC) AS rn
                FROM finance_daily WHERE report_date <= date(?, 'localtime') AND total_assets > 0
            )
            SELECT * FROM ranked WHERE rn <= 5
        """, conn, params=[str(date)])
    finally:
        conn.close()
    if df.empty: return pd.DataFrame()
    df['code'] = df['code'].astype(str).str[:6]
    results = []
    for code, grp in df.groupby('code'):
        grp = grp.sort_values('report_date')
        if len(grp) < 2: continue
        latest = grp.iloc[-1]
        prev = grp.iloc[-2] if len(grp) >= 2 else None
        year_ago = grp.iloc[-5] if len(grp) >= 5 else prev
        if year_ago is not None and year_ago['total_assets'] > 0:
            ag = latest['total_assets']/year_ago['total_assets']-1
        else:
            ag = None
        acc = (latest['net_profit']-latest['operating_cashflow'])/latest['total_assets'] if latest['operating_cashflow'] is not None else None
        results.append({'code': code, 'ag': ag, 'acc': acc})
    return pd.DataFrame(results)


def calc_fwd_ret(date):
    """计算 T+5 前瞻收益"""
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query("""
            WITH base AS (
                SELECT code, date, close,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn_past,
                       COUNT(*) OVER (PARTITION BY code) as cnt
                FROM stock_daily_meta WHERE date <= ? AND close > 0
            ),
            future AS (
                SELECT code, date, close,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY date ASC) AS rn_future
                FROM stock_daily_meta WHERE date > ? AND close > 0
            )
            SELECT b.code, b.close as close_now, f.close as close_5d
            FROM base b
            LEFT JOIN future f ON b.code = f.code AND f.rn_future = 5
            WHERE b.rn_past = 1
        """, conn, params=[date, date])
    finally:
        conn.close()
    if df.empty: return pd.DataFrame()
    df['code'] = df['code'].astype(str).str.replace('.SH','').str.replace('.SZ','').str[:6]
    df['fwd_ret_5d'] = df['close_5d']/df['close_now'] - 1
    return df[['code','fwd_ret_5d']].dropna(subset=['fwd_ret_5d'])


def main():
    dates = get_month_end_dates()
    print(f"抽样 {len(dates)} 个月末截面: {dates[0]} ~ {dates[-1]}")

    all_ics = {'rsh': [], 'ag': [], 'acc': []}

    for d in dates:
        # RSH 因子
        rsh_df = calc_rsh_factor(d)
        # Anomaly 因子
        anom_df = calc_anomaly_factor(d)
        # T+5 收益
        fwd_df = calc_fwd_ret(d)

        if rsh_df.empty or fwd_df.empty:
            continue

        # Merge RSH
        merged = rsh_df.merge(fwd_df, on='code', how='inner')
        if len(merged) >= 30:
            ic_rsh, _ = spearmanr(merged['rsh'], merged['fwd_ret_5d'])
            if not np.isnan(ic_rsh):
                all_ics['rsh'].append(ic_rsh)

        # Merge Anomaly
        if not anom_df.empty:
            merged2 = anom_df.merge(fwd_df, on='code', how='inner')
            merged2 = merged2.dropna(subset=['ag'])
            if len(merged2) >= 30:
                ic_ag, _ = spearmanr(merged2['ag'], merged2['fwd_ret_5d'])
                if not np.isnan(ic_ag):
                    all_ics['ag'].append(ic_ag)
            merged3 = anom_df.dropna(subset=['acc']).merge(fwd_df, on='code', how='inner')
            if len(merged3) >= 30:
                ic_acc, _ = spearmanr(merged3['acc'], merged3['fwd_ret_5d'])
                if not np.isnan(ic_acc):
                    all_ics['acc'].append(ic_acc)

        print(f"  {d}: rsh={all_ics['rsh'][-1]:.4f}" if all_ics['rsh'] else f"  {d}: rsh=N/A")

    # 汇总
    print(f"\n{'='*60}")
    print(f"IC 验证汇总 ({len(dates)} 个月末截面)")
    print(f"{'='*60}")
    for name, ics in all_ics.items():
        if ics:
            arr = np.array(ics)
            mean_ic = arr.mean()
            icir = mean_ic / arr.std() if arr.std() > 0 else 0
            pos_rate = (arr > 0).mean()
            neg_rate = (arr < 0).mean()
            direction = "正选" if mean_ic > 0 else "反选"
            print(f"\n{name}:")
            print(f"  mean IC = {mean_ic:.4f}")
            print(f"  ICIR    = {icir:.4f}")
            print(f"  方向: {direction} (正:{pos_rate:.0%} 负:{neg_rate:.0%})")
            print(f"  样本数: {len(ics)}")
            # 判定
            if abs(mean_ic) > 0.03 and max(pos_rate, neg_rate) >= 0.8:
                print(f"  ✅ 有效因子（|IC|>0.03 且方向一致>80%）")
            elif abs(mean_ic) > 0.02:
                print(f"  🟡 弱信号（|IC|>0.02）")
            else:
                print(f"  ⚠️ IC 过弱")


if __name__ == "__main__":
    main()
