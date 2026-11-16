# -*- coding: utf-8 -*-
"""PREREG-DDPROTECT-V6 (指纹 b411db6294b5144a) 执行器.

门控降鞭挞变体验证: 载体=引擎A信号池组合(与V3零漂移), 8新变体+1对照.
判据=保护效率×干预频率三维. 唯一写入=结果JSON; panel/qfq只读.
用法: python -u ddprotect_v6_20260914.py
"""
import hashlib
import json
import math
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
HERE = r'D:\zcodesj\portrait_agent'
PREREG_PATH = r'D:\zcodesj\data\prereg_ddprotect_v6_20260914.json'
OUT = r'D:\zcodesj\data\ddprotect_v6_results_20260914.json'
COST = 0.0015
START = '20200101'


def metrics(r: pd.Series):
    r = r.dropna()
    ann_vol = r.std(ddof=1) * math.sqrt(252)
    ann_ret = r.mean() * 252
    eq = (1 + r).cumprod()
    mdd = float((eq / eq.cummax() - 1).min()) * 100
    worst = np.sort(r.values)[:max(1, int(len(r) * 0.05))]
    return {'ann_ret': round(ann_ret * 100, 2), 'sharpe': round(ann_ret / ann_vol, 3) if ann_vol > 0 else None,
            'mdd': round(mdd, 2), 'cvar5': round(float(worst.mean()) * 100, 3), 'n': int(len(r))}


