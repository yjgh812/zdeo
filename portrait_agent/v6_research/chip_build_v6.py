# -*- coding: utf-8 -*-
"""筹码分布特征自建(2026-09-06 体系升级A项, 文档§2.3筹码面).

复用 chip_manual_3gate.chip_window(210日三角形+换手衰减, 8/29已验证实现).
口径: 不复权价+daily_basic换手率 → 域=2020+(换手可得期, 先例同), 2013-2019缺失如实标注.
窗口: 每20交易日重算(与组合C窗口对齐), F1获利比例/F2筹码宽度(c95-c05)/close/F3乖离(close-c50)/c50.
验证: F1/F2/F3 截面五分位的 fwd20 超额 + 高获利组暴跌率(下跌画像联动).
"""
import os, sys, json, time
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
from chip_manual_3gate import chip_window

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
TRAD_PREFIX = ('600','601','603','605','000','001','002','003','300','301','302','688','689')
HERE = os.path.dirname(os.path.abspath(__file__))
T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

def main():
    # 不复权日线 + 换手率
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    meta = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    px = pd.read_sql_query(
        "SELECT code,date,open,high,low,close FROM stock_daily_meta_qfq "
        "WHERE date>='20190101'", con)   # 210日预热→2020起有效
    con.close()
    px = px[px['code'].str[:3].isin(TRAD_PREFIX)]
    con2 = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    db = pd.read_sql_query(
        "SELECT ts_code,trade_date,turnover_rate FROM daily_basic WHERE trade_date>='20190101'", con2)
    ind = pd.read_sql_query("SELECT code,name FROM industry_map_ths", con2)
    con2.close()
    st = set(ind[ind['name'].fillna('').str.contains('ST|退', regex=True)]['code'])
    db['code'] = db['ts_code'].str[:6]
    turn_map = db.set_index(['code', 'trade_date'])['turnover_rate']
    log(f'价格{len(px)} 换手{len(db)}')

    dates = sorted(px['date'].unique())
    win_dates = set(dates[::20])   # 20日窗口
    rows = []
    for k, (code, g) in enumerate(px.groupby('code', sort=False)):
        if k % 800 == 0: log(f'stock {k}')
        if code in st: continue
        g = g.drop_duplicates('date').sort_values('date').reset_index(drop=True)
        o = g['open'].to_numpy(float); c = g['close'].to_numpy(float)
        h = g['high'].to_numpy(float); l = g['low'].to_numpy(float)
        tr = np.array([turn_map.get((code, d), np.nan) for d in g['date']], dtype=float)
        # 有效起点: 换手可得
        valid0 = np.where(~np.isnan(tr))[0]
        if len(valid0) < 260: continue
        i0 = max(valid0[0], 209)
        for i in range(i0, len(g), 1):
            if g['date'].iloc[i] not in win_dates: continue
            w = slice(i-209, i+1)
            tw = tr[w]
            if np.isnan(tw).any(): continue   # 窗口内换手缺失→跳过(fail-safe同先例)
            r = chip_window(o[w], c[w], h[w], l[w], tw, c[i])
            if r is None: continue
            f1, f2, f3 = r
            rows.append({'code': code, 'date': g['date'].iloc[i],
                         'benefit': f1, 'width': f2, 'bias_c50': f3})
    df = pd.DataFrame(rows)
    log(f'筹码特征 {df.shape} 域={df["date"].min()}~{df["date"].max()}')

    # 画像验证: 与panel_v5 merge取fwd20
    panel = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v5_full.pkl'))
    mm = panel[['code', 'date', 'fwd20_close', 'close']].merge(df, on=['code', 'date'], how='inner')
    pool = panel.dropna(subset=['fwd20_close'])
    day_mean = pool.groupby('date')['fwd20_close'].mean()
    mm['ex'] = mm['fwd20_close'] - mm['date'].map(day_mean)
    mm = mm.dropna(subset=['ex'])
    mm['dn'] = (mm['fwd20_close'] <= -0.15)
    out = {'n_rows': len(mm), 'n_stocks': int(mm['code'].nunique()),
           '域': f'{mm["date"].min()}~{mm["date"].max()}'}
    prof = []
    for f in ['benefit', 'width', 'bias_c50']:
        mm['q'] = mm.groupby('date')[f].rank(pct=True)
        for lo, hi, tag in [(0.8, 1.01, 'q80-100高'), (0.0, 0.2, 'q0-20低')]:
            sub = mm[(mm['q'] >= lo) & (mm['q'] < hi)]
            if len(sub) < 200: continue
            t_val = float(sub['ex'].mean()/(sub['ex'].std()/np.sqrt(len(sub)))) if sub['ex'].std() > 0 else np.nan
            prof.append({'feat': f, 'grp': tag, 'n': len(sub),
                         'ex20_pct': round(sub['ex'].mean()*100, 3),
                         't': round(t_val, 2) if not np.isnan(t_val) else None,
                         'win_pct': round((sub['ex'] > 0).mean()*100, 1),
                         'P_跌15': round(sub['dn'].mean()*100, 2)})
    prof_df = pd.DataFrame(prof)
    print('\n筹码特征画像(20日截面五分位端组):\n' + prof_df.to_string(index=False))
    out['profile'] = prof_df.to_dict('records')
    df.to_pickle(os.path.join(HERE, 'cache', 'chip_feat_v1.pkl'))
    json.dump(out, open(os.path.join(HERE, 'reports', 'chip_build_v6_20260906.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log('落盘 chip_feat_v1.pkl + chip_build_v6_20260906.json')

if __name__ == '__main__':
    main()
