# -*- coding: utf-8 -*-
"""monthly_selection_pnl.py — 月度选股盈亏归因（每月1日 schtasks，P0-3 固化）。
A组=当月买入仍持有：浮动盈亏(WeStockClient 现价×当月买入股数 − 当月买入金额)
B组=当月卖出：已实现盈亏(卖出金额 − 卖出股数×全史加权成本)
分组=来源；现价/持仓=westock 真值（trade_executor 同源）。
用法: python monthly_selection_pnl.py [--month YYYYMM](默认当月) [--outdir reports]"""
import os, sys, json, sqlite3, argparse, subprocess, datetime
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"D:/workbuddyspace")
sys.path.insert(0, r"D:/zcodesj")

ap = argparse.ArgumentParser()
ap.add_argument('--month', default=datetime.date.today().strftime('%Y%m'))
ap.add_argument('--outdir', default=r'D:/zcodesj/reports')
args = ap.parse_args()
MONTH = args.month
print(f'[monthly_pnl] month={MONTH}')
try:
    from westock_client import WeStockClient
    ws = WeStockClient()
    pos_resp = ws.positions()
    positions = (pos_resp or {}).get('data', {}).get('positions', [])
except Exception as e:
    print(f'[monthly_pnl] FAIL-CLOSED westock: {e}')
    sys.exit(2)
PX, HELD = {}, {}
for x in positions:
    c = str(x.get('code',''))[-6:]
    try: q = int(x.get('quantity', 0))
    except (TypeError, ValueError): continue
    if q <= 0: continue
    HELD[c] = q
    try: PX[c] = float(x.get('currentPrice'))
    except (TypeError, ValueError): pass
print(f'[monthly_pnl] positions={len(HELD)}')
con = sqlite3.connect(r'D:/zcodesj/attribution/attribution.db', timeout=10)
tr = pd.read_sql("SELECT trade_date, code, name, action, price, volume, subsystems, signal_type, notes "
                 f"FROM trade_records WHERE trade_date>='{MONTH}' AND action IN ('BUY','SELL')", con)
allbuy = pd.read_sql("SELECT code, price, volume FROM trade_records WHERE action='BUY'", con)
con.close()
tr['code6'] = tr['code'].astype(str).str[-6:]
allbuy['code6'] = allbuy['code'].astype(str).str[-6:]

def src_cat(row):
    s = (str(row['subsystems']) + str(row['signal_type']) + str(row['notes'])).lower()
    if 'app' in s or 'import' in s or '补登' in s: return 'App端补登'
    if 'bp_ep' in s or 'master' in s or 'whitelist' in s or 'ml' in s: return '白名单系统'
    return '主人决策/授权链'

tr['src'] = tr.apply(src_cat, axis=1)

# A组: 当月买入仍持有
# 11/3修复(11/1 23:10 MONTHLY_PNL_D1 rc=1实锤): 月度任务月初空月无交易→buys为空表,
# pandas空表apply(axis=1)返回类型不稳定(多列DF赋单列崩"Cannot set a DataFrame")。
# 根治=向量化np.minimum+map: 空表天然安全, 免逐行apply; 语义不变(持有量=min(买入量,当前持仓))
buys = tr[tr['action'] == 'BUY'].copy()
held_map = buys['code6'].map(HELD).fillna(0).astype('int64')
buys['held_in_month'] = np.minimum(buys['volume'].astype('int64'), held_map)
A = buys[(held_map > 0) & (buys['held_in_month'] > 0)].copy()
A['px'] = A['code6'].map(PX)
A = A.dropna(subset=['px'])
A['pnl'] = (A['px'] - A['price']) * A['held_in_month']
A['amt'] = A['price'] * A['held_in_month']

# B组: 9月卖出, 加权成本(全史)
wavg = {}
for c, g in allbuy.groupby('code6'):
    wavg[c] = (g['price'] * g['volume']).sum() / g['volume'].sum() if g['volume'].sum() > 0 else None
sells = tr[tr['action'] == 'SELL'].copy()
sells['cost'] = sells['code6'].map(wavg)
sells['pnl'] = (sells['price'] - sells['cost']) * sells['volume'].astype(float)
sells['amt'] = sells['price'] * sells['volume'].astype(float)

def agg(df, label, by=None):
    if len(df) == 0:
        print(f'{label}: 空'); return
    tot_p = df['pnl'].sum(); tot_a = df['amt'].sum()
    win = (df['pnl'] > 0).sum(); lose = (df['pnl'] < 0).sum()
    print(f"{label}: n={len(df)} 涨{win}/跌{lose} 金额{tot_a:,.0f} 盈亏{tot_p:+,.0f} ({tot_p/tot_a*100:+.2f}%)")
    if by:
        g = df.groupby(by)['pnl'].agg(['count', 'sum'])
        for k, r in g.iterrows():
            print(f'   - {k}: n={int(r["count"])} pnl={r["sum"]:+,.0f}')

print('════ A组: 本月买入·仍持有的浮动盈亏 ════')
agg(A, '合计', by='src')
print('逐票:')
for r in A.itertuples():
    print(f"  {r.trade_date} {r.code6} {r.name} {int(r.held_in_month)}股 @{r.price:.3f}→{r.px:.2f} {r.pnl:+,.0f} [{r.src}]")
print()
print('════ B组: 本月卖出的已实现盈亏 ════')
agg(sells, '合计', by='src')
print('逐笔:')
for r in sells.itertuples():
    print(f"  {r.trade_date} {r.code6} {r.name} {int(r.volume)}股 卖@{r.price:.3f} 成本@{r.cost if r.cost and r.cost==r.cost else 0:.3f} {r.pnl:+,.0f} [{r.src}]")
print()
print('════ 总账 ════')
tot = A['pnl'].sum() + sells['pnl'].sum()
print(f'A组浮动 {A["pnl"].sum():+,.0f} + B组已实现 {sells["pnl"].sum():+,.0f} = {tot:+,.0f}')
outp = os.path.join(args.outdir, f'monthly_selection_pnl_{MONTH}.json')
json.dump({'month': MONTH, 'A_float': float(A['pnl'].sum()), 'B_realized': float(sells['pnl'].sum()),
           'total': float(tot), 'A_n': int(len(A)), 'B_n': int(len(sells))},
          open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('saved', outp)
