# -*- coding: utf-8 -*-
"""组合C 2016 回测 next-bar 敏感性补算:
对每笔实际入选交易 (ws, code), 用 qfq 主库真实序列计算
nb_ret = close(t+20交易日) / open(t+1交易日) - 1  (t+1 开盘买入, t+20 收盘卖出)。
输出单笔胜率/均值 与 组合层面(每窗入选均值-千三)年化, 对照 close-to-close 冻结口径。
产出: reports/combo_c_2016_nextbar_20260906.json"""
import os, sys, json, time
import numpy as np
import pandas as pd
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
TRADES = os.path.join(HERE, 'reports', 'combo_c_2016_trades_20260906.csv')
OUT = os.path.join(HERE, 'reports', 'combo_c_2016_nextbar_20260906.json')
COST_RT = 0.003

def log(s): print(s, flush=True)

def main():
    t0 = time.time()
    tr = pd.read_csv(TRADES, dtype={'code': str, 'date': str})
    codes = sorted(tr['code'].unique())
    wss = sorted(tr['date'].unique())
    log(f'trades {len(tr)} 笔, codes {len(codes)}, 窗 {len(wss)}')
    con = sqlite3.connect(QFQ_DB)
    con.execute('CREATE TEMP TABLE tmp_codes(code TEXT PRIMARY KEY)')
    con.executemany('INSERT OR IGNORE INTO tmp_codes VALUES(?)', [(c,) for c in codes])
    con.execute('CREATE TEMP TABLE tmp_ws(d TEXT PRIMARY KEY)')
    con.executemany('INSERT OR IGNORE INTO tmp_ws VALUES(?)', [(w,) for w in wss])
    sql = """
    SELECT code, date, next_open, close20 FROM (
      SELECT code, date,
             lead(open, 1)  OVER w AS next_open,
             lead(close, 20) OVER w AS close20
      FROM stock_daily_meta_qfq
      WHERE code IN (SELECT code FROM tmp_codes) AND date >= '20160101'
      WINDOW w AS (PARTITION BY code ORDER BY date)
    ) WHERE date IN (SELECT d FROM tmp_ws)
    """
    px = pd.read_sql_query(sql, con)
    con.close()
    log(f'qfq 窗口查询 {len(px)} 行 {time.time()-t0:.0f}s')

    m = tr.merge(px, left_on=['code', 'date'], right_on=['code', 'date'], how='left')
    m = m[m['next_open'].notna() & m['close20'].notna() & (m['next_open'] > 0)].copy()
    m['nb_ret'] = m['close20'] / m['next_open'] - 1
    cov = len(m) / len(tr)
    win = float((m['nb_ret'] > 0).mean() * 100)
    mean = float(m['nb_ret'].mean() * 100)
    # 组合层面: 每窗入选均值-成本, 复利年化
    g = m.groupby('date')['nb_ret'].mean() - COST_RT
    n_win = len(g)
    years = n_win * 20 / 243
    ann = ((1 + g).prod() ** (1 / years) - 1) * 100
    res = {
        '口径': 'nb_ret = close(t+20交易日)/open(t+1交易日) - 1; 组合=每窗入选等权均值-千三复利',
        '覆盖笔数': int(len(m)), '覆盖率': round(cov, 3),
        '单笔胜率_pct': round(win, 2), '单笔均值_pct': round(mean, 3),
        'nextbar成本后年化_pct': round(ann, 2), '窗口数': int(n_win),
    }
    log(json.dumps(res, ensure_ascii=False, indent=1))
    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log(f'落盘 {OUT} {time.time()-t0:.0f}s')

if __name__ == '__main__':
    main()
