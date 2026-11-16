# -*- coding: utf-8 -*-
"""Top20 输出形态诊断(只读): 检验"固定Top20"设定的问题所在.
维度: ①Top20边界分数与并列组大小(入选任意性) ②相邻截面重合度(信号稳定性/交易成本) ③Top20分数区间 vs 全池分布(区分度) ④分数阈值形态下名单宽度波动.
数据: panel_v1(全池特征) + 模型打分(内存) 或 reports/portrait_scores_*.csv(存档分数)."""
import os, sys, glob, json
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import lightgbm as lgb
from model_v1 import FEATURES, PUBLISH_GATE

panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
panel['date'] = panel['date'].astype(str)
recent = sorted(panel['date'].unique())[-40:]
bst = lgb.Booster(model_file=r'D:\zcodesj\portrait_agent\models\v1.0.0_short.txt')

rows = []
tops = {}
for T in recent:
    cs = panel[panel['date'] == T]
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    gate = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
    gate['prob'] = bst.predict(gate[FEATURES])
    s = gate.sort_values(['prob', 'turnover20'], ascending=False).reset_index(drop=True)
    p = s['prob'].values
    n = len(s)
    # ① Top20边界: 第20与第21名分数差, 及边界并列组大小
    b20, b21 = p[19], p[20]
    ties = int((p == b20).sum())
    # ③ 区分度: Top20分数跨度 vs 全池分布
    rows.append({'date': T, 'n': n, 'cut20': b20, 'cut21': b21, 'gap': b20 - b21,
                 'tie_at_cut': ties, 'top20_span': p[0] - p[19],
                 'pool_std': float(p.std()), 'pool_p99': float(np.percentile(p, 99)),
                 'pool_p90': float(np.percentile(p, 90)),
                 'unique_ratio': len(np.unique(p)) / n})
    tops[T] = set(s['code'].head(20))

df = pd.DataFrame(rows)
print('=== ① Top20 边界与并列(近40截面) ===')
print(f"边界分数(第20名) 均值={df['cut20'].mean():.4f} 范围[{df['cut20'].min():.4f},{df['cut20'].max():.4f}]")
print(f"第20-21名分差 中位={df['gap'].median():.5f} 最小={df['gap'].min():.5f} 为0的截面数={(df['gap']==0).sum()}")
print(f"边界并列组大小(含第20名同分) 中位={df['tie_at_cut'].median():.1f} 最大={df['tie_at_cut'].max()}")
print(f"Top20分数跨度 中位={df['top20_span'].median():.4f} vs 池std中位={df['pool_std'].median():.4f} (跨度/std={df['top20_span'].median()/df['pool_std'].median():.2f})")
print(f"唯一分数占比 中位={df['unique_ratio'].median():.3f} (1.0=无并列)")
print()
print('=== ② 相邻截面Top20重合度(信号稳定性) ===')
ds = sorted(tops.keys())
ov = [len(tops[ds[i]] & tops[ds[i+1]]) / 20 * 100 for i in range(len(ds) - 1)]
print(f"相邻重合 中位={np.median(ov):.0f}% 最小={min(ov):.0f}% 最大={max(ov):.0f}%")
print(f"隔5截面重合 中位={np.median([len(tops[ds[i]] & tops[ds[i+5]])/20*100 for i in range(len(ds)-5)]):.0f}%")
print()
print('=== ④ 阈值形态: 分数阈值对应的名单宽度波动(近40截面) ===')
w80, w90, w95 = [], [], []
for T in recent:
    cs = panel[panel['date'] == T]
    cs = cs[cs['close'].notna() & (~cs['lim_today'])]
    gate = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
               & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
    if len(gate) < 100: continue
    gate['prob'] = bst.predict(gate[FEATURES])
    p = gate['prob'].values
    w80.append(int((p >= np.percentile(p, 80)).sum()))
    w90.append(int((p >= np.percentile(p, 90)).sum()))
    w95.append(int((p >= np.percentile(p, 95)).sum()))
for arr, name in ((w80, 'p80阈值'), (w90, 'p90阈值'), (w95, 'p95阈值')):
    print(f"{name}: 宽度 中位={int(np.median(arr))} 最小={min(arr)} 最大={max(arr)} 波动={max(arr)-min(arr)}")
print("说明: 分位阈值宽度=池×10%/5%, 波动仅来自池规模; 若改用绝对分数阈值, 塌缩下宽度将剧烈漂移")

print('\n=== 结论要点 ===')
print(f"1) 边界任意性: 并列组中位{df['tie_at_cut'].median():.0f}只 — 第20名与第21名分差中位{df['gap'].median():.5f}, {(df['gap']<1e-9).sum()}/{len(df)}截面分差≈0(完全并列)")
print(f"2) 信号稳定性: 相邻截面Top20重合仅{np.median(ov):.0f}% — 名单日换手约{100-np.median(ov):.0f}%")
print(f"3) 区分度: Top20跨度/池std = {df['top20_span'].median()/df['pool_std'].median():.2f} (>2=头部明显分离)")
json.dump(rows, open(r'D:\zcodesj\data\_top20_form_diag_20260913.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=float)
