# -*- coding: utf-8 -*-
"""屠龙正统选股: bp_ep 9月名单(唯一合法来源§10-4) → SG过滤 → 个股历史月度胜率排名.
出处: ①名单 multifactor/reports/daily_top20/bp_ep_monthly_20260901_20260903_n50.csv
     ②月度胜率 = qfq 月末收盘 2021-09~2026-09 上涨占比(个股自身统计)
     ③ml_prob = stock_ml_scores_v22@20260911  ④画像分位 = portrait_feed@0911(研究标注零决策权).
"""
import sys, sqlite3, glob, json
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
from stock_name_lookup import get_name

BS = chr(92)
# 1. 候选池
cand = pd.read_csv(r'D:\zcodesj\multifactor\reports\daily_top20\bp_ep_monthly_20260901_20260903_n50.csv',
                   encoding='utf-8-sig', dtype={'code': str})
cand['code'] = cand['code'].str.zfill(6)
print(f"候选池: {len(cand)} 只 (bp_ep 9月名单)")

# 2. 过滤: l3=value_trap / 价格>30(宪法)
f1 = cand[cand['l3'].fillna('') != 'value_trap']
print(f"剔 l3=value_trap: {len(cand)-len(f1)} 只 → {len(f1)}")
f2 = f1[f1['close'] <= 30].copy()
print(f"剔 close>30元(宪法): {len(f1)-len(f2)} 只 → {len(f2)}")

# 3. ml_prob 分位(最新分区)
con = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro', uri=True, timeout=30)
ml = pd.read_sql_query("SELECT code, ml_prob FROM stock_ml_scores_v22 WHERE date=(SELECT MAX(date) FROM stock_ml_scores_v22)", con)
con.close()
ml['code'] = ml['code'].str.zfill(6)
ml['ml_pct'] = ml['ml_prob'].rank(pct=True) * 100
f2 = f2.merge(ml[['code', 'ml_prob', 'ml_pct']], on='code', how='left')

# 4. 画像分位(研究标注)
try:
    feed = json.load(open(r'D:\zcodesj\data\portrait_feed.json', encoding='utf-8'))
    ps = feed.get('scores_short', {})
    f2['pa_pct'] = f2['code'].map(lambda c: ps.get(c, {}).get('pct'))
except Exception:
    f2['pa_pct'] = None

# 5. 月度胜率(qfq 月末收盘, 2021-09~最新)
con = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True, timeout=120)
codes = f2['code'].tolist()
chunks = []
for i in range(0, len(codes), 25):
    ch = codes[i:i+25]
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
win = win[win['count'] >= 30]  # 至少30个月样本
f2 = f2.merge(win.reset_index().rename(columns={'count': 'months'}), on='code', how='left')
n_valid = f2['winrate'].notna().sum()
print(f"月度胜率可算: {n_valid}/{len(f2)} (>=30个月样本)")

# 6. 排名: 胜率降序, 同胜率按 ml_pct
out = f2[f2['winrate'].notna()].sort_values(['winrate', 'ml_pct'], ascending=False).copy()
out['name2'] = out['code'].map(lambda c: get_name(c) or '?')
cols = ['rank', 'code', 'name2', 'industry', 'close', 'winrate', 'months', 'composite_score', 'ml_pct', 'pa_pct', 'l3']
out_disp = out[cols].head(15).copy()
out_disp.columns = ['名单rank', '代码', '名称', '行业', '收盘', '月度胜率%', '样本月数', 'bp_ep综合分', 'ml分位%', '画像分位%', '标签']
print("\n=== 按月度胜率排名 Top15 (Top10 为交付) ===")
print(out_disp.to_string(index=False))
out.head(15).to_csv(r'D:\zcodesj\data\_tulong_top10_by_winrate_20260913.csv', index=False, encoding='utf-8-sig')
print('\n落盘 data/_tulong_top10_by_winrate_20260913.csv')
