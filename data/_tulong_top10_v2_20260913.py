# -*- coding: utf-8 -*-
"""屠龙选股 v2(20260913主人裁决: 画像参与选股): 候选池 = bp_ep42只 ∪ 画像Top500(short).
每只: 月度胜率(61月qfq) + ml分位 + 画像分位 + bp_ep综合分(如在名单) → 按胜率排名.
出处: ①画像 short topn_list=portrait_20260911.json ②bp_ep=20260901_0903_n50 ③ml=v22@0911 ④胜率=qfq月末61月."""
import sys, sqlite3, json, glob, os
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
from stock_name_lookup import get_name

# 1. 候选池: 画像Top500(short) ∪ bp_ep42
pj = json.load(open(r'D:\zcodesj\portrait_agent\reports\portrait_20260911.json', encoding='utf-8'))
pa500 = [x['code'].zfill(6) for x in pj['models']['short']['topn_list']]
pa_pct = {x['code'].zfill(6): round(x['分数'] * 100, 1) for x in pj['models']['short']['topn_list']}
cand = pd.read_csv(r'D:\zcodesj\multifactor\reports\daily_top20\bp_ep_monthly_20260901_20260903_n50.csv',
                   encoding='utf-8-sig', dtype={'code': str})
cand['code'] = cand['code'].str.zfill(6)
bpep = cand[cand['l3'].fillna('') != 'value_trap'].copy()  # 42只(剔value_trap)
bpep_rank = dict(zip(bpep['code'], bpep['rank']))
bpep_score = dict(zip(bpep['code'], bpep['composite_score']))
bpep_ind = dict(zip(bpep['code'], bpep['industry']))

pool = sorted(set(pa500) | set(bpep['code']))
src = {c: ('画像' if c in set(pa500) else '') + ('+bp_ep' if c in set(bpep['code']) else '') for c in pool}
print(f"候选池: 画像Top500 ∪ bp_ep42 = {len(pool)} 只 (双源命中={sum(1 for c in pool if src[c]=='画像+bp_ep')})")

# 2. ml 分位
con = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro', uri=True, timeout=30)
ml = pd.read_sql_query("SELECT code, ml_prob FROM stock_ml_scores_v22 WHERE date=(SELECT MAX(date) FROM stock_ml_scores_v22)", con)
con.close()
ml['code'] = ml['code'].str.zfill(6)
ml['ml_pct'] = ml['ml_prob'].rank(pct=True) * 100
mlmap = dict(zip(ml['code'], ml['ml_pct']))

# 3. 月度胜率(qfq 61月)
con = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True, timeout=180)
chunks = []
for i in range(0, len(pool), 50):
    ch = pool[i:i+50]
    ph = ','.join('?' * len(ch))
    chunks.append(pd.read_sql_query(
        f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20210901' ORDER BY code,date",
        con, params=ch))
con.close()
px = pd.concat(chunks, ignore_index=True)
px['date'] = px['date'].astype(str)
px['ym'] = px['date'].str[:6]
me = px.sort_values('date').groupby(['code', 'ym'])['close'].last().reset_index()
me = me.sort_values(['code', 'ym'])
me['up'] = me.groupby('code')['close'].pct_change() > 0
win = me[me['ym'] >= '202109'].groupby('code')['up'].agg(['sum', 'count'])
win['winrate'] = win['sum'] / win['count'] * 100
win = win[win['count'] >= 30]

rows = []
for c in pool:
    w = win.loc[c] if c in win.index else None
    if w is None or (isinstance(w, pd.Series) and (w['count'] < 30)):
        continue
    rows.append({'code': c, 'name': get_name(c) or '?', 'src': src[c],
                 'winrate': round(float(w['winrate']), 1), 'months': int(w['count']),
                 'ml_pct': round(mlmap.get(c, np.nan), 1) if mlmap.get(c) == mlmap.get(c) else None,
                 'pa_pct': pa_pct.get(c), 'bpep_rank': bpep_rank.get(c),
                 'bpep_score': round(bpep_score[c], 3) if c in bpep_score else None,
                 'industry': bpep_ind.get(c, '')})
out = pd.DataFrame(rows).sort_values(['winrate', 'ml_pct'], ascending=False)
# ST 剔除(名称含ST)
out = out[~out['name'].str.contains('ST', na=False)]
print(f"胜率可算+剔ST: {len(out)} 只")
cols = ['winrate', 'months', 'code', 'name', 'src', 'industry', 'ml_pct', 'pa_pct', 'bpep_rank', 'bpep_score']
print("\n=== 按月度胜率 Top20 (画像Top500∪bp_ep 候选池) ===")
print(out[cols].head(20).to_string(index=False))
# 行业分散观察: Top10 的来源分布
top10 = out.head(10)
print(f"\nTop10 来源: {top10['src'].value_counts().to_dict()}")
out.head(20).to_csv(r'D:\zcodesj\data\_tulong_top10_v2_20260913.csv', index=False, encoding='utf-8-sig')
print('落盘 data/_tulong_top10_v2_20260913.csv')
