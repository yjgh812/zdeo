# -*- coding: utf-8 -*-
"""信号直构组合v3终版优化(2026-09-06).

原则: 不调参不做网格扫描, 全部用画像已验证特征:
  正池(t>2): chan_buy2_10/chan_buy1_10/island_5/gap_up_hold/dip_reb_10/macd_top_div/
             macd_hist_shrink3/ind_lim_ge3
  强负过滤(|t|>20): vwap60_bias/vwap20_bias/limit_break_5/gap_up_run_5/rounded_20/
             up4of5/new_high_3/lhb_buy_5 + rel_ind_ret当日截面top10%
变体(结构性的, 非参数扫描):
  A pos4原班+强负8过滤     B 全正池9+强负8过滤
  C pos4+强负8+rel_ind过滤 D B+持有10日对照
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')
T0 = __import__('time').time()
def log(m): print(f'[{__import__("time").time()-T0:7.1f}s] {m}', flush=True)

def main():
    log('加载v5面板...')
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m['lim_today'] = m['lim_today'].fillna(False).astype(bool)
    # rel_ind_ret 截面分位(过滤用)
    m['rel_ind_hi'] = m.groupby('date')['rel_ind_ret'].rank(pct=True) >= 0.90
    groups = {d: g for d, g in m.groupby('date')}
    all_dates = sorted(m['date'].unique())
    log(f'截面={len(groups)}')

    POS4 = ['chan_buy2_10', 'chan_buy1_10', 'island_5', 'gap_up_hold']
    POS9 = POS4 + ['dip_reb_10', 'macd_top_div', 'macd_hist_shrink3', 'ind_lim_ge3']
    NEG8 = ['vwap60_bias', 'vwap20_bias', 'limit_break_5', 'gap_up_run_5',
            'rounded_20', 'up4of5', 'new_high_3', 'lhb_buy_5']

    def run(pos, negs, hold=20, cap=60):
        nav_p = nav_b = 1.0; rows = []
        for ws in all_dates[::hold]:
            cs = groups.get(ws)
            if cs is None: continue
            cs2 = cs[cs['fwd20_close'].notna() & (~cs['lim_today'])]
            if len(cs2) < 500: continue
            sig = np.zeros(len(cs2), dtype=bool)
            for c in pos:
                if c in cs2.columns:
                    sig |= (cs2[c].fillna(0) > 0).to_numpy()
            for c in negs:
                if c in cs2.columns:
                    sig &= ~((cs2[c].fillna(0) > 0.5).to_numpy())
            if 'rel_ind_hi' in negs:
                sig &= ~(cs2['rel_ind_hi'].fillna(False).to_numpy())
            fv = cs2['fwd20_close'].astype(float).to_numpy()
            sel = fv[sig]
            if len(sel) < 5: continue
            sel = sel[:cap]
            pr = float(sel.mean()); br = float(fv.mean())
            nav_p *= (1+pr); nav_b *= (1+br)
            rows.append({'date': ws, 'n': int(sig.sum()), 'port': round(pr*100, 3),
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
        yr = rdf.copy(); yr['year'] = yr['date'].str[:4]
        yearly = yr.groupby('year')['ex'].mean().round(3).to_dict()
        neg_yrs = [y for y, v in yearly.items() if v < 0]
        return {'n': len(rdf), 'nav': round(nav_p, 4), 'nav_b': round(nav_b, 4),
                'ann': round(ann_p*100, 2), 'ann_b': round(ann_b*100, 2),
                'excess': round((nav_p/nav_b-1)*100, 2), 'mdd': round(mdd*100, 2),
                'win': round(rdf['win'].mean()*100, 1), 't': round(t_val, 2),
                'avg_n': round(rdf['n'].mean(), 1), 'yearly': yearly,
                'neg_years': neg_yrs, 'detail': rdf.to_dict('records')}

    variants = {
        'A_pos4_neg8': run(POS4, NEG8),
        'B_pos9_neg8': run(POS9, NEG8),
        'C_pos4_neg8_relind': run(POS4, NEG8 + ['rel_ind_hi']),
        'D_pos9_neg8_h10': run(POS9, NEG8, hold=10),
    }
    for k, v in variants.items():
        if 'error' not in v:
            log(f'[{k}] ann={v["ann"]}% base={v["ann_b"]}% ex={v["excess"]}% t={v["t"]} '
                f'win={v["win"]}% mdd={v["mdd"]}% n_sel={v["avg_n"]} 年负{len(v["neg_years"])}/{len(v["yearly"])} '
                f'负年={v["neg_years"]}')
    # 最优变体分年打印
    best = max((k for k in variants if 'error' not in variants[k]),
               key=lambda k: variants[k]['t'])
    log(f'最优(按t): {best}; 分年超额: {variants[best]["yearly"]}')
    fp = os.path.join(REPORTS, 'combo_v3_20260906.json')
    json.dump({k: {kk: vv for kk, vv in v.items() if kk != 'detail'}
               for k, v in variants.items()},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log(f'落盘 {fp}')

if __name__ == '__main__':
    main()
