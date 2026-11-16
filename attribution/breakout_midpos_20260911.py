# -*- coding: utf-8 -*-
"""位置中段突破事件研究——「两端负、中间正」元规律可检验推论（研究层，禁接线生产）。
规则/闸门以 reports/PREREG_BREAKOUT_MIDPOS_20260911.md 为准，零调参自由度。
复测前提（§10-8）：#3 BOX-BREAKOUT-LOWPOS 判死低位带、#5 ENGINE-B-HIGHPOS-BREAKOUT 判死高位带，
中段 (0.35,0.70)×突破未测；主对照=同位置带非突破日（剥离位置暴露）。
价格源=workbuddyspace dragon_quant_qfq.db 生产主库 fail-closed。"""
import sqlite3, json, hashlib, time, sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
ST_CACHE = r'D:/zcodesj/attribution/stock_info_cache.json'
PREREG = r'D:/zcodesj/reports/PREREG_BREAKOUT_MIDPOS_20260911.md'
OUT_JSON = r'D:/zcodesj/attribution/breakout_midpos_result_20260911.json'
EVAL_START, EVAL_END = 20160904, 20260910
COST = 0.002
VOL_X = 1.2
LOW_POS, HIGH_POS = 0.35, 0.70
S2_LO, S2_HI = 0.40, 0.65
ALLOWED = ('600', '601', '603', '605', '000', '001', '002', '003', '300')

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def day_t(rows_ret, rows_date):
    g = pd.Series(rows_ret).groupby(pd.Series(rows_date)).mean()
    return float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))) if len(g) > 2 and g.std(ddof=1) > 0 else float('nan')

