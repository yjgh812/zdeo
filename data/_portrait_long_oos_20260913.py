# -*- coding: utf-8 -*-
"""mid/short 长窗口样本外回溯(一次性研究, 只读): panel全量20250811~20260903共~267截面可实现口径评估.
证据分级: 主证据=严格测试段(20260512起, 训练7:2:1的末段10%, 完全样本外); 次级=验证段(20250811起, early stopping用过未参与梯度).
口径与shadow_gate v2完全一致: 发布闸(剔688/689+close>=4) → Top20(prob,turnover20降序) → 可实现收益(entry=T后第2根bar开盘, exit=+5日收盘, 真实等权).
输出: data/_portrait_long_oos_20260913.{json} + 控制台分段摘要."""
import os, sys, json, time
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd, sqlite3
import lightgbm as lgb
from scipy.stats import spearmanr
from model_v1 import FEATURES, PUBLISH_GATE

T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
D_START = '20250801'
TEST_START = '20260512'   # 严格样本外起点(当前panel 7:2:1的i9)

panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel['date'] = panel['date'].astype(str)
dates = sorted(d for d in panel['date'].unique() if d >= D_START)
log(f"面板加载完成, 回溯截面 {len(dates)} 个: {dates[0]}..{dates[-1]}")

codes = sorted(panel[panel['date'].isin(dates)]['code'].unique())
con = sqlite3.connect(QFQ, uri=True, timeout=120)
chunks = []
for i in range(0, len(codes), 800):
    chunk = codes[i:i+800]
    ph = ','.join('?' * len(chunk))
    chunks.append(pd.read_sql_query(
        f"SELECT code,date,open,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='{D_START}' ORDER BY code,date",
        con, params=chunk))
con.close()
bars = pd.concat(chunks, ignore_index=True)
bars['date'] = bars['date'].astype(str)
bar_by_code = {c: g.reset_index(drop=True) for c, g in bars.groupby('code')}
log(f"qfq行情 {len(bars)}行 {len(codes)}只")

def realizable(c, T):
    g = bar_by_code.get(c)
    if g is None: return np.nan
    idx = g.index[g['date'] > T]
    if len(idx) < 6: return np.nan
    e, x = g.loc[idx[1]], g.loc[idx[5]]
    if not e['open'] or not x['close']: return np.nan
    return x['close'] / e['open'] - 1

bst = {t: lgb.Booster(model_file=rf'D:\zcodesj\portrait_agent\models\v1.0.0_{t}.txt') for t in ('short', 'mid')}
rows = []
for T in dates:
    cs = panel[panel['date'] == T]
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    gate = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
    pool_map = {c: realizable(c, T) for c in gate['code']}
    pool = np.array([v for v in pool_map.values() if not np.isnan(v)])
    if len(pool) < 500:
        continue
    for tag, b in bst.items():
        gate['prob'] = b.predict(gate[FEATURES])
        top = gate.sort_values(['prob', 'turnover20'], ascending=False).head(20)
        t = np.array([pool_map.get(c, np.nan) for c in top['code']], dtype=float)
        t = t[~np.isnan(t)]
        if len(t) < 15: continue
        pairs = [(p, pool_map[c]) for c, p in zip(gate['code'], gate['prob']) if not np.isnan(pool_map[c])]
        ic = float(spearmanr([x[0] for x in pairs], [x[1] for x in pairs]).statistic)
        rows.append({'date': T, 'tag': tag, 'seg': 'test' if T >= TEST_START else 'valid',
                     'excess': round((t.mean() - pool.mean()) * 100, 2),
                     'top_port': round(t.mean() * 100, 2), 'base_port': round(pool.mean() * 100, 2),
                     'ic': round(ic, 4), 'hit': round(float((t > 0).mean() * 100), 1),
                     'base_hit': round(float((pool > 0).mean() * 100), 1)})
log(f"完成 {len(rows)} 条双线记录")

df = pd.DataFrame(rows)
json.dump(rows, open(r'D:\zcodesj\data\_portrait_long_oos_20260913.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=float)
for seg in ('valid', 'test'):
    for tag in ('short', 'mid'):
        g = df[(df['seg'] == seg) & (df['tag'] == tag)]
        if not len(g): continue
        nav = np.cumprod([1 + x / 100 for x in g['excess']])  # 累计超额净值(逐截面复利近似)
        print(f"[{seg}]{tag} n={len(g)}: 超额中位={g['excess'].median():.2f}pp 均值={g['excess'].mean():.2f}pp "
              f"正占比={(g['excess']>0).mean()*100:.0f}% IC中位={g['ic'].median():.4f} "
              f"hit-base中位={(g['hit']-g['base_hit']).median():.1f}pp 累计超额净值={nav[-1]:.3f}(≈{(nav[-1]-1)*100:.1f}%)")
# 逐月
df['ym'] = df['date'].str[:6]
piv = df.pivot_table(index='ym', columns=['tag'], values='excess', aggfunc='median')
print("\n逐月超额中位(pp):")
print(piv.round(2).to_string())
print("\n落盘 data/_portrait_long_oos_20260913.json")
