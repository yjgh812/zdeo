# -*- coding: utf-8 -*-
"""
hnew2_width_20260916.py — H-NEW2 板块分化·宽度口径重定义（描述性补验，一次性, 只读计算）
背景: 原口径(指数量增x成分总量缩量)与指数volume同源, 全史n=1无信息量(规则库§14.2)。
重定义: 宽度=行业内上涨家数占比。分化=指数涨(≥1%)而宽度<=40%(少数票拉指数=虚涨);
对照=指数涨且宽度>=60%(普涨)。
口径: 主板60/00剔ST/退市/次新(同规则库v2); 行业映射industry_map静态(覆盖率91.6%);
行业指数=industry_kline_sw L1; 信号日2018+; 指数fwd20=next-bar。
声明: 开发样本内描述性统计, 非prereg, 不构成生产放行。
"""
import json, sqlite3, time
import numpy as np
import pandas as pd

T0 = time.time()
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
FUND = r'D:\workbuddyspace\databases\fundamental_data.db'
SWDB = r'D:\workbuddyspace\databases\sw_industry.db'
OUT = r'D:\zcodesj\data\_hnew2_width_20260916_result.json'
R = {'meta': {'disclaimer': 'development-sample descriptive stats only, NOT prereg',
              'definition': '宽度=行业内上涨家数占比; 分化=指数日涨>=1%且宽度<=40%; 普涨对照=指数涨>=1%且宽度>=60%'}}

def ro(p):
    return sqlite3.connect(f'file:{p}?mode=ro', uri=True)

info = pd.read_sql("SELECT code,name,is_st,status,instrument_type,list_date FROM stock_info", ro(MAIN))
valid = info[(info.instrument_type == 'stock') & (info.is_st == '0') & (info.status == 'normal')
             & (info.code.str.startswith(('60', '00'))) & (info.list_date < '20170101')]
codes_all = sorted(valid.code.tolist())
im = pd.read_sql("SELECT ts_code, industry_name FROM industry_map", ro(FUND))
code2ind = {t[:6]: n for t, n in zip(im.ts_code, im.industry_name) if pd.notna(n)}

# 行业指数日收益
con = ro(SWDB)
sw = pd.read_sql("SELECT ts_code,name,trade_date,close FROM industry_kline_sw WHERE level='L1'", con)
con.close()
sw = sw[sw.close > 0].copy()
sw['trade_date'] = sw.trade_date.astype(str).str.replace('-', '', regex=False)
sw = sw.sort_values(['ts_code', 'trade_date'])
sw['iret'] = sw.groupby('ts_code', sort=False)['close'].pct_change()
sw['entry'] = sw.groupby('ts_code', sort=False)['close'].shift(-1)
sw['ifwd20'] = sw.groupby('ts_code', sort=False)['close'].shift(-21) / sw['entry'] - 1.0
idx = sw[['name', 'trade_date', 'iret', 'ifwd20']].rename(columns={'name': 'industry', 'trade_date': 'date'})
print(f'[sw] L1 {idx.industry.nunique()}', flush=True)

# 成分宽度: 逐日逐行业 上涨家数占比
BATCH = 600
w_frames = []
for bi in range(0, len(codes_all), BATCH):
    ch = codes_all[bi:bi + BATCH]
    con = ro(QFQ)
    df = pd.read_sql(f"SELECT code,date,change_pct FROM stock_daily_meta_qfq WHERE date>='20170101' AND code IN ({','.join('?'*len(ch))})",
                     con, params=ch)
    con.close()
    df = df[df.change_pct.notna()]
    df['industry'] = df.code.map(code2ind)
    df = df[df.industry.notna()]
    w_frames.append(df)
width_raw = pd.concat(w_frames, ignore_index=True)
print(f'[qfq] 行业映射行 {len(width_raw):,} t={time.time()-T0:.0f}s', flush=True)
width_raw['up'] = (width_raw.change_pct > 0).astype(np.int8)
agg = width_raw.groupby(['industry', 'date'], as_index=False).agg(up_ratio=('up', 'mean'), n=('up', 'size'))
agg = agg[agg.n >= 8]  # 行业内样本过少的日子不可信
print(f'[width] 聚合行 {len(agg):,}', flush=True)

mg = agg.merge(idx, on=['industry', 'date'], how='inner')
mg = mg.dropna(subset=['iret', 'ifwd20'])
mg = mg[mg.date >= '20180101']
up_day = mg[mg.iret >= 0.01]
res = {}
for nm, m in [('H2w1_分化(指数涨>=1%且宽度<=40%)', (up_day.up_ratio <= 0.40)),
              ('H2w2_普涨对照(指数涨>=1%且宽度>=60%)', (up_day.up_ratio >= 0.60)),
              ('H2w3_隐性走强(指数跌<=-1%且宽度>=60%)', None)]:
    if m is None:
        dn_day = mg[mg.iret <= -0.01]
        s = dn_day.loc[dn_day.up_ratio >= 0.60, 'ifwd20']
    else:
        s = up_day.loc[m, 'ifwd20']
    res[nm] = {'n': int(len(s)), 'fwd20_mean': round(float(s.mean()), 4), 'win': round(float((s > 0).mean()), 4)}
    print(nm, res[nm], flush=True)
# 分层细化: 分化日内按宽度分桶
s40 = up_day[up_day.up_ratio <= 0.40]
for lo, hi in [(0.0, 0.20), (0.20, 0.30), (0.30, 0.40)]:
    s = s40.loc[(s40.up_ratio >= lo) & (s40.up_ratio < hi), 'ifwd20']
    key = f'H2w1细化_宽度{lo:.0%}-{hi:.0%}'
    res[key] = {'n': int(len(s)), 'fwd20_mean': round(float(s.mean()), 4), 'win': round(float((s > 0).mean()), 4)}
    print(key, res[key], flush=True)
R['H2宽度口径'] = res
R['meta']['样本说明'] = 'up_ratio按行业内成分等权; n>=8过滤; 指数ifwd20=next-bar'
json.dump(R, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'[done] {OUT} {time.time()-T0:.0f}s', flush=True)
