# -*- coding: utf-8 -*-
"""L1-LightGBM A股上涨预测基线(计划书§2.2步骤4).
标签(计划书原文口径): y_short=未来5日最高价涨幅≥8%; y_mid=未来20日收盘涨幅≥15%.
切分: 7:2:1时间序列. 参数: v1.1.0采纳=计划书原文参数+early stopping+正则收敛
(2026-09-05体检sweep, 证据=reports/tune_v1_1_result.json: 基线300树AUC gap超标,
es后y_short 9~11树即收敛且RankIC反升0.246→0.258/gap 0.034过阈; y_mid全部第1树早停且RankIC转负=无可泛化信号, 保留预置基准).
KPI(计划书§2.2步骤4校验, 历史口径): 样本外RankIC≥0.03 / Top20五日命中率≥55% / AUC差≤0.05 / 分层无显著偏差.
发布/评价层名单宽度=PORTFOLIO_TOPN(2026-09-13主人裁决Top500); KPI内Top20为训练期历史合同(已被口径v2判据取代)."""
import os, sys, json, time
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
MODELS = os.path.join(HERE, 'models'); os.makedirs(MODELS, exist_ok=True)
from feature_engine import FEATURES

# 发布闸(单一真源: portrait_infer名单发布与shadow_gate评价共用; 宪法§0剔科创板+超低价面)
# 作用于发布/评价层, 不改训练面(模型见全池, 产品边界在输出端执行)
PUBLISH_GATE = {'excluded_prefixes': ('688', '689'), 'min_close': 4.0}

# 名单宽度(单一真源, 2026-09-13 主人裁决"放宽到Top500"): 池~3170只的Top500≈前16%=候选池/排序层语义,
# 非买入名单; 大N同时缓解Top20形态诊断实锤的边界任意性(第20/21名分差中位0.00023/并列组最大112只)
PORTFOLIO_TOPN = 500

PARAMS = dict(objective='binary', max_depth=5, num_leaves=15, learning_rate=0.05,
              subsample=0.8, colsample_bytree=0.8, min_child_samples=500,
              scale_pos_weight=2, n_estimators=2000, verbose=-1, n_jobs=4)
ES_ROUNDS = 50   # 早停轮数: 在7:2:1的验证段上自适应定树数(计划书切分内, 测试段不参与)

def make_labels(panel):
    panel = panel.copy()
    panel['y_short'] = (panel['fwd5_high'] >= 0.08).astype(int)
    panel.loc[panel['fwd5_high'].isna(), 'y_short'] = np.nan
    panel['y_mid'] = (panel['fwd20_close'] >= 0.15).astype(int)
    panel.loc[panel['fwd20_close'].isna(), 'y_mid'] = np.nan
    return panel

