# -*- coding: utf-8 -*-
"""PREREG-DDPROTECT-V5 (指纹 3d7d4c6047c71252) 执行器.

组合C载体 × 门控信号外生化: 门控源=市场等权池指数(非载体自身指数).
唯一写入=结果JSON; qfq只读. 符号约定: MDD负值, 改善=覆盖MDD-持有MDD>0.
用法: python -u ddprotect_v5_20260914.py
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
TRADES = r'D:\zcodesj\portrait_agent\reports\combo_c_2016_trades_20260906.csv'
PREREG_PATH = r'D:\zcodesj\data\prereg_ddprotect_v5_20260914.json'
OUT = r'D:\zcodesj\data\ddprotect_v5_results_20260914.json'
COST = 0.0015
START = '20160101'


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


def overlay(r: pd.Series, mret: pd.Series, ma_n: int, floor: float, cash: float = 0.0):
    idx_px = (1 + mret).cumprod()          # 外生门控: 市场池指数
    gate = idx_px > idx_px.rolling(ma_n).mean()
    w = pd.Series(np.where(gate, 1.0, floor), index=r.index)
    w_exec = w.shift(1)
    cash_yield = (1 - w_exec) * cash / 252
    return (w_exec * r + cash_yield - (w_exec - w_exec.shift(1)).abs() * COST).dropna()


def main() -> int:
    prereg = json.load(open(PREREG_PATH, encoding='utf-8'))
    fp = prereg['fingerprint']
    body = {k: v for k, v in prereg.items() if k != 'fingerprint'}
    assert fp == hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                                .encode('utf-8')).hexdigest()[:16], '指纹不符'
    print(f'[prereg] DDPROTECT-V5 指纹校验通过 {fp}')

    tr = pd.read_csv(TRADES, dtype={'code': str, 'date': str})
    tr = tr.drop_duplicates(subset=['date', 'code'])

    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    con.execute('CREATE TEMP TABLE tmp_codes(code TEXT PRIMARY KEY)')
    con.executemany('INSERT OR IGNORE INTO tmp_codes VALUES(?)',
                    [(c,) for c in sorted(tr['code'].unique())])
    tds = [str(r0[0]) for r0 in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date", (START,)).fetchall()]
    dpos = {d: i for i, d in enumerate(tds)}
    n_days = len(tds)

    px = pd.read_sql_query(
        "SELECT code,date,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>=? AND code IN (SELECT code FROM tmp_codes)", con, params=(START,))
    mkt = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>=? GROUP BY date ORDER BY date", con, params=(START,))
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

    mret = (mkt.assign(date=mkt['date'].astype(str))
               .set_index(pd.to_datetime(mkt['date'], format='%Y%m%d'))['ret']
               .astype(float).reindex(pd.to_datetime(pd.Series(tds), format='%Y%m%d')).fillna(0.0))

    # ---- carrier: 与V4同法重建 ----
    sums = np.zeros(n_days)
    cnts = np.zeros(n_days)
    skipped = 0
    for d, c in tr[['date', 'code']].values:
        i0 = dpos.get(d)
        ci = cpos.get(str(c))
        if i0 is None or ci is None or i0 + 20 >= n_days:
            skipped += 1
            continue
        sl = slice(i0 + 1, i0 + 21)
        colv = R[sl, ci]
        mask = ~np.isnan(colv)
        sums[sl][mask] += colv[mask]
        cnts[sl][mask] += 1
    carrier = np.where(cnts > 0, sums / np.maximum(cnts, 1), 0.0)
    r = pd.Series(carrier, index=pd.to_datetime(pd.Series(tds), format='%Y%m%d'))
    active_ratio = float((cnts > 0).mean())
    m_bh = metrics(r)
    print(f'[carrier] 活跃日占比={active_ratio:.1%} 年化={m_bh["ann_ret"]}% MDD={m_bh["mdd"]}% '
          f'CVaR5={m_bh["cvar5"]}% (skipped={skipped})')

    res = {'prereg_id': prereg['id'], 'fingerprint': fp,
           'carrier': {**m_bh, 'active_day_ratio': round(active_ratio, 3), 'skipped_trades': skipped}}
    p0 = bool(len(r) >= 800)

    variants = []
    for ma_n in (20, 60):
        for floor in (0.5, 0.7):
            rp = overlay(r, mret, ma_n, floor)
            m = metrics(rp)
            drag = round(m_bh['ann_ret'] - m['ann_ret'], 2)
            variants.append({'ma': ma_n, 'floor': floor, 'mdd': m['mdd'],
                             'mdd_impr': round(m['mdd'] - m_bh['mdd'], 2), 'drag': drag,
                             'cvar5': m['cvar5'], 'sharpe': m['sharpe'],
                             'pass_p1': bool((m['mdd'] - m_bh['mdd']) >= 5 and drag <= 2
                                             and m['cvar5'] >= m_bh['cvar5'])})
    res['variants'] = variants
    prim = next(v for v in variants if v['ma'] == 20 and v['floor'] == 0.5)
    n_p1 = sum(1 for v in variants if v['pass_p1'])

    rp = overlay(r, mret, 20, 0.5)
    mid = rp.index[len(rp) // 2]
    mdd_h1 = (metrics(rp[rp.index <= mid])['mdd'], metrics(r[r.index <= mid])['mdd'])
    mdd_h2 = (metrics(rp[rp.index > mid])['mdd'], metrics(r[r.index > mid])['mdd'])
    yearly_drag = {}
    for y, gi in rp.groupby(rp.index.year).groups.items():
        yearly_drag[str(y)] = round(metrics(r.loc[gi])['ann_ret'] - metrics(rp.loc[gi])['ann_ret'], 2)
    drag_ok_years = sum(1 for v in yearly_drag.values() if v <= 2) / len(yearly_drag)

    p1 = bool(prim['pass_p1'])
    p2 = bool(n_p1 >= 2 and (mdd_h1[0] - mdd_h1[1]) > 0 and (mdd_h2[0] - mdd_h2[1]) > 0
              and drag_ok_years >= 0.6)
    verdict = 'PASS' if (p0 and p1 and p2) else ('INSUFFICIENT_POWER' if not p0 else 'FAIL')

    rp_cash = overlay(r, mret, 20, 0.5, cash=0.018)
    res['cash_sens_descriptive'] = {'mdd': metrics(rp_cash)['mdd'], 'ann_ret': metrics(rp_cash)['ann_ret']}
    # V4自身门控对照基线(引用V4结果文件, 如实并报)
    try:
        v4 = json.load(open(r'D:\zcodesj\data\ddprotect_v4_results_20260914.json', encoding='utf-8'))
        res['v4_selfgate_baseline'] = v4['variants']
    except Exception:
        res['v4_selfgate_baseline'] = 'V4结果文件不可读'
    res.update({'P0': {'eval_days': len(r), 'pass': p0},
                'P1': {'primary': prim, 'pass': p1},
                'P2': {'n_variants_pass_p1': n_p1,
                       'halves_mdd_impr': [round(mdd_h1[0] - mdd_h1[1], 2), round(mdd_h2[0] - mdd_h2[1], 2)],
                       'yearly_drag': yearly_drag, 'drag_ok_year_ratio': round(drag_ok_years, 2), 'pass': p2},
                'verdict': verdict})
    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n===== 判定结果(外生市场指数门控) =====')
    for v in variants:
        print(f"  MA{v['ma']}/floor{v['floor']}: MDD={v['mdd']} (改善{v['mdd_impr']}pp) "
              f"拖累={v['drag']}pp CVaR5={v['cvar5']} P1={v['pass_p1']}")
    print('P2:', {k: res['P2'][k] for k in ('n_variants_pass_p1', 'halves_mdd_impr', 'drag_ok_year_ratio')})
    print('现金敏感性:', res['cash_sens_descriptive'])
    print(f"VERDICT = {verdict}")
    print(f'结果已写: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
