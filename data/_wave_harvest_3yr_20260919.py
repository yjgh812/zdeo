# -*- coding: utf-8 -*-
"""A股三年全市场波段特征采集与统计学习（2026-09-19 主人令）
目的: 采集全部股票2023-2026的上涨/下跌波段特征 → 统计学习两类问题:
  Q1 什么样的上涨波段走得远(大波段vs小波段的启动特征差异)
  Q2 什么样的下跌波段衰竭(后续反弹幅度大的下跌 vs 继续跌的下跌)
产出: data/wave_harvest_3yr_20260919.json (分箱统计) + 控制台摘要
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
    pivots=[0]; direction=0; ext=0
    for i in range(1, len(closes)):
        if direction >= 0:
            if closes[i] > closes[ext]: ext=i
            elif closes[i]/closes[ext]-1 <= -pct: pivots.append(ext); direction=-1; ext=i
        else:
            if closes[i] < closes[ext]: ext=i
            elif closes[i]/closes[ext]-1 >= pct: pivots.append(ext); direction=1; ext=i
    pivots.append(ext if ext > pivots[-1] else len(closes)-1)
    return pivots

db = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20230101'").fetchall()]
print(f'全市场 {len(codes)} 只, 流式采集三年波段...')

# 统计容器
up_by_size = defaultdict(lambda: defaultdict(list))   # size_bin -> feat -> vals
down_by_outcome = defaultdict(lambda: defaultdict(list))  # outcome_bin -> feat
up_feat_names = ['pre60_dd','pre20_shrink','start_vr','vp','days','slope']
down_feat_names = ['chg','days','vol_ratio','vp','end_shrink','pre_pos']
n_up = n_down = 0

for ci, code in enumerate(codes):
    if ci % 1000 == 0: print(f'  ...{ci}/{len(codes)}', flush=True)
    h = db.execute("SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? AND date>='20230101' AND date<='20260701' ORDER BY date ASC", (code,)).fetchall()
    rows = [(f3(a), f3(b)) for a, b in h if f3(a) is not None]
    if len(rows) < 300: continue
    closes=[r[0] for r in rows]; vols=[r[1] or 0 for r in rows]
    n=len(closes); avg_v=sum(vols)/n
    if not avg_v: continue
    pv = zigzag(closes, 0.12)
    for k in range(len(pv)-1):
        a,b = pv[k], pv[k+1]
        if b-a < 5: continue
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
        hi250=max(closes[max(0,a-250):a]) if a>10 else closes[a]
        lo250=min(closes[max(0,a-250):a]) if a>10 else closes[a]
        pre_pos=100*(closes[a]-lo250)/(hi250-lo250) if hi250>lo250 else 50
        if chg > 0:
            n_up += 1
            size_bin = '大(>=40%)' if chg>=40 else ('中(15-40%)' if chg>=15 else '小(<15%)')
            up_by_size[size_bin]['pre60_dd'].append(pre_dd)
            up_by_size[size_bin]['pre20_shrink'].append(pre_shrink)
            up_by_size[size_bin]['start_vr'].append(start_vr)
            up_by_size[size_bin]['vp'].append(vp)
            up_by_size[size_bin]['days'].append(b-a)
            up_by_size[size_bin]['slope'].append(chg/(b-a))
        else:
            n_down += 1
            # 后续: 下跌波段结束后下一上涨波段幅度
            nxt = (closes[pv[k+2]]/closes[pv[k+1]]-1)*100 if k+2 < len(pv) else None
            if nxt is None: continue
            outcome = '强反(>=25%)' if nxt>=25 else ('弱反(8-25%)' if nxt>=8 else '续跌/平(<8%)')
            down_by_outcome[outcome]['chg'].append(chg)
            down_by_outcome[outcome]['days'].append(b-a)
            down_by_outcome[outcome]['vol_ratio'].append(vmean/avg_v)
            down_by_outcome[outcome]['vp'].append(vp)
            down_by_outcome[outcome]['end_shrink'].append(vmean/avg_v)
            down_by_outcome[outcome]['pre_pos'].append(pre_pos)
db.close()

def med(x): return round(statistics.median(x), 2) if x else None
OUT = {'n_up': n_up, 'n_down': n_down}
print(f'采集完成: 上涨波段 {n_up} 个, 下跌波段 {n_down} 个')
print()
print('====== Q1: 上涨波段大小分箱的启动特征(中位数) ======')
print(f'{"幅度箱":12}{"n":>7}{"启动前60日跌":>10}{"前20日缩量":>9}{"启动日量比":>9}{"波段量价":>8}{"历时":>6}{"斜率%/日":>8}')
OUT['Q1'] = {}
for bin_ in ('大(>=40%)','中(15-40%)','小(<15%)'):
    d = up_by_size[bin_]; n_ = len(d['days'])
    if n_ < 50: continue
    row = {k: med(d[k]) for k in up_feat_names}
    row['n'] = n_
    OUT['Q1'][bin_] = row
    print(f'{bin_:12}{n_:>7}{row["pre60_dd"]:>9.1f}%{row["pre20_shrink"]:>9.2f}{row["start_vr"]:>9.2f}{row["vp"]:>7.2f}x{row["days"]:>5.0f}日{row["slope"]:>8.3f}')
print()
print('====== Q2: 下跌波段后续结果的衰竭特征(中位数) ======')
print(f'{"后续":12}{"n":>7}{"本段跌幅":>9}{"历时":>6}{"量比":>7}{"跌段量价":>8}{"启动前pos":>9}')
OUT['Q2'] = {}
for bin_ in ('强反(>=25%)','弱反(8-25%)','续跌/平(<8%)'):
    d = down_by_outcome[bin_]; n_ = len(d['days'])
    if n_ < 50: continue
    row = {k: med(d[k]) for k in down_feat_names}; row['n'] = n_
    OUT['Q2'][bin_] = row
    print(f'{bin_:12}{n_:>7}{row["chg"]:>8.1f}%{row["days"]:>5.0f}日{row["vol_ratio"]:>7.2f}{row["vp"]:>7.2f}x{row["pre_pos"]:>8.0f}%')

json.dump(OUT, open(os.path.join(ROOT, 'data', 'wave_harvest_3yr_20260919.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print()
print('[HARVEST-OK] data/wave_harvest_3yr_20260919.json')
