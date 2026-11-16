# -*- coding: utf-8 -*-
"""PREREG-DRAWDOWN-PROTECT-V3 (指纹 9c6198e42dfd5242) 执行器.

回撤保护覆盖层验证: 载体=引擎A信号池等权组合, 机制=上行满仓/下行压敞口,
判据=MDD/尾部保护效率(非Sharpe). 唯一写入=结果JSON; panel/qfq只读.
用法: python -u ddprotect_v3_20260914.py
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
PREREG_PATH = r'D:\zcodesj\data\prereg_ddprotect_v3_20260914.json'
OUT = r'D:\zcodesj\data\ddprotect_v3_results_20260914.json'
COST = 0.0015
START = '20200101'


def metrics(r: pd.Series):
    r = r.dropna()
    ann_vol = r.std(ddof=1) * math.sqrt(252)
    ann_ret = r.mean() * 252
    eq = (1 + r).cumprod()
    mdd = float((eq / eq.cummax() - 1).min()) * 100
    worst = np.sort(r.values)[:max(1, int(len(r) * 0.05))]
    return {'ann_ret': round(ann_ret * 100, 2), 'ann_vol': round(ann_vol * 100, 2),
            'sharpe': round(ann_ret / ann_vol, 3) if ann_vol > 0 else None,
            'mdd': round(mdd, 2), 'cvar5': round(float(worst.mean()) * 100, 3), 'n': int(len(r))}


def overlay(r: pd.Series, ma_n: int, floor: float, cash: float = 0.0):
    idx_px = (1 + r).cumprod()
    gate = idx_px > idx_px.rolling(ma_n).mean()
    w = pd.Series(np.where(gate, 1.0, floor), index=r.index)
    w_exec = w.shift(1)
    cash_yield = (1 - w_exec) * cash / 252
    rp = (w_exec * r + cash_yield - (w_exec - w_exec.shift(1)).abs() * COST).dropna()
    return rp, w_exec


def main() -> int:
    prereg = json.load(open(PREREG_PATH, encoding='utf-8'))
    fp = prereg['fingerprint']
    body = {k: v for k, v in prereg.items() if k != 'fingerprint'}
    assert fp == hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                                .encode('utf-8')).hexdigest()[:16], '指纹不符'
    print(f'[prereg] DRAWDOWN-PROTECT-V3 指纹校验通过 {fp}')

    # ---- 信号(引擎A 19d5c135 全量) ----
    print('[signals] 载入 panel_v6_full.pkl ...')
    panel = pd.read_pickle(HERE + r'\cache\panel_v6_full.pkl')
    panel['date'] = panel['date'].astype(str)
    panel = panel[panel['date'] >= START]
    pos = pd.to_numeric(panel['pos250'], errors='coerce')
    ev = (panel[['chan_buy1_10', 'chan_buy2_10', 'island_5', 'gap_up_hold']].fillna(0) > 0.5).any(axis=1)
    neg = (panel[['rounded_20', 'lhb_buy_5', 'vwap60_bias', 'vwap20_bias']].fillna(1) <= 0.5).all(axis=1)
    sig = panel[(pos <= 0.40) & ev & neg][['date', 'code']].copy()
    del panel, pos, ev, neg
    sig = sig[~sig['code'].astype(str).str.startswith('688')]
    print(f'[signals] {len(sig)}行')

    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    tds = [r0[0] for r0 in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date", (START,)).fetchall()]
    tds = [str(t) for t in tds]
    dpos = {d: i for i, d in enumerate(tds)}
    n_days = len(tds)

    # 尾部fwd20不完整信号剔除: 信号s+21须在窗内
    sig = sig[sig['date'].map(dpos).notna()]
    sig = sig[sig['date'].map(lambda d: dpos[d] + 21 < n_days)]
    print(f'[signals] fwd20完整 {len(sig)}行, 窗口至{tds[max(dpos[d] for d in sig["date"])]}')

    # ---- carrier: 活跃仓位等权日收益 ----
    px = pd.read_sql_query(
        "SELECT code,date,change_pct FROM stock_daily_meta_qfq WHERE date>=?", con, params=(START,))
    con.close()
    px['date'] = px['date'].astype(str)
    px['ret'] = pd.to_numeric(px['change_pct'], errors='coerce') / 100.0
    codes_all = sorted(px['code'].astype(str).unique())
    cpos = {c: i for i, c in enumerate(codes_all)}
    R = np.full((n_days, len(codes_all)), np.nan, dtype=np.float64)
    tds_arr = np.array(tds)
    row = np.searchsorted(tds_arr, px['date'].values)
    col = np.array([cpos.get(str(c), -1) for c in px['code'].values])
    ok = (col >= 0) & (row < n_days)
    R[row[ok], col[ok]] = px['ret'].values[ok]
    sums = np.zeros(n_days)
    cnts = np.zeros(n_days)
    sig_arr = sig[['date', 'code']].values
    for d, c in sig_arr:
        i0 = dpos[d]
        ci = cpos.get(str(c))
        if ci is None:
            continue
        sl = slice(i0 + 2, i0 + 22)
        col = R[sl, ci]
        mask = ~np.isnan(col)
        sums[sl][mask] += col[mask]
        cnts[sl][mask] += 1
    carrier = np.where(cnts > 0, sums / np.maximum(cnts, 1), 0.0)
    r = pd.Series(carrier, index=pd.to_datetime(pd.Series(tds), format='%Y%m%d'))
    active_ratio = float((cnts > 0).mean())
    print(f'[carrier] 活跃仓位日占比={active_ratio:.1%}, 年化={r.mean()*252*100:.2f}%, 波动={r.std()*math.sqrt(252)*100:.2f}%')

    # ---- 评估窗 + 6变体 ----
    m_bh = metrics(r)
    res = {'prereg_id': prereg['id'], 'fingerprint': fp,
           'carrier': {**m_bh, 'active_day_ratio': round(active_ratio, 3)}}
    p0 = bool(len(r) >= 800 and active_ratio >= 0.30)

    variants = []
    for ma_n in (20, 60):
        for floor in (0.3, 0.5, 0.7):
            rp, _we = overlay(r, ma_n, floor)
            m = metrics(rp)
            drag = round(m_bh['ann_ret'] - m['ann_ret'], 2)
            variants.append({'ma': ma_n, 'floor': floor,
                             'mdd': m['mdd'], 'mdd_impr': round(m['mdd'] - m_bh['mdd'], 2),
                             'drag': drag, 'cvar5': m['cvar5'],
                             'sharpe': m['sharpe'], 'bh_sharpe': m_bh['sharpe'],
                             'pass_p1': bool((m['mdd'] - m_bh['mdd']) >= 5 and drag <= 2 and m['cvar5'] >= m_bh['cvar5'])})
    res['variants'] = variants
    prim = next(v for v in variants if v['ma'] == 20 and v['floor'] == 0.5)
    n_p1 = sum(1 for v in variants if v['pass_p1'])

    rp, _we = overlay(r, 20, 0.5)
    bh_idx = r
    mid = rp.index[len(rp) // 2]
    mdd_h1 = (metrics(rp[rp.index <= mid])['mdd'], metrics(bh_idx[bh_idx.index <= mid])['mdd'])
    mdd_h2 = (metrics(rp[rp.index > mid])['mdd'], metrics(bh_idx[bh_idx.index > mid])['mdd'])
    yearly_drag = {}
    for y, gi in rp.groupby(rp.index.year).groups.items():
        mo = metrics(rp.loc[gi])
        mb = metrics(r.loc[gi])
        yearly_drag[str(y)] = round(mb['ann_ret'] - mo['ann_ret'], 2)
    drag_ok_years = sum(1 for v in yearly_drag.values() if v <= 2) / len(yearly_drag)

    p1 = bool(prim['pass_p1'])
    p2 = bool(n_p1 >= 4 and (mdd_h1[0] - mdd_h1[1]) > 0 and (mdd_h2[0] - mdd_h2[1]) > 0
              and drag_ok_years >= 0.6)
    verdict = 'PASS' if (p0 and p1 and p2) else ('INSUFFICIENT_POWER' if not p0 else 'FAIL')

    # 敏感性: 现金1.8%
    rp_cash, _we = overlay(r, 20, 0.5, cash=0.018)
    res['cash_sens_descriptive'] = {'mdd': metrics(rp_cash)['mdd'],
                                    'ann_ret': metrics(rp_cash)['ann_ret']}

    res.update({'P0': {'eval_days': len(r), 'active_ratio': round(active_ratio, 3), 'pass': p0},
                'P1': {'primary': prim, 'pass': p1},
                'P2': {'n_variants_pass_p1': n_p1,
                       'halves_mdd_impr': [round(mdd_h1[0] - mdd_h1[1], 2), round(mdd_h2[0] - mdd_h2[1], 2)],
                       'yearly_drag': yearly_drag, 'drag_ok_year_ratio': round(drag_ok_years, 2), 'pass': p2},
                'verdict': verdict})
    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n===== 判定结果 =====')
    print('carrier:', res['carrier'])
    for v in variants:
        print(f"  MA{v['ma']}/floor{v['floor']}: MDD={v['mdd']} (改善{v['mdd_impr']}pp) "
              f"拖累={v['drag']}pp CVaR5={v['cvar5']} (vs持有{m_bh['cvar5']}) P1={v['pass_p1']}")
    print('P2:', {k: res['P2'][k] for k in ('n_variants_pass_p1', 'halves_mdd_impr', 'drag_ok_year_ratio')})
    print('现金敏感性:', res['cash_sens_descriptive'])
    print(f"VERDICT = {verdict}")
    print(f'结果已写: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
