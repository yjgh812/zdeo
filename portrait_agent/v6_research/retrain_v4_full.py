# -*- coding: utf-8 -*-
"""v4全维画像特征重训(2026-09-05 主人指令: 缠论/MACD结构/K线形态/时序模式全加入).

特征构成(56):
  23 统计量(原特征族, maxstreak250版)
  6  形态事件(v3: island/limit_break/gap_up_run/new_high/dip_reb/rounded)
  23 新量价: K线组合5(早晨之星/红三兵/看涨吞没/低位锤头/多方炮)
           + MACD结构6(水上金叉/水下金叉/柱缩3日/顶背离/二次金叉/上穿零轴)
           + 时序模式7(5日4阳/缩量回调/波动收缩VCP/均线粘合发散/底部抬高/20日新高/跳空持有)
           + 筹码VWAP2(20/60日成本乖离) + NR7变盘 + inside bar突破
  4  缠论事件(buy1/buy2/buy3/底背驰, 近10日内)
标签: 截面相对强弱top10%(收盘口径). 7:2:1+早停. 对比v2(纯统计特征).
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
from retrain_v2_bigdata import FEATURES_V2, PARAMS, ES_ROUNDS
from morph_profile import load_pool, compute_patterns

TOPN = 20; MIN_SECTION = 500; QUANTILE = 0.90
T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

EVENT6 = ['island_5', 'limit_break_5', 'gap_up_run_5', 'new_high_3', 'dip_reb_10', 'rounded_20']
NEW_QP = ['morning_star', 'red_3_soldiers', 'bull_engulf', 'hammer_low', 'duofangpao',
          'macd_golden_hi', 'macd_golden_lo', 'macd_hist_shrink3', 'macd_top_div',
          'macd_2golden', 'macd_zero_up', 'up4of5', 'vol_shrink_pb', 'vol_contract',
          'ma_tangle_div', 'higher_low', 'new_high20', 'gap_up_hold',
          'vwap20_bias', 'vwap60_bias', 'nr7', 'inside_break',
          'vwap20_bias_raw', 'vwap60_bias_raw']  # raw=连续值版本
CHAN4 = ['chan_buy1_10', 'chan_buy2_10', 'chan_buy3_10', 'chan_bdiv_10']
FEATURES_V4 = FEATURES_V2 + EVENT6 + NEW_QP + CHAN4

def compute_new_patterns(g, dif, hist):
    """新23量价特征(输入per code: OHLCV+dif/hist)."""
    o = g['open'].astype(float); c = g['close'].astype(float)
    hi = g['high'].astype(float); lo = g['low'].astype(float)
    amt = g['amount'].astype(float); vol = g['volume'].astype(float)
    chg = g['change_pct'].astype(float)
    idx = g.index
    out = {}
    body = (c - o).abs(); rng = (hi - lo).replace(0, np.nan)
    # K线组合
    big_dn = c.shift(2) < o.shift(2) * 0.98
    small_body = (c.shift(1) - o.shift(1)).abs() < body.shift(2) * 0.4
    big_up = c > o * 1.02
    recover = c > (o.shift(2) + c.shift(2)) / 2
    out['morning_star'] = (big_dn & small_body & big_up & recover).astype(float)
    up3 = (c > o) & (c > c.shift(1))
    body_dom = body > rng * 0.5
    out['red_3_soldiers'] = (up3 & up3.shift(1) & up3.shift(2) &
                             body_dom & body_dom.shift(1) & body_dom.shift(2)).astype(float)
    dn_yes = c.shift(1) < o.shift(1)
    engulf = (c > o) & (o <= c.shift(1)) & (c >= o.shift(1))
    out['bull_engulf'] = (dn_yes & engulf & (body > body.shift(1))).astype(float)
    lower_shadow = np.minimum(o, c) - lo
    upper_shadow = hi - np.maximum(o, c)
    pos20 = c.rolling(20).rank(pct=True)
    out['hammer_low'] = ((lower_shadow >= 2 * body.replace(0, np.nan)) &
                         (upper_shadow < body.replace(0, np.nan)) &
                         (pos20 < 0.3)).astype(float)
    up1 = (c.shift(2) > o.shift(2)); dn2 = (c.shift(1) < o.shift(1)); up3d = (c > o)
    out['duofangpao'] = (up1 & dn2 & up3d & (c > c.shift(2))).astype(float)
    # MACD结构(dea=dif-hist; 金叉=hist由负转正)
    golden = (hist > 0) & (hist.shift(1) <= 0)
    out['macd_golden_hi'] = (golden & (dif > 0)).astype(float)
    out['macd_golden_lo'] = (golden & (dif <= 0)).astype(float)
    shrink = (hist.abs() < hist.abs().shift(1))
    out['macd_hist_shrink3'] = (shrink & shrink.shift(1) & shrink.shift(2)).astype(float)
    price_hi40 = c >= c.rolling(40).max() * 0.98
    dif_hi40 = dif.rolling(40).max()
    out['macd_top_div'] = (price_hi40 & (dif < dif_hi40 * 0.98)).astype(float)
    g_sig = golden.astype(float)
    out['macd_2golden'] = (g_sig.rolling(30).sum() >= 2).astype(float)
    out['macd_zero_up'] = ((dif > 0) & (dif.shift(1) <= 0)).astype(float)
    # 时序模式
    yang = (c > o).astype(float)
    out['up4of5'] = (yang.rolling(5).sum() >= 4).astype(float)
    dn3 = (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (c.shift(2) < c.shift(3))
    vsh3 = (amt < amt.shift(1)) & (amt.shift(1) < amt.shift(2))
    out['vol_shrink_pb'] = (dn3 & vsh3).astype(float)
    amp10 = (hi - lo) / c.shift(1)
    out['vol_contract'] = (amp10.rolling(10).mean() <
                           amp10.shift(10).rolling(10).mean() * 0.6).astype(float)
    ma5, ma10, ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()
    ma_max = pd.concat([ma5, ma10, ma20], axis=1).max(axis=1)
    ma_min = pd.concat([ma5, ma10, ma20], axis=1).min(axis=1)
    tangle = (ma_max / ma_min - 1) < 0.015
    out['ma_tangle_div'] = (tangle.shift(1) & (chg > 1)).astype(float)
    out['higher_low'] = (lo.rolling(10).min() > lo.shift(10).rolling(10).min() * 1.02).astype(float)
    out['new_high20'] = (c >= hi.shift(1).rolling(20).max() * 0.998).astype(float)
    ph, pl = hi.shift(1), lo.shift(1)
    gu3 = ((lo > ph) & (lo.shift(1) > ph.shift(1)) & (lo.shift(2) > ph.shift(2)) &
           (lo.shift(3) <= ph.shift(3)))
    out['gap_up_hold'] = gu3.astype(float)
    # 筹码VWAP(同口径复权: 量加权收盘价)
    vwc20 = (c * vol).rolling(20).sum() / vol.rolling(20).sum()
    vwc60 = (c * vol).rolling(60).sum() / vol.rolling(60).sum()
    out['vwap20_bias_raw'] = c / vwc20 - 1
    out['vwap60_bias_raw'] = c / vwc60 - 1
    out['vwap20_bias'] = ((c / vwc20 - 1) > 0.05).astype(float)
    out['vwap60_bias'] = ((c / vwc60 - 1) > 0.10).astype(float)
    # NR7变盘 + inside bar突破
    amp_today = (hi - lo)
    out['nr7'] = (amp_today < amp_today.shift(1).rolling(6).min()).astype(float)
    inside = (hi.shift(1) < hi.shift(2)) & (lo.shift(1) > lo.shift(2))
    out['inside_break'] = (inside.shift(1) & (c > hi.shift(1))).astype(float)
    return pd.DataFrame(out, index=idx)

def main():
    log('加载行情...')
    df = load_pool()
    df = df.sort_values(['code', 'date']).reset_index(drop=True)
    df['fwd5_close'] = df.groupby('code')['close'].transform(lambda s: s.shift(-5)/s - 1)
    df['fwd20_close'] = df.groupby('code')['close'].transform(lambda s: s.shift(-20)/s - 1)
    # 经典15形态
    parts = []
    for k, (code, g) in enumerate(df.groupby('code', sort=False)):
        if k % 1000 == 0: log(f'  形态 {k}')
        pats = compute_patterns(g)
        pats['code'] = code; pats['date'] = g['date'].values
        parts.append(pats)
    pats_all = pd.concat(parts, ignore_index=True)
    # 池过滤
    df['amt20_'] = df.groupby('code')['amount'].transform(
        lambda s: s.rolling(20, min_periods=10).mean())
    bar_cnt = df.groupby('code')['code'].transform('size')
    df = df[(df['close'] <= 20.0) & (df['amt20_'] >= 500e4) & (bar_cnt >= 120)]
    log(f'池过滤后 {len(df)} stocks={df["code"].nunique()}')
    # 新23特征(需dif/hist: 从大面板join)
    panel = pd.read_pickle(os.path.join(CACHE, 'panel_v2_big.pkl'))
    keep = list(dict.fromkeys(['code', 'date', 'dif', 'hist', 'lim_today', 'close'] +
                               [f for f in FEATURES_V2 if f != 'maxstreak250']))  # 保序去重(防dif/hist重复列)
    pm = panel[keep].copy()
    df = df.merge(pm, on=['code', 'date'], how='left', suffixes=('', '_p'))
    df['close'] = df['close_p'].combine_first(df['close'])
    df = df.drop(columns=['close_p'])
    parts2 = []
    for k, (code, g) in enumerate(df.groupby('code', sort=False)):
        if k % 1000 == 0: log(f'  新特征 {k}')
        g = g.reset_index(drop=True)  # 防merge后index对齐冲突
        np_ = compute_new_patterns(g, g['dif'], g['hist'])
        np_['code'] = code; np_['date'] = g['date'].values
        parts2.append(np_)
    new_all = pd.concat(parts2, ignore_index=True)
    cols = list(dict.fromkeys(['code', 'date', 'close', 'fwd5_close', 'fwd20_close', 'lim_today'] +
                               [f for f in FEATURES_V2 if f != 'maxstreak250']))  # dif/hist在V2内, 去重防重名列
    df2 = df[cols].copy()
    m = df2.merge(pats_all, on=['code', 'date'], how='inner').merge(
        new_all, on=['code', 'date'], how='inner')
    log(f'面板 {m.shape}')
    del df, pats_all, parts, panel, pm, new_all, parts2, df2

    # 事件特征(6经典+缠论4)
    log('事件特征...')
    m = m.sort_values(['code', 'date']).reset_index(drop=True)
    m['island_5'] = m.groupby('code')['island_bottom'].transform(lambda s: s.rolling(5).max())
    m['limit_break_5'] = m.groupby('code')['limit_break'].transform(lambda s: s.rolling(5).max())
    m['gap_up_run_5'] = m.groupby('code')['gap_up_run'].transform(lambda s: s.rolling(5).max())
    m['new_high_3'] = m.groupby('code')['new_high250'].transform(lambda s: s.rolling(3).max())
    m['dip_reb_10'] = m.groupby('code')['dip_rebound'].transform(lambda s: s.rolling(10).max())
    m['rounded_20'] = m.groupby('code')['rounded_bottom'].transform(lambda s: s.rolling(20).max())
    chan_fp = os.path.join(CACHE, 'chan_signals_v1.pkl')
    if os.path.exists(chan_fp):
        chan = pd.read_pickle(chan_fp)
        for t, col in [('buy1', 'chan_buy1_10'), ('buy2', 'chan_buy2_10'),
                       ('buy3', 'chan_buy3_10'), ('bottom_div', 'chan_bdiv_10')]:
            sig = chan[chan['type'] == t][['code', 'date']].drop_duplicates()
            sig['sig'] = 1.0
            m = m.merge(sig, on=['code', 'date'], how='left')
            m['sig'] = m['sig'].fillna(0)
            m[col] = m.groupby('code')['sig'].transform(lambda s: s.rolling(10).max())
            m = m.drop(columns=['sig'])
        cnts = {c: int(m[c].sum()) for c in CHAN4}
        log(f'缠论特征信号数: {cnts}')
    else:
        log('⚠️ 缠论信号缺失, 4特征置0')
        for c in CHAN4: m[c] = 0.0
    # maxstreak250
    lim_arr = m['lim_today'].fillna(False).astype('int8').to_numpy()
    code_arr = m['code'].to_numpy()
    same = np.r_[False, (code_arr[1:] == code_arr[:-1]) & (lim_arr[1:] == lim_arr[:-1])]
    grp_id = np.cumsum(~same)
    cur = pd.Series(lim_arr).groupby(grp_id).cumsum().astype(float)
    m['maxstreak250'] = pd.Series(cur.values).groupby(code_arr).transform(
        lambda s: s.rolling(250, min_periods=1).max()).values
    # 标签
    for fwd, y in [('fwd5_close', 'y_short_v4'), ('fwd20_close', 'y_mid_v4')]:
        pct = m.groupby('date')[fwd].rank(pct=True)
        m[y] = (pct >= QUANTILE).astype(float)
        m.loc[m[fwd].isna(), y] = np.nan
    m.to_pickle(os.path.join(CACHE, 'panel_v4_full.pkl'))  # 缓存复用(重跑免5分钟特征计算)

    # ==== 新特征画像(lift+截面超额) ====
    log('全维特征画像...')
    valid = m.dropna(subset=['y_mid_v4'])
    up, dn = valid[valid['y_mid_v4'] == 1], valid[valid['y_mid_v4'] == 0]
    pool20 = m.dropna(subset=['fwd20_close'])
    day_mean20 = pool20.groupby('date')['fwd20_close'].mean()
    prof = []
    for p in EVENT6 + NEW_QP + CHAN4:
        col = m[p]
        is_bin = col.dropna().isin([0, 1]).all()
        if is_bin:
            p_up, p_dn = up[p].mean(), dn[p].mean()
            sub = m[m[p] > 0].dropna(subset=['fwd20_close'])
            ex = (sub['fwd20_close'] - sub['date'].map(day_mean20)).dropna()
            t_val = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if len(ex) > 5 and ex.std() > 0 else np.nan
            prof.append({'feat': p, 'type': '事件', 'rate_up': round(p_up*100, 3),
                         'rate_dn': round(p_dn*100, 3),
                         'lift': round(p_up/p_dn, 3) if p_dn > 0 else None,
                         'ex20_pct': round(ex.mean()*100, 3) if len(ex) else None,
                         'ex20_t': round(t_val, 2) if not np.isnan(t_val) else None,
                         'win_pct': round((ex > 0).mean()*100, 1) if len(ex) else None,
                         'n': int(m[p].sum())})
    prof_df = pd.DataFrame(prof).sort_values('lift', ascending=False)
    log('\n全维事件特征画像:\n' + prof_df.to_string(index=False))

    # ==== v4训练 ====
    out = {'kpi': {}, 'backtest': {}, 'profile': prof_df.to_dict('records')}
    trained = {}
    for target, tag, hold in [('y_short_v4', 'v4_short', 5), ('y_mid_v4', 'v4_mid', 20)]:
        log(f'=== 训练 {target} ===')
        d = m[m[target].notna() & m['close'].notna()]
        dates = sorted(d['date'].unique()); n = len(dates); i7, i9 = int(n*0.7), int(n*0.9)
        tr = d[d['date'] < dates[i7]]
        va = d[(d['date'] >= dates[i7]) & (d['date'] < dates[i9])]
        te = d[d['date'] >= dates[i9]]
        mdl = lgb.LGBMClassifier(**PARAMS)
        mdl.fit(tr[FEATURES_V4].to_numpy(dtype=np.float32), tr[target],
                eval_set=[(va[FEATURES_V4].to_numpy(dtype=np.float32), va[target])],
                eval_metric='auc', callbacks=[lgb.early_stopping(ES_ROUNDS, verbose=False)])
        def _pred(frame):
            out_ = np.empty(len(frame), dtype=np.float32)
            for i in range(0, len(frame), 400_000):
                out_[i:i+400_000] = mdl.predict_proba(
                    frame[FEATURES_V4].iloc[i:i+400_000].to_numpy(dtype=np.float32))[:, 1]
            return out_
        te = te.copy(); te['prob'] = _pred(te)
        auc_te = roc_auc_score(te[target], te['prob'])
        fwd_col = 'fwd5_close' if 'short' in target else 'fwd20_close'
        ics = []
        for dd, gsec in te.groupby('date'):
            g2 = gsec[gsec[fwd_col].notna()]
            if len(g2) > 200:
                ic, _ = spearmanr(g2['prob'], g2[fwd_col])
                if not np.isnan(ic): ics.append(ic)
        hits = []
        for dd, gsec in te.groupby('date'):
            g2 = gsec[gsec['fwd5_close'].notna()].nlargest(TOPN, 'prob')
            if len(g2) >= TOPN: hits.append((g2['fwd5_close'] > 0).mean())
        out['kpi'][tag] = {'auc_test': round(auc_te, 4),
                           'rankIC': round(float(np.mean(ics)), 4),
                           'hit5': round(float(np.mean(hits))*100, 2),
                           'iter': int(mdl.best_iteration_ or 0),
                           'feat_top10': dict(sorted(zip(FEATURES_V4, mdl.feature_importances_.astype(float)),
                                                     key=lambda x: -x[1])[:10])}
        log(f'KPI {tag}: {json.dumps({k: v for k, v in out["kpi"][tag].items() if k != "feat_top10"}, ensure_ascii=False)}')
        mdl.booster_.save_model(os.path.join(MODELS, f'v4.0.0_{tag.split("_")[1]}.txt'))
        trained[tag] = (mdl, hold)
    for tag, hold in [('v2_short', 5), ('v2_mid', 20)]:
        mp = os.path.join(MODELS, f'v2.0.0_{tag.split("_")[1]}.txt')
        if os.path.exists(mp):
            trained[tag] = (lgb.Booster(model_file=mp), hold)

    # ==== 回测 ====
    all_dates = sorted(m['date'].unique()); i7 = int(len(all_dates)*0.7)
    log('回测预分组...')
    bt_groups = {d: g for d, g in m.groupby('date')}   # 预分组: 消除每窗口800万行全表扫描(曾致1小时挂起)
    def backtest(model, feats, tag, hold, segment, _groups, _all_dates):
        fwd_col = 'fwd5_close' if hold == 5 else 'fwd20_close'
        dts = _all_dates[:i7] if segment == 'train' else (_all_dates[i7:] if segment == 'test' else _all_dates)
        nav_p = nav_b = 1.0; rows = []
        for ws in dts[::hold]:
            cs = _groups.get(ws)
            if cs is None: continue
            cs = cs[cs[fwd_col].notna() & (cs['lim_today'].fillna(False).astype(bool) == False)]
            if len(cs) < MIN_SECTION: continue
            cs = cs.copy()
            cs['prob'] = model.predict(cs[feats].to_numpy(dtype=np.float32))
            top = cs.nlargest(TOPN, 'prob')
            if len(top) < TOPN: continue
            pr = float(top[fwd_col].astype(float).mean()); br = float(cs[fwd_col].astype(float).mean())
            nav_p *= (1+pr); nav_b *= (1+br)
            rows.append({'date': ws, 'port': round(pr*100, 3), 'base': round(br*100, 3),
                         'ex': round((pr-br)*100, 3), 'nav_p': round(nav_p, 4), 'win': int(pr > br)})
        rdf = pd.DataFrame(rows)
        if rdf.empty: return {'error': 'empty'}
        rp = rdf['port'].values/100; years = len(rdf)*hold/243
        ann_p = nav_p**(1/years)-1 if years > 0 else 0
        ann_b = nav_b**(1/years)-1 if years > 0 else 0
        nav_arr = rdf['nav_p'].values
        mdd = float((nav_arr/np.maximum.accumulate(nav_arr)-1).min())
        sharpe = (rp.mean()-0.02*hold/243)/rp.std()*np.sqrt(243/hold) if rp.std() > 0 else 0
        return {'n': len(rdf), 'nav': round(nav_p, 4), 'nav_b': round(nav_b, 4),
                'ann': round(ann_p*100, 2), 'ann_b': round(ann_b*100, 2),
                'excess': round((nav_p/nav_b-1)*100, 2), 'mdd': round(mdd*100, 2),
                'sharpe': round(float(sharpe), 3), 'win': round(rdf['win'].mean()*100, 1)}
    for tag, (model, hold) in trained.items():
        feats = FEATURES_V4 if tag.startswith('v4') else FEATURES_V2
        out['backtest'][tag] = {}
        for seg in ['full', 'train', 'test']:
            r = backtest(model, feats, tag, hold, seg, bt_groups, all_dates)
            out['backtest'][tag][seg] = r
            if 'error' not in r:
                log(f'[{tag}/{seg}] ann={r["ann"]}% base={r["ann_b"]}% ex={r["excess"]}% '
                    f'mdd={r["mdd"]}% win={r["win"]}% n={r["n"]}')

    fp = os.path.join(REPORTS, 'retrain_v4_full_20260905.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log(f'落盘 {fp}')
    print('\n' + '='*100)
    print(f'{"模型":<10} {"段":<6} {"净值":<9} {"基准":<9} {"年化%":<8} {"基准%":<8} {"超额%":<8} {"MDD%":<8} {"胜率%":<6}')
    print('-'*100)
    for tag in ['v2_short', 'v4_short', 'v2_mid', 'v4_mid']:
        for seg in ['full', 'train', 'test']:
            r = out['backtest'].get(tag, {}).get(seg, {})
            if 'error' in r or not r: continue
            print(f'{tag:<10} {seg:<6} {r["nav"]:<9} {r["nav_b"]:<9} {r["ann"]:<8} '
                  f'{r["ann_b"]:<8} {r["excess"]:<8} {r["mdd"]:<8} {r["win"]:<6}')
    print('='*100)
    for tag in ['v4_short', 'v4_mid']:
        k = out['kpi'].get(tag, {})
        if k:
            print(f'{tag} Top特征: {list(k["feat_top10"].items())[:8]}')

if __name__ == '__main__':
    main()
