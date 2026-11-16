# -*- coding: utf-8 -*-
"""首板蓄势二波 全历史事件研究（研究层，禁接线生产）。
规则/判据全部以 data/PREREG_BREAKOUT_SCOUT_TRADEABLE_20260906.json 为准，本脚本零调参自由度。
价格源=workbuddyspace factor_engine qfq 库（box 20260904 回测同款生产库，至 20260904 完好；
zcodesj 主库 0 字节重建中）。
与 BOX-BREAKOUT-LOWPOS(9/4 FALSIFIED) 划界（prereg frozen）：
  彼=横盘箱体放量突破当日即买；此=首板涨停(资金确认)→蓄势不破(控盘确认)→间隔>=3交易日
  再板(二波确认)，入场=二板日次日收盘。同脚本复算 box 信号集用于重叠率判据。
窗口注记：prereg 冻结文本 execution_plan 写 2019-2026，但 G2 判据要求 2018 分年卡
（box 死于 2018 胜率 28.9%），二者内在矛盾；取忠实于判据意图的窗口 20180101 起，
在此如实声明（判据本身未改）。
"""
import sqlite3, json, hashlib, time, sys
import numpy as np
import pandas as pd

DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
ST_CACHE = r'D:\zcodesj\attribution\stock_info_cache.json'
PREREG = r'D:\zcodesj\data\PREREG_BREAKOUT_SCOUT_TRADEABLE_20260906.json'
EVAL_START, EVAL_END = 20180101, 20260904
COST = 0.003                     # 往返合计千三（prereg"双边千三"，单次扣减口径同 box 先例）
HORIZONS = (20,)                 # prereg 锁定判据窗口；另附 5/60 参考
ALLOWED = ('600', '601', '603', '605', '000', '001', '002', '003', '300')
LOW_POS_MAX, VOL_X, HOLD_FLOOR, HOLD_DD, MIN_GAP, TRACK_WIN = 0.25, 2.0, 0.97, 0.15, 3, 20

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    t0 = time.time()
    pre = json.load(open(PREREG, encoding='utf-8'))
    st = json.load(open(ST_CACHE, encoding='utf-8'))['stocks']
    st_codes = {k for k, v in st.items() if v.get('is_st')}

    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    df = pd.read_sql(
        "SELECT code,date,open,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>='20170601' AND date<='20260904' ORDER BY code,date", con)
    con.close()
    df['code6'] = df['code'].astype(str).str.extract(r'(\d{6})')[0]
    df = df.dropna(subset=['code6', 'open', 'close', 'change_pct'])
    df['date'] = df['date'].astype(np.int64)
    for c in ('open', 'close', 'volume', 'change_pct'):
        df[c] = df[c].astype(np.float64)
    print(f'load rows={len(df)} stocks={df.code6.nunique()} {time.time()-t0:.0f}s', flush=True)

    sig_rows, base_rows = [], []     # (sig_date, ret, unf, code6)
    box_rows = []                    # box 信号日（重叠率用）: (code6, date)
    n_sig = 0

    for code6, g in df.groupby('code6', sort=False):
        if code6[:3] not in ALLOWED or f'{code6[:2]}{code6}' in st_codes:
            continue
        n = len(g)
        if n < 261:
            continue
        o = g['open'].to_numpy(); c = g['close'].to_numpy(); v = g['volume'].to_numpy()
        d = g['date'].to_numpy(); pct = g['change_pct'].to_numpy()
        dn = (pd.to_datetime(pd.Series(d.astype(np.int64).astype(str)), format='%Y%m%d')
              - pd.Timestamp('2000-01-01')).dt.days.to_numpy()
        thr = 19.8 if code6[:3] == '300' else 9.8
        s_c, s_v, s_pct = pd.Series(c), pd.Series(v), pd.Series(pct)

        # ---- 本信号：首板 → 蓄势 → 二波 ----
        is_limit = s_pct >= thr
        prior10_lim = is_limit.shift(1).rolling(10, min_periods=10).sum().to_numpy()
        c60 = s_c.shift(60)
        with np.errstate(invalid='ignore', divide='ignore'):
            gain60 = c / c60.to_numpy() - 1.0
        vol_ma20 = s_v.shift(1).rolling(20, min_periods=20).mean().to_numpy()
        board = (is_limit.to_numpy() & (prior10_lim == 0) & (gain60 < LOW_POS_MAX)
                 & (v >= VOL_X * vol_ma20) & (c <= 30.0)
                 & ~np.isnan(prior10_lim) & ~np.isnan(gain60) & ~np.isnan(vol_ma20))
        elig = board | ((c <= 30.0) & ~np.isnan(vol_ma20) & ~np.isnan(c60.to_numpy()))

        # box 信号（逐字照抄 box_breakout_lowpos_backtest_20260904 定义，重叠率判据用）
        ps = pd.Series(c)
        mn250 = ps.rolling(250, min_periods=250).min().to_numpy()
        mx250 = ps.rolling(250, min_periods=250).max().to_numpy()
        prev = s_c.shift(1)
        box_mx = prev.rolling(60, min_periods=60).max().to_numpy()
        box_mn = prev.rolling(60, min_periods=60).min().to_numpy()
        with np.errstate(invalid='ignore', divide='ignore'):
            pos = (c - mn250) / np.where(mx250 > mn250, mx250 - mn250, np.nan)
            amp = box_mx / box_mn - 1.0
        box_sig = ((pos <= 0.35) & (amp <= 0.25) & (c > box_mx) & (v >= 1.2 * vol_ma20)
                   & (c <= 30.0) & ~np.isnan(pos) & ~np.isnan(box_mx) & ~np.isnan(vol_ma20))

        b_idx = np.where(board)[0]
        if len(b_idx) == 0 and not elig.any():
            continue
        sig_local = set()
        for i in b_idx:                      # i=首板日
            floor = o[i] * HOLD_FLOOR
            broke = False
            for j in range(i + 1, min(i + 1 + TRACK_WIN, n)):   # j=首板后第 j-i 交易日
                if pct[j] >= thr and (j - i) >= MIN_GAP:
                    sig_local.add(j)          # 二板日=信号日
                    broke = True
                    break
                if c[j] < floor or (c[j] / c[i] - 1.0) < -HOLD_DD:
                    break
            # 超窗口未二波=stale，无动作
        for i in np.where(elig)[0]:
            if not (EVAL_START <= d[i] <= EVAL_END):
                continue
            j = i + 1
            if j >= n or dn[j] - dn[i] > 10:
                continue
            entry = c[j]                      # prereg: 信号日次日收盘 next-bar
            if not np.isfinite(entry) or entry <= 0:
                continue
            unf = (entry / c[i] - 1.0) >= (thr - 0.3) / 100.0   # 次日收盘仍涨停≈封板不可买
            for h in HORIZONS:
                k = j + h
                if k >= n or dn[k] - dn[j] > h * 1.6 + 15:
                    continue
                if not np.isfinite(c[k]):
                    continue
                ret = c[k] / entry - 1.0 - COST
                row = (d[i], ret, unf, code6)
                base_rows.append(row)
                if i in sig_local:
                    sig_rows.append(row)
                    if h == 20:
                        n_sig += 1
        if EVAL_END >= 20180101:
            for i in np.where(box_sig & (d >= EVAL_START) & (d <= EVAL_END))[0]:
                box_rows.append((code6, int(d[i])))
    print(f'signals(T+20)={n_sig} base_days={len(base_rows)} box_days={len(box_rows)} '
          f'loop {time.time()-t0:.0f}s', flush=True)

    def stats(rows):
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
            g = pd.Series(r).groupby(pd.Series(dd)).mean()
            res['days'] = int(len(g))
            res['t_day'] = float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))) \
                if len(g) > 2 and g.std(ddof=1) > 0 else float('nan')
            out[tag] = res
        return out

    def yearly(rows):
        arr = np.array([(r[0], r[1]) for r in rows], dtype=np.float64)
        yr = (arr[:, 0] // 10000).astype(int)
        return {int(y): {'n': int((yr == y).sum()),
                         'win': round(float((arr[yr == y, 1] > 0).mean()), 4),
                         'mean_net': round(float(arr[yr == y, 1].mean()), 5)}
                for y in sorted(set(yr))}

    result = {'prereg': pre['name'], 'prereg_sha256': sha256(PREREG),
              'window': [EVAL_START, EVAL_END], 'cost': COST, 'db': DB,
              'horizons': {'20': {'signal': stats(sig_rows), 'baseline': stats(base_rows)}}}
    hs, hb = stats(sig_rows), stats(base_rows)
    print('signal  ', json.dumps(hs, ensure_ascii=False))
    print('baseline', json.dumps(hb, ensure_ascii=False))
    ys = yearly(sig_rows); yb = yearly(base_rows)
    result['yearly_sig'], result['yearly_base'] = ys, yb

    # G1 事件20日超额>=0 且 t>=2（月度切片配对为主判据；日聚簇并列参考）
    ds_ = pd.Series([r[1] for r in sig_rows]).groupby([r[0] for r in sig_rows]).mean()
    db_ = pd.Series([r[1] for r in base_rows]).groupby([r[0] for r in base_rows]).mean()
    common = ds_.index.intersection(db_.index)
    diff_day = ds_[common] - db_[common]
    t_day = float(diff_day.mean() / (diff_day.std(ddof=1) / np.sqrt(len(diff_day)))) \
        if len(diff_day) > 2 and diff_day.std(ddof=1) > 0 else float('nan')
    m_sig = pd.Series([r[1] for r in sig_rows]).groupby([str(r[0])[:6] for r in sig_rows]).mean()
    m_base = pd.Series([r[1] for r in base_rows]).groupby([str(r[0])[:6] for r in base_rows]).mean()
    mc = m_sig.index.intersection(m_base.index)
    diff_m = m_sig[mc] - m_base[mc]
    t_month = float(diff_m.mean() / (diff_m.std(ddof=1) / np.sqrt(len(diff_m)))) \
        if len(diff_m) > 2 and diff_m.std(ddof=1) > 0 else float('nan')
    delta = hs['all']['mean'] - hb['all']['mean']
    g1 = (delta >= 0) and t_month == t_month and t_month >= 2

    # G2 2018 分年（box 死因针对性闸）
    y18s, y18b = ys.get(2018), yb.get(2018)
    g2 = False
    if y18s and y18b:
        ex18 = y18s['mean_net'] - y18b['mean_net']
        g2 = (ex18 > -0.02) and (y18s['win'] > 0.40)
    else:
        ex18, y18s = None, None

    # G3 与 box 信号重叠率（同股同日口径）
    my_set = {(r[3], r[0]) for r in sig_rows}
    box_set = set(box_rows)
    ov = len(my_set & box_set)
    overlap = ov / len(my_set) if my_set else float('nan')
    g3 = overlap < 0.30

    # G4 正超额年占比>=50%
    pos_years = sum(1 for y in ys if y in yb and (ys[y]['mean_net'] - yb[y]['mean_net']) > 0)
    tot_years = sum(1 for y in ys if y in yb)
    g4 = tot_years > 0 and pos_years / tot_years >= 0.5

    result['gates'] = {
        'G1_excess>=0&t_month>=2': bool(g1), 'excess_pp': round(delta * 100, 3),
        't_month': t_month, 't_day_ref': t_day, 'n_months': int(len(mc)),
        'G2_2018_ex>-2%&win>40%': bool(g2), 'y2018': {'sig': y18s, 'excess': ex18},
        'G3_overlap<30%': bool(g3), 'overlap': round(overlap, 4),
        'overlap_n': ov, 'my_n': len(my_set), 'box_n': len(box_set),
        'G4_posyears>=50%': bool(g4), 'pos_years': pos_years, 'tot_years': tot_years,
        'verdict': 'PASS_CANDIDATE' if (g1 and g2 and g3 and g4) else 'FAIL'}
    print('GATES:', json.dumps(result['gates'], ensure_ascii=False))
    out_json = r'D:\zcodesj\attribution\breakout_scout_eventstudy_result_20260906.json'
    json.dump(result, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', out_json, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
