# -*- coding: utf-8 -*-
"""v3: 形态事件特征族加入画像模型重训(2026-09-05 主人指令: 捕捉上涨形态特征).

依据 morph_profile.py 14年画像研究:
  正信号: island_bottom(lift1.57, fwd5超额+1.73% t=9.49), limit_break(fwd5 +2.45% t=12.91),
          gap_up_run(fwd5 +0.59% t=4.95), new_high250(lift1.77)
  反信号: dip_rebound(fwd20 -2.12% t=-9.78), rounded_bottom(fwd20 -1.47% t=-14.96)
事件特征制作: 信号日后N日内=1(rolling(N).max), N=信号半衰期近似(island/limit/gap=5,
  new_high=3, 反向标记=10/20).
v3特征集 = FEATURES_V2(23统计量) + 6事件特征 = 29特征.
另修正D3分年bug(全池日均值对照), 对真信号分年验证.
"""
import os, sys, json, time
import sqlite3
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
from retrain_v2_bigdata import FEATURES_V2, PARAMS, ES_ROUNDS, add_features_labels
from morph_profile import load_pool, compute_patterns, PATTERNS

TOPN = 20; MIN_SECTION = 500; QUANTILE = 0.90
T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

EVENT_FEATS = ['island_5', 'limit_break_5', 'gap_up_run_5', 'new_high_3',
               'dip_reb_10', 'rounded_20']
FEATURES_V3 = FEATURES_V2 + EVENT_FEATS

def build_morph_events(m):
    """在研究面板(含15形态0/1列)上制作事件衰减特征."""
    m['island_5'] = m.groupby('code')['island_bottom'].transform(lambda s: s.rolling(5).max())
    m['limit_break_5'] = m.groupby('code')['limit_break'].transform(lambda s: s.rolling(5).max())
    m['gap_up_run_5'] = m.groupby('code')['gap_up_run'].transform(lambda s: s.rolling(5).max())
    m['new_high_3'] = m.groupby('code')['new_high250'].transform(lambda s: s.rolling(3).max())
    m['dip_reb_10'] = m.groupby('code')['dip_rebound'].transform(lambda s: s.rolling(10).max())
    m['rounded_20'] = m.groupby('code')['rounded_bottom'].transform(lambda s: s.rolling(20).max())
    return m

def fixed_yearly(m, patterns):
    """修正版分年(全池日均值对照)."""
    pool = m.dropna(subset=['fwd20_close'])
    day_mean = pool.groupby('date')['fwd20_close'].mean()
    out = {}
    for p in patterns:
        sub = m[(m[p] > 0)].dropna(subset=['fwd20_close']).copy()
        sub['ex'] = sub['fwd20_close'] - sub['date'].map(day_mean)
        sub['year'] = sub['date'].str[:4]
        yr = sub.groupby('year')['ex'].agg(['size', 'mean', lambda s: (s > 0).mean()*100])
        yr.columns = ['n', 'avg_ex_pct', 'win_pct']
        yr['avg_ex_pct'] = (yr['avg_ex_pct']*100).round(3)
        yr['win_pct'] = yr['win_pct'].round(1)
        out[p] = yr.to_dict('index')
    return out

def train_v3(m, target):
    feats = FEATURES_V3
    df = m[m[target].notna() & m['close'].notna()].copy()
    dates = sorted(df['date'].unique())
    n = len(dates); i7, i9 = int(n*0.7), int(n*0.9)
    tr = df[df['date'] < dates[i7]]
    va = df[(df['date'] >= dates[i7]) & (df['date'] < dates[i9])]
    te = df[df['date'] >= dates[i9]]
    log(f'{target}: train={len(tr)} test={len(te)} test段={dates[i9]}~')
    mdl = lgb.LGBMClassifier(**PARAMS)
    mdl.fit(tr[feats].to_numpy(dtype=np.float32), tr[target],
            eval_set=[(va[feats].to_numpy(dtype=np.float32), va[target])],
            eval_metric='auc', callbacks=[lgb.early_stopping(ES_ROUNDS, verbose=False)])
    def _pred(frame, chunk=400_000):
        out = np.empty(len(frame), dtype=np.float32)
        for i in range(0, len(frame), chunk):
            out[i:i+chunk] = mdl.predict_proba(frame[feats].iloc[i:i+chunk].to_numpy(dtype=np.float32))[:, 1]
        return out
    te = te.copy(); te['prob'] = _pred(te)
    auc_te = roc_auc_score(te[target], te['prob'])
    fwd_col = 'fwd5_close' if 'short' in target else 'fwd20_close'
    ics = []
    for d, g in te.groupby('date'):
        g2 = g[g[fwd_col].notna()]
        if len(g2) > 200:
            ic, _ = spearmanr(g2['prob'], g2[fwd_col])
            if not np.isnan(ic): ics.append(ic)
    hits = []
    for d, g in te.groupby('date'):
        g2 = g[g['fwd5_close'].notna()].nlargest(TOPN, 'prob')
        if len(g2) >= TOPN: hits.append((g2['fwd5_close'] > 0).mean())
    kpi = {'auc_test': round(auc_te, 4),
           'rankIC_mean': round(float(np.mean(ics)), 4), 'rankIC_n': len(ics),
           'top20_hit5_close': round(float(np.mean(hits))*100, 2),
           'best_iter': int(mdl.best_iteration_ or 0),
           'feat_top10': dict(sorted(zip(feats, mdl.feature_importances_.astype(float)),
                                     key=lambda x: -x[1])[:10])}
    log(f'KPI: {json.dumps({k: v for k, v in kpi.items() if k != "feat_top10"}, ensure_ascii=False)}')
    return mdl, kpi, i7

