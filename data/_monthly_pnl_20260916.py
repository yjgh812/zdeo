# -*- coding: utf-8 -*-
"""_monthly_pnl_20260916.py — 9月月度选股盈亏归因（只读一次性）。
A组=9月买入仍持有：浮动盈亏(westock现价×9月买入股数 − 9月买入金额)
B组=9月卖出：已实现盈亏(卖出金额 − 卖出股数×全史加权成本)
分组=来源(manual授权链/bp_ep白名单/App补登)。现价源=westock 20260916晚真值快照。"""
import sys, json, sqlite3
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# westock 20260916 晚真值快照(code6 -> currentPrice)
PX = {'601328':7.58,'002379':17.57,'003006':19.42,'600719':7.64,'601919':16.38,'601975':4.44,
'000983':6.60,'600470':5.72,'300817':16.06,'603585':10.40,'603230':11.14,'002688':5.13,
'600694':14.31,'600717':4.29,'600035':3.65,'600018':5.46,'600020':3.84,'002174':14.50,
'601999':6.90,'600704':4.91,'600827':7.67,'002011':13.66,'300296':4.88,'301370':8.33,
'002481':4.21,'002152':9.22,'002777':12.50,'002478':6.49,'600987':6.41,'600626':3.57,
'001390':16.49,'603209':14.54,'601022':8.15,'000937':4.45,'600348':9.18,'601866':2.45,
'002942':16.24,'002966':9.05,'601077':6.58,'603637':14.22,'300214':10.20,'300657':36.99}
# 当前持有股数(同源)
HELD = {'601328':2600,'002379':500,'003006':2100,'600719':1500,'601919':1500,'601975':5100,
'000983':1700,'600470':2400,'300817':2400,'603585':1800,'603230':2100,'002688':5900,
'600694':300,'600717':1100,'600035':1300,'600018':1800,'600020':1500,'002174':100,
'601999':500,'600704':1000,'600827':200,'002011':800,'300296':1400,'301370':1400,
'002481':1700,'002152':700,'002777':500,'002478':1000,'600987':1100,'600626':2100,
'001390':400,'603209':1600,'601022':2800,'000937':5300,'600348':2600,'601866':9900,
'002942':100,'002966':2100,'601077':2900,'603637':1400,'300214':2000,'300657':5600}

con = sqlite3.connect(r'D:/zcodesj/attribution/attribution.db', timeout=10)
tr = pd.read_sql("SELECT trade_date, code, name, action, price, volume, subsystems, signal_type, notes "
                 "FROM trade_records WHERE trade_date>='20260901' AND action IN ('BUY','SELL')", con)
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

# A组: 9月买入仍持有
buys = tr[tr['action'] == 'BUY'].copy()
buys['held_in_month'] = buys.apply(lambda r: min(int(r['volume']), HELD.get(r['code6'], 0)), axis=1)
A = buys[buys.apply(lambda r: HELD.get(r['code6'], 0) > 0 and r['held_in_month'] > 0, axis=1)].copy()
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
