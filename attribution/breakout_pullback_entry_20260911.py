# -*- coding: utf-8 -*-
"""突破后「缩量回踩贴线」vs 次日追入 的入场时机配对研究（研究层，禁接线生产）。
规则/闸门以 reports/PREREG_BREAKOUT_PULLBACK_ENTRY_20260911.md 为准，零调参自由度。
复测前提（§10-8）：9/9 档案明文未测项；与 R31 判死三条件差异见 prereg §〇；不复用 9/9 本口径事件。
价格源=workbuddyspace dragon_quant_qfq.db 生产主库 fail-closed。"""
import sqlite3, json, hashlib, time, sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
ST_CACHE = r'D:/zcodesj/attribution/stock_info_cache.json'
PREREG = r'D:/zcodesj/reports/PREREG_BREAKOUT_PULLBACK_ENTRY_20260911.md'
OUT_JSON = r'D:/zcodesj/attribution/breakout_pullback_entry_result_20260911.json'
EVAL_START, EVAL_END = 20160904, 20260910
COST = 0.002
VOL_X = 1.2
HIGH_POS = 0.70
# (name, k_lo, k_hi,贴线阈值)
CFGS = [('main', 1, 5, 0.03), ('s1_win13', 1, 3, 0.03),
        ('s2_win110', 1, 10, 0.03), ('s3_thr2', 1, 5, 0.02), ('s3_thr5', 1, 5, 0.05)]
ALLOWED = ('600', '601', '603', '605', '000', '001', '002', '003', '300')

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def day_t(rets, dates):
    g = pd.Series(rets).groupby(pd.Series(dates)).mean()
    return float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))) if len(g) > 2 and g.std(ddof=1) > 0 else float('nan')