def backtest(m, model, feats, tag, hold, segment, i7):
    fwd_col = 'fwd5_close' if hold == 5 else 'fwd20_close'
    dates = sorted(m['date'].unique())
    if segment == 'train': dates = dates[:i7]
    elif segment == 'test': dates = dates[i7:]
    nav_p = nav_b = 1.0; rows = []
    groups = {d: g for d, g in m.groupby('date')}   # 预分组(消除全表扫描)
    for ws in dates[::hold]:
        cs = groups.get(ws)
        if cs is None: continue
        cs = cs[cs[fwd_col].notna() & (~cs['lim_today'].astype(bool))]
        if len(cs) < MIN_SECTION: continue
        cs['prob'] = model.predict(cs[feats].to_numpy(dtype=np.float32))
        top = cs.nlargest(TOPN, 'prob')
        if len(top) < TOPN: continue
        pr = float(top[fwd_col].astype(float).mean()); br = float(cs[fwd_col].astype(float).mean())
        nav_p *= (1+pr); nav_b *= (1+br)
        rows.append({'date': ws, 'port': round(pr*100, 3), 'base': round(br*100, 3),
                     'ex': round((pr-br)*100, 3), 'nav_p': round(nav_p, 4), 'win': int(pr > br)})
    df = pd.DataFrame(rows)
    if df.empty: return {'error': 'empty'}
    rp = df['port'].values/100; years = len(df)*hold/243
    ann_p = nav_p**(1/years)-1 if years > 0 else 0
    ann_b = nav_b**(1/years)-1 if years > 0 else 0
    nav_arr = df['nav_p'].values
    mdd = float((nav_arr/np.maximum.accumulate(nav_arr)-1).min())
    sharpe = (rp.mean()-0.02*hold/243)/rp.std()*np.sqrt(243/hold) if rp.std() > 0 else 0
    return {'tag': tag, 'segment': segment, 'hold': hold, 'n': len(df),
            'range': f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}",
            'nav': round(nav_p, 4), 'nav_b': round(nav_b, 4),
            'ann': round(ann_p*100, 2), 'ann_b': round(ann_b*100, 2),
            'excess': round((nav_p/nav_b-1)*100, 2), 'mdd': round(mdd*100, 2),
            'sharpe': round(float(sharpe), 3), 'win': round(df['win'].mean()*100, 1),
            'detail': df.to_dict('records')}

