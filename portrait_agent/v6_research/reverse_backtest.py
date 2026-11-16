# -*- coding: utf-8 -*-
"""反向选股验证: v2模型RankIC稳定为负 → Bottom20(prob最低)可能正alpha(2026-09-05).

假设: 模型学到'高波动+高位'→未来top10%概率高, 但这类股票实际收益差(负IC);
反向选prob最低的20只 = 低波动/低位特征组合, 可能稳定跑赢等权基准.
同框架: 非重叠窗口/收盘口径/Top20等权/全截面等权基准/分full-train-test.
对照: v1与v2模型 × Top20与Bottom20.
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
from feature_engine import FEATURES
from retrain_v2_bigdata import FEATURES_V2, add_features_labels

TOPN = 20
MIN_SECTION = 500
T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

def backtest_dir(panel, booster, feats, hold=5, pick='bottom', segment='full', i7=0):
    fwd_col = 'fwd5_close' if hold == 5 else 'fwd20_close'
    dates = sorted(panel['date'].unique())
    if segment == 'train': dates = dates[:i7]
    elif segment == 'test': dates = dates[i7:]
    nav_p = nav_b = 1.0
    rows = []
    groups = {d: g for d, g in panel.groupby('date')}   # 预分组(消除全表扫描)
    for ws in dates[::hold]:
        cs = groups.get(ws)
        if cs is None: continue
        cs = cs[cs[fwd_col].notna() & (~cs['lim_today'])]
        if len(cs) < MIN_SECTION: continue
        cs['prob'] = booster.predict(cs[feats].to_numpy(dtype=np.float32))
        sel = cs.nsmallest(TOPN, 'prob') if pick == 'bottom' else cs.nlargest(TOPN, 'prob')
        if len(sel) < TOPN: continue
        pr = float(sel[fwd_col].astype(float).mean())
        br = float(cs[fwd_col].astype(float).mean())
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
    return {'n': len(df), 'range': f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}",
            'nav': round(nav_p, 4), 'nav_b': round(nav_b, 4),
            'ann': round(ann_p*100, 2), 'ann_b': round(ann_b*100, 2),
            'excess': round((nav_p/nav_b-1)*100, 2), 'mdd': round(mdd*100, 2),
            'sharpe': round(float(sharpe), 3), 'win': round(df['win'].mean()*100, 1),
            'avg_ex_pct': round(df['ex'].mean(), 3),
            'detail': df.to_dict('records')}

def main():
    log('加载大面板...')
    panel = pd.read_pickle(os.path.join(CACHE, 'panel_v2_big.pkl'))
    panel = add_features_labels(panel)
    all_dates = sorted(panel['date'].unique())
    i7 = int(len(all_dates)*0.7)

    out = {}
    combos = [
        ('v2_short_bottom', 'v2.0.0_short.txt', FEATURES_V2, 5, 'bottom'),
        ('v2_mid_bottom',   'v2.0.0_mid.txt',   FEATURES_V2, 20, 'bottom'),
        ('v1_short_bottom', 'v1.0.0_short.txt', FEATURES, 5, 'bottom'),
        ('v1_mid_bottom',   'v1.0.0_mid.txt',   FEATURES, 20, 'bottom'),
    ]
    for name, mf, feats, hold, pick in combos:
        mp = os.path.join(MODELS, mf)
        if not os.path.exists(mp): continue
        bst = lgb.Booster(model_file=mp)
        out[name] = {}
        for seg in ['full', 'train', 'test']:
            r = backtest_dir(panel, bst, feats, hold, pick, seg, i7)
            out[name][seg] = r
            if 'error' not in r:
                log(f'[{name}/{seg}] ann={r["ann"]}% base={r["ann_b"]}% excess={r["excess"]}% '
                    f'mdd={r["mdd"]}% win={r["win"]}% n={r["n"]}')

    # 分年稳定性(最有说服力的维度): v2_short_bottom/v2_mid_bottom 的逐年窗口超额
    log('分年超额稳定性...')
    yearly = {}
    for name, mf, feats, hold in [('v2_short_bottom', 'v2.0.0_short.txt', FEATURES_V2, 5),
                                  ('v2_mid_bottom', 'v2.0.0_mid.txt', FEATURES_V2, 20)]:
        bst = lgb.Booster(model_file=os.path.join(MODELS, mf))
        r = backtest_dir(panel, bst, feats, hold, 'bottom', 'full', i7)
        det = pd.DataFrame(r['detail'])
        det['year'] = det['date'].str[:4]
        yr = det.groupby('year').agg(n=('ex', 'size'), avg_ex=('ex', 'mean'),
                                     win=('win', 'mean'), port=('port', 'mean'),
                                     base=('base', 'mean')).round(3)
        yearly[name] = yr.to_dict('index')

    fp = os.path.join(REPORTS, 'reverse_backtest_20260905.json')
    json.dump({'backtest': {k: {s: {kk: vv for kk, vv in r.items() if kk != 'detail'}
                                 for s, r in v.items()} for k, v in out.items()},
               'yearly': yearly}, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)

    print('\n' + '='*95)
    print(f'{"组合":<20} {"段":<6} {"n":<5} {"净值":<9} {"基准":<9} {"年化%":<8} {"基准年化%":<9} '
          f'{"超额%":<9} {"MDD%":<8} {"夏普":<6} {"胜率%":<6}')
    print('-'*95)
    for name in out:
        for seg in ['full', 'train', 'test']:
            r = out[name].get(seg, {})
            if 'error' in r or not r: continue
            print(f'{name:<20} {seg:<6} {r["n"]:<5} {r["nav"]:<9} {r["nav_b"]:<9} {r["ann"]:<8} '
                  f'{r["ann_b"]:<9} {r["excess"]:<9} {r["mdd"]:<8} {r["sharpe"]:<6} {r["win"]:<6}')
    print('='*95)
    print('\n分年超额(平均每窗口超额%, win=窗口胜率):')
    for name, yr in yearly.items():
        print(f'\n{name}:')
        for y, v in sorted(yr.items()):
            print(f'  {y}: n={v["n"]} avg_ex={v["avg_ex"]}% win={v["win"]*100:.0f}% '
                  f'port={v["port"]}% base={v["base"]}%')
    log(f'落盘 {fp}')

if __name__ == '__main__':
    main()