def fwd_ret(c, o, dn, n, j, h=20):
    """j 开盘入场 T+h 收盘退出；入场停牌跨日>10 或越界返回 nan"""
    if j >= n or dn[j] - dn[j - 1] > 10:
        return np.nan
    k = j + h
    if k >= n:
        return np.nan
    entry = o[j]
    if not np.isfinite(entry) or entry <= 0 or not np.isfinite(c[k]):
        return np.nan
    return c[k] / entry - 1.0 - COST

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

    recs = []   # (date_E, chase, main, s1, s2, s3a, s3b, pos)
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
        box_top = prev.rolling(60, min_periods=60).max().to_numpy()
        vol_ma20 = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid='ignore', divide='ignore'):
            pos = (c - mn250) / np.where(mx250 > mn250, mx250 - mn250, np.nan)
            brk = (c > box_top) & (v >= VOL_X * vol_ma20)
        brk &= ~np.isnan(box_top) & ~np.isnan(vol_ma20)
        idxs = np.where(brk & (c <= 30.0))[0]
        if len(idxs) == 0:
            continue
        for i in idxs:
            if not (EVAL_START <= d[i] <= EVAL_END):
                continue
            chase = fwd_ret(c, o, dn, n, i + 1)
            kmax_abs = min(i + 10, n - 1)
            hit = {name: None for name, _, _, _ in CFGS}
            for k in range(i + 1, kmax_abs + 1):
                if dn[k] - dn[k - 1] > 10:
                    break   # 停牌断档，K 窗失效
                pull = c[k] < c[i] and v[k] < v[i]
                if not pull:
                    continue
                bt = box_top[i]
                if not (np.isfinite(bt) and bt > 0):
                    continue
                dev = abs(c[k] - bt) / bt
                for name, klo, khi, thr in CFGS:
                    if hit[name] is None and (k - i) <= khi and (k - i) >= klo and dev <= thr:
                        hit[name] = k
                if all(x is not None for x in hit.values()):
                    break
            row = [int(d[i]), chase]
            for name, _, _, _ in CFGS:
                k = hit[name]
                row.append(fwd_ret(c, o, dn, n, k + 1) if k is not None else np.nan)
            p_ = float(pos[i]) if np.isfinite(pos[i]) else np.nan
            row.append(p_)
            ap(tuple(row))
    print(f'events={len(recs)} loop {time.time()-t0:.0f}s', flush=True)

    r = pd.DataFrame(recs, columns=['d', 'chase', 'main', 's1_win13', 's2_win110',
                                    's3_thr2', 's3_thr5', 'pos'])
    out = {'prereg_sha256': pre_hash, 'window': [EVAL_START, EVAL_END], 'cost': COST,
           'n_events_chase': int(r['chase'].notna().sum())}
    r['y'] = r['d'] // 10000

    def paired_stats(col):
        m = r.dropna(subset=['chase', col])
        diff = m[col] - m['chase']
        gd = diff.groupby(m['d']).mean()
        t_d = float(gd.mean() / (gd.std(ddof=1) / np.sqrt(len(gd)))) if len(gd) > 2 and gd.std(ddof=1) > 0 else float('nan')
        gd_y = diff.groupby(m['y']).mean()
        pos_y = int((gd_y > 0).sum())
        pb_all = r.dropna(subset=[col])
        t_pb = day_t(pb_all[col], pb_all['d'])
        return {'pairs': int(len(m)), 'event_days': int(m['d'].nunique()),
                'pull_rate_of_chaseable': round(float(len(m) / max(1, r['chase'].notna().sum())), 4),
                'chase_mean': float(r['chase'].mean()),
                'pullback_mean_all': float(pb_all[col].mean()),
                'pullback_t_day': t_pb,
                'delta_pp': round(float(diff.mean()) * 100, 3),
                't_paired_by_day': t_d, 'pos_years': pos_y, 'n_years': int(gd_y.notna().sum())}

    def gates(st_):
        g1 = st_['pullback_mean_all'] > 0 and st_['pullback_t_day'] == st_['pullback_t_day'] and st_['pullback_t_day'] >= 2
        g2 = st_['delta_pp'] >= 1.0 and st_['t_paired_by_day'] == st_['t_paired_by_day'] and st_['t_paired_by_day'] >= 2
        g3 = st_['pos_years'] >= 6
        return {'G1_pullback_abs': bool(g1), 'G2_delta>=1pp&t>=2': bool(g2),
                'G3_posyears>=6': bool(g3),
                'verdict': 'PASS_CANDIDATE' if (g1 and g2 and g3) else 'FAIL'}

    out['main'] = paired_stats('main')
    out['main']['gates'] = gates(out['main'])
    print('MAIN:', json.dumps(out['main'], ensure_ascii=False))
    for name in ['s1_win13', 's2_win110', 's3_thr2', 's3_thr5']:
        out[name] = paired_stats(name)
        out[name]['gates'] = gates(out[name])
        print(f'{name}:', json.dumps({k: out[name][k] for k in ['pairs', 'delta_pp', 't_paired_by_day', 'pos_years']}, ensure_ascii=False))
    # S4 高位子带（pos>=0.70，贴近9/9语境但不复用其事件定义）
    hi = r[r['pos'] >= HIGH_POS]
    if len(hi):
        mh = hi.dropna(subset=['chase', 'main'])
        diff = mh['main'] - mh['chase']
        gd = diff.groupby(mh['d']).mean()
        t_h = float(gd.mean() / (gd.std(ddof=1) / np.sqrt(len(gd)))) if len(gd) > 2 and gd.std(ddof=1) > 0 else float('nan')
        out['s4_highpos'] = {'n_events': int(len(hi.dropna(subset=['chase']))),
                             'pairs': int(len(mh)), 'delta_pp': round(float(diff.mean()) * 100, 3),
                             't_paired_by_day': t_h,
                             'chase_mean': float(hi['chase'].mean()),
                             'pullback_mean': float(hi['main'].mean())}
        print('S4:', json.dumps(out['s4_highpos'], ensure_ascii=False))
    json.dump(out, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', OUT_JSON, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
