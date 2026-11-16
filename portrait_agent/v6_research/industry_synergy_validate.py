# -*- coding: utf-8 -*-
"""板块联动×组合C信号交叉验证(2026-09-06 主人指令: 验证ind_lim_ge3进组合构建).

假设(预指定主假设, 防数据窥探):
  H1(主): 组合C信号股中, 当日所属行业涨停联动≥3(ind_lim_ge3)的子组, fwd20超额显著高于无联动子组
  逻辑: 强板块内的低位信号股=补涨候选(板块正联动+0.19%t17 × 个股低位反转t29), 且规避板块内追高(负t-44)
  H2(次, 描述性): 行业20日涨幅rank高位子组(Ind_mom_hi)同理——不作决策依据
验证两层:
  L1 子组分层(14年): 联动组 vs 无联动组的超额/t/胜率 + 组间差检验
  L2 组合级回测: 每窗口优先联动股(cap内先选联动组) vs 组合C基线, 对比t/年化/胜率
判定: H1过阈=联动组t≥2 且 组间差t≥2 → combo_d候选prereg; 否则登记证伪
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, 'reports')
POS4 = ['chan_buy2_10', 'chan_buy1_10', 'island_5', 'gap_up_hold']
NEG8 = ['vwap60_bias', 'vwap20_bias', 'limit_break_5', 'gap_up_run_5',
        'rounded_20', 'up4of5', 'new_high_3', 'lhb_buy_5']

def main():
    m = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v5_full.pkl'))
    m['lim_today'] = m['lim_today'].fillna(False).astype(bool)
    m['rel_ind_hi'] = m.groupby('date')['rel_ind_ret'].rank(pct=True) >= 0.90
    pool = m.dropna(subset=['fwd20_close'])
    day_mean = pool.groupby('date')['fwd20_close'].mean()
    # 板块联动(主假设) 与 行业动量(次要)
    m['ind_lim3'] = m['ind_limit_cnt'] >= 3
    m['ind_mom_hi'] = m.groupby('date')['ind_ret_rank'].transform(lambda s: s >= 0.8)

    # ===== L1 子组分层 =====
    sig = np.zeros(len(m), dtype=bool)
    for c in POS4:
        sig |= (m[c].fillna(0) > 0).to_numpy()
    for c in NEG8:
        sig &= ~((m[c].fillna(0) > 0.5).to_numpy())
    sig &= ~(m['rel_ind_hi'].fillna(False).to_numpy())
    m['combo_sig'] = sig
    sub = m[sig & m['fwd20_close'].notna()].copy()
    sub['ex'] = sub['fwd20_close'] - sub['date'].map(day_mean)
    print(f'组合C信号全样本: {len(sub)}股次')
    def _stat(g, label):
        if len(g) < 100:
            print(f'{label}: n={len(g)} 样本不足'); return None
        t = float(g['ex'].mean()/(g['ex'].std()/np.sqrt(len(g))))
        r = {'label': label, 'n': len(g), 'ex_mean_pct': round(g['ex'].mean()*100, 3),
             't': round(t, 2), 'win_pct': round((g['ex'] > 0).mean()*100, 1),
             'fwd_mean_pct': round(g['fwd20_close'].mean()*100, 3),
             'fwd_win_pct': round((g['fwd20_close'] > 0).mean()*100, 1),
             'dn_rate': round((g['fwd20_close'] <= -0.15).mean()*100, 2)}
        print(f"{label}: n={r['n']} 超额={r['ex_mean_pct']}%(t={r['t']}) 胜率={r['fwd_win_pct']}% "
              f"绝对均值={r['fwd_mean_pct']}% 暴跌率={r['dn_rate']}%")
        return r
    print('\n===== L1 子组分层(H1主假设: 行业涨停联动≥3) =====')
    g1 = sub[sub['ind_lim3']]; g0 = sub[~sub['ind_lim3']]
    r1 = _stat(g1, '联动组(涨停≥3)')
    r0 = _stat(g0, '无联动组')
    # 组间差(两独立样本均值差检验)
    if r1 and r0:
        d = g1['ex'].values - g1['ex'].mean()  # 占位
        from scipy.stats import ttest_ind
        tt = ttest_ind(g1['ex'].dropna(), g0['ex'].dropna(), equal_var=False)
        print(f'组间差: {r1["ex_mean_pct"]-r0["ex_mean_pct"]:+.3f}pp, Welch t={tt.statistic:.2f} p={tt.pvalue:.4f}')
    print('\n(H2次要描述: 行业20日动量rank≥0.8)')
    _stat(sub[sub['ind_mom_hi']], '行业动量高组')
    _stat(sub[~sub['ind_mom_hi']], '行业动量低组')

    # ===== L2 组合级回测: 联动优先 vs 基线 =====
    print('\n===== L2 组合级回测(每20日窗口, cap=60) =====')
    groups = {d: g for d, g in m.groupby('date')}
    all_dates = sorted(m['date'].unique())
    def bt(priority=None, hold=20):
        nav_p = nav_b = 1.0; rows = []
        for ws in all_dates[::hold]:
            cs = groups.get(ws)
            if cs is None: continue
            cs2 = cs[cs['fwd20_close'].notna() & (~cs['lim_today'])]
            if len(cs2) < 500: continue
            s = np.zeros(len(cs2), dtype=bool)
            for c in POS4: s |= (cs2[c].fillna(0) > 0).to_numpy()
            for c in NEG8: s &= ~((cs2[c].fillna(0) > 0.5).to_numpy())
            s &= ~(cs2['rel_ind_hi'].fillna(False).to_numpy())
            fv = cs2['fwd20_close'].astype(float).to_numpy()
            idx = np.where(s)[0]
            if len(idx) < 5: continue
            if priority is not None:
                lim = cs2['ind_lim3'].fillna(False).to_numpy()[idx]
                idx = np.concatenate([idx[lim], idx[~lim]])   # 联动组优先
            idx = idx[:60]
            pr = float(fv[idx].mean()); br = float(fv.mean())
            nav_p *= (1+pr); nav_b *= (1+br)
            rows.append({'date': ws, 'port': round(pr*100, 3), 'base': round(br*100, 3),
                         'ex': round((pr-br)*100, 3), 'nav': round(nav_p, 4), 'win': int(pr > br)})
        rdf = pd.DataFrame(rows)
        ex = rdf['ex'].values/100
        t = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if ex.std() > 0 else np.nan
        yrs = len(rdf)*hold/243
        return {'n': len(rdf), 'ann': round((nav_p**(1/yrs)-1)*100, 2) if yrs > 0 else None,
                'ann_b': round((nav_b**(1/yrs)-1)*100, 2) if yrs > 0 else None,
                't': round(t, 2), 'win': round(rdf['win'].mean()*100, 1),
                'avg_ex': round(ex.mean()*100, 3)}
    b0 = bt()
    b1 = bt(priority='lim3')
    print(f"基线组合C:  {b0}")
    print(f"联动优先版: {b1}")

    # 判定
    h1_pass = r1 is not None and r1['t'] >= 2 and (r1['ex_mean_pct'] - r0['ex_mean_pct']) > 0 and tt_p_ok(tt) if False else None
    fp = os.path.join(REPORTS, 'industry_synergy_20260906.json')
    json.dump({'L1': {'lim_grp': r1, 'nolim_grp': r0,
                      'welch_t': round(float(tt.statistic), 2), 'p': round(float(tt.pvalue), 4)},
               'L2': {'baseline': b0, 'lim_priority': b1}},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    print(f'\n落盘 {fp}')

def tt_p_ok(tt): return tt.pvalue < 0.05

if __name__ == '__main__':
    main()