def main():
    log('加载行情+计算形态...')
    df = load_pool()
    df = df.sort_values(['code', 'date']).reset_index(drop=True)
    df['fwd5_close'] = df.groupby('code')['close'].transform(lambda s: s.shift(-5)/s - 1)
    df['fwd20_close'] = df.groupby('code')['close'].transform(lambda s: s.shift(-20)/s - 1)
    # 形态
    parts = []
    for k, (code, g) in enumerate(df.groupby('code', sort=False)):
        if k % 1000 == 0: log(f'  形态 {k}')
        pats = compute_patterns(g)
        pats['code'] = code; pats['date'] = g['date'].values
        parts.append(pats)
    pats_all = pd.concat(parts, ignore_index=True)
    # 池过滤(与大面板一致): close<=20 + amt20>=500万 + bar>=120 (fwd/形态已按全序列算好)
    df['amt20_'] = df.groupby('code')['amount'].transform(
        lambda s: s.rolling(20, min_periods=10).mean())
    bar_cnt = df.groupby('code')['code'].transform('size')
    df = df[(df['close'] <= 20.0) & (df['amt20_'] >= 500e4) & (bar_cnt >= 120)]
    log(f'池过滤后 {len(df)} stocks={df["code"].nunique()}')
    # join大面板(统计特征+lim_today; maxstreak250不落盘→merge后现算)
    panel = pd.read_pickle(os.path.join(CACHE, 'panel_v2_big.pkl'))
    keep = ['code', 'date'] + [f for f in FEATURES_V2 if f != 'maxstreak250'] + ['lim_today', 'close']
    pm = panel[keep].copy()
    df2 = df[['code', 'date', 'close', 'fwd5_close', 'fwd20_close']].copy()
    m = df2.merge(pats_all, on=['code', 'date'], how='inner')
    m = m.merge(pm, on=['code', 'date'], how='left', suffixes=('', '_p'))
    m['close'] = m['close_p'].combine_first(m['close'])
    m = m.drop(columns=['close_p'])
    log(f'研究面板 {m.shape}')
    del df, pats_all, parts, panel, pm, df2

    # 事件特征
    log('事件衰减特征+maxstreak250...')
    m = build_morph_events(m)
    # maxstreak250(250日滚动最大连板, 消除起点依赖) — numpy判段实现
    m = m.sort_values(['code', 'date']).reset_index(drop=True)
    lim_arr = m['lim_today'].fillna(False).astype('int8').to_numpy()
    code_arr = m['code'].to_numpy()
    same = np.r_[False, (code_arr[1:] == code_arr[:-1]) & (lim_arr[1:] == lim_arr[:-1])]
    grp_id = np.cumsum(~same)
    cur = pd.Series(lim_arr).groupby(grp_id).cumsum().astype(float)
    m['maxstreak250'] = pd.Series(cur.values).groupby(code_arr).transform(
        lambda s: s.rolling(250, min_periods=1).max()).values
    # 标签(相对top10%)
    for fwd, y in [('fwd5_close', 'y_short_v3'), ('fwd20_close', 'y_mid_v3')]:
        pct = m.groupby('date')[fwd].rank(pct=True)
        m[y] = (pct >= QUANTILE).astype(float)
        m.loc[m[fwd].isna(), y] = np.nan

    # 修正版分年(真信号)
    log('修正版分年验证(全池对照)...')
    yearly = fixed_yearly(m, ['island_bottom', 'limit_break', 'gap_up_run'])
    for p, yr in yearly.items():
        neg = [y for y, v in yr.items() if v['avg_ex_pct'] < 0]
        exs = [v['avg_ex_pct'] for v in yr.values()]
        log(f'  {p}: {len(yr)}年中{len(neg)}年负; 均值{np.mean(exs):+.3f}%/年; 负年={neg}')

    # v3训练
    out = {'kpi': {}, 'backtest': {}, 'yearly_fixed': yearly}
    class WrapM:
        def __init__(self, m_): self.m = m_
        def predict(self, X): return self.m.predict(X)
    trained = {}
    for target, tag, hold in [('y_short_v3', 'v3_short', 5), ('y_mid_v3', 'v3_mid', 20)]:
        log(f'=== 训练 {target} ===')
        mdl, kpi, i7 = train_v3(m, target)
        out['kpi'][tag] = kpi
        mp = os.path.join(MODELS, f'v3.0.0_{tag.split("_")[1]}.txt')
        mdl.booster_.save_model(mp)
        trained[tag] = (mdl, hold)
        log(f'存 {mp}')
    # v2对照模型
    for tag, hold in [('v2_short', 5), ('v2_mid', 20)]:
        mp = os.path.join(MODELS, f'v2.0.0_{tag.split("_")[1]}.txt')
        if os.path.exists(mp):
            trained[tag] = (lgb.Booster(model_file=mp), hold)

    all_dates = sorted(m['date'].unique())
    i7 = int(len(all_dates)*0.7)
    for tag, (model, hold) in trained.items():
        feats = FEATURES_V3 if tag.startswith('v3') else FEATURES_V2
        out['backtest'][tag] = {}
        for seg in ['full', 'train', 'test']:
            r = backtest(m, model, feats, tag, hold, seg, i7)
            out['backtest'][tag][seg] = r
            if 'error' not in r:
                log(f'[{tag}/{seg}] ann={r["ann"]}% base={r["ann_b"]}% excess={r["excess"]}% '
                    f'mdd={r["mdd"]}% win={r["win"]}% n={r["n"]}')

    fp = os.path.join(REPORTS, 'retrain_v3_morph_20260905.json')
    json.dump({'kpi': out['kpi'],
               'backtest': {k: {s: {kk: vv for kk, vv in r.items() if kk != 'detail'}
                                for s, r in v.items()} for k, v in out['backtest'].items()},
               'yearly_fixed': yearly},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log(f'落盘 {fp}')

    print('\n' + '='*100)
    print(f'{"模型":<10} {"段":<6} {"n":<5} {"净值":<9} {"基准":<9} {"年化%":<8} {"基准%":<8} '
          f'{"超额%":<8} {"MDD%":<8} {"夏普":<6} {"胜率%":<6}')
    print('-'*100)
    for tag in ['v2_short', 'v3_short', 'v2_mid', 'v3_mid']:
        for seg in ['full', 'train', 'test']:
            r = out['backtest'].get(tag, {}).get(seg, {})
            if 'error' in r or not r: continue
            print(f'{tag:<10} {seg:<6} {r["n"]:<5} {r["nav"]:<9} {r["nav_b"]:<9} {r["ann"]:<8} '
                  f'{r["ann_b"]:<8} {r["excess"]:<8} {r["mdd"]:<8} {r["sharpe"]:<6} {r["win"]:<6}')
    print('='*100)
    for tag in ['v3_short', 'v3_mid']:
        k = out['kpi'].get(tag, {})
        if k:
            print(f'{tag}: RankIC={k["rankIC_mean"]} AUC={k["auc_test"]} hit5={k["top20_hit5_close"]}% '
                  f'iter={k["best_iter"]} 事件特征重要性=' +
                  str({f: v for f, v in k['feat_top10'].items() if f in EVENT_FEATS}))

if __name__ == '__main__':
    main()