def main():
    t0 = time.time()
    pre_hash = sha256(PREREG)
    st = json.load(open(ST_CACHE, encoding='utf-8'))['stocks']
    st_codes = {k for k, v in st.items() if v.get('is_st')}

    con = sqlite3.connect(DB)
    mx = con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0]
    if int(mx) < EVAL_END:
        raise SystemExit(f'FAIL-CLOSED: qfq max={mx} < EVAL_END={EVAL_END}')
    df = pd.read_sql(
        "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>='20150101' ORDER BY code,date", con)
    con.close()
    df['code6'] = df['code'].str.extract(r'(\d{6})')[0]
    df = df.dropna(subset=['code6', 'open', 'close'])
    df['date'] = df['date'].astype(np.int64)
    print(f'load rows={len(df)} stocks={df.code6.nunique()} {time.time()-t0:.0f}s', flush=True)

    recs = []   # (date, pos, ret20, ret5, brk, brk2, unf, code6int)
    ap = recs.append
    for code6, g in df.groupby('code6', sort=False):
        if code6[:3] not in ALLOWED or f'{code6[:2]}{code6}' in st_codes:
            continue
        n = len(g)
        if n < 261:
            continue
        o = g['open'].to_numpy(); c = g['close'].to_numpy(); v = g['volume'].to_numpy()
        d = g['date'].to_numpy()
        dn = (pd.to_datetime(pd.Series(d.astype(np.int64).astype(str)), format='%Y%m%d')
              - pd.Timestamp('2000-01-01')).dt.days.to_numpy()
        s = pd.Series(c)
        mn250 = s.rolling(250, min_periods=250).min().to_numpy()
        mx250 = s.rolling(250, min_periods=250).max().to_numpy()
        prev = s.shift(1)
        box_mx = prev.rolling(60, min_periods=60).max().to_numpy()
        vol_ma20 = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid='ignore', divide='ignore'):
            pos = (c - mn250) / np.where(mx250 > mn250, mx250 - mn250, np.nan)
            brk = (c > box_mx) & (v >= VOL_X * vol_ma20)
        brk &= ~np.isnan(pos) & ~np.isnan(box_mx) & ~np.isnan(vol_ma20)
        brk2 = (c > box_mx) & ~np.isnan(box_mx)
        elig = np.where((c <= 30.0) & ~np.isnan(pos) & ~np.isnan(box_mx))[0]
        if len(elig) == 0:
            continue
        c6i = int(code6)
        for i in elig:
            if not (EVAL_START <= d[i] <= EVAL_END):
                continue
            j = i + 1
            if j >= n or dn[j] - dn[i] > 10:
                continue
            entry = o[j]
            if not np.isfinite(entry) or entry <= 0:
                continue
            gap = entry / c[i] - 1.0
            unf = 1.0 if gap >= (0.19 if code6[:3] == '300' else 0.095) else 0.0
            r20 = r5 = np.nan
            k = j + 20
            if k < n and dn[k] - dn[j] <= 20 * 1.6 + 15 and np.isfinite(c[k]):
                r20 = c[k] / entry - 1.0 - COST
            k5 = j + 5
            if k5 < n and dn[k5] - dn[j] <= 5 * 1.6 + 15 and np.isfinite(c[k5]):
                r5 = c[k5] / entry - 1.0 - COST
            ap((int(d[i]), float(pos[i]), r20, r5, bool(brk[i]), bool(brk2[i]), unf, c6i))
    print(f'eligible rows={len(recs)} loop {time.time()-t0:.0f}s', flush=True)

    r_all = pd.DataFrame(recs, columns=['d', 'pos', 'r20', 'r5', 'brk', 'brk2', 'unf', 'c6'])
    r_all['band'] = pd.cut(r_all['pos'], [-np.inf, LOW_POS, HIGH_POS, np.inf],
                           labels=['low', 'mid', 'high'])
    out = {'prereg_sha256': pre_hash, 'window': [EVAL_START, EVAL_END], 'cost': COST,
           's1_pure_breakout': {}, 's2_narrow_band': {}, 's3_t5': {}, 's4_pos_quintile_decomp': {}}

    def grp(sub, ret='r20'):
        sub = sub.dropna(subset=[ret])
        return sub, float(sub[ret].mean()), day_t(sub[ret], sub['d'])

    def paired(a, b, ret='r20'):
        da = a.groupby('d')[ret].mean(); db = b.groupby('d')[ret].mean()
        common = da.index.intersection(db.index)
        if len(common) <= 2:
            return float('nan'), 0
        diff = da[common] - db[common]
        t = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))) if diff.std(ddof=1) > 0 else float('nan')
        return t, int(len(common))

    def yearly_delta(a, b, ret='r20'):
        ya = a.groupby(a['d'] // 10000)[ret].mean(); yb = b.groupby(b['d'] // 10000)[ret].mean()
        common = ya.index.intersection(yb.index)
        return {int(y): round(float(v) * 100, 3) for y, v in (ya[common] - yb[common]).items()}

    for tag, sub_all in {'all': r_all}.items():
        mb_a, mb_m, mb_t = grp(sub_all[(sub_all['band'] == 'mid') & sub_all['brk']])
        mn_a, mn_m, mn_t = grp(sub_all[(sub_all['band'] == 'mid') & ~sub_all['brk']])
        lb_a, lb_m, lb_t = grp(sub_all[(sub_all['band'] == 'low') & sub_all['brk']])
        hb_a, hb_m, hb_t = grp(sub_all[(sub_all['band'] == 'high') & sub_all['brk']])
        base_a, base_m, base_t = grp(sub_all)
        t_cu, n_pair = paired(mb_a, mn_a)
        t_cb, _ = paired(mb_a, base_a)
        delta = float(mb_m - mn_m)
        yd = yearly_delta(mb_a, mn_a)
        pos_years = sum(1 for v_ in yd.values() if v_ > 0)
        g1 = mb_m > 0 and mb_t == mb_t and mb_t >= 2
        g2 = delta >= 0.010 and t_cu == t_cu and t_cu >= 2
        g3 = pos_years >= 6
        sanity_ok = (lb_m < mb_m) or not (lb_t == lb_t and lb_t >= 2)
        res = {'mid_brk': {'n': len(mb_a), 'days': mb_a['d'].nunique(), 'win': float((mb_a['r20'] > 0).mean()),
                           'mean': float(mb_m), 't_day': mb_t},
               'mid_nobrk': {'n': len(mn_a), 'days': mn_a['d'].nunique(), 'win': float((mn_a['r20'] > 0).mean()),
                             'mean': float(mn_m), 't_day': mn_t},
               'low_brk': {'n': len(lb_a), 'mean': float(lb_m), 't_day': lb_t},
               'high_brk': {'n': len(hb_a), 'mean': float(hb_m), 't_day': hb_t},
               'baseline_all': {'n': len(base_a), 'mean': float(base_m), 't_day': base_t},
               'delta_vs_same_band_pp': round(delta * 100, 3), 't_paired': t_cu, 'pairs': n_pair,
               'delta_vs_base_pp': round(float(mb_m - base_m) * 100, 3), 't_vs_base': t_cb,
               'yearly_delta_pp': yd, 'pos_years': pos_years,
               'sanity_low_lt_mid': bool(sanity_ok),
               'gates': {'G1': bool(g1), 'G2': bool(g2), 'G3': bool(g3),
                         'verdict': 'PASS_CANDIDATE' if (g1 and g2 and g3 and sanity_ok) else 'FAIL'}}
        fmb, fmn = mb_a[mb_a['unf'] == 0], mn_a[mn_a['unf'] == 0]
        if len(fmb) and len(fmn):
            res['fill_ok_delta_pp'] = round(float(fmb['r20'].mean() - fmn['r20'].mean()) * 100, 3)
        out[tag] = res
        print(f'--- {tag} T+20 ---')
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))

        # S1: 纯突破（去放量）
        mb2 = sub_all[(sub_all['band'] == 'mid') & sub_all['brk2']].dropna(subset=['r20'])
        t2, _ = paired(mb2, mn_a)
        out.setdefault('s1_pure_breakout', {})[tag] = {
            'n': len(mb2), 'mean': float(mb2['r20'].mean()), 't_day': day_t(mb2['r20'], mb2['d']),
            'delta_vs_mid_nobrk_pp': round(float(mb2['r20'].mean() - mn_m) * 100, 3), 't': t2}
        # S2: 收窄带 [0.40,0.65]
        s2 = sub_all[(sub_all['pos'] > S2_LO) & (sub_all['pos'] < S2_HI)].dropna(subset=['r20'])
        s2b = s2[s2['brk']]; s2n = s2[~s2['brk']]
        t3, _ = paired(s2b, s2n)
        out['s2_narrow_band'][tag] = {
            'brk_n': len(s2b), 'brk_mean': float(s2b['r20'].mean()), 'nobrk_mean': float(s2n['r20'].mean()),
            'delta_pp': round(float(s2b['r20'].mean() - s2n['r20'].mean()) * 100, 3), 't': t3} if len(s2b) and len(s2n) else {'empty': True}
        # S3: T+5
        mb5 = sub_all[(sub_all['band'] == 'mid') & sub_all['brk']].dropna(subset=['r5'])
        mn5 = sub_all[(sub_all['band'] == 'mid') & ~sub_all['brk']].dropna(subset=['r5'])
        t4, _ = paired(mb5, mn5, 'r5')
        out['s3_t5'][tag] = {'brk_mean': float(mb5['r5'].mean()), 'nobrk_mean': float(mn5['r5'].mean()),
                             'delta_pp': round(float(mb5['r5'].mean() - mn5['r5'].mean()) * 100, 3), 't': t4}
        # S4: mid 带内 pos 五分位增量分解
        mid_all = sub_all[(sub_all['band'] == 'mid')].dropna(subset=['r20'])
        q = pd.qcut(mid_all['pos'], 5, labels=False, duplicates='drop')
        dec = {}
        for qn in sorted(q.dropna().unique()):
            sub_q = mid_all[q == qn]
            qb = sub_q[sub_q['brk']]; qn_ = sub_q[~sub_q['brk']]
            if len(qb) > 10 and len(qn_) > 10:
                tq, npair = paired(qb, qn_)
                dec[int(qn)] = {'pos_lo': round(float(sub_q['pos'].min()), 3),
                                'brk_mean': round(float(qb['r20'].mean()), 5),
                                'nobrk_mean': round(float(qn_['r20'].mean()), 5),
                                'delta_pp': round(float(qb['r20'].mean() - qn_['r20'].mean()) * 100, 3),
                                't': tq, 'pairs': npair}
        out.setdefault('s4_pos_quintile_decomp', {})[tag] = dec
        print('S1:', json.dumps(out['s1_pure_breakout'][tag], ensure_ascii=False))
        print('S2:', json.dumps(out['s2_narrow_band'][tag], ensure_ascii=False))
        print('S3:', json.dumps(out['s3_t5'][tag], ensure_ascii=False))
        print('S4:', json.dumps(out['s4_pos_quintile_decomp'][tag], ensure_ascii=False))

    json.dump(out, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    print('saved', OUT_JSON, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
