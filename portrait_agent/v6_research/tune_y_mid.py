# -*- coding: utf-8 -*-
"""y_mid 特征/标签改造扫描(主人2026-09-05'现在就执行').
标签变体: 阈值档15/12/10/8% + 超额口径(减截面中位) + fwd60长周期 + 截面rank前10%
特征增强: ind_mom20(行业等权ret20) + ret20_rel(行业内相对动量) + ret120(长期动量)
全部在缓存面板派生(零重建), 每变体带早停跑KPI, 变体永不触碰生产模型(prod_tag=None)."""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import model_v1
from model_v1 import FEATURES

HERE = os.path.dirname(os.path.abspath(__file__))
T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

panel = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v1.pkl'))
panel = model_v1.make_labels(panel)
log(f'panel {len(panel)} rows')

# ---- 派生: fwd60 / 截面中位超额 / rank标签 / 行业特征 ----
g = panel.sort_values(['code', 'date']).groupby('code')
panel['fwd60_close'] = g['close'].transform(lambda s: s.shift(-60)/s - 1)
panel['fwd20_excess'] = panel['fwd20_close'] - panel.groupby('date')['fwd20_close'].transform('median')
panel['fwd60_excess'] = panel['fwd60_close'] - panel.groupby('date')['fwd60_close'].transform('median')
panel['ret120'] = g['close'].transform(lambda s: s/s.shift(120) - 1)
# 行业动量(申万L1等权)与行业内相对强弱
ind_mom = panel.groupby(['date', 'L1'])['ret20'].transform('mean')
panel['ind_mom20'] = ind_mom
panel['ret20_rel'] = panel['ret20'] - ind_mom
FEATS_V2 = FEATURES + ['ind_mom20', 'ret20_rel', 'ret120']
log('派生列完成')

def mk_binary(col, th):
    y = (panel[col] >= th).astype(float)
    y[panel[col].isna()] = np.nan
    return y

VARIANTS = {}
for th in [0.15, 0.12, 0.10, 0.08]:
    VARIANTS[f'th{int(th*100)}'] = {'col': f'ymid_th{int(th*100)}', 'fwd': 'fwd20_close',
                                    'label': mk_binary('fwd20_close', th)}
VARIANTS['excess10'] = {'col': 'ymid_ex10', 'fwd': 'fwd20_excess',
                        'label': mk_binary('fwd20_excess', 0.10)}
VARIANTS['fwd60_20'] = {'col': 'ymid_f60', 'fwd': 'fwd60_close',
                        'label': mk_binary('fwd60_close', 0.20)}
VARIANTS['fwd60ex15'] = {'col': 'ymid_f60ex', 'fwd': 'fwd60_excess',
                         'label': mk_binary('fwd60_excess', 0.15)}
# 截面rank前10%标签(fwd20)
rk = panel.groupby('date')['fwd20_close'].rank(pct=True)
VARIANTS['rank_top10'] = {'col': 'ymid_rank', 'fwd': 'fwd20_close',
                          'label': (rk >= 0.90).astype(float).where(panel['fwd20_close'].notna())}

results = {}
for name, v in VARIANTS.items():
    panel[v['col']] = v['label']
    for feat_tag, feats in [('F23', FEATURES), ('F26v2', FEATS_V2)]:
        key = f'{name}_{feat_tag}'
        try:
            res, m, te = model_v1.fit_and_eval(panel, target=v['col'], features=feats,
                                               fwd_col_override=v['fwd'], prod_tag=None)
            results[key] = {k: res[k] for k in ['rankIC_mean', 'top20_hit5_mean', 'auc_gap', 'best_iter', 'baseline_pass']}
            results[key]['layers'] = {k: round(x['rankIC'], 3) for k, x in res['layers'].items()}
            star = '✅' if res['baseline_pass'] else '  '
            log(f"{star} {key:<16} RankIC={res['rankIC_mean']:+.4f} 命中={res['top20_hit5_mean']:.1f}% gap={res['auc_gap']:+.4f} 树={res['best_iter']}")
        except Exception as e:
            results[key] = {'error': str(e)[:120]}
            log(f"❌ {key}: {e}")
    del panel[v['col']]

json.dump(results, open(os.path.join(HERE, 'reports', 'tune_y_mid_result.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, default=float, indent=1)
best = max((k for k in results if 'rankIC_mean' in results[k]), key=lambda k: results[k]['rankIC_mean'])
log(f'最优变体: {best} => {results[best]}')
log('结果已存 reports/tune_y_mid_result.json (变体扫描不触碰生产模型; 上线须过双门槛流程)')