def fit_and_eval(panel, target='y_mid', topn=20, features=None, fwd_col_override=None, prod_tag=None):
    """7:2:1 时间序列切分(计划书口径) + KPI.
    features: 变体特征集覆盖(默认FEATURES); fwd_col_override: RankIC对照的未来收益列(标签变体扫描用).
    prod_tag: 生产落盘开关('short'/'mid'=KPI过或生产空缺时写v1.0.0_{tag}.txt; None=纯评估不落盘, 变体扫描安全默认)."""
    feats = features if features is not None else FEATURES
    df = panel[panel[target].notna() & panel['close'].notna()].copy()  # 科创板基本面NaN参与(LightGBM原生)
    dates = sorted(df['date'].unique())
    n = len(dates); i7 = int(n*0.7); i9 = int(n*0.9)
    tr, va, te = df[df['date'] < dates[i7]], df[(df['date'] >= dates[i7]) & (df['date'] < dates[i9])], df[df['date'] >= dates[i9]]
    m = lgb.LGBMClassifier(**PARAMS)
    m.fit(tr[feats].to_numpy(dtype=np.float32), tr[target], eval_set=[(va[feats].to_numpy(dtype=np.float32), va[target])],
          eval_metric='auc', callbacks=[lgb.early_stopping(ES_ROUNDS, verbose=False)])
    from sklearn.metrics import roc_auc_score
    def _pred(frame, chunk=400_000):
        # 分块+float32: 根治 (n,28) float64 全量转换 607MiB 分配崩(2026-09-05 实测)
        out = np.empty(len(frame), dtype=np.float32)
        for i in range(0, len(frame), chunk):
            out[i:i+chunk] = m.predict_proba(frame[feats].iloc[i:i+chunk].to_numpy(dtype=np.float32))[:, 1]
        return out
    auc_tr = roc_auc_score(tr[target], _pred(tr))
    auc_te = roc_auc_score(te[target], _pred(te))
    te = te.copy(); te['prob'] = _pred(te)
    # RankIC: 每个测试截面 spearman(prob, fwd)
    fwd_col = fwd_col_override or ('fwd5_high' if target == 'y_short' else 'fwd20_close')
    ics = []
    for d, g in te.groupby('date'):
        g2 = g[g[fwd_col].notna()]
        if len(g2) > 200:
            ic, _ = spearmanr(g2['prob'], g2[fwd_col])
            if not np.isnan(ic):
                ics.append(ic)
    # Top20五日命中率(KPI历史合同, 计划书§2.2原文口径): 已被20260913口径v2判据取代(hit绝对门槛废除,
    # 因其只衡量市场方向) — 保留供历史对照, 不作为验收门; 输出宽度由PORTFOLIO_TOPN管理(发布/评价层用)
    hits = []
    for d, g in te.groupby('date'):
        g2 = g[g['fwd5_high'].notna()].nlargest(topn, 'prob')
        if len(g2) >= topn:
            hits.append((g2['fwd5_high'] > 0).mean())
    # 分层
    layers = {}
    for b, g in te.groupby('board'):
        g2 = g[g[fwd_col].notna()]
        if len(g2) > 200:
            ic, _ = spearmanr(g2['prob'], g2[fwd_col])
            layers[b] = {'n': len(g2), 'rankIC': float(ic)}
    res = {'target': target, 'n_train': len(tr), 'n_test': len(te),
           'test_dates': f"{dates[i9]}~{dates[-1]}",
           'auc_train': float(auc_tr), 'auc_test': float(auc_te), 'auc_gap': float(auc_tr-auc_te),
           'rankIC_mean': float(np.mean(ics)), 'rankIC_n_sections': len(ics),
           'top20_hit5_mean': float(np.mean(hits))*100, 'top20_n_sections': len(hits),
           'layers': layers,
           'feature_importance': dict(sorted(zip(feats, m.feature_importances_.astype(float)),
                                             key=lambda x: -x[1])[:10])}
    ok = (res['rankIC_mean'] >= 0.03 and res['top20_hit5_mean'] >= 55 and res['auc_gap'] <= 0.05)
    res['baseline_pass'] = bool(ok)
    res['best_iter'] = int(m.best_iteration_ or 0)
    # 模型落盘: 仅当显式指定 prod_tag 且(全过或生产空缺)时才写, 变体扫描(prod_tag=None)绝不触碰生产
    # (计划书§2.2步骤4失败回滚=预置基准; 修复原无条件覆盖缺陷, 防fail模型污染生产)
    if prod_tag:
        prod_f = os.path.join(MODELS, f'v1.0.0_{prod_tag}.txt')
        if ok or not os.path.exists(prod_f):
            m.booster_.save_model(prod_f)
        else:
            res['prod_kept'] = 'KPI未过, 保留现有生产模型(预置基准)'
    return res, m, te

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    panel = pd.read_pickle(os.path.join(CACHE, 'panel_v1.pkl'))
    panel = make_labels(panel)
    out = {}
    for tgt in ['y_short', 'y_mid']:
        res, m, te = fit_and_eval(panel, tgt)
        out[tgt] = res
        print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    json.dump(out, open(os.path.join(HERE, 'reports', 'baseline_v1_result.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, default=float, indent=1)
    print('saved baseline_v1_result.json')
