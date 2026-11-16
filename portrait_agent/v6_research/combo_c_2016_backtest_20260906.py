# -*- coding: utf-8 -*-
"""画像特征选股 2016-01-01 至今回测（组合C 冻结配方复算, 不改任何配方参数）。
引擎逐行复用 combo_c_v2_recompute_20260906.py（POS4/NEG8/rel_ind_hi/lim_today/head(60)/20bar 窗/成本千三）,
仅新增: ①窗口截断 date>=20160101 ②单笔(股×窗)层面胜率/均值/分位 ③分年统计
④信号族分解(各正信号族在不加负过滤与加负过滤两口径下的单笔胜率/均值, 纯描述性)
⑤next-bar 敏感性: t+1 开盘买入 -> t+20 收盘卖出口径重算组合层面。
产出: reports/combo_c_2016_backtest_20260906.json"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')
COST_RT = 0.003
START = '20160101'  # 面板 date 列为 TEXT 型, 用定长字符串比较
POS_CHAN = ['chan_buy1_10_v2', 'chan_buy2_10_v2']
POS_FIXED = ['island_5', 'gap_up_hold']
POS_ALL = POS_CHAN + POS_FIXED
NEG8 = ['vwap60_bias', 'vwap20_bias', 'limit_break_5', 'gap_up_run_5',
        'rounded_20', 'up4of5', 'new_high_3', 'lhb_buy_5']

def log(s): print(s, flush=True)

def rebuild_chan(panel, pkl):
    """与 retrain_v4_full.py / combo_c_v2_recompute 逐行同构: merge→fillna→groupby rolling(10).max()"""
    chan = pd.read_pickle(pkl)
    chan = chan[chan['type'].isin(['buy1', 'buy2'])][['code', 'date', 'type']].drop_duplicates()
    out = panel[['code', 'date']].copy()
    for t, col in [('buy1', 'chan_buy1_10_v2'), ('buy2', 'chan_buy2_10_v2')]:
        sig = chan[chan['type'] == t][['code', 'date']].drop_duplicates()
        sig['sig'] = 1.0
        out = out.merge(sig.rename(columns={'sig': 'sig_'}), on=['code', 'date'], how='left')
        out['sig_'] = out['sig_'].fillna(0)
        out[col] = out.groupby('code')['sig_'].transform(lambda s: s.rolling(10).max())
        out = out.drop(columns=['sig_'])
    return out[['chan_buy1_10_v2', 'chan_buy2_10_v2']]

def main():
    full_window = '--full-window' in sys.argv  # 对账模式: 不截断, 应复现9/6登记值 15.72/t3.75/64.8
    t0 = time.time()
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m['lim_today'] = m['lim_today'].astype('boolean').fillna(False).astype(bool)
    m['rel_ind_hi'] = m.groupby('date')['rel_ind_ret'].rank(pct=True) >= 0.90
    log(f'面板 {len(m)} 行 载入 {time.time()-t0:.0f}s')
    m[['chan_buy1_10_v2', 'chan_buy2_10_v2']] = rebuild_chan(m, os.path.join(CACHE, 'chan_signals_v2.pkl'))
    log(f'v2列重建完成 {time.time()-t0:.0f}s')

    m = m if full_window else m[m['date'] >= START].copy()
    # next-bar 敏感性列: t+1 开盘买入 -> t+20 收盘卖出
    has_open = 'open' in m.columns
    if has_open:
        m['nb_ret20'] = m.groupby('code', sort=False)['open'].transform(
            lambda s: s.shift(-20) / s.shift(-1) - 1)
        log(f'nb_ret20 预算完成 has_open={True} {time.time()-t0:.0f}s')
    all_dates = sorted(m['date'].unique())
    log(f'2016起 窗口数={len(all_dates)} 行={len(m)}')

    groups = {d: g for d, g in m.groupby('date')}
    nav_pc = nav_b = nav_nb = 1.0
    prev_set = set()
    rows, trades = [], []
    yearly = {}
    for ws in all_dates[::20]:
        cs = groups.get(ws)
        if cs is None:
            continue
        cs2 = cs[cs['fwd20_close'].notna() & (~cs['lim_today'])]
        if len(cs2) < 500:
            continue
        sig = np.zeros(len(cs2), dtype=bool)
        for c in POS_ALL:
            sig |= (cs2[c].fillna(0) > 0).to_numpy()
        for c in NEG8:
            sig &= ~((cs2[c].fillna(0) > 0.5).to_numpy())
        sig &= ~(cs2['rel_ind_hi'].fillna(False).to_numpy())
        sel_df = cs2[sig]
        if len(sel_df) < 5:
            continue
        sel60 = sel_df.head(60)
        codes = sel60['code'].tolist()
        fv = sel60['fwd20_close'].astype(float).to_numpy()
        fv_all = cs2['fwd20_close'].astype(float).to_numpy()
        pr = float(fv.mean()); br = float(fv_all.mean())
        pr_nb = float(sel60['nb_ret20'].astype(float).mean()) if has_open else np.nan
        cur_set = set(codes)
        inter = len(cur_set & prev_set)
        union = len(cur_set | prev_set) if prev_set else len(cur_set)
        turnover = 1 - inter / len(cur_set) if cur_set and prev_set else 1.0
        prev_set = cur_set
        nav_pc *= (1 + pr - COST_RT); nav_b *= (1 + br)
        if has_open and not np.isnan(pr_nb):
            nav_nb *= (1 + pr_nb - COST_RT)
        y = str(ws)[:4]
        rows.append({'date': ws, 'ex': (pr - br), 'pr_c': pr - COST_RT,
                     'win': int(pr > br), 'n_sel': len(codes), 'turnover': turnover})
        trades.extend([(ws, c, float(v)) for c, v in zip(codes, fv)])
        yearly.setdefault(y, []).append((pr, br, fv))

    df = pd.DataFrame(rows)
    tr = pd.DataFrame(trades, columns=['date', 'code', 'ret20'])
    ex = df['ex'].values
    tstat = float(ex.mean() / (ex.std() / np.sqrt(len(ex))))
    years = len(df) * 20 / 243
    nav_p0 = float(np.prod(1 + df['pr_c'].values + COST_RT))
    res = {
        '配方': '组合C冻结配方(v2确认日无前视): chan_buy1/2(10日)∪island_5∪gap_up_hold - NEG8 - 行业内追高p90; head60等权; 20bar窗; 成本千三',
        '起始窗口': '全窗口' if full_window else START,
        'n_windows': int(len(df)),
        'n_trades': int(len(tr)),
        '单笔胜率_pct': round(float((tr['ret20'] > 0).mean() * 100), 2),
        '单笔均值_pct': round(float(tr['ret20'].mean() * 100), 3),
        '单笔中位_pct': round(float(tr['ret20'].median() * 100), 3),
        '单笔P25_P75_pct': [round(float(tr['ret20'].quantile(q) * 100), 2) for q in (0.25, 0.75)],
        '单笔P10_P90_pct': [round(float(tr['ret20'].quantile(q) * 100), 2) for q in (0.10, 0.90)],
        '无成本年化_pct': round((nav_p0 ** (1 / years) - 1) * 100, 2),
        '成本后年化_pct': round((nav_pc ** (1 / years) - 1) * 100, 2),
        '基准年化_pct': round((nav_b ** (1 / years) - 1) * 100, 2),
        'nextbar成本后年化_pct': round((nav_nb ** (1 / years) - 1) * 100, 2) if has_open else None,
        '超额t': round(tstat, 2),
        '窗口胜率_pct': round(float(df['win'].mean() * 100), 1),
        '平均换手率': round(float(df['turnover'].mean()), 3),
        '平均每窗入选数': round(float(df['n_sel'].mean()), 1),
    }

    yr_rows = []
    for y, lst in sorted(yearly.items()):
        prs = np.array([x[0] for x in lst]); brs = np.array([x[1] for x in lst])
        fvs = np.concatenate([x[2] for x in lst])
        navp = float(np.prod(1 + prs - COST_RT)); navb = float(np.prod(1 + brs))
        n = len(lst)
        yr_rows.append({'年': y, '窗数': n, '单笔胜率%': round(float((fvs > 0).mean() * 100), 1),
                        '单笔均值%': round(float(fvs.mean() * 100), 2),
                        '成本后年化%': round((navp ** (243 / (n * 20)) - 1) * 100, 2),
                        '基准年化%': round((navb ** (243 / (n * 20)) - 1) * 100, 2),
                        '窗口胜率%': round(float((prs > brs).mean() * 100), 1)})
    res['分年'] = yr_rows

    fam_rows = []
    negmask = pd.Series(False, index=m.index)
    for c in NEG8:
        negmask |= (m[c].fillna(0) > 0.5)
    negmask |= m['rel_ind_hi'].fillna(False)
    for c in POS_ALL:
        base = m[(m[c].fillna(0) > 0) & m['fwd20_close'].notna()]
        clean = base[~negmask.loc[base.index]]
        fam_rows.append({'信号族': c, 'n_原始': len(base),
                         '原始胜率%': round(float((base['fwd20_close'] > 0).mean() * 100), 1),
                         '原始均值%': round(float(base['fwd20_close'].mean() * 100), 2),
                         'n_过负过滤': len(clean),
                         '过滤后胜率%': round(float((clean['fwd20_close'] > 0).mean() * 100), 1) if len(clean) else None,
                         '过滤后均值%': round(float(clean['fwd20_close'].mean() * 100), 2) if len(clean) else None})
    res['信号族分解'] = fam_rows

    log(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    tag = 'fullwindow' if full_window else '2016'
    out_json = os.path.join(REPORTS, f'combo_c_{tag}_backtest_20260906.json')
    json.dump(res, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    if not full_window:
        tr.to_csv(os.path.join(REPORTS, f'combo_c_{tag}_trades_20260906.csv'), index=False)
    log(f'落盘 {out_json} 总耗时 {time.time()-t0:.0f}s')

if __name__ == '__main__':
    main()
