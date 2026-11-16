# -*- coding: utf-8 -*-
"""ml_signal 可实现口径独立验证(只读): 分层收益 + RankIC, 直接对标画像线教训(机会面IC好≠可实现好).
口径: ml_prob 分十分位 → qfq 5日前向收益(收盘→收盘, 简单可实现口径), 近40分区.
对照: D1(最高分)-D10(最低分)收益差 = 分数区分度在收益上的映射; IC=spearman(prob, fwd5)."""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
from scipy.stats import spearmanr

DL = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro'
QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'

con = sqlite3.connect(DL, uri=True, timeout=60)
ml = pd.read_sql_query("SELECT code, date, ml_prob FROM stock_ml_scores_v22 "
                       "WHERE date >= '20260601' ORDER BY date", con)
con.close()
ml['date'] = ml['date'].astype(str)
dates = sorted(ml['date'].unique())[-40:]
ml = ml[ml['date'].isin(dates)]
codes = sorted(ml['code'].unique())
print(f"ml 近{len(dates)}分区 {dates[0]}~{dates[-1]}, {len(codes)}只")

con = sqlite3.connect(QFQ, uri=True, timeout=120)
chunks = []
for i in range(0, len(codes), 800):
    ch = codes[i:i+800]
    ph = ','.join('?' * len(ch))
    chunks.append(pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20260601' ORDER BY code,date",
        con, params=ch))
con.close()
px = pd.concat(chunks, ignore_index=True)
px['date'] = px['date'].astype(str)
px_map = {c: g.set_index('date')['close'] for c, g in px.groupby('code')}
all_d = sorted(px['date'].unique())

def fwd5(c, T):
    s = px_map.get(c)
    if s is None or T not in s.index:
        return np.nan
    i = all_d.index(T) if T in all_d else -1
    if i < 0 or i + 5 >= len(all_d):
        return np.nan
    p0, p1 = s.get(T), s.get(all_d[i + 5])
    if not p0 or not p1:
        return np.nan
    return p1 / p0 - 1

recs = []
for T, g in ml.groupby('date'):
    g = g.copy()
    g['fwd5'] = [fwd5(c, T) for c in g['code']]
    g = g[g['fwd5'].notna()]
    if len(g) < 500:
        continue
    g = g.sort_values('ml_prob', ascending=False).reset_index(drop=True)
    n = len(g)
    dec = {}
    for d in range(1, 11):
        seg = g.iloc[int((d-1)*n/10):int(d*n/10)]['fwd5']
        dec[d] = float(seg.mean() * 100)
    ic = spearmanr(g['ml_prob'], g['fwd5']).statistic
    recs.append({'date': T, 'base': float(g['fwd5'].mean()*100), 'ic': float(ic), **{f'd{d}': v for d, v in dec.items()}})

r = pd.DataFrame(recs)
print(f"\n=== ml_signal 可实现口径验证(收盘→5日后收盘, n={len(r)}分区) ===")
print(f"池均值(5日) 中位={r['base'].median():+.2f}%")
print(f"RankIC 中位={r['ic'].median():+.4f}  正占比={(r['ic']>0).mean()*100:.0f}%")
print("\n分层(超额 vs 池, pp):")
for d in range(1, 11):
    ex = r[f'd{d}'] - r['base']
    print(f"  D{d:<2d}: {r[f'd{d}'].median():+6.2f}% (超额{ex.median():+5.2f}pp)")
print(f"\nD1-D10 收益差 中位={r['d1'].median()-r['d10'].median():+.2f}pp")
print(f"D1超额-D10超额 中位={(r['d1']-r['base']).median()-(r['d10']-r['base']).median():+.2f}pp")
r.to_csv(r'D:\zcodesj\data\_ml_signal_realizable_check_20260913.csv', index=False, encoding='utf-8-sig')
print('\n落盘 data/_ml_signal_realizable_check_20260913.csv')
