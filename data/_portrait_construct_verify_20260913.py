# -*- coding: utf-8 -*-
"""画像模型构造空间验证(一次性研究, 只读): 检验"无alpha"结论是否构造依赖.
维度: ①持有期扫描(3/5/10/20日) ②名单宽度扫描(Top5/10/20/30/50) ③得分十分位单调性 ④反向检验(最低分位是否跑赢最高分位=信号反向).
口径: entry=T后第2根bar开盘(发布滞后), exit=+N日收盘, 真实等权. 分段: valid(20250811~20260511)/test(20260512~).
输出: data/_portrait_construct_verify_20260913.json + 控制台矩阵."""
import os, sys, json, time
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd, sqlite3
import lightgbm as lgb
from model_v1 import FEATURES, PUBLISH_GATE

T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
D_START = '20250801'
TEST_START = '20260512'
HOLDS = (3, 5, 10, 20)
TOPS = (5, 10, 20, 30, 50)

panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel['date'] = panel['date'].astype(str)
dates = sorted(d for d in panel['date'].unique() if d >= D_START)
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
log(f"面板{len(dates)}截面, qfq {len(bars)}行")

def ret_at(c, T, hold):
    """entry=T后第2根bar开盘, exit=+hold根bar收盘(entry为第2根, 故exit=第(2+hold-1)根)"""
    g = bar_by_code.get(c)
    if g is None: return np.nan
    idx = g.index[g['date'] > T]
    need = 2 + hold - 1
    if len(idx) < need: return np.nan
    e, x = g.loc[idx[1]], g.loc[idx[need - 1]]
    if not e['open'] or not x['close']: return np.nan
    return x['close'] / e['open'] - 1

bst = {t: lgb.Booster(model_file=rf'D:\zcodesj\portrait_agent\models\v1.0.0_{t}.txt') for t in ('short', 'mid')}
recs = []
for T in dates:
    cs = panel[panel['date'] == T]
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    gate = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
    # 池与十分位只需 20 日窗口(最长的)
    rm20 = {c: ret_at(c, T, 20) for c in gate['code']}
    pool20 = np.array([v for v in rm20.values() if not np.isnan(v)])
    if len(pool20) < 500:
        continue
    seg = 'test' if T >= TEST_START else 'valid'
    rec = {'date': T, 'seg': seg, 'base': {str(h): None for h in HOLDS}}
    for h in HOLDS:
        vals = [ret_at(c, T, h) for c in gate['code']]
        vals = np.array([v for v in vals if not np.isnan(v)])
        rec['base'][str(h)] = float(vals.mean() * 100) if len(vals) else None
    for tag, b in bst.items():
        gate['prob'] = b.predict(gate[FEATURES])
        order = gate.sort_values(['prob', 'turnover20'], ascending=False)
        n = len(order)
        qcut = np.argsort(np.argsort(-gate['prob'].values))  # 0=最高分
        for h in HOLDS:
            rv = {c: ret_at(c, T, h) for c in gate['code']}
            for k in TOPS:
                t = np.array([rv.get(c, np.nan) for c in order['code'].head(k)], dtype=float)
                t = t[~np.isnan(t)]
                rec[f'{tag}_top{k}_h{h}'] = float(t.mean() * 100) if len(t) >= max(3, k * 0.6) else None
            # 十分位(D1=最高分 ... D10=最低分)
            q = pd.Series(qcut, index=gate['code'].values)
            dec = {}
            for d in range(1, 11):
                members = q[(q >= (d - 1) * n / 10) & (q < d * n / 10)].index
                v = np.array([rv.get(c, np.nan) for c in members], dtype=float)
                v = v[~np.isnan(v)]
                dec[d] = float(v.mean() * 100) if len(v) >= 50 else None
            rec[f'{tag}_dec_h{h}'] = dec
    recs.append(rec)
log(f"完成 {len(recs)} 截面")

json.dump(recs, open(r'D:\zcodesj\data\_portrait_construct_verify_20260913.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=float)
df = pd.DataFrame(recs)
for seg in ('valid', 'test'):
    d = df[df['seg'] == seg]
    if not len(d): continue
    print(f"\n===== {seg} 段 n={len(d)} 截面 =====")
    base_row = ' '.join(f"h{h}={np.nanmean([x[str(h)] if x[str(h)] is not None else np.nan for x in d['base']]):.2f}" for h in HOLDS)
    print(f"池基准均值%: {base_row}")
    for tag in ('short', 'mid'):
        print(f"-- {tag} TopN × 持有期 超额(pp, 中位) --")
        hdr = '        ' + ''.join(f"h{h:<7d}" for h in HOLDS)
        print(hdr)
        for k in TOPS:
            row = []
            for h in HOLDS:
                col = d[f'{tag}_top{k}_h{h}'].values
                bcol = np.array([x[str(h)] if x[str(h)] is not None else np.nan for x in d['base']], dtype=float)
                ex = col - bcol
                ex = ex[~np.isnan(ex)]
                row.append(f"{np.median(ex):+.2f}  " if len(ex) else "  -    ")
            print(f"Top{k:<4d} " + ''.join(f"{x:<9s}" for x in row))
        # 十分位(20日持有)
        print(f"-- {tag} 十分位超额(pp, h20, 中位; D1=最高分) --")
        ds = []
        b20 = np.array([x['20'] if x['20'] is not None else np.nan for x in d['base']], dtype=float)
        for dd in range(1, 11):
            col = np.array([x[f'{tag}_dec_h20'][str(dd)] if x[f'{tag}_dec_h20'][str(dd)] is not None else np.nan for x in d], dtype=float)
            ex = col - b20
            ex = ex[~np.isnan(ex)]
            ds.append(f"D{dd}={np.median(ex):+.2f}" if len(ex) else f"D{dd}= -")
        print('  ' + ' '.join(ds))
print("\n落盘 data/_portrait_construct_verify_20260913.json")
