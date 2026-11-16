# -*- coding: utf-8 -*-
"""_portrait_regime_gate_20260915.py — regime 门控画像检验（prereg 先于运行，零调参）。
规则/闸门以 reports/PREREG_PORTRAIT_REGIME_GATE_20260915.md 为准。
数据=data/_portrait_construct_verify_20260913.json（251截面超额矩阵）× 上证 pos250 T-1。"""
import os, sys, json, hashlib, struct, datetime
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PREREG = r'D:/zcodesj/reports/PREREG_PORTRAIT_REGIME_GATE_20260915.md'
DATA = r'D:/zcodesj/data/_portrait_construct_verify_20260913.json'
VIPDOC_SH = r'D:/anzhuang/tdx/vipdoc/sh/lday/sh000001.day'
OUT_JSON = r'D:/zcodesj/attribution/portrait_regime_gate_20260915.json'
THETA = 0.5

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def sh_pos250_series():
    dates, closes = [], []
    with open(VIPDOC_SH, 'rb') as f:
        data = f.read()
    rec = struct.Struct('<IIIIIfII')
    for i in range(0, len(data) - 31, 32):
        d, o = rec.unpack_from(data, i)[0], rec.unpack_from(data, i)[1]
        if 20140101 <= d <= 20269999:
            dates.append(d); closes.append(o)
    s = pd.Series(closes)
    mn = s.rolling(250, min_periods=250).min()
    mx = s.rolling(250, min_periods=250).max()
    pos = (s - mn) / (mx - mn)
    return dict(zip(dates, pos.round(4)))

def prev_trade_day_pos(pos_map, d):
    """T-1 交易日的 pos250（vipdoc 日期序列中 <d 的最大日期）"""
    ds = [x for x in pos_map if x < d]
    return pos_map.get(max(ds)) if ds else None

def week_t(vals, dates):
    s = pd.Series(vals)
    g = s.groupby(pd.Series(dates).str[:6] + 'W' + pd.to_datetime(
        pd.Series(dates)).dt.isocalendar().week.astype(str)).mean()
    if len(g) <= 2 or g.std(ddof=1) == 0:
        return float('nan'), int(len(g))
    return float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))), int(len(g))

def main():
    t0 = __import__('time').time()
    pre_hash = sha256(PREREG)
    data = json.load(open(DATA, encoding='utf-8'))
    df = pd.DataFrame([{k: v for k, v in it.items() if not isinstance(v, dict)}
                       for it in data])
    # 十分位 dict 列(short_dec_h5等)已被过滤; base 是 dict → 提取 base5
    df['base5'] = [it['base'].get('5') if isinstance(it.get('base'), dict) else None for it in data]
    pos_map = sh_pos250_series()
    df['mkt_pos_t1'] = [prev_trade_day_pos(pos_map, int(d)) for d in df['date']]
    df['regime'] = np.where(df['mkt_pos_t1'].isna(), 'NA',
                   np.where(df['mkt_pos_t1'] >= THETA, 'HIGH', 'LOW'))
    print(f'截面={len(df)} HIGH={int((df.regime=="HIGH").sum())} LOW={int((df.regime=="LOW").sum())} NA={int((df.regime=="NA").sum())} {t0:.0f}')

    out = {'prereg_sha256': pre_hash, 'theta': THETA, 'n': len(df),
           'n_high': int((df.regime == 'HIGH').sum()), 'n_low': int((df.regime == 'LOW').sum())}
    verdicts = {}
    for model in ('short', 'mid'):
        col = f'{model}_top20_h5'
        sub = df.dropna(subset=[col, 'mkt_pos_t1'])
        hi, lo = sub[sub.regime == 'HIGH'], sub[sub.regime == 'LOW']
        t_hi, nwk_hi = week_t(hi[col], hi['date'])
        delta = float(hi[col].mean() - lo[col].mean()) if len(hi) and len(lo) else float('nan')  # 原始值已为百分数
        # Welch t（组间差）
        try:
            from scipy import stats as _s
            t_w = float(_s.ttest_ind(hi[col], lo[col], equal_var=False)[0]) if len(hi) > 2 and len(lo) > 2 else float('nan')
        except Exception:
            n1, n2 = len(hi), len(lo)
            m = hi[col].mean() - lo[col].mean()
            se = np.sqrt(hi[col].var(ddof=1)/n1 + lo[col].var(ddof=1)/n2)
            t_w = float(m/se) if se > 0 else float('nan')
        # G3 按月
        hi_m = hi.assign(mo=hi['date'].str[:6]).groupby('mo')[col].mean()
        pos_m = float((hi_m > 0).mean()) if len(hi_m) else float('nan')
        g1 = (hi[col].mean() > 0) and t_hi == t_hi and t_hi >= 2
        g2 = (delta >= 0.02) and t_w == t_w and t_w >= 2
        g3 = pos_m >= 2/3
        blk = {'HIGH_n': int(len(hi)), 'HIGH_mean_pp': round(float(hi[col].mean()), 3),
               'HIGH_t_week': round(t_hi, 2) if t_hi == t_hi else None, 'HIGH_weeks': nwk_hi,
               'LOW_n': int(len(lo)), 'LOW_median': round(float(lo[col].median()), 3) if len(lo) else None,
               'LOW_dates': sorted(lo['date'].tolist())[:20], 'LOW_mean_pp': round(float(lo[col].mean()), 3),
               'delta_pp': round(delta, 3), 't_welch': round(t_w, 2) if t_w == t_w else None,
               'HIGH_pos_month_ratio': round(pos_m, 3),
               'G1': bool(g1), 'G2': bool(g2), 'G3': bool(g3),
               'verdict': 'PASS_CANDIDATE' if (g1 and g2 and g3) else 'FAIL'}
        verdicts[model] = blk
        print(model, json.dumps(blk, ensure_ascii=False))
    out['verdicts'] = verdicts
    overall = any(v['verdict'] == 'PASS_CANDIDATE' for v in verdicts.values())
    out['overall'] = 'PASS_CANDIDATE(诊断性)' if overall else 'FAIL'
    print('OVERALL:', out['overall'])
    # 敏感性 θ=0.4/0.6（仅披露）
    sens = {}
    for th in (0.4, 0.6):
        sub = df.dropna(subset=['short_top20_h5', 'mkt_pos_t1'])
        hi = sub[sub.mkt_pos_t1 >= th]; lo = sub[sub.mkt_pos_t1 < th]
        sens[f'theta_{th}'] = {'HIGH_mean_pp': round(float(hi['short_top20_h5'].mean()), 3) if len(hi) else None,
                               'LOW_mean_pp': round(float(lo['short_top20_h5'].mean()), 3) if len(lo) else None,
                               'n_high': int(len(hi)), 'n_low': int(len(lo))}
    out['sensitivity_short_top20h5'] = sens
    print('SENS:', json.dumps(sens, ensure_ascii=False))
    json.dump(out, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', OUT_JSON, f'{__import__("time").time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
