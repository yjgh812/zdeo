# -*- coding: utf-8 -*-
"""百亚深挖StepC: 全维度(F1~F9)指纹扫描v2(只读研究件, 20260915)

在v1(质量+位置+回撤+低波+非妖+价格域)基础上, 从qfq一次过补算剩余维度:
  F4量能: 量比5v20, 20日均额(亿)
  F5事件: 近60日上跳空次数, 近60日创20日新高天数
  F6结构: MACD状态(DIF/DEA零轴上下)
  F7资金: 乖离20/60(MA口径), 近60日量价齐升天数
  F8分布: 近120日偏度/胜率/盈亏比, 收盘位置20, 高开占比120
基准雷达=百亚股份@20260813入选时点(全部同口径现算, 非引用profile工具)。
用法: python data/_baya_deep_scan_v2_20260915.py
输出: data/_baya_deep_scan_v2_20260915.csv(v1命中137只的全维雷达) + 控制台Tier排序
边界: 全维度=特征化描述非独立alpha主张; 唯一冻结主张仍是prereg的63负过滤。
"""
import sys, os, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
PIT = r'D:\zcodesj\factor_pit.db'
FUND = r'D:\workbuddyspace\databases\fundamental_data.db'
OUT = r'D:\zcodesj\data\_baya_deep_scan_v2_20260915.csv'
BAYA_T0 = 20260813

conn = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
df = pd.read_sql_query(
    "SELECT code, date, open, high, low, close, volume, amount FROM stock_daily_meta_qfq "
    "WHERE date>='20250901' ORDER BY code, date", conn)
conn.close()

def dims(g, t0=None):
    """对单只股票(升序)计算全维特征; t0给定时截断到该日(百亚入选时口径)"""
    if t0 is not None:
        g = g[g.date <= str(t0)]
        if len(g) < 60:
            return None
    c = g.close.values; o = g.open.values; h = g.high.values
    v = g.volume.values; a = g.amount.values
    ret = np.append(np.diff(np.log(c)), np.nan)[:-1]
    ret = c[1:] / c[:-1] - 1  # len n-1, 对齐最后n-1根
    last = c[-1]
    lo250, hi250 = np.min(c[-250:]), np.max(h[-250:])
    out = {}
    out['close'] = round(float(last), 2)
    out['pos250'] = round((last - lo250) / (hi250 - lo250) * 100, 1) if hi250 > lo250 else np.nan
    out['dist_hi'] = round((last / hi250 - 1) * 100, 1)
    out['ret20'] = round((last / c[-21] - 1) * 100, 1)
    out['ret60'] = round((last / c[-61] - 1) * 100, 1)
    out['vol20'] = round(float(np.std(ret[-20:], ddof=1) * 100), 2) if len(ret) >= 20 else np.nan
    # F4量能
    out['vr520'] = round(float(np.mean(v[-5:]) / np.mean(v[-20:])), 2)
    out['amt20y'] = round(float(np.mean(a[-20:]) / 1e8), 2)
    # F5事件
    po = o[1:]; pc = c[:-1]; ph = h[:-1]
    last60 = min(60, len(po))
    out['gap_up_60'] = int(np.sum(po[-last60:] > ph[-last60:]))
    roll20 = pd.Series(c).rolling(20).max().values
    out['nh20_60'] = int(np.sum(c[-last60:] >= roll20[-last60:]))
    # F6结构 MACD
    s = pd.Series(c)
    dif = s.ewm(span=12).mean() - s.ewm(span=26).mean()
    dea = dif.ewm(span=9).mean()
    out['macd'] = ('金叉零上' if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-1] > 0 else
                   '金叉零下' if dif.iloc[-1] > dea.iloc[-1] else
                   '死叉零上' if dif.iloc[-1] > 0 else '死叉零下')
    # F7资金
    out['bias20'] = round((last / np.mean(c[-20:]) - 1) * 100, 1)
    out['bias60'] = round((last / np.mean(c[-60:]) - 1) * 100, 1)
    up = ret > 0; vv = v[1:] > np.mean(v[-21:-1])
    out['pv_up_60'] = int(np.sum((up & vv)[-60:]))
    # F8分布
    r120 = ret[-120:] * 100
    out['skew120'] = round(float(pd.Series(r120).skew()), 2)
    pos_r, neg_r = r120[r120 > 0], r120[r120 < 0]
    out['win120'] = round(len(pos_r) / len(r120) * 100, 1)
    out['pl120'] = round(float(pos_r.mean() / abs(neg_r.mean())), 2) if len(neg_r) and neg_r.mean() != 0 else np.nan
    lo20, hi20 = np.min(c[-20:]), np.max(c[-20:])
    out['cpos20'] = round((last - lo20) / (hi20 - lo20) * 100, 0) if hi20 > lo20 else np.nan
    out['gap_open_120'] = round(float(np.mean(po[-120:] > pc[-120:]) * 100), 0)
    out['date'] = g.date.iloc[-1]
    return out

