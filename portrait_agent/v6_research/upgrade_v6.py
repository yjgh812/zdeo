# -*- coding: utf-8 -*-
"""画像体系升级v6: 压力测试+敏感性+60日标签(2026-09-06 主人指令: 完善优化迭代升级).

B 极端行情压力测试(文档§5.2): 组合C在2015股灾/2016熔断/2018熊/2024小盘崩盘/2025关税冲击的分段表现
C 配方敏感性(文档§5.3): leave-one-out(正4去一/负9去一共13变体)的t值矩阵 → 判定配方稳健性
D 60日中线标签(文档§1.2): fwd60_close画像 + 组合C信号族与真信号族的60日表现
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')
POS4 = ['chan_buy2_10', 'chan_buy1_10', 'island_5', 'gap_up_hold']
NEG8 = ['vwap60_bias', 'vwap20_bias', 'limit_break_5', 'gap_up_run_5',
        'rounded_20', 'up4of5', 'new_high_3', 'lhb_buy_5']

def main():
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m['lim_today'] = m['lim_today'].fillna(False).astype(bool)
    # rel_ind_hi
    if 'rel_ind_hi' not in m.columns:
        m['rel_ind_hi'] = m.groupby('date')['rel_ind_ret'].rank(pct=True) >= 0.90
    # D: fwd60_close
    m = m.sort_values(['code', 'date']).reset_index(drop=True)
    m['fwd60_close'] = m.groupby('code')['close'].transform(lambda s: s.shift(-60)/s - 1)
    groups = {d: g for d, g in m.groupby('date')}
    all_dates = sorted(m['date'].unique())
    print(f'面板 {m.shape} 截面={len(groups)}')

    def run(pos, negs, hold=20, fwd_col='fwd20_close', cap=60, ret_detail=False):
        nav_p = nav_b = 1.0; rows = []
        for ws in all_dates[::hold]:
            cs = groups.get(ws)
            if cs is None: continue
            cs2 = cs[cs[fwd_col].notna() & (~cs['lim_today'])]
            if len(cs2) < 500: continue
            sig = np.zeros(len(cs2), dtype=bool)
            for c in pos:
                if c in cs2.columns: sig |= (cs2[c].fillna(0) > 0).to_numpy()
            for c in negs:
                if c in cs2.columns: sig &= ~((cs2[c].fillna(0) > 0.5).to_numpy())
            if 'rel_ind_hi' in negs:
                sig &= ~(cs2['rel_ind_hi'].fillna(False).to_numpy())
            fv = cs2[fwd_col].astype(float).to_numpy()
            sel = fv[sig]
            if len(sel) < 5: continue
            sel = sel[:cap]
            pr = float(sel.mean()); br = float(fv.mean())
            nav_p *= (1+pr); nav_b *= (1+br)
            rows.append({'date': ws, 'n': int(sig.sum()), 'port': round(pr*100, 3),
                         'base': round(br*100, 3), 'ex': round((pr-br)*100, 3),
                         'win': int(pr > br)})
        rdf = pd.DataFrame(rows)
        if rdf.empty: return None
        ex = rdf['ex'].values/100
        t_val = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if ex.std() > 0 else np.nan
        years = len(rdf)*hold/243
        return {'n': len(rdf), 'ann': round((nav_p**(1/years)-1)*100, 2) if years > 0 else None,
                'ann_b': round((nav_b**(1/years)-1)*100, 2) if years > 0 else None,
                't': round(t_val, 2), 'win': round(rdf['win'].mean()*100, 1),
                'avg_ex': round(ex.mean()*100, 3), 'detail': rdf if ret_detail else None}

    # ===== B 压力测试 =====
    print('\n===== B 极端行情压力测试 =====')
    base = run(POS4, NEG8 + ['rel_ind_hi'], ret_detail=True)
    det = base['detail'].copy(); det['year'] = det['date'].str[:4]
    STRESS = {'2015股灾(6-8月)': ('20150601', '20150930'),
              '2016熔断(1月)': ('20160101', '20160131'),
              '2018贸易战熊市': ('20180101', '20181231'),
              '2024小盘崩盘(1-2月)': ('20240101', '20240229'),
              '2025关税冲击(4月)': ('20250401', '20250430')}
    stress_out = {}
    for name, (d0, d1) in STRESS.items():
        seg = det[(det['date'] >= d0) & (det['date'] <= d1)]
        if seg.empty:
            stress_out[name] = {'n': 0, 'note': '窗口未覆盖(20日窗口采样稀疏)'}
            continue
        port_cum = float(np.prod(1 + seg['port'].values/100) - 1)
        base_cum = float(np.prod(1 + seg['base'].values/100) - 1)
        stress_out[name] = {'n_windows': len(seg),
                            '组合累计%': round(port_cum*100, 2),
                            '基准累计%': round(base_cum*100, 2),
                            '超额%': round((port_cum-base_cum)*100, 2),
                            '最差窗口超额%': round(seg['ex'].min(), 2),
                            '胜率%': round(seg['win'].mean()*100, 1)}
        print(f'  {name}: n={len(seg)} 组合{port_cum*100:+.1f}% vs 基准{base_cum*100:+.1f}% '
              f'超额{(port_cum-base_cum)*100:+.1f}% 最差窗{seg["ex"].min():+.2f}% 胜率{seg["win"].mean()*100:.0f}%')

    # ===== C 敏感性 leave-one-out =====
    print('\n===== C 配方敏感性(leave-one-out) =====')
    full_neg = NEG8 + ['rel_ind_hi']
    variants = {'baseline(全配方)': (POS4, full_neg)}
    for p in POS4:
        variants[f'去正信号[{p}]'] = ([x for x in POS4 if x != p], full_neg)
    for ng in full_neg:
        variants[f'去负过滤[{ng}]'] = (POS4, [x for x in full_neg if x != ng])
    sens = {}
    for name, (p, n) in variants.items():
        r = run(p, n)
        if r:
            sens[name] = {k: r[k] for k in ('n', 'ann', 't', 'win', 'avg_ex')}
            print(f'  {name:<28} t={r["t"]:>6} 年化={r["ann"]:>7}% 胜率={r["win"]}% 平均超额={r["avg_ex"]}%')
    base_t = sens['baseline(全配方)']['t']
    degr = max(abs(base_t - v['t']) for v in sens.values())
    print(f'  → t值波动范围 {min(v["t"] for v in sens.values())}~{max(v["t"] for v in sens.values())} '
          f'(基线{base_t}); 最大退化{degr:.2f}')

    # ===== D 60日中线标签 =====
    print('\n===== D 60日中线标签画像 =====')
    pool60 = m.dropna(subset=['fwd60_close'])
    day_mean60 = pool60.groupby('date')['fwd60_close'].mean()
    prof60 = []
    for f in POS4 + ['chan_bdiv_10', 'dip_reb_10', 'macd_top_div', 'ind_lim_ge3',
                     'lhb_buy_5', 'new_high_3', 'limit_break_5']:
        sub = m[m[f].fillna(0) > 0].dropna(subset=['fwd60_close'])
        if len(sub) < 100: continue
        ex = (sub['fwd60_close'] - sub['date'].map(day_mean60)).dropna()
        t_val = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if ex.std() > 0 else np.nan
        prof60.append({'feat': f, 'ex60_pct': round(ex.mean()*100, 3),
                       't': round(t_val, 2) if not np.isnan(t_val) else None,
                       'win_pct': round((ex > 0).mean()*100, 1), 'n': len(sub)})
    print(pd.DataFrame(prof60).to_string(index=False))
    # 组合C 60日持有
    r60 = run(POS4, full_neg, hold=60, fwd_col='fwd60_close')
    if r60:
        print(f'  组合C(60日持有): 年化={r60["ann"]}% vs 基准{r60["ann_b"]}% t={r60["t"]} '
              f'胜率={r60["win"]}% n={r60["n"]}')

    fp = os.path.join(REPORTS, 'upgrade_v6_20260906.json')
    json.dump({'stress': stress_out, 'sensitivity': sens, 'profile60': prof60,
               'combo_c_60d': r60 if r60 else None},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    print(f'\n落盘 {fp}')

if __name__ == '__main__':
    main()
