# -*- coding: utf-8 -*-
"""y_realizable 变体训练+强化判据评估(prereg: PREREG_PORTRAIT_REALIZABLE_20260913.md).
训练: panel截到20260904(复现9/5训练边界→切分点与v1.0.0一致) + merge realizable标签, target=y_realizable, prod_tag=None(绝不触生产).
判据(冻结): ①test段(20260430起) RankIC>=0.03 且 Top20-base5>=0  ②分段稳定性: 按半年分段超额中位>=0占比>=2/3  ③构造稳健性: Top10/h10 与 Top50/h5 超额中位>=0.
对照臂: v1.0.0_short 同口径同段复算."""
import os, sys, json, time
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd, sqlite3
import lightgbm as lgb
from scipy.stats import spearmanr
from model_v1 import FEATURES, PUBLISH_GATE, fit_and_eval

T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

TEST_START = '20260430'   # v1.0.0 训练切分 i9(20260430, 883截面口径复核实锚)
THRESH = 0.03

# === 1. 训练 ===
panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel['date'] = panel['date'].astype(str)
panel = panel[panel['date'] <= '20260904'].copy()   # 复现9/5训练边界
lab = pd.read_parquet(r'D:\zcodesj\data\_realizable_labels_20260913.parquet')
lab['date'] = lab['date'].astype(str)
panel = panel.merge(lab, on=['code', 'date'], how='left')
panel['y_realizable'] = np.where(panel['ret_realizable'].isna(), np.nan,
                                 (panel['ret_realizable'] >= THRESH).astype(float))
n_lab = panel['y_realizable'].notna().sum()
log(f'训练集: panel截0904行={len(panel)}, 标签非空={n_lab}, 正样本率={np.nanmean(panel["y_realizable"])*100:.1f}%')

res, m, te = fit_and_eval(panel, target='y_realizable', prod_tag=None, fwd_col_override='ret_realizable')
print(json.dumps({k: v for k, v in res.items() if k != 'feature_importance'}, ensure_ascii=False, indent=1, default=float))
print('top10特征:', list(res['feature_importance'].items())[:5])

# === 2. 评估(构造验证同口径, test段) ===
QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
panel_full = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel_full['date'] = panel_full['date'].astype(str)
dates = sorted(d for d in panel_full['date'].unique() if d >= '20250801' and d >= TEST_START)
# h20 完整的截面(与construct_verify同口径)
con = sqlite3.connect(QFQ, uri=True, timeout=120)
codes = sorted(panel_full[panel_full['date'].isin(dates)]['code'].unique())
chunks = []
for i in range(0, len(codes), 800):
    chunk = codes[i:i+800]
    ph = ','.join('?' * len(chunk))
    chunks.append(pd.read_sql_query(
        f"SELECT code,date,open,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20250801' ORDER BY code,date",
        con, params=chunk))
con.close()
bars = pd.concat(chunks, ignore_index=True)
bars['date'] = bars['date'].astype(str)
bar_by_code = {c: d.reset_index(drop=True) for c, d in bars.groupby('code')}
log(f'评估bars {len(bars)}行')

def ret_at(c, T, hold):
    d = bar_by_code.get(c)
    if d is None: return np.nan
    idx = d.index[d['date'] > T]
    need = 2 + hold - 1
    if len(idx) < need: return np.nan
    e, x = d.loc[idx[1]], d.loc[idx[need - 1]]
    if not e['open'] or not x['close']: return np.nan
    return x['close'] / e['open'] - 1

bst_short = lgb.Booster(model_file=r'D:\zcodesj\portrait_agent\models\v1.0.0_short.txt')
recs = []
for T in dates:
    cs = panel_full[panel_full['date'] == T]
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    gate = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
    rm = {h: {c: ret_at(c, T, h) for c in gate['code']} for h in (5, 10, 20)}
    pool = {h: np.array([v for v in rm[h].values() if not np.isnan(v)]) for h in (5, 10, 20)}
    if len(pool[20]) < 500:
        continue
    rec = {'date': T}
    # 变体(内存模型m)
    gate['prob'] = m.predict_proba(gate[FEATURES].to_numpy(dtype=np.float32))[:, 1]
    gate['prob0'] = bst_short.predict(gate[FEATURES])
    for tag, col in (('var', 'prob'), ('v1short', 'prob0')):
        order = gate.sort_values([col, 'turnover20'], ascending=False)
        for k, h in ((20, 5), (10, 10), (50, 5)):
            t = np.array([rm[h].get(c, np.nan) for c in order['code'].head(k)], dtype=float)
            t = t[~np.isnan(t)]
            rec[f'{tag}_top{k}_h{h}'] = float(t.mean() * 100) if len(t) >= max(3, k * 0.6) else None
        pairs = [(p, rm[10][c]) for c, p in zip(gate['code'], gate[col]) if not np.isnan(rm[10].get(c, np.nan))]
        rec[f'{tag}_ic_h10'] = float(spearmanr([x[0] for x in pairs], [x[1] for x in pairs]).statistic)
    rec['base_h5'] = float(pool[5].mean() * 100)
    rec['base_h10'] = float(pool[10].mean() * 100)
    recs.append(rec)
log(f'评估截面 {len(recs)} ({recs[0]["date"]}~{recs[-1]["date"]})')

df = pd.DataFrame(recs)
df['ym'] = df['date'].str[:6]
json.dump(recs, open(r'D:\zcodesj\data\_realizable_variant_eval_20260913.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=float)

print(f"\n===== y_realizable 变体 vs v1.0.0_short (test段 {TEST_START}起 n={len(df)}) =====")
for tag, name in (('var', 'y_realizable变体'), ('v1short', 'v1.0.0_short对照')):
    ex20 = df[f'{tag}_top20_h5'] - df['base_h5']
    ex10 = df[f'{tag}_top10_h10'] - df['base_h10']
    ex50 = df[f'{tag}_top50_h5'] - df['base_h5']
    ic = df[f'{tag}_ic_h10']
    print(f"\n-- {name} --")
    print(f"  ① Top20/h5 超额中位={ex20.median():+.2f}pp (正占比{(ex20>0).mean()*100:.0f}%)  IC(h10)中位={ic.median():+.4f}")
    print(f"  ③ 构造旁证: Top10/h10 超额中位={ex10.median():+.2f}pp | Top50/h5 超额中位={ex50.median():+.2f}pp")
    # ② 分段稳定性(按半年)
    df['half'] = np.where(df['ym'] < '202608', 'H1(0430~0731)', 'H2(0801~)')
    seg = df.groupby('half').apply(lambda g: (g[f'{tag}_top20_h5'] - g['base_h5']).median(), include_groups=False)
    pos_half = (seg >= 0).sum()
    print(f"  ② 分段(半年)超额中位: " + ' | '.join(f"{k}={v:+.2f}pp" for k, v in seg.items()) + f" → 非负段={pos_half}/{len(seg)}")
    c1 = (ex20.median() >= 0) and (ic.median() >= 0.03)
    c2 = pos_half >= 2 * len(seg) / 3
    c3 = (ex10.median() >= 0) and (ex50.median() >= 0)
    verdict = 'PASS(影子候选)' if (c1 and c2 and c3) else 'FAIL'
    print(f"  判据: ①{'✅' if c1 else '❌'} ②{'✅' if c2 else '❌'} ③{'✅' if c3 else '❌'} → **{verdict}**")
print("\n落盘 data/_realizable_variant_eval_20260913.json")
