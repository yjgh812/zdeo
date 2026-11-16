# -*- coding: utf-8 -*-
"""突破事件作 ml Top50 候选之上的二次确认过滤器 增量验证（研究层，禁接线生产）。
规则/基线/闸门全部以 reports/PREREG_BREAKOUT_CONFIRM_FILTER_20260911.md 为准，本脚本零调参自由度。
价格源=workbuddyspace dragon_quant_qfq.db（生产主库，fail-closed）；ml分数=同目录 dragon_quant.db.stock_ml_scores_v22。
复测前提（§10-8）：与 8/23 独立选股判死（用法不同）、9/4 低位横盘突破判死（基座+条件不同）三差异见 prereg §〇。"""
import sqlite3, json, hashlib, time, sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

QFQ_DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
ML_DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant.db'
ST_CACHE = r'D:/zcodesj/attribution/stock_info_cache.json'
PREREG = r'D:/zcodesj/reports/PREREG_BREAKOUT_CONFIRM_FILTER_20260911.md'
OUT_JSON = r'D:/zcodesj/attribution/breakout_confirm_filter_result_20260911.json'
EVAL_START, EVAL_END = 20200102, 20260904
COST = 0.002
TOPN = 50
VOL_X = 1.2
HORIZON_MAIN, HORIZON_S2 = 20, 5
ALLOWED = ('600', '601', '603', '605', '000', '001', '002', '003', '300')

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def day_stats(rows):
    """rows: (date, ret, unf) -> all/fill_ok 统计 + 日聚簇 t"""
    arr = np.array(rows, dtype=np.float64)
    ret, dd, unf = arr[:, 1], arr[:, 0].astype(np.int64), arr[:, 2] > 0.5
    out = {}
    for tag, mask in (('all', np.ones(len(arr), bool)), ('fill_ok', ~unf)):
        r, d_ = ret[mask], dd[mask]
        g = pd.Series(r).groupby(pd.Series(d_)).mean()
        out[tag] = {'n': int(len(r)), 'days': int(len(g)), 'win': float((r > 0).mean()),
                    'mean': float(r.mean()),
                    't_day': float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g))))
                    if len(g) > 2 and g.std(ddof=1) > 0 else float('nan')}
    return out

def paired_t(a_rows, b_rows, key=1):
    """按日配对均值差 t（#3 同款）"""
    da = pd.Series([r[key] for r in a_rows]).groupby([r[0] for r in a_rows]).mean()
    db = pd.Series([r[key] for r in b_rows]).groupby([r[0] for r in b_rows]).mean()
    common = da.index.intersection(db.index)
    if len(common) <= 2:
        return float('nan'), int(len(common))
    diff = da[common] - db[common]
    t = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))) if diff.std(ddof=1) > 0 else float('nan')
    return t, int(len(common))

