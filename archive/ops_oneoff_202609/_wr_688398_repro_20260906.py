# -*- coding: utf-8 -*-
"""688398 赛特新材 影子配方只读全量复现 v2(归因: 为何不在v2终版15只)."""
import sys, sqlite3
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd, numpy as np
from morph_profile import compute_patterns

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
CODE = '688398'
LAST = '20260904'

con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
g = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
    "WHERE code=? AND date>='20130101' ORDER BY date", con, params=[CODE])
con.close()
print(f'bars={len(g)} 截面={g["date"].iloc[-1]}')
g = g.reset_index(drop=True)
pats = compute_patterns(g)
pats['date'] = g['date'].values
pats['date'] = pats['date'].astype(str)

tds = sorted(pats['date'].unique())
w5 = set(tds[-5:]); w20 = set(tds[-20:]); w3 = set(tds[-3:])
def win_flag(col, win):
    sub = pats[(pats[col] > 0) & (pats['date'].isin(win))]
    return sorted(sub['date'])

print('== NEG9 逐条(窗口口径=combo_c_shadow) ==')
print('rounded_20(圆弧底,20交易日):', win_flag('rounded_bottom', w20) or '无')
print('limit_break_5:', win_flag('limit_break', w5) or '无')
print('gap_up_run_5:', win_flag('gap_up_run', w5) or '无')
print('island_5(这是POS):', win_flag('island_bottom', w5) or '无')
print('new_high_3:', win_flag('new_high250', w3) or '无')

last = g[g['date'].astype(str) == LAST].iloc[0]
print('lim_today(9/4涨幅%):', round(float(last['change_pct']), 2))
r60 = g.tail(60)
vwap = (r60['close'] * r60['volume']).sum() / r60['volume'].sum()
print(f"vwap60_bias: close={float(last['close']):.2f} vwap60={vwap:.2f} 触发={bool(float(last['close']) > vwap * 1.10)}")
u5 = g.tail(5)
print('up4of5(近5日阳线):', int((u5['close'] > u5['open']).sum()), '/5')
con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
lb = con.execute("SELECT trade_date,net_buy FROM dragon_tiger WHERE substr(ts_code,1,6)=? AND trade_date>=?",
                 (CODE, tds[-5])).fetchall()
ind = pd.read_sql_query("SELECT code,industry FROM industry_map_ths", con)
con.close()
ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]
l1 = ind[ind['code'] == CODE]['L1']
l1 = l1.iloc[0] if len(l1) else '未知'
print('lhb_buy_5(5交易日):', lb or '无')

mm = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v5_full.pkl')
lastd = mm['date'].max()
cc = mm[mm['date'] == lastd][['code', 'close']].copy()
prevc = mm[mm['date'] < lastd].groupby('code')['close'].last()
cc['pct'] = cc['code'].map(prevc)
cc['pct'] = (cc['close'] / cc['pct'] - 1) * 100
cc = cc.merge(ind[['code', 'L1']].drop_duplicates('code'), on='code', how='left')
grp = cc[cc['L1'] == l1]
rk = grp['pct'].rank(pct=True)
r6 = float(rk[grp['code'] == CODE].iloc[0])
print(f'rel_ind_hi: 板块[{l1}]{len(grp)}只, 9/4涨幅分位={r6:.2f} 触发={r6 >= 0.90}')

print('== 池过滤 ==')
a20 = float(g['amount'].tail(20).mean())
print(f"close={float(last['close']):.2f}<=20:{float(last['close']) <= 20} "
      f"amt20(万)={a20/1e4:.0f}>=500:{a20 >= 500e4} bars={len(g)}>=120:{len(g) >= 120}")
print('== POS: chan_buy2@20260824 在win15内=True(v2真源前已验证); gap_up_hold影子9/4全池=0只 ==')
