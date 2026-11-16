# -*- coding: utf-8 -*-
"""百亚深挖StepB: 全池扫描"百亚入选时指纹"候选(只读研究件, 20260915)

指纹口径(经StepA历届198事件校准):
  质量层  PIT最新期 ep>0 且 roe>=5        (bp_ep质量成长域, 名单层已剔亏损的延伸)
  位置层  pos250<=45 且 距年高<=-30%      (覆盖三winner百亚2.9/科森39/杭电41; 剔Q5高位段fwd20-8.16%)
  回撤中  ret60<=0                        (三winner入选时全负)
  非崩塌  ret20>=-12                      (剔正在崩的下跌刀)
  低波    vol20<=4.0                      (百亚2.08/科森3.94; 杭电4.91超本阈仅注记)
  非妖    近250日涨幅>=9.7%天数<=3        (百亚2次; 近似口径含创业板大阳)
域: qfq全池 剔ST/688/北交所/次新(<60交易日), 现价<=30元(系统红线); <=20元标注"池内可排雷"。
边界: 纯观察研究池, 非买入名单; 买入仍须白名单闸门。
"""
import sys, os, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
PIT = r'D:\zcodesj\factor_pit.db'
FUND = r'D:\workbuddyspace\databases\fundamental_data.db'
OUT = r'D:\zcodesj\data\_baya_deep_scan_20260915_result.csv'

conn = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
df = pd.read_sql_query(
    "SELECT code, date, close, high, volume FROM stock_daily_meta_qfq "
    "WHERE date>='20250901' ORDER BY code, date", conn)
conn.close()
print(f'加载行数: {len(df)}, 股票数: {df.code.nunique()}')

g = df.groupby('code')
res = pd.DataFrame({
    'last_date': g.date.last(),
    'close': g.close.last(),
    'n_bars': g.size(),
    'lo250': g.close.min(),
    'hi250': g.high.max(),
})
res = res[res.n_bars >= 60]
last20 = g.tail(21)
first60 = g.tail(61)
res['ret20'] = last20.groupby('code').close.apply(lambda s: (s.iloc[-1]/s.iloc[0]-1)*100)
res['ret60'] = first60.groupby('code').close.apply(lambda s: (s.iloc[-1]/s.iloc[0]-1)*100)
chg = df.groupby('code').close.apply(lambda s: (s/s.shift(1)-1).abs())
vol20 = chg.groupby('code').tail(20).groupby('code').mean()*100
res['vol20'] = vol20
big_days = df.assign(up=(df.groupby('code').close.transform(lambda s: s/s.shift(1)-1) >= 0.097).astype(int))
res['big_up_250'] = big_days.groupby('code').up.sum()
res['pos250'] = (res.close - res.lo250) / (res.hi250 - res.lo250) * 100
res['dist_hi'] = (res.close / res.hi250 - 1) * 100
res = res[res.last_date == '20260914']  # 只取数据新鲜的

# 域过滤: 剔科创688/北交所(4,8,92开头)/ST由名称层剔; 价格红线30
res = res[res.close <= 30]
res = res[~res.index.str.startswith(('688', '4', '8', '92'))]
print(f'域内(<=30元,>=60bar,主板/创业板): {len(res)}')

# PIT质量层
pit_c = sqlite3.connect(f'file:{PIT}?mode=ro', uri=True)
pit = pd.read_sql_query(
    "SELECT code, roe, ep, pit_avail_date, report_date FROM stock_factors_fundamental_pit_v2 "
    "WHERE pit_avail_date<='20260915' ORDER BY code, pit_avail_date, report_date", pit_c)
pit_c.close()
pit = pit.drop_duplicates('code', keep='last').set_index('code')
res = res.join(pit[['roe', 'ep']])

# 行业名称
fund = sqlite3.connect(f'file:{FUND}?mode=ro', uri=True)
try:
    ind = pd.read_sql_query('SELECT code, name, industry FROM industry_map_ths', fund)
except Exception:
    ind = pd.read_sql_query('SELECT code, name, NULL AS industry FROM industry_map_ths', fund)
fund.close()
ind = ind.drop_duplicates('code').set_index('code')
res = res.join(ind)

# 指纹过滤
mask = ((res.ep > 0) & (res.roe >= 5) &
        (res.pos250 <= 45) & (res.dist_hi <= -30) &
        (res.ret60 <= 0) & (res.ret20 >= -12) &
        (res.vol20 <= 4.0) & (res.big_up_250 <= 3) &
        res.name.notna() & ~res.name.str.contains('ST'))
cand = res[mask].copy()
cand['池内'] = cand.close <= 20
cand = cand.sort_values(['roe'], ascending=False)
cols = ['name', 'industry', 'close', 'pos250', 'dist_hi', 'ret20', 'ret60', 'vol20',
        'big_up_250', 'roe', 'ep', '池内']
cand[cols].round(2).to_csv(OUT, encoding='utf-8-sig')
print(f'\n指纹命中: {len(cand)} 只 (其中池内<=20元 {int(cand.池内.sum())} 只可跑排雷)')
print(cand[cols].head(30).round(2).to_string())
print(f'\n落盘 {OUT}')
