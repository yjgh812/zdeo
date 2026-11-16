# -*- coding: utf-8 -*-
"""蓝盾光电(300862) 2026-03-01至今 全量K线分析：技术指标+关键节点识别"""
import json, math

k = json.load(open(r'D:\zcodesj\ldgd_kline_20260301_20260815.json', encoding='utf-8'))
# 每行: [date, open, close, high, low, volume(手)]
rows = []
for r in k:
    rows.append({'date': r[0], 'open': float(r[1]), 'close': float(r[2]),
                 'high': float(r[3]), 'low': float(r[4]), 'vol': float(r[5])})

n = len(rows)
closes = [r['close'] for r in rows]
vols = [r['vol'] for r in rows]

def ma(arr, p):
    out = [None]*len(arr)
    for i in range(p-1, len(arr)):
        out[i] = sum(arr[i-p+1:i+1])/p
    return out

ma5, ma10, ma20, ma60 = ma(closes,5), ma(closes,10), ma(closes,20), ma(closes,60)

# MACD (12,26,9) 标准EMA
def ema(arr, p):
    out=[None]*len(arr); e=arr[0]; alpha=2/(p+1)
    for i,v in enumerate(arr):
        e = v*alpha + e*(1-alpha) if i>0 else v
        out[i]=e
    return out
ema12, ema26 = ema(closes,12), ema(closes,26)
dif=[ema12[i]-ema26[i] for i in range(n)]
dea=ema(dif,9)
macd=[(dif[i]-dea[i])*2 for i in range(n)]

# RSI14
def rsi(arr,p=14):
    out=[None]*len(arr)
    for i in range(p,len(arr)):
        g=l=0
        for j in range(i-p+1,i+1):
            d=arr[j]-arr[j-1]
            g+= max(d,0); l+= max(-d,0)
        out[i]= 100*g/(g+l) if (g+l)>0 else 50
    return out
rsi14=rsi(closes)

# 涨跌幅、振幅、量比(对5日均量)
print('='*110)
print('一、全区间概览')
print(f"区间: {rows[0]['date']} → {rows[-1]['date']}  共{n}个交易日")
print(f"起点收盘 {closes[0]:.2f} → 终点收盘 {closes[-1]:.2f}  区间涨幅 {(closes[-1]/closes[0]-1)*100:+.1f}%")
hi=max(rows,key=lambda r:r['high']); lo=min(rows,key=lambda r:r['low'])
print(f"区间最高 {hi['high']:.2f}({hi['date']})  区间最低 {lo['low']:.2f}({lo['date']})")
print(f"当前 MA5={ma5[-1]:.2f} MA10={ma10[-1]:.2f} MA20={ma20[-1]:.2f} MA60={ma60[-1]:.2f}")
print(f"最新收盘 {closes[-1]:.2f}  偏离MA20 {(closes[-1]/ma20[-1]-1)*100:+.1f}%  偏离MA60 {(closes[-1]/ma60[-1]-1)*100:+.1f}%")
print(f"RSI14={rsi14[-1]:.1f}  DIF={dif[-1]:.3f} DEA={dea[-1]:.3f} MACD柱={macd[-1]:.3f}")

# 每日涨跌幅
print()
print('二、逐日行情(涨跌幅≥3%或量比≥2标★)')
print(f"{'日期':<12}{'开':>8}{'收':>8}{'高':>8}{'低':>8}{'涨跌%':>8}{'振幅%':>7}{'量(手)':>10}{'量比5':>7}  标记")
for i,r in enumerate(rows):
    pct = (r['close']/closes[i-1]-1)*100 if i>0 else 0
    amp = (r['high']-r['low'])/closes[i-1]*100 if i>0 else 0
    vr = r['vol']/(sum(vols[max(0,i-5):i])/5) if i>=5 else None
    mark=[]
    if pct>=9.9: mark.append('涨停级')
    if pct<=-9.9: mark.append('跌停级')
    if vr and vr>=2: mark.append(f'放量{vr:.1f}x')
    if r['vol']==max(vols): mark.append('区间天量')
    flag='★' if (abs(pct)>=3 or (vr and vr>=2)) else ' '
    if flag=='★' or i>=n-5:
        print(f"{r['date']:<12}{r['open']:>8.2f}{r['close']:>8.2f}{r['high']:>8.2f}{r['low']:>8.2f}{pct:>8.2f}{amp:>7.1f}{r['vol']:>10.0f}{(f'{vr:.1f}' if vr else '-'):>7}  {','.join(mark)}")

# 涨跌分布
up=[ (rows[i]['close']/closes[i-1]-1)*100 for i in range(1,n)]
big_up=[x for x in up if x>=5]; big_dn=[x for x in up if x<=-5]
print()
print('三、涨跌分布')
print(f"上涨日 {sum(1 for x in up if x>0)}/{len(up)}  日均涨跌 {sum(up)/len(up):+.2f}%")
print(f"≥+5%日: {len(big_up)}个  ≤-5%日: {len(big_dn)}个  最大单日涨 {max(up):+.2f}%  最大单日跌 {min(up):+.2f}%")

# 阶段划分: 用MA20方向+价格分段
print()
print('四、阶段划分(MA20趋势法)')
seg=[]
cur=None
for i in range(19,n):
    trend='UP' if closes[i]>ma20[i] else 'DOWN'
    if cur and cur[0]==trend:
        cur[2]=i
    else:
        if cur: seg.append(cur)
        cur=[trend,i,i]
if cur: seg.append(cur)
for s in seg:
    a,b=s[1],s[2]
    ret=(closes[b]/closes[a-1]-1)*100 if a>0 else 0
    days=b-a+1
    avg_vol=sum(vols[a:b+1])/days
    print(f"  {rows[a]['date']}→{rows[b]['date']}  {s[0]:<5} {days:>3}天  段内涨幅{ret:>+7.1f}%  日均量{avg_vol:>9.0f}手")

# 量价背离检查: 近20日
print()
print('五、量价特征')
v20=sum(vols[-20:])/20; v20_prior=sum(vols[-40:-20])/20
print(f"近20日均量 {v20:.0f}手 vs 前20日均量 {v20_prior:.0f}手  比值 {v20/v20_prior:.2f}")
print(f"区间累计换手估算: 总量{sum(vols)/10000:.1f}万手")
