# -*- coding: utf-8 -*-
"""第二批数据面全面接入(2026-09-06 主人指令: 数据全面接入, 继续).

接入(在panel_v4_full基础上, 不重训模型——LightGBM已三次否定, 本轮走信号层):
  A 情绪面(qfq派生, 14年): 行业涨停联动数/行业涨幅rank/行业内上涨占比/个股-行业相对强度/
    市场涨停家数/市场最高连板数
  B 基本面PIT(factor_pit.db, 1994-2026): ep/bp/sp/roe/lev, 按 pit_avail_date 无前视对齐
  C 龙虎榜(fundamental_data.db, 仅20260112-20260902约8个月): 上榜净买入事件(近期验证层)
  D 资金面: 14年个股资金流本地不可得, 已有量价代理(VWAP乖离/amt5_amt20/换手turnover20),
    龙虎榜作事件级资金代理 —— 诚实标注

输出: 新特征画像(lift+截面超额+t值) + 信号直构组合v2(负信号过滤+多信号共振).
"""
import os, sys, json, time
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports'); os.makedirs(REPORTS, exist_ok=True)
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'

T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

SENT_FEATS = ['ind_limit_cnt', 'ind_ret_rank', 'ind_up_pct', 'rel_ind_ret',
              'mkt_limit_cnt', 'mkt_lianban_max']
PIT_FEATS = ['pit_ep', 'pit_bp', 'pit_roe', 'pit_lev']
LHB_FEATS = ['lhb_buy_5']

