# -*- coding: utf-8 -*-
"""切分点敏感性重算(从已落盘construct_verify JSON读回): 真实训练切分 i9=20260430 vs 我用的20260512,
重分段看结论稳健性. 同时给出"真实test段"(20260430起)全矩阵."""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np

recs = json.load(open(r'D:\zcodesj\data\_portrait_construct_verify_20260913.json', encoding='utf-8'))
HOLDS, TOPS = (3, 5, 10, 20), (5, 10, 20, 30, 50)

for TEST_START, label in (('20260430', '真实切分(i9=20260430)'), ('20260512', '我原用(i9位移5日)')):
    d = [r for r in recs if r['date'] >= TEST_START]
    # 只用 h20 完整的截面(与构造验证同口径)
    d = [r for r in d if r['base']['20'] is not None]
    if not d:
        print(f'{label}: 无样本'); continue
    base = {h: np.array([r['base'][str(h)] if r['base'][str(h)] is not None else np.nan for r in d], dtype=float) for h in HOLDS}
    print(f"\n===== test段[{label}] n={len(d)} 截面 ({d[0]['date']}~{d[-1]['date']}) =====")
    for tag in ('short', 'mid'):
        print(f"-- {tag} --")
        for k in TOPS:
            row = []
            for h in HOLDS:
                col = np.array([r[f'{tag}_top{k}_h{h}'] if r[f'{tag}_top{k}_h{h}'] is not None else np.nan for r in d], dtype=float)
                ex = col - base[h]
                ex = ex[~np.isnan(ex)]
                row.append(float(np.median(ex)) if len(ex) else np.nan)
            line = ' '.join(f"h{h}={v:+6.2f}" if not np.isnan(v) else f"h{h}=   -  " for h, v in zip(HOLDS, row))
            flag = '  ← 正' if any((not np.isnan(v)) and v > 0 for v in row) else ''
            print(f"  Top{k:<3d} {line}{flag}")
    # 十分位反向检验
    b20 = base[20]
    for tag in ('short', 'mid'):
        d10 = np.array([r[f'{tag}_dec_h20']['10'] if r[f'{tag}_dec_h20']['10'] is not None else np.nan for r in d], dtype=float)
        d1 = np.array([r[f'{tag}_dec_h20']['1'] if r[f'{tag}_dec_h20']['1'] is not None else np.nan for r in d], dtype=float)
        delta = (d10 - b20) - (d1 - b20)
        delta = delta[~np.isnan(delta)]
        print(f"  反向检验 {tag}: D10-D1 中位={np.median(delta):+.2f}pp (n={len(delta)})")

# 全构造正数统计(两切分对比)
print("\n===== 关键裁决: 两个切分点下 test 段正超额构造数 =====")
for TEST_START, label in (('20260430', '真实切分'), ('20260512', '原用切分')):
    d = [r for r in recs if r['date'] >= TEST_START and r['base']['20'] is not None]
    pos_cnt = 0
    tot = 0
    for tag in ('short', 'mid'):
        for k in TOPS:
            for h in HOLDS:
                col = np.array([r[f'{tag}_top{k}_h{h}'] if r[f'{tag}_top{k}_h{h}'] is not None else np.nan for r in d], dtype=float)
                bc = np.array([r['base'][str(h)] if r['base'][str(h)] is not None else np.nan for r in d], dtype=float)
                ex = col - bc
                ex = ex[~np.isnan(ex)]
                if len(ex):
                    tot += 1
                    if np.median(ex) > 0:
                        pos_cnt += 1
    print(f"{label}: {tot} 个(模型×构造)组合中 超额中位>0 的 = {pos_cnt}")
