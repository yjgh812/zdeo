# -*- coding: utf-8 -*-
"""画像模型v2: 2013+大数据量重训 + 相对强弱标签 + maxstreak250(2026-09-05 主人指令).

三处根因修复(vs v1):
1. 数据量: warm 20130101→(约14年 vs v1的3.7年), 面板~900万行
2. 标签: 绝对阈值(fwd5_high>=8%最高价/fwd20_close>=15%) → 截面相对强弱top10%
   - 最高价口径收盘不可实现(v1实测年化-60%) → 全部收盘口径
   - 相对标签 regime 鲁棒(牛熊正样本率恒定10%), 直接优化排序质量(与TopN用法对齐)
3. 特征: maxstreak(面板起点累计,train/serve偏斜) → maxstreak250(250日滚动,无起点依赖)

训练: 7:2:1 时间切分+早停(同v1超参,不调参). 模型存 v2.0.0_{tag}.txt(不动v1生产).
回测: 与v1完全同框架(非重叠窗口/收盘口径/Top20等权/全截面等权基准), 分full/train/test.
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, 'models')
REPORTS = os.path.join(HERE, 'reports'); os.makedirs(REPORTS, exist_ok=True)
CACHE = os.path.join(HERE, 'cache')
from feature_engine import FEATURES

FEATURES_V2 = [f if f != 'maxstreak' else 'maxstreak250' for f in FEATURES]

PARAMS = dict(objective='binary', max_depth=5, num_leaves=15, learning_rate=0.05,
              subsample=0.8, colsample_bytree=0.8, min_child_samples=500,
              scale_pos_weight=2, n_estimators=2000, verbose=-1, n_jobs=4)
ES_ROUNDS = 50
TOPN = 20
MIN_SECTION = 500
QUANTILE = 0.90   # 相对强弱标签: 截面前10%

T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

def add_features_labels(panel):
    """maxstreak250 + fwd5_close + 相对强弱标签."""
    panel = panel.sort_values(['code', 'date']).reset_index(drop=True)
    log(f'fwd5_close 计算 n={len(panel)}')
    panel['fwd5_close'] = panel.groupby('code')['close'].transform(lambda s: s.shift(-5) / s - 1)
    log('maxstreak250 计算(连板→250日滚动最大)')
    g = panel.groupby('code')['lim_today']
    grp_id = (g != g.shift()).cumsum()
    # 连续True段内cumsum=1,2,3...; False段=0
    cur = panel.groupby(['code', grp_id], sort=False)['lim_today'].cumsum().astype(float)
    panel['maxstreak250'] = cur.groupby(panel['code'], sort=False).transform(
        lambda s: s.rolling(250, min_periods=1).max())
    log('相对强弱标签(截面分位top10%)')
    for fwd, y in [('fwd5_close', 'y_short_v2'), ('fwd20_close', 'y_mid_v2')]:
        pct = panel.groupby('date')[fwd].rank(pct=True)
        panel[y] = (pct >= QUANTILE).astype(float)
        panel.loc[panel[fwd].isna(), y] = np.nan
    return panel

def train(panel, target='y_short_v2', feats=None):
    feats = feats or FEATURES_V2
    df = panel[panel[target].notna() & panel['close'].notna()].copy()
    dates = sorted(df['date'].unique())
    n = len(dates); i7, i9 = int(n*0.7), int(n*0.9)
    tr = df[df['date'] < dates[i7]]
    va = df[(df['date'] >= dates[i7]) & (df['date'] < dates[i9])]
    te = df[df['date'] >= dates[i9]]
    log(f'{target}: train={len(tr)} va={len(va)} test={len(te)}; '
        f'{dates[0]}~{dates[-1]}; test段={dates[i9]}~')
    m = lgb.LGBMClassifier(**PARAMS)
    m.fit(tr[feats].to_numpy(dtype=np.float32), tr[target],
          eval_set=[(va[feats].to_numpy(dtype=np.float32), va[target])],
          eval_metric='auc', callbacks=[lgb.early_stopping(ES_ROUNDS, verbose=False)])
    def _pred(frame, chunk=400_000):
        out = np.empty(len(frame), dtype=np.float32)
        for i in range(0, len(frame), chunk):
            out[i:i+chunk] = m.predict_proba(frame[feats].iloc[i:i+chunk].to_numpy(dtype=np.float32))[:, 1]
        return out
    auc_tr = roc_auc_score(tr[target], _pred(tr))
    te = te.copy(); te['prob'] = _pred(te)
    auc_te = roc_auc_score(te[target], te['prob'])
    fwd_col = 'fwd5_close' if target == 'y_short_v2' else 'fwd20_close'
    ics = []
    for d, gsec in te.groupby('date'):
        g2 = gsec[gsec[fwd_col].notna()]
        if len(g2) > 200:
            ic, _ = spearmanr(g2['prob'], g2[fwd_col])
            if not np.isnan(ic): ics.append(ic)
    hits = []
    for d, gsec in te.groupby('date'):
        g2 = gsec[gsec['fwd5_close'].notna()].nlargest(TOPN, 'prob')
        if len(g2) >= TOPN:
            hits.append((g2['fwd5_close'] > 0).mean())
    kpi = {'target': target, 'auc_train': round(auc_tr, 4), 'auc_test': round(auc_te, 4),
           'auc_gap': round(auc_tr-auc_te, 4), 'rankIC_mean': round(float(np.mean(ics)), 4),
           'rankIC_n': len(ics), 'top20_hit5_close': round(float(np.mean(hits))*100, 2),
           'best_iter': int(m.best_iteration_ or 0),
           'feat_top10': dict(sorted(zip(feats, m.feature_importances_.astype(float)),
                                     key=lambda x: -x[1])[:10])}
    log(f'KPI: {json.dumps({k: v for k, v in kpi.items() if k != "feat_top10"}, ensure_ascii=False)}')
    return m, kpi, dates, i7

def backtest(panel, model, feats, tag='', hold=5, segment='full', split_dates=None, i7=0):
    fwd_col = 'fwd5_close' if hold == 5 else 'fwd20_close'
    dates = sorted(panel['date'].unique())
    n = len(dates)
    if segment == 'train':
        dates = dates[:i7]
    elif segment == 'test':
        dates = dates[i7:]
    rows = []
    nav_p = nav_b = 1.0
    groups = {d: g for d, g in panel.groupby('date')}   # 预分组(消除全表扫描)
    for ws in dates[::hold]:
        cs = groups.get(ws)
        if cs is None: continue
        cs = cs[cs[fwd_col].notna() & (~cs['lim_today'])]
        if len(cs) < MIN_SECTION: continue
        cs['prob'] = model.predict(cs[feats].to_numpy(dtype=np.float32))
        top = cs.nlargest(TOPN, 'prob')
        if len(top) < TOPN: continue
        pr = float(top[fwd_col].astype(float).mean())
        br = float(cs[fwd_col].astype(float).mean())
        nav_p *= (1+pr); nav_b *= (1+br)
        rows.append({'date': ws, 'port_ret': round(pr*100, 3), 'base_ret': round(br*100, 3),
                     'excess': round((pr-br)*100, 3), 'nav_p': round(nav_p, 4),
                     'nav_b': round(nav_b, 4), 'win': int(pr > br)})
    df = pd.DataFrame(rows)
    if df.empty: return {'error': 'empty'}
    rp = df['port_ret'].values/100; rb = df['base_ret'].values/100
    years = len(df)*hold/243
    ann_p = nav_p**(1/years)-1 if years > 0 else 0
    ann_b = nav_b**(1/years)-1 if years > 0 else 0
    nav_arr = df['nav_p'].values
    mdd = float((nav_arr/np.maximum.accumulate(nav_arr)-1).min())   # 正确口径: 负百分比
    sharpe = (rp.mean()-0.02*hold/243)/rp.std()*np.sqrt(243/hold) if rp.std() > 0 else 0
    return {'tag': tag, 'segment': segment, 'hold': hold, 'n_windows': len(df),
            'date_range': f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}",
            'nav_port': round(nav_p, 4), 'nav_base': round(nav_b, 4),
            'ann_ret_pct': round(ann_p*100, 2), 'ann_base_pct': round(ann_b*100, 2),
            'excess_total_pct': round((nav_p/nav_b-1)*100, 2),
            'avg_port_ret_pct': round(rp.mean()*100, 3), 'avg_base_ret_pct': round(rb.mean()*100, 3),
            'mdd_pct': round(mdd*100, 2), 'sharpe': round(float(sharpe), 3),
            'win_rate_pct': round(df['win'].mean()*100, 1)}

def main():
    log('加载大面板...')
    panel = pd.read_pickle(os.path.join(CACHE, 'panel_v2_big.pkl'))
    log(f'面板 {panel.shape} stocks={panel["code"].nunique()} '
        f'dates={panel["date"].min()}~{panel["date"].max()}')
    panel = add_features_labels(panel)
    log('面板特征+标签完成')

    out = {'kpi': {}, 'backtest': {}}
    trained = {}
    for target, tag, hold in [('y_short_v2', 'v2_short', 5), ('y_mid_v2', 'v2_mid', 20)]:
        log(f'=== 训练 {target} ===')
        m, kpi, dates, i7 = train(panel, target)
        out['kpi'][tag] = kpi
        trained[tag] = (m, dates, i7, hold)
        # 模型落盘(研究版, 不动v1生产)
        mp = os.path.join(MODELS, f'v2.0.0_{tag.split("_")[1]}.txt')
        m.booster_.save_model(mp)
        log(f'模型已存 {mp}')

    # v1旧模型对照(在大面板上, maxstreak口径与v1训练时不同→v1成绩可能失真, 标注)
    class BoosterWrap:
        def __init__(self, path): self.b = lgb.Booster(model_file=path)
        def predict(self, X): return self.b.predict(X)
    for tag, hold in [('v1_short', 5), ('v1_mid', 20)]:
        mp = os.path.join(MODELS, f'v1.0.0_{tag.split("_")[1]}.txt')
        if not os.path.exists(mp): continue
        trained[tag] = (BoosterWrap(mp), None, None, hold)

    log('=== 回测对比(同框架) ===')
    all_dates = sorted(panel['date'].unique())
    i7 = int(len(all_dates)*0.7)
    for tag, (model, _, _, hold) in trained.items():
        feats = FEATURES_V2 if tag.startswith('v2') else FEATURES
        out['backtest'][tag] = {}
        for seg in ['full', 'train', 'test']:
            r = backtest(panel, model, feats, tag=tag, hold=hold, segment=seg, i7=i7)
            out['backtest'][tag][seg] = r
            if 'error' not in r:
                log(f'[{tag}/{seg}] ann={r["ann_ret_pct"]}% base={r["ann_base_pct"]}% '
                    f'excess={r["excess_total_pct"]}% mdd={r["mdd_pct"]}% '
                    f'win={r["win_rate_pct"]}% n={r["n_windows"]}')

    fp = os.path.join(REPORTS, 'retrain_v2_bigdata_20260905.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    # 窗口明细CSV
    for tag in out['backtest']:
        for seg in out['backtest'][tag]:
            r = out['backtest'][tag][seg]
            if 'date_range' in r:
                pass  # 明细在回测函数内未保存全部行, 主报告以json汇总为准
    log(f'落盘 {fp}')

    # 汇总表
    print('\n' + '='*100)
    print(f'{"模型":<10} {"段":<6} {"窗口":<5} {"净值":<10} {"基准":<10} {"年化%":<8} {"基准年化%":<9} '
          f'{"超额%":<8} {"MDD%":<7} {"夏普":<6} {"胜率%":<6}')
    print('-'*100)
    for tag in ['v1_short', 'v2_short', 'v1_mid', 'v2_mid']:
        for seg in ['full', 'train', 'test']:
            r = out['backtest'].get(tag, {}).get(seg, {})
            if 'error' in r or not r: continue
            print(f'{tag:<10} {seg:<6} {r["n_windows"]:<5} {r["nav_port"]:<10} {r["nav_base"]:<10} '
                  f'{r["ann_ret_pct"]:<8} {r["ann_base_pct"]:<9} {r["excess_total_pct"]:<8} '
                  f'{r["mdd_pct"]:<7} {r["sharpe"]:<6} {r["win_rate_pct"]:<6}')
    print('='*100)
    for tag in ['v2_short', 'v2_mid']:
        if tag in out['kpi']:
            k = out['kpi'][tag]
            print(f'\n{tag} KPI: AUC train/test={k["auc_train"]}/{k["auc_test"]} gap={k["auc_gap"]} '
                  f'RankIC={k["rankIC_mean"]} Top20收盘命中率={k["top20_hit5_close"]}% '
                  f'best_iter={k["best_iter"]}')
            print(f'  特征重要性Top5: {list(k["feat_top10"].items())[:5]}')

if __name__ == '__main__':
    main()
