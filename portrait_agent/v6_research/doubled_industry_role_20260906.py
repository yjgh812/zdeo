# -*- coding: utf-8 -*-
"""翻倍股行业板块角色画像(20260906 "其他继续"指令, 画像最后一块).

口径: 行业=industry_map_ths(THS); 行业收益=行业内成分股等权中位; 板块角色=主升月该股
      在同行业全部成分股中的涨幅排名(金健"8月板块第一"同构量化).
数据: qfq weekly备份(20260906全史amount修复版) + fundamental_data.db行业映射.
复检: 修复后库上重算137只YTD, 与v1逐只对照(应分毫不差=修复未动close).
"""
import os, sys, json, time, sqlite3
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

QFQ_DB = r'D:\zcodesj\backups\dragon_quant_qfq\weekly\dragon_quant_qfq_20260906.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
import argparse
_ap = argparse.ArgumentParser(); _ap.add_argument('--year', choices=['2026', '2025'], default='2026'); _yargs = _ap.parse_args(); YEAR = _yargs.year
V1_JSON = {'2026': r'D:\zcodesj\data\doubled_batch_features_20260906.json',
           '2025': r'D:\zcodesj\data\doubled_batch_features_2025.json'}[YEAR]
OUT = {'2026': r'D:\zcodesj\portrait_agent\reports\doubled_industry_role_20260906.json',
       '2025': r'D:\zcodesj\portrait_agent\reports\doubled_industry_role_2025.json'}[YEAR]
Y0, YEND = {'2026': ('20260101', '20260904'), '2025': ('20250101', '20251231')}[YEAR]
T0 = time.time()
def log(m): print(f'[{time.time()-T0:6.1f}s] {m}', flush=True)

v1 = json.load(open(V1_JSON, encoding='utf-8'))['features']
main = {c: f for c, f in v1.items() if f['kind'] == 'doubled_now'}
log(f'主口径 {len(main)} 只')

# 行业映射(列: code,name,industry,src; industry=THS三级串, 取一级=首段)
con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
ind = pd.read_sql_query("SELECT code, industry FROM industry_map_ths", con)
con.close()
ind['industry'] = ind['industry'].fillna('').str.split('-').str[0]
log(f'行业映射 {len(ind)} 行, 一级行业数={ind["industry"].nunique()}')
ind_map = dict(zip(ind['code'], ind['industry']))

# 全市场月末收盘 → 月收益(修复后库复检+行业排名双用)
con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
df = pd.read_sql_query(
    "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>=? AND date<=?", con, params=[('20251120' if YEAR == '2026' else '20241120'), YEND])
con.close()
cp = df.pivot(index='date', columns='code', values='close').sort_index()
me_idx = [d for d in cp.index if d[6:8] >= '25' or d[4:6] in ('01','02','03','04','05','06','07','08','09')]
# 取每月最后交易日; 2025-12为YTD基准月(=20251231, 与v1口径一致)
mons = sorted(set(cp.index.str[:6]))
month_end = {mo: [d for d in cp.index if d.startswith(mo)][-1] for mo in mons}
mons = [m for m in mons if m >= ('202601' if YEAR == '2026' else '202501')]
prev_end = {mons[0]: month_end['202512'] if YEAR == '2026' else month_end['202412']}
for i in range(1, len(mons)):
    prev_end[mons[i]] = month_end[mons[i-1]]
# 月收益矩阵(全市场)
log('计算全市场月收益矩阵...')
mon_ret = {}
for mo in mons:
    mon_ret[mo] = (cp.loc[month_end[mo]] / cp.loc[prev_end[mo]] - 1) * 100
# YTD
ytd_all = (cp.loc[month_end[mons[-1]]] / cp.loc[prev_end[mons[0]]] - 1) * 100
log(f'全市场 {cp.shape[1]} 只 × {len(mons)} 月就绪')

# 行业聚合 + 137只角色
rows_out = {}
industry_of = {}
for c in cp.columns:
    industry_of[c] = ind_map.get(c, '未分类')
ind_series = pd.Series(industry_of)
role_rows = []
for code, f in main.items():
    if code not in cp.columns: continue
    bm = f['主升月'][:6]
    if bm not in mon_ret: continue
    ind_name = industry_of.get(code, '未分类')
    peers = ind_series[ind_series == ind_name].index
    mr = mon_ret[bm].reindex(peers).dropna()
    rank = int((mr > mr.get(code, -999)).sum()) + 1
    ytd_peers = ytd_all.reindex(peers).dropna()
    ytd_rank = int((ytd_peers > ytd_all.get(code, -999)).sum()) + 1
    role_rows.append({
        'code': code, 'name': f['name'], '行业': ind_name,
        '行业成分数': len(mr), '主升月': bm,
        '主升月涨幅%': f['主升月涨幅%'],
        '主升月行业内排名': rank, '主升月行业分位%': round(rank / len(mr) * 100, 1),
        'YTD行业内排名': ytd_rank,
    })
df_role = pd.DataFrame(role_rows)
# 复检: YTD对照
recheck = []
for code, f in main.items():
    if code in ytd_all.index and f['YTD%'] is not None:
        d = abs(round(float(ytd_all[code]), 2) - f['YTD%'])
        if d > 0.01: recheck.append((code, d))
log(f'修复库YTD复检: {"137/137 分毫不差 PASS" if not recheck else f"FAIL {recheck[:5]}"}')

# 聚合
agg = {
    '样本': len(df_role),
    '主升月行业内第1(板块龙头率%)': round(float((df_role['主升月行业内排名'] == 1).mean() * 100), 1),
    '主升月行业内前3占比%': round(float((df_role['主升月行业内排名'] <= 3).mean() * 100), 1),
    '主升月行业内前10%分位占比%': round(float((df_role['主升月行业分位%'] <= 10).mean() * 100), 1),
    '主升月行业中位分位%': round(float(df_role['主升月行业分位%'].median()), 1),
    '行业分布TOP10': df_role['行业'].value_counts().head(10).to_dict(),
    '主升月分布(行业角色视角)': df_role.groupby('主升月')['主升月行业内排名'].apply(lambda s: round(float((s == 1).mean() * 100), 1)).to_dict(),
}
agg['主升月分布(行业角色视角)'] = {k: f'龙头率{v}%' for k, v in agg['主升月分布(行业角色视角)'].items()}
json.dump({'agg': agg, 'roles': role_rows}, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
log(f'落盘 {OUT}')
print(json.dumps(agg, ensure_ascii=False, indent=1))