def main():
    log('加载v4面板...')
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v4_full.pkl'))
    log(f'面板 {m.shape}')

    # ==== A 情绪面派生 ====
    log('情绪面派生...')
    m = m.sort_values(['code', 'date']).reset_index(drop=True)
    m['chg'] = m.groupby('code')['close'].pct_change() * 100
    # 行业映射(当前快照, 历史固定——口径标注)
    con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    ind = pd.read_sql_query("SELECT code,industry FROM industry_map_ths", con)
    # 2026-09-10 D1: 迁移 dragon_tiger(8/10 冻结) → dragon_tiger_new(日更), net_buy→net_amount
    lhb = pd.read_sql_query(
        "SELECT trade_date,ts_code,net_amount AS net_buy FROM dragon_tiger_new", con)
    con.close()
    ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]
    m = m.merge(ind[['code', 'L1']].drop_duplicates('code'), on='code', how='left')
    m['L1'] = m['L1'].fillna('未知')
    # 当日连板数(连续涨停)
    lim = m['lim_today'].fillna(False).astype('int8').to_numpy()
    code_arr = m['code'].to_numpy()
    same = np.r_[False, (code_arr[1:] == code_arr[:-1]) & (lim[1:] == lim[:-1])]
    grp = np.cumsum(~same)
    m['lianban'] = pd.Series(lim).groupby(grp).cumsum().to_numpy()
    # 行业/市场聚合
    m['lim_b'] = m['lim_today'].fillna(False).astype(int)
    day_ind = m.groupby(['date', 'L1']).agg(
        ind_limit_cnt=('lim_b', 'sum'), ind_mean_chg=('chg', 'mean'),
        ind_up_pct=('chg', lambda s: (s > 0).mean())).reset_index()
    day_mkt = m.groupby('date').agg(
        mkt_limit_cnt=('lim_b', 'sum'),
        mkt_lianban_max=('lianban', 'max')).reset_index()
    # 行业涨幅rank(每日)
    day_ind['ind_ret_rank'] = day_ind.groupby('date')['ind_mean_chg'].rank(pct=True)
    m = m.merge(day_ind, on=['date', 'L1'], how='left').merge(day_mkt, on='date', how='left')
    m['rel_ind_ret'] = m['chg'] - m['ind_mean_chg']
    log(f'情绪特征完成; 板块数={m["L1"].nunique()}')

    # ==== B 基本面PIT (merge_asof 无前视) ====
    log('PIT因子对齐(pit_avail_date<=date)...')
    pit_con = sqlite3.connect('file:factor_pit.db?mode=ro', uri=True)
    pit = pd.read_sql_query(
        "SELECT code,pit_avail_date,report_date,ep,bp,sp,roe,lev "
        "FROM stock_factors_fundamental_pit_v2", pit_con)
    pit_con.close()
    pit = pit.rename(columns={'pit_avail_date': 'avail'})
    pit = pit.sort_values(['code', 'avail'])
    # 每(code, avail)去重(同日多报告期取最新report_date)
    pit = pit.sort_values(['code', 'avail', 'report_date']).drop_duplicates(
        ['code', 'avail'], keep='last')
    m_sorted = m.sort_values(['code', 'date']).reset_index(drop=True)
    mm = m_sorted[['code', 'date']].copy()
    mm['idx'] = mm.index
    pit2 = pit[['code', 'avail', 'ep', 'bp', 'roe', 'lev']].copy()
    pit2['date_n'] = pit2['avail'].astype(int)
    # 逐股 searchsorted 无前视对齐(pit_avail_date<=date 取最近一期)
    m_sorted = m.sort_values(['code', 'date'])  # 保留原index(回填用)
    m_sorted['date_n'] = m_sorted['date'].astype(int)
    pit_map = {}
    for code, pg in pit2.groupby('code'):
        pg = pg.sort_values('date_n')
        pit_map[code] = (pg['date_n'].to_numpy(),
                         pg[['ep', 'bp', 'roe', 'lev']].to_numpy(dtype=float))
    n = len(m_sorted)
    pit_cols = {c: np.full(n, np.nan) for c in ['ep', 'bp', 'roe', 'lev']}
    codes_arr = m_sorted['code'].to_numpy(); dts_arr = m_sorted['date_n'].to_numpy()
    idx_by_code = m_sorted.groupby('code', sort=False).indices
    for code, gidx in idx_by_code.items():
        pm = pit_map.get(code)
        if pm is None: continue
        pdates, pvals = pm
        pos = np.searchsorted(pdates, dts_arr[gidx], side='right') - 1
        valid = pos >= 0
        pv = pvals[np.clip(pos, 0, len(pdates)-1)]
        for j, c in enumerate(['ep', 'bp', 'roe', 'lev']):
            pit_cols[c][gidx] = np.where(valid, pv[:, j], np.nan)
    for src, dst in [('ep', 'pit_ep'), ('bp', 'pit_bp'), ('roe', 'pit_roe'), ('lev', 'pit_lev')]:
        s = pd.Series(pit_cols[src], index=m_sorted.index)
        m[dst] = s.reindex(m.index).values
    cov = m['pit_roe'].notna().mean()
    log(f'PIT对齐完成: roe覆盖率={cov*100:.1f}%')

    # ==== C 龙虎榜(8个月, 近期层) ====
    log('龙虎榜事件接入...')
    lhb['code'] = lhb['ts_code'].str[:6]
    lhb['date'] = lhb['trade_date'].astype(str)
    lhb['net_buy'] = pd.to_numeric(lhb['net_buy'], errors='coerce')
    lhb_buy = lhb[lhb['net_buy'] > 0][['code', 'date']].drop_duplicates()
    lhb_buy['lhb_buy'] = 1.0
    m = m.merge(lhb_buy, on=['code', 'date'], how='left')
    m['lhb_buy'] = m['lhb_buy'].fillna(0)
    m['lhb_buy_5'] = m.groupby('code')['lhb_buy'].transform(lambda s: s.rolling(5).max())
    log(f'龙虎榜事件: 净买入上榜{int(m["lhb_buy"].sum())}条')

    # ==== 画像验证(lift+截面超额) ====
    log('新特征画像...')
    pool = m.dropna(subset=['fwd20_close'])
    day_mean = pool.groupby('date')['fwd20_close'].mean()
    # 情绪二值特征先建列(切片前)
    m['ind_lim_ge3'] = (m['ind_limit_cnt'] >= 3).astype(float)
    m['ind_lim_ge5'] = (m['ind_limit_cnt'] >= 5).astype(float)
    m['mkt_hot'] = (m['mkt_limit_cnt'] >= 100).astype(float)
    pct = m.groupby('date')['fwd20_close'].rank(pct=True)
    m['is_up'] = (pct >= 0.90).astype(float)
    m.loc[m['fwd20_close'].isna(), 'is_up'] = np.nan
    valid = m.dropna(subset=['is_up'])
    up, dn = valid[valid['is_up'] == 1], valid[valid['is_up'] == 0]

    def profile_bin(col, frame=m):
        p_up, p_dn = up[col].mean(), dn[col].mean()
        sub = frame[frame[col] > 0].dropna(subset=['fwd20_close'])
        ex = (sub['fwd20_close'] - sub['date'].map(day_mean)).dropna()
        t_val = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if len(ex) > 5 and ex.std() > 0 else np.nan
        return {'feat': col, 'rate_up': round(p_up*100, 3), 'rate_dn': round(p_dn*100, 3),
                'lift': round(p_up/p_dn, 3) if p_dn > 0 else None,
                'ex20_pct': round(ex.mean()*100, 3) if len(ex) else None,
                't': round(t_val, 2) if not np.isnan(t_val) else None,
                'win_pct': round((ex > 0).mean()*100, 1) if len(ex) else None,
                'n': int(frame[col].sum())}
    def profile_cont(col, q=0.9):
        # 连续特征: 高分位组 vs 全池
        th = m[col].quantile(q)
        sub = m[m[col] >= th].dropna(subset=['fwd20_close'])
        ex = (sub['fwd20_close'] - sub['date'].map(day_mean)).dropna()
        t_val = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if len(ex) > 5 and ex.std() > 0 else np.nan
        hi = m[m[col] >= th].dropna(subset=['is_up'])
        p_up, p_dn = hi['is_up'].mean(), valid['is_up'].mean()
        return {'feat': f'{col}_q{int(q*100)}', 'rate_up': round(p_up*100, 3),
                'rate_dn': round(p_dn*100, 3), 'lift': round(p_up/p_dn, 3),
                'ex20_pct': round(ex.mean()*100, 3) if len(ex) else None,
                't': round(t_val, 2) if not np.isnan(t_val) else None,
                'win_pct': round((ex > 0).mean()*100, 1) if len(ex) else None,
                'n': len(sub)}
    prof = []
    prof.append(profile_bin('ind_lim_ge3'))
    prof.append(profile_bin('ind_lim_ge5'))
    for c in ['ind_ret_rank', 'ind_up_pct', 'rel_ind_ret', 'mkt_limit_cnt', 'mkt_lianban_max']:
        prof.append(profile_cont(c))
    prof.append(profile_bin('mkt_hot'))
    # PIT
    for c in PIT_FEATS:
        prof.append(profile_cont(c, 0.8))   # 高ROE/低杠杆等高质量组
        prof.append(profile_cont(c, 0.2))
    # 龙虎榜
    prof.append(profile_bin('lhb_buy_5', m))
    # 2026段单独口径(龙虎榜仅8个月)
    seg26 = m[m['date'] >= '20260112']
    if len(seg26):
        sub = seg26[seg26['lhb_buy'] > 0].dropna(subset=['fwd20_close'])
        dm26 = seg26.dropna(subset=['fwd20_close']).groupby('date')['fwd20_close'].mean()
        ex = (sub['fwd20_close'] - sub['date'].map(dm26)).dropna()
        t26 = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if len(ex) > 5 and ex.std() > 0 else np.nan
        prof.append({'feat': 'lhb_buy_2026段', 'ex20_pct': round(ex.mean()*100, 3) if len(ex) else None,
                     't': round(t26, 2) if not np.isnan(t26) else None, 'n': len(sub)})
    prof_df = pd.DataFrame(prof)
    log('\n第二批特征画像:\n' + prof_df.to_string(index=False))

    # ==== D 信号直构组合v2 ====
    log('信号直构组合v2(负过滤+共振)...')
    groups = {d: g for d, g in m.groupby('date')}
    all_dates = sorted(m['date'].unique())
    POS = ['chan_buy2_10', 'chan_buy1_10', 'island_5', 'gap_up_hold', 'lhb_buy_5']
    def sig_v2(hold=20, topn_cap=60, min_cnt=1, use_neg_filter=True, pos=None):
        pos = pos or POS
        dts = all_dates[::hold]
        nav_p = nav_b = 1.0; rows = []
        for ws in dts:
            cs = groups.get(ws)
            if cs is None: continue
            cs2 = cs[cs['fwd20_close'].notna() & (~cs['lim_today'].fillna(False).astype(bool))]
            if len(cs2) < 500: continue
            cnt = np.zeros(len(cs2))
            for c_ in pos:
                if c_ in cs2.columns:
                    cnt += (cs2[c_].fillna(0) > 0).to_numpy()
            sel_mask = cnt >= min_cnt
            if use_neg_filter:
                for neg, thr in [('chan_buy3_10', 0.5), ('new_high_3', 0.5), ('vwap60_bias', 0.5)]:
                    if neg in cs2.columns:
                        sel_mask &= ~((cs2[neg].fillna(0) > thr).to_numpy())
            fv = cs2['fwd20_close'].astype(float).to_numpy()
            sel = fv[sel_mask]
            if len(sel) < 5: continue
            sel = sel[:topn_cap]
            pr = float(sel.mean()); br = float(fv.mean())
            nav_p *= (1+pr); nav_b *= (1+br)
            rows.append({'date': ws, 'n_sig': int(sel_mask.sum()), 'port': round(pr*100, 3),
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
        return {'n': len(rdf), 'nav': round(nav_p, 4), 'nav_b': round(nav_b, 4),
                'ann': round(ann_p*100, 2), 'ann_b': round(ann_b*100, 2),
                'excess': round((nav_p/nav_b-1)*100, 2), 'mdd': round(mdd*100, 2),
                'win': round(rdf['win'].mean()*100, 1), 't': round(t_val, 2),
                'avg_nsig': round(rdf['n_sig'].mean(), 1), 'yearly': yearly,
                'detail': rdf.to_dict('records')}
    combos = {
        'v2_pos5_negfilter': sig_v2(),
        'v2_pos5_negfilter_2sig': sig_v2(min_cnt=2),   # 共振>=2
        'v2_pos4_lhbout': sig_v2(pos=['chan_buy2_10', 'chan_buy1_10', 'island_5', 'gap_up_hold']),
    }
    for k, v in combos.items():
        if 'error' not in v:
            neg_yrs = sum(1 for x in v['yearly'].values() if x < 0)
            log(f'[{k}] ann={v["ann"]}% base={v["ann_b"]}% ex={v["excess"]}% t={v["t"]} '
                f'win={v["win"]}% mdd={v["mdd"]}% nsig={v["avg_nsig"]} 年负{neg_yrs}/{len(v["yearly"])}')

    m.to_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    fp = os.path.join(REPORTS, 'data_enrich_v5_20260906.json')
    json.dump({'profile': prof_df.to_dict('records'),
               'combos': {k: {kk: vv for kk, vv in v.items() if kk != 'detail'}
                          for k, v in combos.items()}},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log(f'面板落盘 panel_v5_full.pkl; 报告 {fp}')
    print('\n' + prof_df.to_string(index=False))

if __name__ == '__main__':
    main()
