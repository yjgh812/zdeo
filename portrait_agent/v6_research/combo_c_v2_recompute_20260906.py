# -*- coding: utf-8 -*-
"""P2 修复第 3 步 · 组合C 用 v2 无前视信号重算（同面板双臂对照）。
A=panel 内置 v1 列(基线锚, 应复现登记值≈年化20.7%/t3.78) B=v2确认日重建立列(裁决臂)。
门禁(prereg_combo_c 新指纹重登记): B 超额 t>=3.0 且 成本后年化超额>0 -> 过, chan 成分可回影子。
引擎逐行复用 combo_c_validation.py（POS4/NEG8/head(60)/20bar 窗/成本口径）, 仅 chan 两列数据源不同。"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')
COST_RT = 0.003
POS_CHAN = ['chan_buy1_10', 'chan_buy2_10']
POS_FIXED = ['island_5', 'gap_up_hold']
NEG8 = ['vwap60_bias', 'vwap20_bias', 'limit_break_5', 'gap_up_run_5',
        'rounded_20', 'up4of5', 'new_high_3', 'lhb_buy_5']

def log(s): print(s, flush=True)

def rebuild_chan(panel, pkl, tag):
    """与 retrain_v4_full.py 逐行同构: merge→fillna→groupby rolling(10).max()"""
    chan = pd.read_pickle(pkl)
    chan = chan[chan['type'].isin(['buy1', 'buy2'])][['code', 'date', 'type']].drop_duplicates()
    out = panel[['code', 'date']].copy()
    for t, col in [('buy1', f'chan_buy1_10_{tag}'), ('buy2', f'chan_buy2_10_{tag}')]:
        sig = chan[chan['type'] == t][['code', 'date']].drop_duplicates()
        sig['sig'] = 1.0
        out = out.merge(sig.rename(columns={'sig': 'sig_'}), on=['code', 'date'], how='left')
        out['sig_'] = out['sig_'].fillna(0)
        out[col] = out.groupby('code')['sig_'].transform(lambda s: s.rolling(10).max())
        out = out.drop(columns=['sig_'])
    return out[[f'chan_buy1_10_{tag}', f'chan_buy2_10_{tag}']]

def run_sim(m, pos_cols, tag):
    groups = {d: g for d, g in m.groupby('date')}
    all_dates = sorted(m['date'].unique())
    nav_p0 = nav_pc = nav_b = 1.0
    prev_set = set()
    rows = []
    for ws in all_dates[::20]:
        cs = groups.get(ws)
        if cs is None:
            continue
        cs2 = cs[cs['fwd20_close'].notna() & (~cs['lim_today'])]
        if len(cs2) < 500:
            continue
        sig = np.zeros(len(cs2), dtype=bool)
        for c in pos_cols:
            sig |= (cs2[c].fillna(0) > 0).to_numpy()
        for c in NEG8:
            sig &= ~((cs2[c].fillna(0) > 0.5).to_numpy())
        sig &= ~(cs2['rel_ind_hi'].fillna(False).to_numpy())
        sel_df = cs2[sig]
        if len(sel_df) < 5:
            continue
        codes = sel_df.head(60)['code'].tolist()
        fv = sel_df.head(60)['fwd20_close'].astype(float).to_numpy()
        fv_all = cs2['fwd20_close'].astype(float).to_numpy()
        pr = float(fv.mean()); br = float(fv_all.mean())
        cur_set = set(codes)
        inter = len(cur_set & prev_set)
        union = len(cur_set | prev_set) if prev_set else len(cur_set)
        turnover = 1 - inter / len(cur_set) if cur_set and prev_set else 1.0
        prev_set = cur_set
        nav_p0 *= (1 + pr); nav_pc *= (1 + pr - COST_RT); nav_b *= (1 + br)
        rows.append({'date': ws, 'ex': (pr - br), 'pr_c': pr - COST_RT, 'win': int(pr > br)})
    df = pd.DataFrame(rows)
    ex = df['ex'].values
    t0 = float(ex.mean() / (ex.std() / np.sqrt(len(ex))))
    years = len(df) * 20 / 243
    ann0 = nav_p0 ** (1 / years) - 1
    annc = nav_pc ** (1 / years) - 1
    annb = nav_b ** (1 / years) - 1
    return {'tag': tag, 'n_windows': len(df),
            'ann_nocost_pct': round(ann0 * 100, 2), 'ann_cost_pct': round(annc * 100, 2),
            'ann_base_pct': round(annb * 100, 2),
            'excess_t': round(t0, 2), 'win_rate_pct': round(df['win'].mean() * 100, 1)}

def main():
    t0 = time.time()
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m['lim_today'] = m['lim_today'].fillna(False).astype(bool)
    m['rel_ind_hi'] = m.groupby('date')['rel_ind_ret'].rank(pct=True) >= 0.90
    log(f'面板 {len(m)} 行 载入 {time.time()-t0:.0f}s')
    # v2 列重建
    m[['chan_buy1_10_v2', 'chan_buy2_10_v2']] = rebuild_chan(m, os.path.join(CACHE, 'chan_signals_v2.pkl'), 'v2')
    log(f'v2列重建完成 {time.time()-t0:.0f}s (buy2_v2>0行={int((m["chan_buy2_10_v2"]>0).sum())})')
    arms = [
        ('v1_中心日(带前视,基线锚)', ['chan_buy1_10', 'chan_buy2_10'] + POS_FIXED),
        ('v2_确认日(无前视,裁决臂)', ['chan_buy1_10_v2', 'chan_buy2_10_v2'] + POS_FIXED),
    ]
    results = {}
    for tag, pos in arms:
        r = run_sim(m, pos, tag)
        results[tag] = r
        log(f'[{tag}] 年化(无成本)={r["ann_nocost_pct"]}% 成本后={r["ann_cost_pct"]}% '
            f'基准={r["ann_base_pct"]}% 超额t={r["excess_t"]} 胜率={r["win_rate_pct"]}% 窗={r["n_windows"]}')
    b = results['v2_确认日(无前视,裁决臂)']
    gate = (b['excess_t'] >= 3.0 and b['ann_cost_pct'] > b['ann_base_pct'])
    results['gate'] = {'条件': '超额t>=3.0 且 成本后年化>基准', '结果': bool(gate)}
    log(f'[GATE] 新指纹重登记影子: {"PASS" if gate else "FAIL"}')
    json.dump(results, open(os.path.join(REPORTS, 'combo_c_v2_recompute_20260906.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log('落盘 combo_c_v2_recompute_20260906.json')

if __name__ == '__main__':
    main()
