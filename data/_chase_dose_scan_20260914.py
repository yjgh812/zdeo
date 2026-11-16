# -*- coding: utf-8 -*-
"""追高闸门阈值剂量-收益扫描 20260914
全市场近5年(qfq), 对每个"T日买入"场景计算 T-1 时点追高剂量:
  mom20 = close[T-1]/close[T-21]-1   (R-S5/R32口径, 阈值+2%/30%)
  ext20 = close[T-1]/MA20[T-1]-1     (R31口径, 阈值15%/20%)
分桶统计 fwd5/fwd10 均值/中位/胜率/P90尾部 -> 判定当前阈值是否在胜率恶化拐点。
纯研究输出, 不改任何闸门语义; 服务 B1 裁决(追高WARN后 tier 管理的剂量依据)。
"""
import sqlite3, sys, io
import pandas as pd
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

con = sqlite3.connect('file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro', uri=True)
df = pd.read_sql("SELECT code, date, close FROM stock_daily_meta_qfq WHERE date>='20210901' ORDER BY code, date", con)
con.close()
print("rows=%d codes=%d span=%s~%s" % (len(df), df['code'].nunique(), df['date'].min(), df['date'].max()))

g = df.groupby('code')['close']
close_p1 = g.shift(1)
close_p21 = g.shift(21)
close_n5 = g.shift(-5)
close_n10 = g.shift(-10)
df['ma20'] = g.transform(lambda s: s.rolling(20).mean())
ma20_p1 = df.groupby('code')['ma20'].shift(1)
df['mom20'] = close_p1 / close_p21 - 1
df['ext20'] = close_p1 / ma20_p1 - 1
df['fwd5'] = close_n5 / df['close'] - 1
df['fwd10'] = close_n10 / df['close'] - 1
df = df.dropna(subset=['mom20', 'ext20', 'fwd5'])
print("有效样本:", len(df))

def bucket_table(col, bins, labels, title):
    df['_b'] = pd.cut(df[col], bins=bins, labels=labels)
    print("\n===== %s 剂量-收益曲线 =====" % title)
    print("剂量桶 | n | fwd5均 | fwd5胜率 | fwd5P90(尾部) | fwd10均 | fwd10胜率")
    for b in labels:
        sub = df[df['_b'] == b]
        if len(sub) < 100:
            print("%s | %d | (样本<100略)" % (b, len(sub))); continue
        f5, f10 = sub['fwd5']*100, sub['fwd10'].dropna()*100
        print("%s | %d | %+.2f%% | %.1f%% | %+.1f%% | %+.2f%% | %.1f%%" % (
            b, len(sub), f5.mean(), 100*(f5 > 0).mean(), f5.quantile(0.9),
            f10.mean() if len(f10) else float('nan'),
            100*(f10 > 0).mean() if len(f10) else float('nan')))

bucket_table('mom20',
    bins=[-np.inf, 0, 0.02, 0.10, 0.20, 0.30, 0.50, np.inf],
    labels=['≤0', '(0,2%]', '(2%,10%]', '(10%,20%]', '(20%,30%]', '(30%,50%]', '>50%'],
    title="mom20 (R-S5阈值+2% / R32阈值30%)")
bucket_table('ext20',
    bins=[-np.inf, -0.10, 0, 0.05, 0.10, 0.15, 0.20, 0.30, np.inf],
    labels=['≤-10%', '(-10%,0]', '(0,5%]', '(5%,10%]', '(10%,15%]', '(15%,20%]', '(20%,30%]', '>30%'],
    title="ext20 (R31阈值15%WARN/20%REJECT)")

# 分年度稳健性: mom20>2% 组的分年 fwd5 胜率(防单regime误导)
print("\n===== mom20>2% 组分年 fwd5 稳健性 =====")
hi = df[df['mom20'] > 0.02].copy()
hi['year'] = hi['date'].str[:4]
lo = df[df['mom20'] <= 0.02].copy()
lo['year'] = lo['date'].str[:4]
print("年份 | 追高组fwd5均(胜率,n) | 未追高组fwd5均(胜率,n) | 差值pp")
for y in sorted(hi['year'].unique()):
    h = hi[hi['year'] == y]['fwd5']*100
    l = lo[lo['year'] == y]['fwd5']*100
    if len(h) < 100 or len(l) < 100: continue
    print("%s | %+.2f%% (%.0f%%, n=%d) | %+.2f%% (%.0f%%, n=%d) | %+.2f" % (
        y, h.mean(), 100*(h > 0).mean(), len(h),
        l.mean(), 100*(l > 0).mean(), len(l), h.mean()-l.mean()))
