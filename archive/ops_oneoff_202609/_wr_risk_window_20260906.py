# -*- coding: utf-8 -*-
"""10只合格名单·窗口口径排雷复算(修正panel点态口径低估)."""
import sys, sqlite3
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
from morph_profile import compute_patterns

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
CODES = ['001390', '002152', '002478', '002481', '002777', '300296', '300773', '301370', '600626', '600987']
LAST = '20260904'

con0 = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
tds = [r[0] for r in con0.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date")]
w3, w5, w15, w20 = set(tds[-3:]), set(tds[-5:]), set(tds[-15:]), set(tds[-20:])
chan = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\chan_signals_v2.pkl')
chan['date'] = chan['date'].astype(str)
lhb = pd.read_sql_query("SELECT trade_date,ts_code,net_buy FROM dragon_tiger", sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True))
lhb['net_buy'] = pd.to_numeric(lhb['net_buy'], errors='coerce')
lhb['code'] = lhb['ts_code'].str[:6]
lb5 = set(lhb[(lhb['net_buy'] > 0) & (lhb['trade_date'].astype(str).isin(w5))]['code'])
print(f'窗口: w20起点{tds[-20]} 截面{LAST}')
print(f'{"code":8s}{"圆弧底20":>10s}{"涨停破板5":>10s}{"跳空连涨5":>10s}{"新高3":>8s}{"三买10":>8s}{"龙虎榜5":>8s}{"窗口排雷合计":>10s}')
for code in CODES:
    g = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
        "WHERE code=? AND date>='20130101' ORDER BY date", con0, params=[code])
    g = g.reset_index(drop=True)
    p = compute_patterns(g)
    p['date'] = g['date'].values
    p['date'] = p['date'].astype(str)
    def wf(col, win):
        return int(((p[col] > 0) & (p['date'].isin(win))).sum())
    r20 = wf('rounded_bottom', w20); l5 = wf('limit_break', w5)
    gp5 = wf('gap_up_run', w5); nh3 = wf('new_high250', w3)
    cb3 = int(((chan['code'] == code) & (chan['type'] == 'buy3') & (chan['date'].isin(w15))).sum())
    lb = int(code in lb5)
    tot = r20 + (1 if l5 else 0) + (1 if gp5 else 0) + (1 if nh3 else 0) + (1 if cb3 else 0) + (1 if lb else 0)
    print(f'{code:8s}{r20:>10d}{l5:>10d}{gp5:>10d}{nh3:>8d}{cb3:>8d}{lb:>8d}{tot:>10d}')
    det = {'rounded20': win_flag_days(p, 'rounded_bottom', w20)} if False else None
con0.close()

def win_flag_days(p, col, win):
    return sorted(p[(p[col] > 0) & (p['date'].isin(win))]['date'])
