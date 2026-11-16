# -*- coding: utf-8 -*-
"""组合C生产前验证收口(2026-09-06 主人指令: 继续=成本测算+池增量+prereg数据).

1 成本侵蚀: 双边0.3%(佣金+印花+滑点, 宪法口径)/20日窗口; 另算真实换手率口径
2 bp+ep池增量: 池=reports/drift_check_20260901e_0901end/bp_ep_core_trades.csv
   (2016+, 2039笔持仓区间); 组合C信号分池内/池外, 各自20日窗口超额+重叠率
   ——同B4三闸递进逻辑的'池内增量'闸
输出: reports/combo_c_validation_20260906.json (prereg登记数据源)
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')
BP_TRADES = r'D:\zcodesj\reports\drift_check_20260901e_0901end\bp_ep_core_trades.csv'
COST_RT = 0.003   # 双边0.3%(宪法: 双边千三)
POS4 = ['chan_buy2_10', 'chan_buy1_10', 'island_5', 'gap_up_hold']
NEG8 = ['vwap60_bias', 'vwap20_bias', 'limit_break_5', 'gap_up_run_5',
        'rounded_20', 'up4of5', 'new_high_3', 'lhb_buy_5']

def main():
    def log(s): print(s, flush=True)
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m['lim_today'] = m['lim_today'].fillna(False).astype(bool)
    m['rel_ind_hi'] = m.groupby('date')['rel_ind_ret'].rank(pct=True) >= 0.90
    groups = {d: g for d, g in m.groupby('date')}
    all_dates = sorted(m['date'].unique())
    # bp_ep 持仓区间
    bp = pd.read_csv(BP_TRADES)
    bp['entry'] = bp['entry_date'].str.replace('-', '')
    bp['exit'] = bp['exit_date'].str.replace('-', '')
    holding = []   # (code, entry, exit)
    for r in bp.itertuples():
        holding.append((str(r.symbol).zfill(6), r.entry, r.exit))
    holding.sort(key=lambda x: x[1])
    import bisect
    h_starts = [h[1] for h in holding]

    # 预构建每日bp_ep池集合(展开2039笔持仓区间到交易日)
    from collections import defaultdict
    pool_by_day = defaultdict(set)
    dates_arr = sorted(m['date'].unique())
    for code, e, x in holding:
        i0 = bisect.bisect_left(dates_arr, e)
        i1 = bisect.bisect_right(dates_arr, x)
        for d in dates_arr[i0:i1]:
            pool_by_day[d].add(code)
    log(f'bp_ep池展开完成: 覆盖{len(pool_by_day)}日')

    def in_bp_pool(code, d):
        return code in pool_by_day.get(d, ())

    log_rows = []
    nav_p0 = nav_pc = nav_pt = nav_b = 1.0   # 0=无成本 c=固定成本 t=真实换手
    prev_set = set()
    for ws in all_dates[::20]:
        cs = groups.get(ws)
        if cs is None: continue
        cs2 = cs[cs['fwd20_close'].notna() & (~cs['lim_today'])]
        if len(cs2) < 500: continue
        sig = np.zeros(len(cs2), dtype=bool)
        for c in POS4:
            sig |= (cs2[c].fillna(0) > 0).to_numpy()
        for c in NEG8:
            sig &= ~((cs2[c].fillna(0) > 0.5).to_numpy())
        sig &= ~(cs2['rel_ind_hi'].fillna(False).to_numpy())
        sel_df = cs2[sig]
        if len(sel_df) < 5: continue
        sel_df = sel_df.nlargest(60, 'fwd20_close') if False else sel_df.head(60)  # 保持原序(无排序=全等权)
        codes = sel_df['code'].tolist()
        fv_all = cs2['fwd20_close'].astype(float).to_numpy()
        fv = sel_df['fwd20_close'].astype(float).to_numpy()
        pr = float(fv.mean()); br = float(fv_all.mean())
        # 池标注
        in_flags = [in_bp_pool(c, ws) for c in codes]
        # 真实换手(相邻窗口)
        cur_set = set(codes)
        inter = len(cur_set & prev_set)
        union = len(cur_set | prev_set) if prev_set else len(cur_set)
        turnover = 1 - inter/len(cur_set) if cur_set and prev_set else 1.0
        prev_set = cur_set
        # 成本口径
        pr_c = pr - COST_RT
        pr_t = pr - COST_RT * turnover
        nav_p0 *= (1+pr); nav_pc *= (1+pr_c); nav_pt *= (1+pr_t); nav_b *= (1+br)
        log_rows.append({'date': ws, 'n': len(codes), 'port': round(pr*100, 3),
                         'base': round(br*100, 3), 'ex': round((pr-br)*100, 3),
                         'nav0': round(nav_p0, 4), 'navc': round(nav_pc, 4),
                         'navt': round(nav_pt, 4), 'turnover': round(turnover, 3),
                         'n_in_pool': int(sum(in_flags)),
                         'ex_in': round((fv[np.array(in_flags)].mean() - br)*100, 3) if any(in_flags) else None,
                         'ex_out': round((fv[~np.array(in_flags)].mean() - br)*100, 3) if not all(in_flags) else None,
                         'win': int(pr > br)})
    df = pd.DataFrame(log_rows)
    ex = df['ex'].values/100
    t0 = float(ex.mean()/(ex.std()/np.sqrt(len(ex))))
    years = len(df)*20/243
    ann0 = nav_p0**(1/years)-1
    annc = nav_pc**(1/years)-1
    annt = nav_pt**(1/years)-1
    annb = nav_b**(1/years)-1
    # 池内/池外累计与t(2016+域才有效, 2013-2015 bp_ep无数据→in_pool恒False, 分层自动归池外)
    seg = df[df['date'] >= '20160101']
    ex_in = seg['ex_in'].dropna().values/100
    ex_out = seg['ex_out'].dropna().values/100
    def _t(x):
        return float(x.mean()/(x.std()/np.sqrt(len(x)))) if len(x) > 5 and x.std() > 0 else np.nan
    overlap = seg['n_in_pool'].sum() / seg['n'].sum() * 100
    out = {
        'cost': {
            'ann_nocost_pct': round(ann0*100, 2), 'ann_cost_fixed_pct': round(annc*100, 2),
            'ann_cost_turnover_pct': round(annt*100, 2), 'ann_base_pct': round(annb*100, 2),
            'avg_turnover': round(df['turnover'].mean(), 3),
            't_nocost': round(t0, 2),
            '侵蚀年化pp': round((ann0-annt)*100, 1),
            'n_windows': len(df),
        },
        'pool_increment': {
            '域': '2016+(bp_ep持仓数据起点)',
            'avg_overlap_pct': round(overlap, 1),
            'ex_in_pool_mean_pct': round(ex_in.mean()*100, 3) if len(ex_in) else None,
            'ex_in_pool_t': round(_t(ex_in), 2) if len(ex_in) else None,
            'ex_out_pool_mean_pct': round(ex_out.mean()*100, 3) if len(ex_out) else None,
            'ex_out_pool_t': round(_t(ex_out), 2) if len(ex_out) else None,
            'n_in_windows': int(seg['n_in_pool'].sum()), 'n_out_windows': int((seg['n']-seg['n_in_pool']).sum()),
        },
    }
    t_exout = _t(ex_out)
    log(json.dumps(out, ensure_ascii=False, indent=1))
    df.to_csv(os.path.join(REPORTS, 'combo_c_validation_windows_20260906.csv'), index=False)
    json.dump(out, open(os.path.join(REPORTS, 'combo_c_validation_20260906.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log('落盘 combo_c_validation_20260906.json/.csv')

if __name__ == '__main__':
    main()
