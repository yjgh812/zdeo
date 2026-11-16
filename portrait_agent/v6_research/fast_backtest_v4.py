# -*- coding: utf-8 -*-
"""v4快速回测(用已落盘的panel_v4_full.pkl, 预分组消除全表扫描).

v4模型已训练落盘(v4.0.0_short/mid.txt), 本脚本只做回测: v2 vs v4 × full/train/test.
优化: m.groupby('date') 预分组成 dict[date]->截面, 窗口循环内查表(原版每窗口扫800万行→1小时,
      本版一次分组后每窗口取数微秒级).
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import lightgbm as lgb

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, 'models')
REPORTS = os.path.join(HERE, 'reports')
CACHE = os.path.join(HERE, 'cache')
from retrain_v2_bigdata import FEATURES_V2
from retrain_v4_full import FEATURES_V4, TOPN, MIN_SECTION

T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

def main():
    log('加载面板缓存...')
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v4_full.pkl'))
    log(f'面板 {m.shape}')
    m['lim_today'] = m['lim_today'].fillna(False).astype(bool)
    all_dates = sorted(m['date'].unique())
    i7 = int(len(all_dates)*0.7)

    # 预分组: 每截面只留回测需要的列(内存友好)
    need = ['date', 'fwd5_close', 'fwd20_close', 'lim_today', 'code']
    log('预分组截面...')
    groups = {d: g for d, g in m.groupby('date')}
    log(f'截面数={len(groups)}')

    models = {}
    for tag, hold in [('v2_short', 5), ('v2_mid', 20), ('v4_short', 5), ('v4_mid', 20)]:
        ver = tag.split('_')[0]
        mp = os.path.join(MODELS, f'{ver}.0.0_{tag.split("_")[1]}.txt')
        if os.path.exists(mp):
            models[tag] = (lgb.Booster(model_file=mp), hold,
                           FEATURES_V4 if ver == 'v4' else FEATURES_V2)

    def backtest(booster, feats, hold, segment):
        fwd_col = 'fwd5_close' if hold == 5 else 'fwd20_close'
        dts = all_dates[:i7] if segment == 'train' else (all_dates[i7:] if segment == 'test' else all_dates)
        nav_p = nav_b = 1.0; rows = []
        for ws in dts[::hold]:
            cs = groups.get(ws)
            if cs is None: continue
            cs = cs[cs[fwd_col].notna() & (~cs['lim_today'])]
            if len(cs) < MIN_SECTION: continue
            prob = booster.predict(cs[feats].to_numpy(dtype=np.float32))
            pr = float(cs[fwd_col].astype(float).nlargest(TOPN).mean()) if False else None
            # Top20 by prob
            idx = np.argpartition(prob, -TOPN)[-TOPN:]
            fv = cs[fwd_col].astype(float).to_numpy()
            pr = float(fv[idx].mean()); br = float(fv.mean())
            nav_p *= (1+pr); nav_b *= (1+br)
            rows.append({'date': ws, 'port': round(pr*100, 3), 'base': round(br*100, 3),
                         'ex': round((pr-br)*100, 3), 'nav_p': round(nav_p, 4),
                         'win': int(pr > br)})
        rdf = pd.DataFrame(rows)
        if rdf.empty: return {'error': 'empty'}
        rp = rdf['port'].values/100; years = len(rdf)*hold/243
        ann_p = nav_p**(1/years)-1 if years > 0 else 0
        ann_b = nav_b**(1/years)-1 if years > 0 else 0
        nav_arr = rdf['nav_p'].values
        mdd = float((nav_arr/np.maximum.accumulate(nav_arr)-1).min())
        sharpe = (rp.mean()-0.02*hold/243)/rp.std()*np.sqrt(243/hold) if rp.std() > 0 else 0
        return {'n': len(rdf), 'range': f"{rdf['date'].iloc[0]}~{rdf['date'].iloc[-1]}",
                'nav': round(nav_p, 4), 'nav_b': round(nav_b, 4),
                'ann': round(ann_p*100, 2), 'ann_b': round(ann_b*100, 2),
                'excess': round((nav_p/nav_b-1)*100, 2), 'mdd': round(mdd*100, 2),
                'sharpe': round(float(sharpe), 3), 'win': round(rdf['win'].mean()*100, 1),
                'detail': rdf.to_dict('records')}

    out = {}
    for tag, (booster, hold, feats) in models.items():
        out[tag] = {}
        for seg in ['full', 'train', 'test']:
            r = backtest(booster, feats, hold, seg)
            out[tag][seg] = {k: v for k, v in r.items() if k != 'detail'}
            if 'error' not in r:
                log(f'[{tag}/{seg}] ann={r["ann"]}% base={r["ann_b"]}% ex={r["excess"]}% '
                    f'mdd={r["mdd"]}% win={r["win"]}% n={r["n"]}')

    # 信号直构组合(不经模型): 强事件特征Top组合验证 —— chan_buy2+gap_up_hold+island
    log('信号直构组合回测(20日持有, 每窗口取信号股等权Top20)...') 
    sig_cols = ['chan_buy2_10', 'island_5', 'gap_up_hold', 'chan_buy1_10']
    def sig_backtest(pick_cols, hold=20, topn=TOPN):
        dts = all_dates[::hold]
        nav_p = nav_b = 1.0; rows = []
        for ws in dts:
            cs = groups.get(ws)
            if cs is None: continue
            cs2 = cs[cs['fwd20_close'].notna() & (~cs['lim_today'])]
            if len(cs2) < MIN_SECTION: continue
            sig_mask = None
            for c_ in pick_cols:
                if c_ not in cs2.columns: continue
                mk = (cs2[c_].fillna(0) > 0).to_numpy()
                sig_mask = mk if sig_mask is None else (sig_mask | mk)
            if sig_mask is None: continue
            fv = cs2['fwd20_close'].astype(float).to_numpy()
            sel = fv[sig_mask]
            if len(sel) < 5: continue
            # 信号股多于topn时等权全持(信号稀疏), 上限60只防过度集中
            sel = sel[:60] if len(sel) > 60 else sel
            pr = float(sel.mean()); br = float(fv.mean())
            nav_p *= (1+pr); nav_b *= (1+br)
            rows.append({'date': ws, 'n_sig': int(sig_mask.sum()), 'port': round(pr*100, 3),
                         'base': round(br*100, 3), 'ex': round((pr-br)*100, 3),
                         'nav_p': round(nav_p, 4), 'win': int(pr > br)})
        rdf = pd.DataFrame(rows)
        if rdf.empty: return {'error': 'empty'}
        rp = rdf['port'].values/100; years = len(rdf)*hold/243
        ann_p = nav_p**(1/years)-1 if years > 0 else 0
        ann_b = nav_b**(1/years)-1 if years > 0 else 0
        nav_arr = rdf['nav_p'].values
        mdd = float((nav_arr/np.maximum.accumulate(nav_arr)-1).min())
        ex = rdf['ex'].values/100
        t_val = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if ex.std() > 0 else np.nan
        return {'n': len(rdf), 'nav': round(nav_p, 4), 'nav_b': round(nav_b, 4),
                'ann': round(ann_p*100, 2), 'ann_b': round(ann_b*100, 2),
                'excess': round((nav_p/nav_b-1)*100, 2), 'mdd': round(mdd*100, 2),
                'win': round(rdf['win'].mean()*100, 1),
                'avg_ex_pct': round(ex.mean()*100, 3), 't': round(t_val, 2),
                'avg_nsig': round(rdf['n_sig'].mean(), 1),
                'detail': rdf.to_dict('records')}

    sig_out = {}
    sig_out['union4_full'] = sig_backtest(sig_cols)
    if 'error' not in sig_out['union4_full']:
        s = sig_out['union4_full']
        log(f'[信号并集4组/full] ann={s["ann"]}% base={s["ann_b"]}% ex={s["excess"]}% '
            f't={s["t"]} win={s["win"]}% mdd={s["mdd"]}% 平均信号数={s["avg_nsig"]}')
    # 分年
    if 'detail' in sig_out['union4_full']:
        det = pd.DataFrame(sig_out['union4_full']['detail'])
        det['year'] = det['date'].str[:4]
        yr = det.groupby('year')['ex'].mean().round(3)
        sig_out['union4_yearly'] = yr.to_dict()
        log('分年超额%: ' + str(dict(yr)))

    fp = os.path.join(REPORTS, 'fast_backtest_v4_20260905.json')
    json.dump({'model_backtest': out, 'signal_port': {k: {kk: vv for kk, vv in v.items() if kk != 'detail'}
                                                       for k, v in sig_out.items()}},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log(f'落盘 {fp}')

    print('\n' + '='*100)
    print(f'{"模型":<10} {"段":<6} {"净值":<9} {"基准":<9} {"年化%":<8} {"基准%":<8} {"超额%":<8} {"MDD%":<8} {"胜率%":<6}')
    print('-'*100)
    for tag in ['v2_short', 'v4_short', 'v2_mid', 'v4_mid']:
        for seg in ['full', 'train', 'test']:
            r = out.get(tag, {}).get(seg, {})
            if 'error' in r or not r: continue
            print(f'{tag:<10} {seg:<6} {r["nav"]:<9} {r["nav_b"]:<9} {r["ann"]:<8} '
                  f'{r["ann_b"]:<8} {r["excess"]:<8} {r["mdd"]:<8} {r["win"]:<6}')
    s = sig_out.get('union4_full', {})
    if 'error' not in s:
        print('-'*100)
        print(f'{"信号直构并集":<12} {s["nav"]:<9} {s["nav_b"]:<9} {s["ann"]:<8} '
              f'{s["ann_b"]:<8} {s["excess"]:<8} {s["mdd"]:<8} {s["win"]:<6}  t={s["t"]}')

if __name__ == '__main__':
    main()