rows = {}
for code, g in df.groupby('code'):
    if len(g) < 61:
        continue
    d = dims(g)
    if d:
        rows[code] = d
res = pd.DataFrame.from_dict(rows, orient='index')
res = res[res.date == '20260914']
res = res[res.close <= 30]
res = res[~res.index.str.startswith(('688', '4', '8', '92'))]
print(f'全池维度计算完成: {len(res)} 只')

# 百亚入选时基准雷达(同口径)
baya_hist = df[df.code == '003006']
b = dims(baya_hist, t0=BAYA_T0)
print('\n===== 基准雷达: 百亚股份@20260813(入选时) =====')
for k, v in b.items():
    print(f'  {k}: {v}')
b_now = dims(baya_hist)
print('===== 百亚@20260914(现况对照) =====')
for k in ['pos250', 'dist_hi', 'ret20', 'ret60', 'vol20', 'vr520', 'amt20y', 'gap_up_60',
          'nh20_60', 'macd', 'bias20', 'bias60', 'pv_up_60', 'skew120', 'win120', 'pl120',
          'cpos20', 'gap_open_120']:
    print(f'  {k}: {b_now[k]}')

# v1硬过滤(与前版一致) + PIT盈利层
pit_c = sqlite3.connect(f'file:{PIT}?mode=ro', uri=True)
pit = pd.read_sql_query(
    "SELECT code, roe, ep FROM stock_factors_fundamental_pit_v2 WHERE pit_avail_date<='20260915' "
    "ORDER BY code, pit_avail_date, report_date", pit_c)
pit_c.close()
res = res.join(pit.drop_duplicates('code', keep='last').set_index('code')[['roe', 'ep']])
fund = sqlite3.connect(f'file:{FUND}?mode=ro', uri=True)
try:
    ind = pd.read_sql_query('SELECT code, name, industry FROM industry_map_ths', fund).drop_duplicates('code').set_index('code')
except Exception:
    ind = pd.read_sql_query('SELECT code, name, NULL AS industry FROM industry_map_ths', fund).drop_duplicates('code').set_index('code')
fund.close()
res = res.join(ind)
v1 = res[(res.ep > 0) & (res.roe >= 5) & (res.pos250 <= 45) & (res.dist_hi <= -30) &
         (res.ret60 <= 0) & (res.ret20 >= -12) & (res.vol20 <= 4.0) & (res.close <= 30) &
         res.name.notna() & ~res.name.str.contains('ST')]
print(f'\nv1硬过滤命中: {len(v1)} 只')

# 全维距离排序(连续维对百亚@8/13归一化距离, 仅特征化非alpha)
DIMS = ['pos250', 'dist_hi', 'ret20', 'ret60', 'vol20', 'vr520', 'amt20y', 'gap_up_60',
        'nh20_60', 'bias20', 'bias60', 'pv_up_60', 'skew120', 'win120', 'pl120', 'cpos20', 'gap_open_120']
X = v1[DIMS].astype(float)
bvec = pd.Series({k: float(b[k]) for k in DIMS})
rng = X.replace([np.inf, -np.inf], np.nan)
scale = (rng.max() - rng.min()).replace(0, 1)
dist = ((X - bvec).abs() / scale).mean(axis=1)
v1 = v1.assign(dist=dist.round(3)).sort_values('dist')
v1['macd_baya'] = (v1['macd'] == b['macd']).astype(int)
cols = ['name', 'industry', 'close', 'pos250', 'dist_hi', 'ret20', 'ret60', 'vol20', 'vr520',
        'amt20y', 'gap_up_60', 'nh20_60', 'macd', 'bias20', 'bias60', 'pv_up_60', 'skew120',
        'win120', 'pl120', 'cpos20', 'roe', 'dist']
v1[cols].round(2).to_csv(OUT, encoding='utf-8-sig')
print(f'\n===== 全维距离Top20(越小越像百亚@入选时) =====')
print(v1[cols].head(20).round(2).to_string())
print(f'\n落盘 {OUT}')
