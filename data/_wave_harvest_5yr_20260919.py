# -*- coding: utf-8 -*-
"""A股5年(2021-09-01~2026-09-18)全市场波段统计——画像系统波段规范v2(完整牛熊周期口径)
统计:
  Q1 上涨波段大小分箱的启动前特征(5年全周期+分年)
  Q2 下跌波段衰竭特征(5年口径)
  Q3 分年度对比(2022熊/2024-25牛的启动特征差异=regime交互)
产物: data/wave_harvest_5yr_20260919.json
"""
import sqlite3, json, statistics, sys, os
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"

def f3(x):
    try: return float(x)
    except Exception: return None

def zigzag(closes, pct=0.12):
    if len(closes) < 10: return [0, len(closes)-1]
    piv=[0]; d=0; ext=0
    for i in range(1, len(closes)):
        if d >= 0:
            if closes[i] > closes[ext]: ext=i
            elif closes[i]/closes[ext]-1 <= -pct: piv.append(ext); d=-1; ext=i
        else:
            if closes[i] < closes[ext]: ext=i
            elif closes[i]/closes[ext]-1 >= pct: piv.append(ext); d=1; ext=i
    piv.append(ext if ext > piv[-1] else len(closes)-1)
    return piv

db = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20210901'").fetchall()]
print(f'全市场 {len(codes)} 只, 5年波段采集...')

up_all = defaultdict(lambda: defaultdict(list))    # size_bin -> feats
up_by_year = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # year -> size_bin -> feats
down_all = defaultdict(lambda: defaultdict(list))
n_up = n_down = 0
FEATS = ['pre60_dd','pre20_shrink','start_vr','vp','days']

for ci, code in enumerate(codes):
    if ci % 800 == 0: print(f'  ...{ci}/{len(codes)}', flush=True)
    h = db.execute("SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? AND date>='20210901' AND date<='20260918' ORDER BY date ASC", (code,)).fetchall()
    rows=[(d, f3(c), f3(v)) for d, c, v in h if f3(c) is not None]
    if len(rows) < 300: continue
    dates=[r[0] for r in rows]; closes=[r[1] for r in rows]; vols=[r[2] or 0 for r in rows]
    n=len(closes); avg_v=sum(vols)/n
    if not avg_v: continue
    pv = zigzag(closes, 0.12)
    for k in range(len(pv)-1):
        a,b = pv[k], pv[k+1]
        if b-a < 10: continue
        chg=(closes[b]/closes[a]-1)*100
        segv=vols[a:b+1]; vmean=sum(segv)/len(segv)
        up_v=dn_v=0.0; up_n=dn_n=0
        for j in range(a+1,b+1):
            d=closes[j]-closes[j-1]
            if d>0: up_v+=vols[j]; up_n+=1
            elif d<0: dn_v+=vols[j]; dn_n+=1
        vp=(up_v/up_n)/(dn_v/dn_n) if up_n and dn_n and dn_v else 1.0
        pre60=closes[max(0,a-60):a]
        pre_dd=(min(pre60)/max(pre60)-1)*100 if pre60 else 0
        v20pre=sum(vols[max(0,a-20):a])/20 if a>=20 else avg_v
        pre_shrink=v20pre/avg_v if avg_v else 1
        start_vr=vols[a]/v20pre if v20pre else 1
        if chg > 0:
            n_up += 1
            size_bin = '大(>=40%)' if chg>=40 else ('中(15-40%)' if chg>=15 else '小(<15%)')
            yr = dates[a][:4]
            for tgt, store in ((up_all, None), ):
                pass
            vals = {'pre60_dd': pre_dd, 'pre20_shrink': pre_shrink, 'start_vr': start_vr, 'vp': vp, 'days': b-a}
            for kk, vv in vals.items():
                up_all[size_bin][kk].append(vv)
                up_by_year[yr][size_bin][kk].append(vv)
        else:
            n_down += 1
            nxt = (closes[pv[k+2]]/closes[pv[k+1]]-1)*100 if k+2 < len(pv) else None
            if nxt is None: continue
            outcome = '强反(>=25%)' if nxt>=25 else ('弱反(8-25%)' if nxt>=8 else '续跌/平(<8%)')
            vals = {'chg': chg, 'days': b-a, 'vol_ratio': vmean/avg_v, 'vp': vp, 'pre_dd': pre_dd}
            for kk, vv in vals.items():
                down_all[outcome][kk].append(vv)
db.close()

def med(x): return round(statistics.median(x), 2) if x else None
OUT = {'n_up': n_up, 'n_down': n_down}
print(f'采集完成: 上涨波段 {n_up}, 下跌波段 {n_down}')
print()
print('====== Q1: 上涨波段启动前特征(5年全周期) ======')
print(f'{"幅度箱":12}{"n":>7}{"前60日跌":>9}{"前20缩量":>8}{"启动量比":>8}{"波段量价":>8}{"历时":>6}')
OUT['Q1'] = {}
for b in ('大(>=40%)','中(15-40%)','小(<15%)'):
    d = up_all[b]; n_ = len(d['days'])
    if n_ < 100: continue
    row = {k: med(d[k]) for k in FEATS}; row['n'] = n_
    OUT['Q1'][b] = row
    print(f'{b:12}{n_:>7}{row["pre60_dd"]:>8.1f}%{row["pre20_shrink"]:>8.2f}{row["start_vr"]:>8.2f}{row["vp"]:>7.2f}x{row["days"]:>5.0f}日')
print()
print('====== Q2: 下跌波段衰竭特征(5年) ======')
print(f'{"后续":12}{"n":>7}{"本段跌":>8}{"历时":>6}{"量比":>7}{"跌段量价":>8}{"启动前跌":>8}')
OUT['Q2'] = {}
for b in ('强反(>=25%)','弱反(8-25%)','续跌/平(<8%)'):
    d = down_all[b]; n_ = len(d['days'])
    if n_ < 100: continue
    row = {k: med(d[k]) for k in d}; row['n'] = n_
    OUT['Q2'][b] = row
    print(f'{b:12}{n_:>7}{row["chg"]:>7.1f}%{row["days"]:>5.0f}日{row["vol_ratio"]:>7.2f}{row["vp"]:>7.2f}x{row["pre_dd"]:>7.1f}%')
print()
print('====== Q3: 分年度 大波段(>=40%)启动前特征(regime交互) ======')
print(f'{"年份":6}{"n":>6}{"前60日跌":>9}{"前20缩量":>8}{"启动量比":>8}')
OUT['Q3'] = {}
for yr in sorted(up_by_year):
    d = up_by_year[yr].get('大(>=40%)')
    if not d or len(d['days']) < 100: continue
    row = {k: med(d[k]) for k in FEATS}; row['n'] = len(d['days'])
    OUT['Q3'][yr] = row
    print(f'{yr:6}{row["n"]:>6}{row["pre60_dd"]:>8.1f}%{row["pre20_shrink"]:>8.2f}{row["start_vr"]:>8.2f}')

json.dump(OUT, open(os.path.join(ROOT, 'data', 'wave_harvest_5yr_20260919.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print()
print('[HARVEST5Y-OK] data/wave_harvest_5yr_20260919.json')