def yearly_delta(conf_rows, unconf_rows):
    yc = pd.DataFrame(conf_rows, columns=['d', 'r', 'u'])
    yu = pd.DataFrame(unconf_rows, columns=['d', 'r', 'u'])
    yc['y'] = (yc['d'] // 10000).astype(int); yu['y'] = (yu['d'] // 10000).astype(int)
    mc, mu = yc.groupby('y')['r'].mean(), yu.groupby('y')['r'].mean()
    common = mc.index.intersection(mu.index)
    delta = (mc[common] - mu[common])
    return {int(y): round(float(v) * 100, 3) for y, v in delta.items()}

def main():
    t0 = time.time()
    pre_hash = sha256(PREREG)
    st = json.load(open(ST_CACHE, encoding='utf-8'))['stocks']
    st_codes = {k for k, v in st.items() if v.get('is_st')}

    # ── 基座：ml Top50 候选集（fail-closed）──
    con = sqlite3.connect(ML_DB)
    ml = pd.read_sql("SELECT code,date,ml_prob FROM stock_ml_scores_v22 "
                     f"WHERE date>={EVAL_START} ORDER BY date,ml_prob DESC,code", con)
    if ml.empty:
        raise SystemExit('FAIL-CLOSED: stock_ml_scores_v22 empty')
    top = ml.groupby('date', sort=False).head(TOPN)[['date', 'code']]
    top_set = set(zip(top['code'], top['date']))
    ml_dates = set(int(x) for x in top['date'].unique())
    print(f'ml_top50 candidate (code,day) pairs={len(top_set)} days={len(ml_dates)} {time.time()-t0:.0f}s', flush=True)

    # S1 敏感性：breakout_prob 当日 p80
    bo = pd.read_sql("SELECT code,date,breakout_prob FROM stock_signals_breakout "
                     "WHERE date>=20190101", con)
    bo['p80'] = bo.groupby('date')['breakout_prob'].transform(lambda s: s.quantile(0.80))
    bo_map = {(r.code, int(r.date)): (float(r.breakout_prob), float(r.p80))
              for r in bo.itertuples(index=False)}
    con.close()
    print(f'breakout_prob rows={len(bo)} {time.time()-t0:.0f}s', flush=True)

    # ── 价格（fail-closed 生产主库）──
    con = sqlite3.connect(QFQ_DB)
    mx = con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0]
    if int(mx) < 20260901:
        raise SystemExit(f'FAIL-CLOSED: qfq stale, max={mx}')
    df = pd.read_sql("SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
                     "WHERE date>='20160101' ORDER BY code,date", con)
    con.close()
    df['code6'] = df['code'].str.extract(r'(\d{6})')[0]
    df = df.dropna(subset=['code6', 'open', 'close'])
    df['date'] = df['date'].astype(np.int64)
    df['cand'] = [1 if (c, d) in top_set else 0 for c, d in zip(df['code6'], df['date'])]
    cand_codes = set(df.loc[df['cand'] == 1, 'code6'].unique())
    df = df[df['code6'].isin(cand_codes)].reset_index(drop=True)
    print(f'price rows(cand stocks)={len(df)} stocks={df.code6.nunique()} {time.time()-t0:.0f}s', flush=True)

    conf_rows, unconf_rows = {}, {}
    conf5, unconf5 = {}, {}
    confb, unconfb = {}, {}   # S1 breakout_prob p80 口径
    n_cand = 0

    for code6, g in df.groupby('code6', sort=False):
        if code6[:3] not in ALLOWED or f'{code6[:2]}{code6}' in st_codes:
            continue
        n = len(g)
        if n < 261:
            continue
        o = g['open'].to_numpy(); c = g['close'].to_numpy(); v = g['volume'].to_numpy()
        d = g['date'].to_numpy(); cand = g['cand'].to_numpy()
        dn = (pd.to_datetime(pd.Series(d.astype(np.int64).astype(str)), format='%Y%m%d')
              - pd.Timestamp('2000-01-01')).dt.days.to_numpy()
        prev = pd.Series(c).shift(1)
        box_mx = prev.rolling(60, min_periods=60).max().to_numpy()
        vol_ma20 = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid='ignore'):
            brk = (c > box_mx) & (v >= VOL_X * vol_ma20)
        brk &= ~np.isnan(box_mx) & ~np.isnan(vol_ma20)
        for i in range(n):
            if cand[i] != 1 or c[i] > 30.0:
                continue
            n_cand += 1
            j = i + 1
            if j >= n or dn[j] - dn[i] > 10:
                continue
            entry = o[j]
            if not np.isfinite(entry) or entry <= 0:
                continue
            gap = entry / c[i] - 1.0
            unf = gap >= (0.19 if code6[:3] == '300' else 0.095)
            is_conf = bool(brk[i])
            bp = bo_map.get((code6, int(d[i])))
            is_conf_b = bool(bp and bp[0] >= bp[1]) if bp else False
            for h, rc, ru in ((HORIZON_MAIN, conf_rows, unconf_rows),
                              (HORIZON_S2, conf5, unconf5)):
                k = j + h
                if k >= n or dn[k] - dn[j] > h * 1.6 + 15:
                    continue
                if not np.isfinite(c[k]):
                    continue
                ret = c[k] / entry - 1.0 - COST
                if is_conf:
                    rc.setdefault(h, []).append((d[i], ret, float(unf)))
                else:
                    ru.setdefault(h, []).append((d[i], ret, float(unf)))
                if h == HORIZON_MAIN and is_conf_b:
                    confb.setdefault(h, []).append((d[i], ret, float(unf)))
                elif h == HORIZON_MAIN:
                    unconfb.setdefault(h, []).append((d[i], ret, float(unf)))
    print(f'candidates={n_cand} loop {time.time()-t0:.0f}s', flush=True)

    result = {'prereg_sha256': pre_hash, 'window': [EVAL_START, EVAL_END],
              'topn': TOPN, 'cost': COST, 'n_candidates_T20': n_cand, 'gates': {}}
    for h, rc_map, ru_map in ((HORIZON_MAIN, conf_rows, unconf_rows),
                              (HORIZON_S2, conf5, unconf5)):
        rc, ru = rc_map.get(h, []), ru_map.get(h, [])
        if not rc or not ru:
            print(f'--- T+{h} --- EMPTY group'); continue
        sc, su = day_stats(rc), day_stats(ru)
        merged = rc + ru
        sm = day_stats(merged)
        t_cu, n_pair = paired_t(rc, ru)
        t_cm, _ = paired_t(rc, merged)
        delta = sc['all']['mean'] - su['all']['mean']
        print(f'--- T+{h} --- confirm {json.dumps(sc, ensure_ascii=False)}')
        print(f'unconfirm {json.dumps(su, ensure_ascii=False)}')
        print(f'merged   {json.dumps(sm, ensure_ascii=False)}')
        print(f'delta(conf-unconf)={delta*100:.3f}pp t={t_cu:.2f} pairs={n_pair} | conf-merged={(sc["all"]["mean"]-sm["all"]["mean"])*100:.3f}pp t={t_cm:.2f}')
        if h == HORIZON_MAIN:
            yd = yearly_delta(rc, ru)
            pos_years = sum(1 for v in yd.values() if v > 0)
            g1 = sc['all']['mean'] > 0 and sc['all']['t_day'] == sc['all']['t_day'] and sc['all']['t_day'] >= 2
            g2 = delta >= 0.010 and t_cu == t_cu and t_cu >= 2
            g3 = pos_years >= 5
            result['yearly_delta_pp'] = yd
            result['gates'] = {'G1_conf_mean>0&t_day>=2': bool(g1), 'G2_delta>=1pp&t>=2': bool(g2),
                               'G3_posyears>=5of7': bool(g3), 'delta_pp': round(delta * 100, 3),
                               't_delta': t_cu, 'pos_years': pos_years,
                               'verdict': 'PASS_CANDIDATE' if (g1 and g2 and g3) else 'FAIL'}
            print('GATES:', json.dumps(result['gates'], ensure_ascii=False))
            # S1 / fill_ok 披露
            if confb.get(h) and unconfb.get(h):
                sbc, sbu = day_stats(confb[h]), day_stats(unconfb[h])
                t_b, _ = paired_t(confb[h], unconfb[h])
                result['s1_breakout_prob_p80'] = {
                    'conf': sbc['all'], 'unconf': sbu['all'],
                    'delta_pp': round((sbc['all']['mean'] - sbu['all']['mean']) * 100, 3),
                    't_delta': t_b}
                print('S1:', json.dumps(result['s1_breakout_prob_p80'], ensure_ascii=False))
            result['fill_ok'] = {'conf_mean': sc['fill_ok']['mean'], 'unconf_mean': su['fill_ok']['mean'],
                                 'delta_pp': round((sc['fill_ok']['mean'] - su['fill_ok']['mean']) * 100, 3)}
    json.dump(result, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', OUT_JSON, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
