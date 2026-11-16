# -*- coding: utf-8 -*-
"""补涨候选扫描器(2026-09-06 板块轮动研究的产品化).

研究依据(sector_rotation_20260906): 2026年12个热门板块月, 板内当月收益Q1(最弱)次月+3.05%均值/51.4%胜率最优,
Q5(龙头)次月-1.31%/36.3%最差——热门板块确认后, 板内低位补涨占优.

扫描逻辑(对应9月=8月热门板块的次月窗口):
  1. 识别上月(202608)热门板块(中位月收益>8%, n≥20)
  2. 板内8月收益后40%分位=滞涨股(补涨候选池)
  3. 过滤: 池内(≤20元)/PIT盈利/排雷≤1/8月跌幅不过深(>-25%, 剔除阴跌弱势)
  4. 输出: 9月前4日已启动度(信息) + 20日持有窗口
用法: python sector_laggard_scan.py
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
RISK12 = ['lhb_buy_5', 'limit_break_5', 'new_high_3', 'vwap60_bias', 'vwap20_bias',
          'gap_up_run_5', 'chan_buy3_10', 'new_high20', 'macd_golden_hi', 'rounded_20',
          'red_3_soldiers', 'higher_low']

def main():
    m = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v5_full.pkl'))
    m['ym'] = m['date'].str[:6]
    m26 = m[m['ym'] >= '202601'].sort_values(['code', 'date'])
    cl = m26.groupby(['code', 'ym'])['close'].agg(['first', 'last']).reset_index()
    cl = cl.sort_values(['code', 'ym'])
    cl['prev_last'] = cl.groupby('code')['last'].shift(1)
    cl['mret'] = np.where(cl['prev_last'].notna(), cl['last']/cl['prev_last']-1, np.nan)
    ind = m26.groupby('code')['L1'].first().reset_index()
    cl = cl.merge(ind, on='code')

    # 1. 上月(202608)热门板块
    sec_m = cl.groupby(['ym', 'L1'])['mret'].median().rename('sec_med').reset_index()
    sec_n = cl.groupby(['ym', 'L1'])['code'].nunique().rename('n').reset_index()
    hot = sec_m.merge(sec_n, on=['ym', 'L1'])
    hot = hot[(hot['ym'] == '202608') & (hot['sec_med'] > 0.08) & (hot['n'] >= 20)]
    print('===== 8月热门板块(轮动研究口径) =====')
    print(hot.sort_values('sec_med', ascending=False).to_string(index=False))

    # 2. 板内滞涨股(8月收益后40%分位) + 质量过滤
    last = m['date'].max()
    cs = m[m['date'] == last].set_index('code')
    con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    nm = dict(con.execute("SELECT code,name FROM industry_map_ths").fetchall())
    con.close()

    cands = []
    for _, h in hot.iterrows():
        l1 = h['L1']
        cur = cl[(cl['ym'] == '202608') & (cl['L1'] == l1) & cl['mret'].notna()].copy()
        if len(cur) < 10: continue
        q40 = cur['mret'].quantile(0.4)
        lag = cur[cur['mret'] <= q40]['code'].tolist()
        for code in lag:
            if code not in cs.index: continue
            r = cs.loc[code]
            if isinstance(r, pd.DataFrame): r = r.iloc[0]
            if r['pit_ep'] is None or pd.isna(r['pit_ep']) or r['pit_ep'] <= 0: continue   # 盈利
            risk = sum(1 for f in RISK12 if pd.notna(r.get(f)) and r[f] > 0.5)
            if risk > 1: continue                                                          # 排雷≤1
            if r['ret20'] is not None and pd.notna(r['ret20']) and r['ret20'] < -0.25: continue  # 阴跌剔
            # 9月已启动度(9/1-9/4涨幅)
            sep = m[(m['code'] == code) & (m['ym'] == '202609')]
            sep_ret = (sep['close'].iloc[-1]/sep['close'].iloc[0]-1)*100 if len(sep) > 1 else 0
            cands.append({'code': code, 'name': nm.get(code, '?'), '板块': l1,
                          '8月收益%': round(cur[cur['code'] == code]['mret'].iloc[0]*100, 1),
                          '9月已动%': round(sep_ret, 1),
                          '排雷': risk, 'ROE%': round(float(r['pit_roe']), 1) if pd.notna(r['pit_roe']) else None,
                          'pos250%': round(r['pos250']*100), '收盘': round(r['close'], 2)})
    df = pd.DataFrame(cands).sort_values(['板块', '8月收益%'])
    print(f'\n===== 补涨候选(8月滞涨40%分位+盈利+排雷≤1+非阴跌): {len(df)}只 =====')
    print(df.to_string(index=False))

    # 9月未动的排前(窗口剩余大)
    fresh = df[df['9月已动%'].abs() < 5]
    print(f'\n其中9月尚未明显启动(|涨幅|<5%): {len(fresh)}只 → 补涨窗口剩余较大')
    out = {'扫描日': last, 'cands': df.to_dict('records'), 'fresh': fresh['code'].tolist()}
    json.dump(out, open(r'D:\zcodesj\data\sector_laggard_scan_20260906.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=str)
    print('落盘 data/sector_laggard_scan_20260906.json')

if __name__ == '__main__':
    main()
