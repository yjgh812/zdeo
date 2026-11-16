# -*- coding: utf-8 -*-
"""_breakeven_sim_20260917.py — 回本止盈规则模拟（浮盈曾≥3%后收盘回落≤成本即退出）vs 实际。只读模拟。"""
import sys, json, sqlite3
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
BASE = r'D:/zcodesj/attribution/selection_deep_analysis_20260916.json'
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
HELD = {'601328':2600,'002379':500,'003006':2100,'600719':1500,'601919':1500,'601975':5100,
'000983':1700,'600470':2400,'300817':2400,'603585':1800,'603230':2100,'002688':5900,
'600694':300,'600717':1100,'600035':1300,'600018':1800,'600020':1500,'002174':100,
'601999':500,'600704':1000,'600827':200,'002011':800,'300296':1400,'301370':1400,
'002481':1700,'002152':700,'002777':500,'002478':1000,'600987':1100,'600626':2100,
'001390':400,'603209':1600,'601022':2800,'000937':5300,'600348':2600,'601866':9900,
'002942':100,'002966':2100,'601077':2900,'603637':1400,'300214':2000,'300657':5600}

trades = json.load(open(BASE, encoding='utf-8'))
df = pd.DataFrame(trades)
codes = sorted(df['code'].unique())
con = sqlite3.connect(QFQ, timeout=60)
ph = ','.join('?'*len(codes))
px = pd.read_sql(f"SELECT substr(code,1,6) code6, date, close FROM stock_daily_meta_qfq "
                 f"WHERE date>='20260720' AND substr(code,1,6) IN ({ph}) ORDER BY code6, date", con, params=codes)
con.close()
px['date'] = px['date'].astype(str).str.replace('-','')
gmap = {c: g.reset_index(drop=True) for c, g in px.groupby('code6')}

ARM = 1.03   # 浮盈3%武装
act_total = sim_total = 0.0
sim_better = sim_worse = n = triggered = 0
per_case = []
for r in df.itertuples(index=False):
    g = gmap.get(r.code)
    if g is None: continue
    hit = g[g['date'] == r.date]
    if not len(hit): continue
    i = hit.index[0]; cost = float(r.price)
    seg = g.iloc[i+1:]
    if len(seg) < 2: continue
    act_pnl = float(r.pnl)
    held = min(float(r.qty), HELD.get(r.code, 0))
    armed = False; sim_exit = None
    for _, row in seg.iterrows():
        if not armed and row['close'] >= cost*ARM: armed = True
        if armed and row['close'] <= cost*1.001:
            sim_exit = float(row['close']); break
    if sim_exit is not None and held > 0:
        sim_pnl = held * (sim_exit*(1-0.002) - cost)
        triggered += 1
    else:
        sim_pnl = act_pnl
    act_total += act_pnl; sim_total += sim_pnl; n += 1
    if sim_pnl > act_pnl + 1: sim_better += 1
    elif sim_pnl < act_pnl - 1: sim_worse += 1
print(f'n={n} 触发回本止盈={triggered}')
print(f'实际总pnl  = {act_total:+,.0f}')
print(f'模拟总pnl  = {sim_total:+,.0f}  (改善 {sim_total-act_total:+,.0f})')
print(f'改善笔数={sim_better} 恶化笔数={sim_worse}')
