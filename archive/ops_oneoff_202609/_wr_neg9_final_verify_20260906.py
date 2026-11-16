# -*- coding: utf-8 -*-
"""10只入选资格·剩余5项NEG独立复算(qfq直算, 不依赖影子文件). 全零=10只100%独立闭合."""
import sys, sqlite3
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd, numpy as np

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
CODES = ['001390', '002152', '002478', '002481', '002777', '300296', '300773', '301370', '600626', '600987']
LAST = '20260904'

con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
ind = pd.read_sql_query("SELECT code,industry FROM industry_map_ths", sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True))
ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]

# 全池9/4涨幅(L1内rank用)
allc = pd.read_sql_query(
    "SELECT code,close,change_pct FROM stock_daily_meta_qfq WHERE date=?", con, params=[LAST])
prevc = pd.read_sql_query(
    "SELECT a.code, a.close AS c912, b.close AS c904 FROM stock_daily_meta_qfq a "
    "JOIN stock_daily_meta_qfq b ON a.code=b.code AND b.date=? "
    "WHERE a.date=(SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<?)",
    con, params=[LAST, LAST])
chgmap = dict(zip(prevc['code'], (prevc['c904']/prevc['c912']-1)*100))
allc['pct'] = allc['code'].map(chgmap)
allc = allc.merge(ind[['code', 'L1']].drop_duplicates('code'), on='code', how='left')
lim_all = set(allc[allc['pct'] >= 9.8]['code'])
rel_all = set()
for l1, grp in allc.dropna(subset=['pct']).groupby('L1'):
    rk = grp['pct'].rank(pct=True)
    rel_all |= set(grp[rk >= 0.90]['code'])

print(f'{"code":8s}{"vwap60偏":>9s}{"up4of5":>7s}{"涨停":>5s}{"板块前10%":>9s}{"跳空未回补":>10s}  判定')
bad_total = 0
for code in CODES:
    g = pd.read_sql_query(
        "SELECT date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE code=? AND date>='20130101' ORDER BY date", con, params=[code])
    g = g.reset_index(drop=True)
    last = g.iloc[-1]
    assert str(last['date']) == LAST, f'{code} 截面异常 {last["date"]}'
    # vwap60_bias
    r60 = g.tail(60)
    vwap = (r60['close'] * r60['volume']).sum() / r60['volume'].sum()
    vw = bool(last['close'] > vwap * 1.10)
    # up4of5 (影子口径: 近10自然日窗口内阳线>=4)
    u = g[g['date'].astype(str) >= str(int(LAST) - 10)]
    up45 = int((u['close'] > u['open']).sum()) >= 4
    # lim_today / rel_ind
    lim = code in lim_all
    rel = code in rel_all
    # gap_up_hold: 近20日尾部, 连续3日低点>前日高点且第3日前一日不成立
    r20 = g.tail(20).reset_index(drop=True)
    ph = r20['high'].shift(1)
    lo = r20['low']
    gu = (lo > ph) & (lo.shift(1) > ph.shift(1)) & (lo.shift(2) > ph.shift(2)) & (lo.shift(3) <= ph.shift(3))
    ghold = bool(gu.iloc[-1])
    flags = [vw, up45, lim, rel, ghold]
    bad_total += sum(flags)
    verdict = 'PASS' if not any(flags) else '!!触发 ' + ','.join(
        n for n, f in zip(['vwap60', 'up4of5', 'lim', 'rel10%', 'gap_hold'], flags) if f)
    print(f'{code:8s}{str(vw):>9s}{str(up45):>7s}{str(lim):>5s}{str(rel):>9s}{str(ghold):>10s}  {verdict}')
con.close()
print(f'\n总计触发: {bad_total} → ' + ('10只NEG9全量独立复算闭合, 入选资格100%实证' if bad_total == 0 else '存在触发, 需查'))
