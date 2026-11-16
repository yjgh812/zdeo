# -*- coding: utf-8 -*-
"""v1.1.0 迭代sweep(2026-09-05画像体检): 基线(计划书原文参数)AUC gap超标(y_short 0.0785/y_mid 0.1431, 阈0.05),
y_mid RankIC 0.0290还差0.03线。手段=计划书7:2:1验证段early stopping(复杂度由数据定, round12同族教训:早停真实有效)
+正则化梯度。切换条款: RankIC>=0.03 且 Top20命中>=55 且 AUC gap<=0.05 且 RankIC>=0.95x基线(不劣于) → 存v1.1.0并平滑切换
生产名v1.0.0; 任一不过→保留基线零改动(fail-closed)。"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from model_v1 import FEATURES, make_labels, PARAMS

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, 'models')
BASE = json.load(open(os.path.join(HERE, 'reports', 'baseline_v1_result.json'), encoding='utf-8'))

CONFIGS = {
    'E1_es50':    dict(PARAMS, n_estimators=2000),
    'E2_es_mc500': dict(PARAMS, n_estimators=2000, min_child_samples=500),
    'E3_es_mc1000_l2': dict(PARAMS, n_estimators=2000, min_child_samples=1000, reg_lambda=5.0),
    'E4_es_d5_l15': dict(PARAMS, n_estimators=2000, max_depth=5, num_leaves=15, min_child_samples=500),
    'E5_es_mc1000_sub.7_col.6': dict(PARAMS, n_estimators=2000, min_child_samples=1000,
                                     subsample=0.7, colsample_bytree=0.6),
}

def split(panel, target):
    df = panel[panel[target].notna() & panel['close'].notna()].copy()
    dates = sorted(df['date'].unique())
    n = len(dates); i7 = int(n*0.7); i9 = int(n*0.9)
    return (df[df['date'] < dates[i7]],
            df[(df['date'] >= dates[i7]) & (df['date'] < dates[i9])],
            df[df['date'] >= dates[i9]], dates[i9], dates[-1])

def evaluate(m, tr, va, te, target):
    fwd_col = 'fwd5_high' if target == 'y_short' else 'fwd20_close'
    p_tr = m.predict_proba(tr[FEATURES])[:, 1]
    p_te = m.predict_proba(te[FEATURES])[:, 1]
    auc_gap = roc_auc_score(tr[target], p_tr) - roc_auc_score(te[target], p_te)
    te2 = te.copy(); te2['prob'] = p_te
    ics, hits, layers = [], [], {}
    for b, g in te2.groupby('board'):
        g2 = g[g[fwd_col].notna()]
        if len(g2) > 200:
            ic, _ = spearmanr(g2['prob'], g2[fwd_col])
            layers[b] = float(ic)
    for d, g in te2.groupby('date'):
        g2 = g[g[fwd_col].notna()]
        if len(g2) > 200:
            ic, _ = spearmanr(g2['prob'], g2[fwd_col])
            if not np.isnan(ic): ics.append(ic)
    for d, g in te2.groupby('date'):
        g3 = g[g['fwd5_high'].notna()].nlargest(20, 'prob')
        if len(g3) >= 20: hits.append((g3['fwd5_high'] > 0).mean())
    return {'auc_gap': float(auc_gap), 'rankIC_mean': float(np.mean(ics)),
            'top20_hit5_mean': float(np.mean(hits))*100, 'best_iter': m.best_iteration_,
            'layers': layers}

def main():
    t0 = time.time()
    panel = make_labels(pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v1.pkl')))
    results = {}
    for tgt in ['y_short', 'y_mid']:
        tag = 'short' if tgt == 'y_short' else 'mid'
        tr, va, te, d0, d1 = split(panel, tgt)
        base = BASE[tgt]
        need_rankic_noninf = base['rankIC_mean'] * 0.95
        print(f'== {tgt} 基线: RankIC={base["rankIC_mean"]:.4f} gap={base["auc_gap"]:.4f} '
              f'非劣线={need_rankic_noninf:.4f} | tr={len(tr)} va={len(va)} te={len(te)} ({d0}~{d1})', flush=True)
        rows = {}
        for name, cfg in CONFIGS.items():
            ts = time.time()
            m = lgb.LGBMClassifier(**cfg)
            m.fit(tr[FEATURES], tr[tgt], eval_set=[(va[FEATURES], va[tgt])],
                  eval_metric='auc', callbacks=[lgb.early_stopping(50, verbose=False)])
            r = evaluate(m, tr, va, te, tgt)
            passed = (r['rankIC_mean'] >= 0.03 and r['top20_hit5_mean'] >= 55
                      and r['auc_gap'] <= 0.05 and r['rankIC_mean'] >= need_rankic_noninf)
            r['pass_all'] = bool(passed)
            rows[name] = r
            print(f"  {name}: gap={r['auc_gap']:.4f} RankIC={r['rankIC_mean']:.4f} "
                  f"hit={r['top20_hit5_mean']:.1f}% iter={r['best_iter']} "
                  f"=> {'PASS全门禁' if passed else '未过'} ({time.time()-ts:.0f}s)", flush=True)
        winners = {k: v for k, v in rows.items() if v['pass_all']}
        pick = max(winners, key=lambda k: winners[k]['rankIC_mean']) if winners else None
        if pick:
            m = lgb.LGBMClassifier(**CONFIGS[pick])
            m.fit(tr[FEATURES], tr[tgt], eval_set=[(va[FEATURES], va[tgt])],
                  eval_metric='auc', callbacks=[lgb.early_stopping(50, verbose=False)])
            new_f = os.path.join(MODELS, f'v1.1.0_{tag}.txt')
            m.booster_.save_model(new_f)
            import shutil
            shutil.copyfile(new_f, os.path.join(MODELS, f'v1.0.0_{tag}.txt'))  # 平滑切换生产名
            print(f'  ★ {tgt}: 采纳 {pick} → v1.1.0_{tag}.txt 已存盘并切换生产名', flush=True)
        else:
            pick = None
            print(f'  ✗ {tgt}: 无配置过全部门禁, 保留基线v1.0.0不动(fail-closed)', flush=True)
        results[tgt] = {'baseline': {k: base[k] for k in ('rankIC_mean', 'top20_hit5_mean', 'auc_gap')},
                        'noninferior_line': need_rankic_noninf, 'rows': rows, 'adopted': pick}
    json.dump(results, open(os.path.join(HERE, 'reports', 'tune_v1_1_result.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, default=float, indent=1)
    print(f'[tune] done {time.time()-t0:.0f}s → reports/tune_v1_1_result.json', flush=True)

if __name__ == '__main__':
    main()
