# -*- coding: utf-8 -*-
"""Top500名单内部分档结构(只读): 1-100/101-200/.../401-500 五档 × 双模型, 近30可评截面.
判据价值: 若分档收益单调(前段vs后段), 名单位置携带信息; 等权则位置无信息(纯随机).
口径同shadow_gate v2: entry=T后第2根bar开盘, +5日收盘, 真实等权."""
import os, sys, time, sqlite3
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import lightgbm as lgb
from model_v1 import FEATURES, PUBLISH_GATE, PORTFOLIO_TOPN

T0 = time.time()
QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel['date'] = panel['date'].astype(str)
all_dates = sorted(panel['date'].unique())
# 近30个可评截面(窗口齐) — 与shadow_gate同口径
con = sqlite3.connect(QFQ, uri=True, timeout=120)
qdates = [str(r[0]) for r in con.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
import bisect
avail = [d for d in all_dates if len(qdates) - bisect.bisect_right(qdates, d) >= 6][-30:]
print(f'可评截面 {avail[0]}~{avail[-1]} (n={len(avail)})')

codes = sorted(panel[panel['date'].isin(avail)]['code'].unique())
chunks = []
for i in range(0, len(codes), 800):
    chunk = codes[i:i+800]
    ph = ','.join('?' * len(chunk))
    chunks.append(pd.read_sql_query(
        f"SELECT code,date,open,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='{avail[0]}' ORDER BY code,date",
        con, params=chunk))
con.close()
bars = pd.concat(chunks, ignore_index=True); bars['date'] = bars['date'].astype(str)
bar_by_code = {c: g.reset_index(drop=True) for c, g in bars.groupby('code')}

def ret5(c, T):
    g = bar_by_code.get(c)
    if g is None: return np.nan
    idx = g.index[g['date'] > T]
    if len(idx) < 6: return np.nan
    e, x = g.loc[idx[1]], g.loc[idx[5]]
    if not e['open'] or not x['close']: return np.nan
    return x['close'] / e['open'] - 1

bst = {t: lgb.Booster(model_file=rf'D:\zcodesj\portrait_agent\models\v1.0.0_{t}.txt') for t in ('short', 'mid')}
BANDS = [(0, 100), (100, 200), (200, 300), (300, 400), (400, 500)]
rows = []
for T in avail:
    cs = panel[panel['date'] == T]
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    gate = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
    rm = {c: ret5(c, T) for c in gate['code']}
    base = np.array([v for v in rm.values() if not np.isnan(v)])
    if len(base) < 500: continue
    rec = {'date': T, 'base': float(base.mean() * 100)}
    for tag, b in bst.items():
        gate['prob'] = b.predict(gate[FEATURES])
        s = gate.sort_values(['prob', 'turnover20'], ascending=False).reset_index(drop=True)
        for lo, hi in BANDS:
            codes_b = s['code'].iloc[lo:hi]
            v = np.array([rm.get(c, np.nan) for c in codes_b], dtype=float)
            v = v[~np.isnan(v)]
            rec[f'{tag}_{lo+1}_{hi}'] = float(v.mean() * 100) if len(v) >= (hi - lo) * 0.6 else None
    rows.append(rec)
df = pd.DataFrame(rows)
print(f"\n=== Top500 内部分档 5日收益(近{len(df)}截面均值, 超额=档-池) ===")
print(f"池基准: {df['base'].mean():+.3f}%")
for tag in ('short', 'mid'):
    print(f"-- {tag} --")
    for lo, hi in BANDS:
        col = df[f'{tag}_{lo+1}_{hi}'].dropna()
        ex = col - df.loc[col.index, 'base']
        print(f"  {lo+1:>3d}-{hi:<3d}: 收益={col.mean():+.3f}%  超额={ex.mean():+.3f}pp (正占比{(ex>0).mean()*100:.0f}%)")
    # 单调性: 头部档 vs 尾部档
    head = (df[f'{tag}_1_100'] - df['base']).dropna()
    tail = (df[f'{tag}_401_500'] - df['base']).dropna()
    print(f"  头尾部差(尾-头): {(tail.mean()-head.mean()):+.3f}pp {'→ 越靠后越好(位置有信息)' if tail.mean()>head.mean() else '→ 无单调'}")
df.to_csv(r'D:\zcodesj\data\_top500_band_structure_20260913.csv', index=False, encoding='utf-8-sig')
print('\n落盘 data/_top500_band_structure_20260913.csv')