def build_carrier(con):
    panel = pd.read_pickle(HERE + r'\cache\panel_v6_full.pkl')
    panel['date'] = panel['date'].astype(str)
    panel = panel[panel['date'] >= START]
    pos = pd.to_numeric(panel['pos250'], errors='coerce')
    ev = (panel[['chan_buy1_10', 'chan_buy2_10', 'island_5', 'gap_up_hold']].fillna(0) > 0.5).any(axis=1)
    neg = (panel[['rounded_20', 'lhb_buy_5', 'vwap60_bias', 'vwap20_bias']].fillna(1) <= 0.5).all(axis=1)
    sig = panel[(pos <= 0.40) & ev & neg][['date', 'code']].copy()
    del panel, pos, ev, neg
    sig = sig[~sig['code'].astype(str).str.startswith('688')]
    tds = [str(r0[0]) for r0 in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date", (START,)).fetchall()]
    dpos = {d: i for i, d in enumerate(tds)}
    n_days = len(tds)
    codes = sorted(sig['code'].astype(str).unique())
    ph = ','.join('?' * len(codes))
    px = pd.read_sql_query(
        f"SELECT code,date,change_pct FROM stock_daily_meta_qfq WHERE date>=? AND code IN ({ph})",
        con, params=[START] + codes)
    px['date'] = px['date'].astype(str)
    px['ret'] = pd.to_numeric(px['change_pct'], errors='coerce') / 100.0
    cpos = {c: i for i, c in enumerate(codes)}
    R = np.full((n_days, len(codes)), np.nan)
    tds_arr = np.array(tds)
    row = np.searchsorted(tds_arr, px['date'].values)
    col = np.array([cpos.get(str(c), -1) for c in px['code'].values])
    ok = (col >= 0) & (row < n_days)
    R[row[ok], col[ok]] = px['ret'].values[ok]
    sums = np.zeros(n_days)
    cnts = np.zeros(n_days)
    for d, c in sig[['date', 'code']].values:
        i0 = dpos.get(d)
        ci = cpos.get(str(c))
        if i0 is None or ci is None or i0 + 21 >= n_days:
            continue
        sl = slice(i0 + 2, i0 + 22)
        colv = R[sl, ci]
        mask = ~np.isnan(colv)
        sums[sl][mask] += colv[mask]
        cnts[sl][mask] += 1
    carrier = np.where(cnts > 0, sums / np.maximum(cnts, 1), 0.0)
    index = pd.to_datetime(pd.Series(tds), format='%Y%m%d')
    return pd.Series(carrier, index=index), index


def gate_series(midx: pd.Series, kind: str, param) -> pd.Series:
    ma20 = midx.rolling(20).mean()
    below = midx < ma20
    above = midx > ma20
    if kind in ('daily', 'baseline'):
        return below  # True=防御
    if kind == 'hyst':
        b = param
        state = pd.Series(index=midx.index, dtype=bool)
        cur = False
        for t in midx.index:
            if not cur and below.loc[t] and (midx.loc[t] < ma20.loc[t] * (1 - b)):
                cur = True
            elif cur and above.loc[t] and (midx.loc[t] > ma20.loc[t] * (1 + b)):
                cur = False
            state.loc[t] = cur
        return state
    if kind == 'confirm2':
        enter = below & below.shift(1).fillna(False)
        exit_ = above & above.shift(1).fillna(False)
        state = pd.Series(index=midx.index, dtype=bool)
        cur = False
        for t in midx.index:
            if not cur and enter.loc[t]:
                cur = True
            elif cur and exit_.loc[t]:
                cur = False
            state.loc[t] = cur
        return state
    if kind == 'weekly':
        daily_gate = below
        fri = pd.Series(daily_gate.index.dayofweek == 4, index=daily_gate.index)
        upd = daily_gate.where(fri).ffill()
        return upd.fillna(False)
    raise ValueError(kind)


def main() -> int:
    prereg = json.load(open(PREREG_PATH, encoding='utf-8'))
    fp = prereg['fingerprint']
    body = {k: v for k, v in prereg.items() if k != 'fingerprint'}
    assert fp == hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                                .encode('utf-8')).hexdigest()[:16], '指纹不符'
    print(f'[prereg] DDPROTECT-V6 指纹校验通过 {fp}')

    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    r, dates = build_carrier(con)
    mkt = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>=? GROUP BY date ORDER BY date", con, params=(START,))
    con.close()
    mret = (mkt.assign(date=mkt['date'].astype(str))
              .set_index(pd.to_datetime(mkt['date'], format='%Y%m%d'))['ret']
              .astype(float).reindex(dates).fillna(0.0))
    midx = (1 + mret).cumprod()
    m_bh = metrics(r)
    print(f'[carrier] 年化={m_bh["ann_ret"]}% MDD={m_bh["mdd"]}% CVaR5={m_bh["cvar5"]}%')

    variants = [('baseline', None, 1.0), ('baseline', None, 0.5)]
    for b in (0.01, 0.02):
        for fl in (0.5, 0.7):
            variants.append(('hyst', b, fl))
    for fl in (0.5, 0.7):
        variants.append(('confirm2', None, fl))
    for fl in (0.5, 0.7):
        variants.append(('weekly', None, fl))

    years = len(r) / 242
    rows = []
    for kind, param, fl in variants:
        defense = gate_series(midx, kind, param)
        w = pd.Series(np.where(defense, fl, 1.0), index=r.index)
        w_exec = w.shift(1)
        rp = (w_exec * r - (w_exec - w_exec.shift(1)).abs() * COST).dropna()
        m = metrics(rp)
        flips = int((defense.dropna() != defense.dropna().shift(1)).sum())
        rows.append({'kind': kind, 'param': param, 'floor': fl,
                     'mdd': m['mdd'], 'mdd_impr': round(m['mdd'] - m_bh['mdd'], 2),
                     'drag': round(m_bh['ann_ret'] - m['ann_ret'], 2), 'cvar5': m['cvar5'],
                     'flips_per_year': round(flips / years, 1),
                     'pass_p1': bool((m['mdd'] - m_bh['mdd']) >= 4 and
                                     (m_bh['ann_ret'] - m['ann_ret']) <= 2 and
                                     flips / years <= 12 and m['cvar5'] >= m_bh['cvar5'])})
    res = {'prereg_id': prereg['id'], 'fingerprint': fp, 'carrier': m_bh,
           'variants': rows,
           'P0': {'eval_days': int(len(r)), 'pass': bool(len(r) >= 800)}}
    passers = [x for x in rows if x['pass_p1']]
    p1 = bool(passers)
    p2 = False
    best = None
    if passers:
        best = sorted(passers, key=lambda x: (x['flips_per_year'], -x['mdd_impr']))[0]
        rp = None
        defense = gate_series(midx, best['kind'], best['param'])
        w = pd.Series(np.where(defense, best['floor'], 1.0), index=r.index)
        w_exec = w.shift(1)
        rp = (w_exec * r - (w_exec - w_exec.shift(1)).abs() * COST).dropna()
        mid = rp.index[len(rp) // 2]
        h1 = metrics(rp[rp.index <= mid])['mdd'] - metrics(r[r.index <= mid])['mdd']
        h2 = metrics(rp[rp.index > mid])['mdd'] - metrics(r[r.index > mid])['mdd']
        p2 = bool(h1 > 0 and h2 > 0)
        res['P2'] = {'best_variant': best, 'halves_mdd_impr': [round(h1, 2), round(h2, 2)], 'pass': p2}
    else:
        res['P2'] = {'pass': False, 'note': '无变体过P1'}
    verdict = 'PASS' if (res['P0']['pass'] and p1 and p2) else ('INSUFFICIENT_POWER' if not res['P0']['pass'] else 'FAIL')
    res['P1'] = {'n_pass': len(passers), 'pass': p1}
    res['verdict'] = verdict

    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n===== 变体表 (载体 MDD', m_bh['mdd'], '/ CVaR5', m_bh['cvar5'], ') =====')
    for x in rows:
        print(f"  {x['kind']:<10}{str(x['param'] or ''):<6} floor{x['floor']}: "
              f"改善{x['mdd_impr']}pp 拖累={x['drag']}pp CVaR5={x['cvar5']} "
              f"翻转={x['flips_per_year']}/年 P1={x['pass_p1']}")
    print(f"VERDICT = {verdict}")
    if best:
        print('推荐变体:', best)
    print(f'结果已写: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
