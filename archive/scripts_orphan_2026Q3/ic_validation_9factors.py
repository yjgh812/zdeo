# -*- coding: utf-8 -*-
"""
Anomaly 9 因子批量 IC 验证（12 个月末截面）
"""
import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

MAIN_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'


def get_month_end_dates(start='20250801', end='20260731'):
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    try:
        dates = pd.read_sql_query(
            "SELECT DISTINCT date FROM stock_daily_meta WHERE date BETWEEN ? AND ? ORDER BY date",
            conn, params=[start, end])['date'].tolist()
    finally:
        conn.close()
    df = pd.DataFrame({'date': dates})
    df['ym'] = df['date'].astype(str).str[:6]
    return sorted(df.groupby('ym')['date'].last().tolist())


def calc_anomaly_snapshot(date):
    """计算全部 9 因子的截面快照"""
    conn = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query("""
            WITH ranked AS (
                SELECT code, report_date, revenue, operating_profit, profit_before_tax,
                       net_profit, operating_cashflow, total_assets, total_equity,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY report_date DESC) AS rn
                FROM finance_daily WHERE report_date <= ? AND total_assets > 0
            )
            SELECT * FROM ranked WHERE rn <= 5
        """, conn, params=[f"{str(date)[:4]}-{str(date)[4:6]}-{str(date)[6:8]}"])
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame()
    df['code'] = df['code'].astype(str).str[:6]
    results = []
    for code, grp in df.groupby('code'):
        grp = grp.sort_values('report_date')
        if len(grp) < 2:
            continue
        latest = grp.iloc[-1]
        year_ago = grp.iloc[-5] if len(grp) >= 5 else grp.iloc[0]
        ta = latest['total_assets']
        eq = latest['total_equity']
        if ta <= 0:
            continue
        net, rev = latest['net_profit'], latest['revenue']
        op, pbt = latest['operating_profit'], latest['profit_before_tax']
        cf = latest['operating_cashflow']
        results.append({
            'code': code,
            'ag': latest['total_assets']/year_ago['total_assets']-1 if year_ago['total_assets']>0 else None,
            'droe': (net/eq - year_ago['net_profit']/year_ago['total_equity']) if eq>0 and year_ago['total_equity']>0 else None,
            'acc': (net-cf)/ta if cf is not None else None,
            'sg': latest['revenue']/year_ago['revenue']-1 if rev and year_ago['revenue'] else None,
            'roa': net/ta,
            'pm': op/rev if rev and rev>0 else None,
            'ato': rev/ta if rev else None,
            'opa': op/ta,
            'tbi': pbt/net if net and net!=0 else None,
            'bl': ta/eq if eq and eq>0 else None,
        })
    return pd.DataFrame(results)


def calc_fwd_ret(date):
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query("""
            WITH base AS (
                SELECT code, date, close,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn_past
                FROM stock_daily_meta WHERE date <= ? AND close > 0
            ),
            future AS (
                SELECT code, date, close,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY date ASC) AS rn_future
                FROM stock_daily_meta WHERE date > ? AND close > 0
            )
            SELECT b.code, b.close as close_now, f.close as close_5d
            FROM base b LEFT JOIN future f ON b.code = f.code AND f.rn_future = 5
            WHERE b.rn_past = 1
        """, conn, params=[date, date])
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame()
    df['code'] = df['code'].astype(str).str[:6]
    df['fwd_ret_5d'] = df['close_5d']/df['close_now'] - 1
    return df[['code','fwd_ret_5d']].dropna(subset=['fwd_ret_5d'])


def main():
    dates = get_month_end_dates()
    factors = ['ag','droe','acc','sg','roa','pm','ato','opa','tbi','bl']
    ics = {f: [] for f in factors}

    for d in dates:
        snap = calc_anomaly_snapshot(d)
        fwd = calc_fwd_ret(d)
        if snap.empty or fwd.empty:
            continue
        merged = snap.merge(fwd, on='code', how='inner')
        for f in factors:
            sub = merged.dropna(subset=[f, 'fwd_ret_5d'])
            if len(sub) >= 30:
                ic, _ = spearmanr(sub[f], sub['fwd_ret_5d'])
                if not np.isnan(ic):
                    ics[f].append(ic)

    print(f"{'因子':<6} {'mean IC':>9} {'ICIR':>8} {'正%':>5} {'负%':>5} {'判定':<12}")
    print("-" * 55)
    for f in factors:
        arr = np.array(ics[f])
        if len(arr) == 0:
            print(f"{f:<6} {'N/A':>9} {'':>8} {'':>5} {'':>5} 数据不足")
            continue
        mean_ic = arr.mean()
        icir = mean_ic / arr.std() if arr.std() > 0 else 0
        pos = (arr > 0).mean()
        neg = (arr < 0).mean()
        if abs(mean_ic) > 0.03 and max(pos, neg) >= 0.8:
            verdict = "✅ 有效"
        elif abs(mean_ic) > 0.02:
            verdict = "🟡 弱"
        else:
            verdict = "⚠️ 无效"
        print(f"{f:<6} {mean_ic:>9.4f} {icir:>8.3f} {pos:>5.0%} {neg:>5.0%} {verdict:<12}")


if __name__ == "__main__":
    main()
