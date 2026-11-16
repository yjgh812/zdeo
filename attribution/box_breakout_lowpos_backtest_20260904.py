# -*- coding: utf-8 -*-
"""低位横盘放量突破 十年全A事件研究（研究层，禁接线生产）。
规则/基线/闸门全部以 reports/PREREG_BOX_BREAKOUT_LOWPOS_20260904.md 为准，本脚本零调参自由度。
价格源=dragon_quant_qfq.db（系统唯一回测价格源）；qfq+next-bar 开盘入场。"""
import sqlite3, json, hashlib, time, re, sys
import numpy as np
import pandas as pd

DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
ST_CACHE = r'D:/zcodesj/attribution/stock_info_cache.json'
PREREG = r'D:/zcodesj/reports/PREREG_BOX_BREAKOUT_LOWPOS_20260904.md'
EVAL_START, EVAL_END = 20160904, 20260903
COST = 0.002
HORIZONS = (5, 20, 60)
ALLOWED = ('600', '601', '603', '605', '000', '001', '002', '003', '300')
LOW_POS, BOX_AMP, VOL_X = 0.35, 0.25, 1.2

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    t0 = time.time()
    pre_hash = sha256(PREREG)
    st = json.load(open(ST_CACHE, encoding='utf-8'))['stocks']
    st_codes = {k for k, v in st.items() if v.get('is_st')}

    con = sqlite3.connect(DB)
    df = pd.read_sql(
        "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>='20150101' AND date<='20260903' ORDER BY code,date", con)
    con.close()
    # code 归一化为6位数字用于前缀/ST匹配
    df['code6'] = df['code'].str.extract(r'(\d{6})')[0]
    df = df.dropna(subset=['code6', 'open', 'close'])
    df['date'] = df['date'].astype(np.int64)
    for c in ('open', 'close', 'volume'):
        df[c] = df[c].astype(np.float64)
    print(f'load rows={len(df)} stocks={df.code6.nunique()} {time.time()-t0:.0f}s', flush=True)

    sig_rows = {h: [] for h in HORIZONS}          # (date, ret, unfillable)
    base_rows = {h: [] for h in HORIZONS}
    n_sig = 0

    for code6, g in df.groupby('code6', sort=False):
        if code6[:3] not in ALLOWED or f'{code6[:2]}{code6}' in st_codes:
            continue
        n = len(g)
        if n < 261:
            continue
        o = g['open'].to_numpy(); c = g['close'].to_numpy(); v = g['volume'].to_numpy()
        d = g['date'].to_numpy()
        # run1 bugfix: YYYYMMDD整数差跨月失真 → 转真实天数差
        dn = (pd.to_datetime(pd.Series(d.astype(np.int64).astype(str)), format='%Y%m%d')
              - pd.Timestamp('2000-01-01')).dt.days.to_numpy()
        s = pd.Series(c)
        # 仅用 ≤t 的窗口（无前视）：250含当日，60/20均用 t-1 截止
        mn250 = s.rolling(250, min_periods=250).min().to_numpy()
        mx250 = s.rolling(250, min_periods=250).max().to_numpy()
        prev = s.shift(1)
        box_mx = prev.rolling(60, min_periods=60).max().to_numpy()
        box_mn = prev.rolling(60, min_periods=60).min().to_numpy()
        vol_ma20 = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid='ignore', divide='ignore'):
            pos = (c - mn250) / np.where(mx250 > mn250, mx250 - mn250, np.nan)
            amp = box_mx / box_mn - 1.0
        sig = (pos <= LOW_POS) & (amp <= BOX_AMP) & (c > box_mx) & (v >= VOL_X * vol_ma20)
        sig &= (c <= 30.0) & ~np.isnan(pos) & ~np.isnan(box_mx) & ~np.isnan(vol_ma20)
        elig = sig | ((c <= 30.0) & ~np.isnan(box_mx))   # 基线=同宇宙eligible日
        idxs = np.where(elig)[0]
        if len(idxs) == 0:
            continue
        for i in idxs:
            if not (EVAL_START <= d[i] <= EVAL_END):   # 预注册十年窗口（信号日口径）
                continue
            j = i + 1
            if j >= n or dn[j] - dn[i] > 10:          # 停牌跨日>10天丢弃
                continue
            entry = o[j]
            if not np.isfinite(entry) or entry <= 0:
                continue
            gap = entry / c[i] - 1.0
            unf = gap >= (0.19 if code6[:3] == '300' else 0.095)
            for h in HORIZONS:
                k = j + h
                if k >= n or dn[k] - dn[j] > h * 1.6 + 15:   # 持有期末停牌跨日上限
                    continue
                if not np.isfinite(c[k]):
                    continue
                ret = c[k] / entry - 1.0 - COST
                if sig[i]:
                    sig_rows[h].append((d[i], ret, unf, code6))
                    if h == 20:
                        n_sig += 1
                base_rows[h].append((d[i], ret, unf, code6))
    print(f'signals(T+20)={n_sig} base_days={len(base_rows[20])} loop {time.time()-t0:.0f}s', flush=True)

    def stats(rows, by_day=True):
        if not rows:
            return None
        arr = np.array([(r[0], r[1], r[2]) for r in rows], dtype=np.float64)
        ret, unf = arr[:, 1], arr[:, 2].astype(bool)
        out = {}
        for tag, mask in (('all', np.ones(len(arr), bool)), ('fill_ok', ~unf)):
            r = ret[mask]
            dd = arr[mask, 0].astype(np.int64)
            res = {'n': int(len(r)), 'win': float((r > 0).mean()), 'mean': float(r.mean()),
                   'median': float(np.median(r))}
            if by_day:
                g = pd.Series(r).groupby(pd.Series(dd)).mean()
                res['days'] = int(len(g))
                res['t_day'] = float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))) if len(g) > 2 and g.std(ddof=1) > 0 else float('nan')
                res['mean_day'] = float(g.mean())
            out[tag] = res
        return out

    def yearly(rows, h):
        arr = np.array([(r[0], r[1]) for r in rows], dtype=np.float64)
        yr = (arr[:, 0] // 10000).astype(int)
        out = {}
        for y in sorted(set(yr)):
            r = arr[yr == y, 1]
            out[int(y)] = {'n': int(len(r)), 'win': round(float((r > 0).mean()), 4),
                           'mean_net': round(float(r.mean()), 5)}
        return out

    result = {'prereg_sha256': pre_hash, 'window': [EVAL_START, EVAL_END], 'cost': COST,
              'horizons': {}, 'gates': {}, 'yearly_T20_sig': None, 'yearly_T20_base': None}
    for h in HORIZONS:
        hs, hb = stats(sig_rows[h]), stats(base_rows[h])
        result['horizons'][h] = {'signal': hs, 'baseline': hb}
        if hs is None or hb is None:
            print(f'--- T+{h} --- EMPTY'); continue
        print(f'--- T+{h} ---')
        print('signal  ', json.dumps(hs, ensure_ascii=False))
        print('baseline', json.dumps(hb, ensure_ascii=False))
        if h == 20:
            result['yearly_T20_sig'] = yearly(sig_rows[h], h)
            result['yearly_T20_base'] = yearly(base_rows[h], h)
            s20, b20 = hs['all']['mean'], hb['all']['mean']
            g1 = s20 > 0 and hs['all'].get('t_day', 0) == hs['all'].get('t_day', 0) and hs['all'].get('t_day', -9) >= 2
            # G2: 增量及增量t（日聚簇配对）
            ds = pd.Series([r[1] for r in sig_rows[20]]).groupby([r[0] for r in sig_rows[20]]).mean()
            db_ = pd.Series([r[1] for r in base_rows[20]]).groupby([r[0] for r in base_rows[20]]).mean()
            common = ds.index.intersection(db_.index)
            diff = (ds[common] - db_[common])
            t_diff = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))) if len(diff) > 2 and diff.std(ddof=1) > 0 else float('nan')
            delta = s20 - b20
            g2 = delta >= 0.010 and t_diff == t_diff and t_diff >= 2
            ys = result['yearly_T20_sig']
            pos_years = sum(1 for y in ys if ys[y]['mean_net'] > 0)
            g3 = pos_years >= 6
            result['gates'] = {'G1_mean>0&t>=2': bool(g1), 'G2_delta>=1pp&t>=2': bool(g2),
                               'G3_posyears>=6': bool(g3), 'delta_pp': round(delta * 100, 3),
                               't_delta': t_diff, 'pos_years': pos_years,
                               'verdict': 'PASS_CANDIDATE' if (g1 and g2 and g3) else 'FAIL'}
            print('GATES:', json.dumps(result['gates'], ensure_ascii=False))
    out_json = r'D:/zcodesj/attribution/box_breakout_lowpos_result_20260904.json'
    json.dump(result, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', out_json, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
