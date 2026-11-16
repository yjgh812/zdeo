# -*- coding: utf-8 -*-
"""构造空间验证 结果打印(从已算JSON读回, 不重跑)"""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd

recs = json.load(open(r'D:\zcodesj\data\_portrait_construct_verify_20260913.json', encoding='utf-8'))
df = pd.DataFrame(recs)
HOLDS = (3, 5, 10, 20)
TOPS = (5, 10, 20, 30, 50)
summary = {}
for seg in ('valid', 'test'):
    d = df[df['seg'] == seg]
    if not len(d):
        continue
    print(f"\n===== {seg} 段 n={len(d)} 截面 =====")
    recs_d = d.to_dict('records')
    base = {}
    for h in HOLDS:
        arr = np.array([r['base'][str(h)] if r['base'][str(h)] is not None else np.nan for r in recs_d], dtype=float)
        base[h] = arr
        print(f"池基准 h{h}: 均值={np.nanmean(arr):+.2f}%  中位={np.nanmedian(arr):+.2f}%")
    for tag in ('short', 'mid'):
        print(f"\n-- {tag} TopN × 持有期 超额中位(pp) --")
        print('       ' + ''.join(f"  h{h:<6d}" for h in HOLDS))
        summary[(seg, tag)] = {}
        for k in TOPS:
            row = []
            for h in HOLDS:
                col = np.array([r[f'{tag}_top{k}_h{h}'] if r[f'{tag}_top{k}_h{h}'] is not None else np.nan for r in recs_d], dtype=float)
                ex = col - base[h]
                ex = ex[~np.isnan(ex)]
                row.append(np.median(ex) if len(ex) else np.nan)
                summary[(seg, tag)][(k, h)] = (float(np.median(ex)) if len(ex) else None, float((ex > 0).mean() * 100) if len(ex) else None, len(ex))
            print(f"Top{k:<4d}" + ''.join(f"  {v:+7.2f}" if not np.isnan(v) else "     -  " for v in row))
        print(f"-- {tag} 十分位超额中位(pp) --")
        for h in (5, 20):
            ds = []
            for dd in range(1, 11):
                col = np.array([r[f'{tag}_dec_h{h}'][str(dd)] if r[f'{tag}_dec_h{h}'][str(dd)] is not None else np.nan for r in recs_d], dtype=float)
                ex = col - base[h]
                ex = ex[~np.isnan(ex)]
                ds.append(f"D{dd}={np.median(ex):+.2f}" if len(ex) else f"D{dd}=  - ")
            print(f"  h{h}: " + ' '.join(ds))

# 最优解判定: 全部 (seg,tag,TopN,hold) 组合中 超额中位最高且正占比>50%
best = []
for (seg, tag), d2 in summary.items():
    for (k, h), (med, pos, n) in d2.items():
        if med is not None:
            best.append((med, pos, seg, tag, k, h, n))
best.sort(reverse=True)
print("\n===== 全组合排序(按超额中位降序 Top8) =====")
for med, pos, seg, tag, k, h, n in best[:8]:
    print(f"{seg} {tag} Top{k} h{h}: 超额中位={med:+.2f}pp 正占比={pos:.0f}% n={n}")
print("\n===== 反向检验(最低分位D10 - 最高分位D1, h20) =====")
for seg in ('valid', 'test'):
    d = df[df['seg'] == seg]
    if not len(d):
        continue
    recs_d = d.to_dict('records')
    b20 = np.array([r['base']['20'] if r['base']['20'] is not None else np.nan for r in recs_d], dtype=float)
    for tag in ('short', 'mid'):
        d10 = np.array([r[f'{tag}_dec_h20']['10'] if r[f'{tag}_dec_h20']['10'] is not None else np.nan for r in recs_d], dtype=float)
        d1 = np.array([r[f'{tag}_dec_h20']['1'] if r[f'{tag}_dec_h20']['1'] is not None else np.nan for r in recs_d], dtype=float)
        delta = (d10 - b20) - (d1 - b20)
        delta = delta[~np.isnan(delta)]
        print(f"{seg} {tag}: D10超额-D1超额 中位={np.median(delta):+.2f}pp (正值=最低分位跑赢最高分位=信号反向)")
