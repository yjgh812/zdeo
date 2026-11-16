# -*- coding: utf-8 -*-
"""下跌画像与排雷分研究(2026-09-06 主人'继续': 体系文档§一下跌线+§7.2持仓避雷落地).

文档口径: 下跌标签=fwd20_close<=-15%(波段) + 截面bottom10%(相对)双口径.
三维度:
  D1 下跌画像: 下跌股 vs 非下跌股的特征出现率 lift(哪些特征是暴跌前兆)
  D2 排雷验证: 每特征触发后的P(20日跌>=15%)/平均收益/超额(负=排雷价值)
  D3 排雷分: 强负信号(|t|>10)计数合成 → 高雷组vs低雷组的暴跌率/收益差
产出: reports/downside_profile_20260906.json; 若排雷分有效→prereg登记建议
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')

ALL_EVENT = ['island_5', 'limit_break_5', 'gap_up_run_5', 'new_high_3', 'dip_reb_10',
             'rounded_20', 'morning_star', 'red_3_soldiers', 'bull_engulf', 'hammer_low',
             'duofangpao', 'macd_golden_hi', 'macd_golden_lo', 'macd_hist_shrink3',
             'macd_top_div', 'macd_2golden', 'macd_zero_up', 'up4of5', 'vol_shrink_pb',
             'vol_contract', 'ma_tangle_div', 'higher_low', 'new_high20', 'gap_up_hold',
             'nr7', 'inside_break', 'chan_buy1_10', 'chan_buy2_10', 'chan_buy3_10',
             'chan_bdiv_10', 'lhb_buy_5', 'ind_lim_ge3', 'mkt_hot']
CONT_HI = ['vwap60_bias', 'vwap20_bias']   # 连续特征高分位二值(高位乖离)
CONT_LO = ['pit_lev']                       # 高杠杆

def main():
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m['vwap60_bias_b'] = (m['vwap60_bias'] > 0.5).astype(float)
    m['vwap20_bias_b'] = (m['vwap20_bias'] > 0.5).astype(float)
    m['lev_hi'] = (m['pit_lev'] >= m['pit_lev'].quantile(0.8)).astype(float)
    m.loc[m['pit_lev'].isna(), 'lev_hi'] = np.nan
    feats = ALL_EVENT + ['vwap60_bias_b', 'vwap20_bias_b', 'lev_hi']

    # 标签双口径
    m['dn_abs'] = (m['fwd20_close'] <= -0.15).astype(float)
    m.loc[m['fwd20_close'].isna(), 'dn_abs'] = np.nan
    pct = m.groupby('date')['fwd20_close'].rank(pct=True)
    m['dn_rel'] = (pct <= 0.10).astype(float)
    m.loc[m['fwd20_close'].isna(), 'dn_rel'] = np.nan
    base_dn_rate = m['dn_abs'].mean()
    print(f'基础暴跌率(fwd20<=-15%): {base_dn_rate*100:.2f}%  n={int(m["dn_abs"].sum())}')

    pool = m.dropna(subset=['fwd20_close'])
    day_mean = pool.groupby('date')['fwd20_close'].mean()
    valid = m.dropna(subset=['dn_abs'])
    dn, ok = valid[valid['dn_abs'] == 1], valid[valid['dn_abs'] == 0]

    rows = []
    for f in feats:
        if f not in m.columns: continue
        col = m[f]
        if col.isna().all(): continue
        p_dn, p_ok = dn[f].mean(), ok[f].mean()
        lift = p_dn/p_ok if p_ok > 0 else np.nan
        sub = m[m[f] > 0].dropna(subset=['fwd20_close'])
        if len(sub) < 100:
            continue
        dn_rate = sub['dn_abs'].mean()
        ex = (sub['fwd20_close'] - sub['date'].map(day_mean)).dropna()
        t_val = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if ex.std() > 0 else np.nan
        rows.append({'feat': f, 'rate_dn': round(p_dn*100, 3), 'rate_ok': round(p_ok*100, 3),
                     'lift_dn': round(lift, 3) if not np.isnan(lift) else None,
                     'P_跌15': round(dn_rate*100, 2),
                     'ex20_pct': round(ex.mean()*100, 3),
                     't': round(t_val, 2) if not np.isnan(t_val) else None,
                     'n': len(sub)})
    df = pd.DataFrame(rows).sort_values('lift_dn', ascending=False)
    print('\n下跌画像(特征→暴跌前兆排序):\n' + df.to_string(index=False))

    # D3 排雷分: 用 lift_dn>1.2 且 t<-3 的特征计数
    risk_feats = df[(df['lift_dn'] > 1.2) & (df['t'] < -3)]['feat'].tolist()
    print(f'\n排雷特征入池({len(risk_feats)}): {risk_feats}')
    rcnt = np.zeros(len(m))
    for f in risk_feats:
        rcnt += (m[f].fillna(0) > 0.5).to_numpy()
    m['risk_score'] = rcnt
    # 分组: 0/1/2/3+
    m['risk_grp'] = pd.cut(m['risk_score'], [-1, 0, 1, 2, 100],
                           labels=['0无雷', '1', '2', '3+'])
    grp = m.dropna(subset=['fwd20_close']).groupby('risk_grp', observed=True).agg(
        n=('fwd20_close', 'size'),
        P_跌15=('dn_abs', 'mean'),
        avg_fwd20=('fwd20_close', 'mean'),
        med_fwd20=('fwd20_close', 'median'))
    grp['P_跌15'] = (grp['P_跌15']*100).round(2)
    grp['avg_fwd20'] = (grp['avg_fwd20']*100).round(3)
    grp['med_fwd20'] = (grp['med_fwd20']*100).round(3)
    print('\n排雷分组(0/1/2/3+个风险特征):\n' + grp.to_string())
    # 高雷组(3+) vs 无雷组的截面超额差
    hi = m[(m['risk_score'] >= 3)].dropna(subset=['fwd20_close'])
    lo = m[(m['risk_score'] == 0)].dropna(subset=['fwd20_close'])
    ex_hi = (hi['fwd20_close'] - hi['date'].map(day_mean)).dropna()
    ex_lo = (lo['fwd20_close'] - lo['date'].map(day_mean)).dropna()
    def _t(x): return float(x.mean()/(x.std()/np.sqrt(len(x)))) if len(x) > 5 and x.std() > 0 else np.nan
    summary_risk = {
        'risk_feats': risk_feats,
        'hi3_n': len(hi), 'hi3_P跌15': round(hi['dn_abs'].mean()*100, 2),
        'hi3_avg_fwd20': round(hi['fwd20_close'].mean()*100, 3),
        'hi3_ex_t': round(_t(ex_hi), 2) if not np.isnan(_t(ex_hi)) else None,
        'lo0_n': len(lo), 'lo0_P跌15': round(lo['dn_abs'].mean()*100, 2),
        'lo0_avg_fwd20': round(lo['fwd20_close'].mean()*100, 3),
        'base_P跌15': round(base_dn_rate*100, 2),
        '分组表': grp.reset_index().astype({'risk_grp': str}).to_dict('records'),
    }
    print(json.dumps(summary_risk, ensure_ascii=False, indent=1, default=float))

    fp = os.path.join(REPORTS, 'downside_profile_20260906.json')
    json.dump({'profile': df.to_dict('records'), 'risk_summary': summary_risk},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    print(f'\n落盘 {fp}')

if __name__ == '__main__':
    main()
