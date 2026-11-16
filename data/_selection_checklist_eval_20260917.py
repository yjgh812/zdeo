# -*- coding: utf-8 -*-
"""_selection_checklist_eval_20260917.py — 选股检查单负画像三问 历史独立段验证（prereg 先于运行）。
规则/闸门以 reports/PREREG_SELECTION_CHECKLIST_20260917.md 为准，零调参。
独立段 20250102-20260731；宇宙=主板+创业板剔ST/次新/≤30元；T+5 前瞻研究口径。"""
import os, sys, json, hashlib, time, sqlite3, datetime
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:/zcodesj/data')

PREREG = r'D:/zcodesj/reports/PREREG_SELECTION_CHECKLIST_20260917.md'
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FD = r'D:/workbuddyspace/databases/fundamental_data.db'
SW = r'D:/workbuddyspace/databases/sw_industry.db'
DQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant.db'
OUT = r'D:/zcodesj/attribution/selection_checklist_eval_20260917.json'
SIG_START, SIG_END = '20250102', '20260731'
COST = 0.002
THETA_POS_LO, THETA_POS_HI = 60, 80
TURN_PCTL = 80

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(65536), b''):
            h.update(c)
    return h.hexdigest()

def main():
    t0 = time.time()
    pre_hash = sha256(PREREG)
    from _industry_map_20260916 import MAP

    # 宇宙: 主板+创业板 剔ST
    con = sqlite3.connect(DQ, timeout=10)
    info = pd.read_sql("SELECT code, name, industry, is_st, list_date FROM stock_info", con)
    con.close()
    info['code6'] = info['code'].astype(str).str[-6:]
    info = info[info['instrument_type'].str.contains('stock', na=False)] if 'instrument_type' in info.columns else info
    ok_prefix = info['code6'].str[:3].isin(('600','601','603','605','000','001','002','003','300'))
    info = info[ok_prefix & (info['is_st'].astype(str) == '0')]
    code_meta = {r.code6: (r.industry, r.list_date) for r in info.itertuples(index=False)}
    codes = sorted(code_meta)
    print(f'universe={len(codes)} {time.time()-t0:.0f}s', flush=True)

    # qfq 价格+pos250+T+5
    con = sqlite3.connect(QFQ, timeout=120)
    ph = ','.join('?'*len(codes))
    px = pd.read_sql(f"SELECT substr(code,1,6) code6, date, close FROM stock_daily_meta_qfq "
                     f"WHERE date>='20240601' AND substr(code,1,6) IN ({ph}) ORDER BY code6, date",
                     con, params=codes)
    con.close()
    px['date'] = px['date'].astype(str).str.replace('-','')
    px['close'] = px['close'].astype(float)
    px = px[px['close'] <= 30.0] if False else px   # 30元过滤在信号日行级做（滚动窗内不能用行过滤）
    print(f'qfq rows={len(px)} {time.time()-t0:.0f}s', flush=True)

    rows = []
    for c, g in px.groupby('code6', sort=False):
        n = len(g)
        if n < 260: continue
        ind_name, list_date = code_meta[c]
        sw_name = MAP.get(ind_name, ind_name)
        # 上市<60交易日近似: 首行日期距信号>60 自然日
        d0 = datetime.datetime.strptime(g['date'].iloc[0], '%Y%m%d')
        closes = g['close'].to_numpy(); dates = g['date'].to_numpy()
        s = pd.Series(closes)
        mn = s.rolling(250, min_periods=250).min(); mx = s.rolling(250, min_periods=250).max()
        pos = ((s-mn)/(mx-mn)*100)
        fwd5 = s.shift(-5)/s - 1.0
        for i in range(n):
            d = dates[i]
            if not (SIG_START <= d <= SIG_END): continue
            if (datetime.datetime.strptime(d, '%Y%m%d') - d0).days < 60: continue
            p = pos.iloc[i]
            if pd.isna(p) or closes[i] > 30.0: continue
            f5 = fwd5.iloc[i]
            rows.append((c, d, float(p), None if pd.isna(f5) else float(f5), sw_name))
    sig = pd.DataFrame(rows, columns=['code6','date','pos','fwd5','sw'])
    print(f'signals={len(sig)} {time.time()-t0:.0f}s', flush=True)

    # Q1 换手截面分位
    con = sqlite3.connect(FD, timeout=120)
    db = pd.read_sql("SELECT ts_code, trade_date, turnover_rate FROM daily_basic WHERE trade_date>='20250102'", con)
    con.close()
    db['code6'] = db['ts_code'].str[:6]
    db['tp'] = db.groupby('trade_date')['turnover_rate'].rank(pct=True)*100
    sig = sig.merge(db[['code6','trade_date','tp']].rename(columns={'trade_date':'date'}), on=['code6','date'], how='left')
    sig = sig.dropna(subset=['fwd5'])
    print(f'with tp+fwd5={len(sig)} {time.time()-t0:.0f}s', flush=True)

    # Q3 行业MA20下行
    con = sqlite3.connect(SW, timeout=30)
    sw = pd.read_sql("SELECT name, trade_date, close FROM industry_kline_sw WHERE level='L1' AND trade_date>='20241201'", con)
    con.close()
    sw['trade_date'] = sw['trade_date'].str.replace('-','')
    sw['close'] = sw['close'].astype(float)
    sw['below_ma20'] = sw.groupby('name')['close'].transform(lambda x: x < x.rolling(20, min_periods=20).mean())
    below_map = {(r.name, r.trade_date): bool(r.below_ma20) for r in sw.itertuples(index=False)}
    sig['q3'] = [below_map.get((swn, d), None) for swn, d in zip(sig['sw'], sig['date'])]

    sig['q1'] = sig['tp'] >= TURN_PCTL
    sig['q2'] = (sig['pos'] >= THETA_POS_LO) & (sig['pos'] < THETA_POS_HI)
    sig['q3'] = sig['q3'].astype('boolean')
    sig = sig[sig['q3'].notna()]
    sig['q3'] = sig['q3'].astype(bool)
    sig['hits'] = sig['q1'].astype(int) + sig['q2'].astype(int) + sig['q3'].astype(int)
    sig['ret'] = sig['fwd5'] - COST
    sig['wk'] = sig['date'].str[:6] + 'W' + pd.to_datetime(sig['date']).dt.isocalendar().week.astype(str)
    print(f'final={len(sig)} {time.time()-t0:.0f}s', flush=True)

    def week_t(sub):
        g = sub.groupby('wk')['ret'].mean()
        if len(g) <= 2 or g.std(ddof=1) == 0: return float('nan')
        return float(g.mean() / (g.std(ddof=1)/np.sqrt(len(g))))
    R = {'prereg_sha256': pre_hash, 'window': [SIG_START, SIG_END], 'n_signals': int(len(sig)), 'buckets': {}}
    means = {}
    for h in (0,1,2,3):
        sub = sig[sig['hits'] == h]
        m = float(sub['ret'].mean()); means[h] = m
        win = float((sub['ret'] > 0).mean()*100)
        R['buckets'][h] = {'n': int(len(sub)), 'mean_pp': round(m*100,3), 'win%': round(win,1),
                           't_week': round(week_t(sub),2)}
        print(f'hits={h}: n={len(sub)} mean={m*100:.3f}pp win={win:.1f}% t_w={R["buckets"][h]["t_week"]}')
    mono = all(means[h] >= means[h+1] for h in (0,1,2))
    g1_sub = sig[sig['hits'] == 3]
    g1 = (g1_sub['ret'].mean() < 0) and (R['buckets'][3]['t_week'] is not None and R['buckets'][3]['t_week'] <= -2)
    g2 = mono and (means[0] - means[3] >= 0.01)
    seg = {'2025H1': ('20250102','20250630'), '2025H2': ('20250701','20251231'), '2026H1': ('20260101','20260731')}
    dir_ok = 0; seg_n = 0
    for lab, (a, b) in seg.items():
        s3 = sig[(sig['hits']==3) & (sig['date']>=a) & (sig['date']<=b)]
        s0 = sig[(sig['hits']==0) & (sig['date']>=a) & (sig['date']<=b)]
        if len(s3) >= 30 and len(s0) >= 30:
            seg_n += 1
            if s3['ret'].mean() < s0['ret'].mean(): dir_ok += 1
    g3 = seg_n >= 2 and dir_ok >= 2
    # 参考臂: pos<20
    lo = sig[sig['pos'] < 20]
    R['ref_pos_lt20'] = {'n': int(len(lo)), 'mean_pp': round(float(lo['ret'].mean())*100, 3),
                         'win%': round(float((lo['ret']>0).mean()*100), 1)}
    # 敏感性
    q2alt = sig[(sig['pos'] >= 80)]
    R['sens_q2_80_100'] = {'n': int(len(q2alt)), 'mean_pp': round(float(q2alt['ret'].mean())*100, 3)}
    any1 = sig[sig['hits'] >= 1]; none0 = sig[sig['hits'] == 0]
    R['sens_any_vs_none'] = {'any1_pp': round(float(any1['ret'].mean())*100,3), 'none_pp': round(float(none0['ret'].mean())*100,3)}
    t10 = None
    R['gates'] = {'G1_3hit_neg_t<=-2': bool(g1), 'G2_monotone_delta>=1pp': bool(g2),
                  'G3_seg_dir>=2of3': bool(g3), 'seg_n': seg_n, 'dir_ok': dir_ok,
                  'verdict': 'PASS_CANDIDATE' if (g1 and g2 and g3) else 'FAIL'}
    R['ref_pos_lt20_note'] = 'H2参考臂非主判定'
    print('GATES:', json.dumps(R['gates'], ensure_ascii=False))
    print('REF pos<20:', json.dumps(R['ref_pos_lt20'], ensure_ascii=False))
    json.dump(R, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', OUT, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
