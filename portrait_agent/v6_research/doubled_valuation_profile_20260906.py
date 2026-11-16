# -*- coding: utf-8 -*-
"""2026翻倍股规模与估值结构画像(20260906 "继续"指令, 画像正交增量维度).

口径: 市值/估值/换手全部来自 daily_basic 独立表(tushare源, 与qfq amount事故无关);
      对照组=同期全市场中位数(结构性差异才有信息量); 137只主口径.
维度: ①市值带分布(年初/现在) ②PE/PB与亏损占比 ③换手率结构 ④对全市场结构差.
"""
import os, sys, json, time, sqlite3
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
import argparse
_ap = argparse.ArgumentParser(); _ap.add_argument('--year', choices=['2026', '2025'], default='2026'); _yargs = _ap.parse_args(); YEAR = _yargs.year
V1_JSON = {'2026': r'D:\zcodesj\data\doubled_batch_features_20260906.json',
           '2025': r'D:\zcodesj\data\doubled_batch_features_2025.json'}[YEAR]
OUT = {'2026': r'D:\zcodesj\portrait_agent\reports\doubled_valuation_profile_20260906.json',
       '2025': r'D:\zcodesj\portrait_agent\reports\doubled_valuation_profile_2025.json'}[YEAR]
D_FIRST, D_LAST = {'2026': ('20260105', '20260904'), '2025': ('20250102', '20251231')}[YEAR]
T0 = time.time()
def log(m): print(f'[{time.time()-T0:6.1f}s] {m}', flush=True)

v1 = json.load(open(V1_JSON, encoding='utf-8'))['features']
main = {c: f for c, f in v1.items() if f['kind'] == 'doubled_now'}
log(f'主口径 {len(main)} 只')

con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
db = pd.read_sql_query(
    "SELECT ts_code, trade_date, close, turnover_rate, volume_ratio, pe_ttm, pb, total_mv, circ_mv "
    "FROM daily_basic WHERE trade_date IN (?, ?)", con, params=[D_FIRST, D_LAST])
con.close()
db['code'] = db['ts_code'].str[:6]
first = db[db['trade_date'] == D_FIRST].set_index('code')
last = db[db['trade_date'] == D_LAST].set_index('code')
log(f'daily_basic 行={len(db)} 首日{len(first)}只 末日{len(last)}只')

def med(s):
    v = s.dropna()
    return round(float(np.median(v)), 2) if len(v) else None

def pctl_of(val, series):
    s = series.dropna()
    return round(float((s < val).mean() * 100), 1) if len(s) else None

rows = []
for code, f in main.items():
    if code not in last.index: continue
    l = last.loc[code]
    fi = first.loc[code] if code in first.index else None
    rows.append({
        'code': code, 'name': f['name'],
        '总市值亿(初)': round(fi['total_mv'] / 1e4, 1) if fi is not None and pd.notna(fi['total_mv']) else None,
        '总市值亿(现)': round(l['total_mv'] / 1e4, 1) if pd.notna(l['total_mv']) else None,
        '流通市值亿(现)': round(l['circ_mv'] / 1e4, 1) if pd.notna(l['circ_mv']) else None,
        'pe_ttm': float(l['pe_ttm']) if pd.notna(l['pe_ttm']) else None,
        'pb': float(l['pb']) if pd.notna(l['pb']) else None,
        '换手率%(现)': float(l['turnover_rate']) if pd.notna(l['turnover_rate']) else None,
    })
dr = pd.DataFrame(rows)
log(f'匹配到 daily_basic 的翻倍股 {len(dr)} 只')

# 全市场对照(末日截面)
mkt = last
mkt_pe = mkt['pe_ttm'].dropna()
mkt_mv = (mkt['total_mv'] / 1e4).dropna()
mkt_turn = mkt['turnover_rate'].dropna()

pe_valid = dr['pe_ttm'].dropna()
neg_pe = int((pe_valid < 0).sum())
pe_pos = dr.loc[dr['pe_ttm'] > 0, 'pe_ttm']
small_mv = int((dr['总市值亿(初)'] < 50).sum())
small_mv30 = int((dr['总市值亿(初)'] < 30).sum())

agg = {
    '样本': len(dr),
    '市值结构': {
        '年初总市值中位(亿)': med(dr['总市值亿(初)']),
        '现总市值中位(亿)': med(dr['总市值亿(现)']),
        '全市场总市值中位(亿)': med(mkt_mv),
        '年初<30亿占比%': round(small_mv30 / len(dr) * 100, 1),
        '年初<50亿占比%': round(small_mv / len(dr) * 100, 1),
        '翻倍股市值分位中位(vs全市场)': round(float(np.median([pctl_of(v, mkt_mv) for v in dr['总市值亿(现)'].dropna()])), 1),
    },
    '估值结构': {
        '亏损股(pe_ttm<0)占比%(有值样本)': round(neg_pe / len(pe_valid) * 100, 1) if len(pe_valid) else None,
        'pe_ttm缺失占比%': round((len(dr) - len(pe_valid)) / len(dr) * 100, 1),
        'PE为正中位': med(pe_pos),
        '全市场PE为正中位': med(mkt[mkt['pe_ttm'] > 0]['pe_ttm']),
        'PB中位': med(dr['pb']),
        '全市场PB中位': med(mkt['pb']),
    },
    '换手结构': {
        '现换手率中位%': med(dr['换手率%(现)']),
        '全市场换手中位%': med(mkt_turn),
        '换手>10%占比%': round(float((dr['换手率%(现)'] > 10).mean() * 100), 1),
    },
    '行业头部补充': '电子50/机械21/化工8(见doubled_industry_role)',
}
# 高估值成长 vs 低估值: pe>60 或 <0 的"题材/成长"占比
agg['估值结构']['PE>60或亏损占比%(题材成长型,有值样本)'] = round(float(((pe_valid > 60) | (pe_valid < 0)).mean() * 100), 1) if len(pe_valid) else None
agg['估值结构']['0<PE<=30占比%(低估值型,有值样本)'] = round(float(((pe_valid > 0) & (pe_valid <= 30)).mean() * 100), 1) if len(pe_valid) else None

json.dump({'agg': agg, 'rows': rows}, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
log(f'落盘 {OUT}')
print(json.dumps(agg, ensure_ascii=False, indent=1))
