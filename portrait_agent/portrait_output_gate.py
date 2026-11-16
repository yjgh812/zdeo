# -*- coding: utf-8 -*-
"""画像输出层质量门(研究工具, 一次性可复用): 模型打分在跑任何回测评估前, 先检验分数区分度.
动机(20260913 Top20形态诊断实锤): v1.0.0 的 Top20 边界分差中位0.00023(18/40截面完全并列, 最大并列组112只),
Top20跨度/池std=0.81(头部未分离), 唯一分数占比仅34.5% — 分数塌缩使"Top20名单"接近密集区随机抽样.
20260913主人裁决名单宽度放宽到Top500(常量=model_v1.PORTFOLIO_TOPN): 边界检查随宽度迁移(实际使用边界);
跨度比指标随宽度调整(宽名单头部跨度自然更大, 阈值放宽至1.5).
判据(任一触发即FAIL): ①边界并列组中位>5  ②跨度/池std<1.5  ③唯一分数占比<0.5  ④相邻截面重合<40%
用法: python portrait_output_gate.py [--model path] [--nsec 40] [--topn 500]"""
import os, sys
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import lightgbm as lgb
from model_v1 import FEATURES, PUBLISH_GATE, PORTFOLIO_TOPN

HERE = r'D:\zcodesj\portrait_agent'

def gate(model_path=None, nsec=40, topn=None):
    tn = topn or PORTFOLIO_TOPN
    mp = model_path or os.path.join(HERE, 'models', 'v1.0.0_short.txt')
    panel = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v1.pkl'))
    panel['date'] = panel['date'].astype(str)
    recent = sorted(panel['date'].unique())[-nsec:]
    bst = lgb.Booster(model_file=mp)
    rows, tops = [], {}
    for T in recent:
        cs = panel[panel['date'] == T]
        cs = cs[cs['close'].notna() & (~cs['lim_today'])]
        gate_df = cs[~cs['code'].str.startswith(PUBLISH_GATE['excluded_prefixes'])
                     & (cs['close'] >= PUBLISH_GATE['min_close'])].copy()
        if len(gate_df) < tn + 1:
            continue
        gate_df['prob'] = bst.predict(gate_df[FEATURES])
        s = gate_df.sort_values(['prob', 'turnover20'], ascending=False).reset_index(drop=True)
        p = s['prob'].values
        rows.append({'date': T, 'tie': int((p == p[tn - 1]).sum()), 'gap': float(p[tn - 1] - p[tn]),
                     'span_ratio': float((p[0] - p[tn - 1]) / p.std()) if p.std() > 0 else np.nan,
                     'uniq': float(len(np.unique(p)) / len(p))})
        tops[T] = set(s['code'].head(tn))
    df = pd.DataFrame(rows)
    ds = sorted(tops)
    ov = np.median([len(tops[ds[i]] & tops[ds[i+1]]) / tn * 100 for i in range(len(ds) - 1)]) if len(ds) > 1 else np.nan
    m = {'边界并列组中位': df['tie'].median(), '边界分差中位': df['gap'].median(),
         '跨度/池std': df['span_ratio'].median(), '唯一分数占比': df['uniq'].median(),
         '相邻截面重合%': ov}
    fails = []
    if m['边界并列组中位'] > 5: fails.append(f"①边界并列组中位{m['边界并列组中位']:.0f}>5")
    if m['跨度/池std'] < 1.5: fails.append(f"②跨度/池std={m['跨度/池std']:.2f}<1.5(区分度不足)")
    if m['唯一分数占比'] < 0.5: fails.append(f"③唯一分数占比{m['唯一分数占比']:.2f}<0.5")
    if not np.isnan(ov) and ov < 40: fails.append(f"④相邻重合{ov:.0f}%<40%")
    print(f"=== 输出层质量门: {os.path.basename(mp)} (近{len(df)}截面, Top{tn}边界) ===")
    for k, v in m.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    verdict = 'FAIL' if fails else 'PASS'
    print(f"  判定: **{verdict}**" + (' — ' + '; '.join(fails) if fails else ' (分数区分度达标, 可进回测)'))
    return verdict, m

if __name__ == '__main__':
    mp = None
    if '--model' in sys.argv:
        mp = sys.argv[sys.argv.index('--model') + 1]
    nsec = int(sys.argv[sys.argv.index('--nsec') + 1]) if '--nsec' in sys.argv else 40
    tn = int(sys.argv[sys.argv.index('--topn') + 1]) if '--topn' in sys.argv else None
    gate(mp, nsec, tn)
