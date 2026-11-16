# -*- coding: utf-8 -*-
"""影子新判据标定(一次性, 只读): 对20260801以来全部截面按'可实现口径'计算 Top20 与池的收益分布,
为 shadow_gate 新判据门槛提供机械推导依据. 口径: entry=T后第2根bar开盘(名单最早T+1 15:16发布,T+2开盘可执行), exit=T后第6根bar收盘(5日持有), 真实等权(不clip).
输出: data/_shadow_calib_realizable_20260913.json + 控制台分布摘要."""
import os, sys, json, time
sys.path.insert(0, r'D:\zcodesj')
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd, sqlite3
import lightgbm as lgb
from scipy.stats import spearmanr
from model_v1 import FEATURES

T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
D0, D1 = '20260730', '20260911'

panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel['date'] = panel['date'].astype(str)
dates = sorted(d for d in panel['date'].unique() if d >= '20260801')
log(f"面板加载完成, 标定截面 {len(dates)} 个: {dates[0]}..{dates[-1]}")

codes = sorted(panel[panel['date'].isin(dates)]['code'].unique())
con = sqlite3.connect(QFQ, uri=True, timeout=60)
chunks = []
for i in range(0, len(codes), 900):
    chunk = codes[i:i+900]
    ph = ','.join('?' * len(chunk))
    chunks.append(pd.read_sql_query(
        f"SELECT code,date,open,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='{D0}' AND date<='{D1}' ORDER BY code,date",
        con, params=chunk))
con.close()
bars = pd.concat(chunks, ignore_index=True)
bars['date'] = bars['date'].astype(str)
bar_by_code = {c: g.reset_index(drop=True) for c, g in bars.groupby('code')}
all_dates = sorted(bars['date'].unique())
log(f"qfq行情加载 {len(bars)} 行, {len(codes)}只, 交易日{len(all_dates)}个")

def realizable(c, T):
    """entry=T后第2根bar open, exit=T后第6根bar close → 收益; 窗口不足/停牌→NaN"""
    g = bar_by_code.get(c)
    if g is None: return np.nan
    idx = g.index[g['date'] > T]
    if len(idx) < 6: return np.nan
    e, x = g.loc[idx[1]], g.loc[idx[5]]
    if not e['open'] or not x['close']: return np.nan
    return x['close'] / e['open'] - 1

bst_short = lgb.Booster(model_file=r'D:\zcodesj\portrait_agent\models\v1.0.0_short.txt')
bst_mid = lgb.Booster(model_file=r'D:\zcodesj\portrait_agent\models\v1.0.0_mid.txt')
rows = []
for T in dates:
    cs = panel[panel['date'] == T].copy()
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    # 发布闸(与改造后 infer 同构): 剔科创板/剔close<4元
    gate = cs[~cs['code'].str.startswith(('688', '689')) & (cs['close'] >= 4.0)].copy()
    pool_ret_map = {c: realizable(c, T) for c in cs['code']}
    pool = np.array([v for v in pool_ret_map.values() if not np.isnan(v)])
    for tag, bst in (('short', bst_short), ('mid', bst_mid)):
        gate['prob'] = bst.predict(gate[FEATURES])
        top = gate.sort_values(['prob', 'turnover20'], ascending=False).head(20)
        tret = np.array([pool_ret_map.get(c, np.nan) for c in top['code']], dtype=float)
        tret_v = tret[~np.isnan(tret)]
        if len(pool) < 500 or len(tret_v) < 15:
            rows.append({'date': T, 'label': tag, 'n_pool': len(pool), 'note': '窗口不足'}); continue
        pairs = [(p, pool_ret_map[c]) for c, p in zip(gate['code'], gate['prob'])
                 if not np.isnan(pool_ret_map.get(c, np.nan))]
        ic = spearmanr([x[0] for x in pairs], [x[1] for x in pairs]).statistic
        rows.append({'date': T, 'label': tag, 'n_pool': int(len(pool)),
                     'top_hit': round(float((tret_v > 0).mean() * 100), 1),
                     'base_hit': round(float((pool > 0).mean() * 100), 1),
                     'top_port': round(float(tret_v.mean()) * 100, 2),
                     'base_port': round(float(pool.mean()) * 100, 2),
                     'excess': round(float(tret_v.mean() - pool.mean()) * 100, 2),
                     'ic': round(float(ic), 4),
                     'n_top_realized': len(tret_v)})
    log(f"{T} 完成")

df = pd.DataFrame([r for r in rows if 'excess' in r])
full = pd.DataFrame(rows)
json.dump(rows, open(r'D:\zcodesj\data\_shadow_calib_realizable_20260913.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=float)
for tag in ('short', 'mid'):
    g = df[df['label'] == tag]
    print(f"\n=== 可实现口径分布({tag}, 5日完整窗口截面 n={len(g)}) ===")
    print(f"top-base超额  中位={g['excess'].median():.2f}pp  均值={g['excess'].mean():.2f}pp  正占比={(g['excess']>0).mean()*100:.0f}%")
    print(f"IC            中位={g['ic'].median():.4f}  正占比={(g['ic']>0).mean()*100:.0f}%  IC≥0.02占比={(g['ic']>=0.02).mean()*100:.0f}%")
    print(f"top_hit       中位={g['top_hit'].median():.1f}%  base_hit中位={g['base_hit'].median():.1f}%  hit-base中位={(g['top_hit']-g['base_hit']).median():.1f}pp")

# === 市场状态分层(条件化假设的量化素材): short/mid 超额 vs 池基准状态 ===
print("\n=== 市场状态分层(池base_hit 三档 × short/mid 超额) ===")
piv = df.pivot_table(index='date', columns='label', values=['excess', 'ic', 'base_hit', 'base_port', 'top_port'])
piv.columns = [f'{a}_{b}' for a, b in piv.columns]
piv['state'] = pd.cut(piv['base_hit_short'], bins=[-1, 40, 60, 101], labels=['退潮(池hit<40)', '中性(40-60)', '顺风(>60)'])
print(piv.groupby('state', observed=True)[['excess_short', 'excess_mid', 'ic_short', 'ic_mid', 'base_port_short']].agg(['mean', 'count']).round(2).to_string())
piv.reset_index().to_csv(r'D:\zcodesj\data\_shadow_calib_state_strata_20260913.csv', index=False, encoding='utf-8-sig')
print("\n分层明细已落盘 data/_shadow_calib_state_strata_20260913.csv")
print("\n逐截面(short+mid):")
print(df.pivot_table(index='date', columns='label', values=['excess', 'ic', 'base_hit']).round(2).to_string())
