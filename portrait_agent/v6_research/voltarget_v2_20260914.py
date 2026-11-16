# -*- coding: utf-8 -*-
"""PREREG-VOLTARGET-V2 (指纹 5a765ec776cd07e4) 实现族级验证执行器.

主人9/14『验证角度』指令: 24网格变体+2趋势门控混合臂全部跑前冻结, 一次跑,
看机制是否有稳健支持. 唯一写入=结果JSON; qfq只读.
用法: python -u voltarget_v2_20260914.py
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
PREREG_PATH = r'D:\zcodesj\data\prereg_voltarget_v2_20260914.json'
OUT = r'D:\zcodesj\data\voltarget_v2_results_20260914.json'
COST = 0.0015
CASH = 0.018  # 现金敏感性(年化1.8%, 非判据)


def metrics(r: pd.Series):
    r = r.dropna()
    if len(r) < 2:
        return {'ann_ret': None, 'ann_vol': None, 'sharpe': None, 'mdd': None, 'n': len(r)}
    ann_vol = r.std(ddof=1) * math.sqrt(252)
    ann_ret = r.mean() * 252
    eq = (1 + r).cumprod()
    return {'ann_ret': round(ann_ret * 100, 2), 'ann_vol': round(ann_vol * 100, 2),
            'sharpe': round(ann_ret / ann_vol, 3) if ann_vol > 0 else None,
            'mdd': round(float((eq / eq.cummax() - 1).min()) * 100, 2), 'n': int(len(r))}


def build_w(ret: pd.Series, est: int, tgt: int, band: tuple) -> pd.Series:
    sigma = ret.rolling(est).std() * math.sqrt(252)
    sigma_star = sigma.rolling(tgt).median()
    return (sigma_star / sigma).clip(*band)


def arm_return(ret: pd.Series, w: pd.Series, weekly: bool, cash: float = 0.0) -> pd.Series:
    if weekly:
        dec = w.where(ret.index.dayofweek == 4)
        w_exec = dec.ffill().shift(1)
    else:
        w_exec = w.shift(1)
    cash_yield = (1 - w_exec) * cash / 252
    return (w_exec * ret + cash_yield - (w_exec - w_exec.shift(1)).abs() * COST).dropna()


def main() -> int:
    prereg = json.load(open(PREREG_PATH, encoding='utf-8'))
    fp = prereg['fingerprint']
    body = {k: v for k, v in prereg.items() if k != 'fingerprint'}
    assert fp == hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                                .encode('utf-8')).hexdigest()[:16], '指纹不符'
    print(f'[prereg] VOLTARGET-V2 指纹校验通过 {fp}')

    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    df = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>='20200101' GROUP BY date ORDER BY date", con)
    con.close()
    df['date'] = df['date'].astype(str)
    ret = df.set_index(pd.to_datetime(df['date'], format='%Y%m%d'))['ret'].astype(float)
    print(f'[data] {ret.index[0].date()}~{ret.index[-1].date()} 共{len(ret)}日')

    # ---- 24 网格变体 ----
    grid = []
    for est in (10, 21, 63):
        for tgt in (252, 504):
            for band in ((0.5, 1.0), (0.8, 1.0)):
                for weekly in (False, True):
                    grid.append((est, tgt, band, weekly))
    rows = []
    for est, tgt, band, weekly in grid:
        w = build_w(ret, est, tgt, band)
        rp = arm_return(ret, w, weekly)
        bh = ret.loc[rp.index]
        mt, mb = metrics(rp), metrics(bh)
        rows.append({'est': est, 'tgt': tgt, 'band': str(band), 'rebal': '周' if weekly else '日',
                     'sharpe': mt['sharpe'], 'bh_sharpe': mb['sharpe'],
                     'uplift': round(mt['sharpe'] - mb['sharpe'], 3),
                     'mdd': mt['mdd'], 'bh_mdd': mb['mdd'], 'n': mt['n']})
    g = pd.DataFrame(rows)
    ups = g['uplift']
    n_good = int((ups >= 0.05).sum())
    p1 = bool(n_good >= 12 and ups.median() >= 0.05)  # ≥50%且中位数≥+0.05
    # primary
    prow = g[(g['est'] == 21) & (g['tgt'] == 252) & (g['band'] == '(0.5, 1.0)') & (g['rebal'] == '日')].iloc[0]
    p1 = p1 and bool(prow['uplift'] >= 0.05 and prow['mdd'] >= prow['bh_mdd'])

    # primary 分年 + 两半窗
    w = build_w(ret, 21, 252, (0.5, 1.0))
    rp = arm_return(ret, w, weekly=False)
    bh = ret.loc[rp.index]
    yearly = {}
    for y, idx in rp.groupby(rp.index.year).groups.items():
        yearly[str(y)] = round(metrics(rp.loc[idx])['sharpe'] - metrics(bh.loc[idx])['sharpe'], 3)
    pos_years = sum(1 for v in yearly.values() if v > 0) / len(yearly)
    mid = rp.index[len(rp) // 2]
    h1 = metrics(rp[rp.index <= mid])['sharpe'] - metrics(bh[bh.index <= mid])['sharpe']
    h2 = metrics(rp[rp.index > mid])['sharpe'] - metrics(bh[bh.index > mid])['sharpe']
    p2 = bool(pos_years >= 0.6 and h1 > -0.02 and h2 > -0.02)
    p0 = bool(prow['n'] >= 800)
    verdict = 'PASS' if (p0 and p1 and p2) else ('INSUFFICIENT_POWER' if not p0 else 'FAIL')

    # ---- 混合臂(趋势门控, 如实报告不进判据) ----
    idx_px = (1 + ret).cumprod()
    trend_up = idx_px > idx_px.rolling(20).mean()
    hybrids = []
    for weekly in (False, True):
        wv = build_w(ret, 21, 252, (0.5, 1.0))
        wh = wv.where(trend_up, other=0.3).clip(0.3, 1.0)
        rph = arm_return(ret, wh, weekly)
        mb = metrics(ret.loc[rph.index])
        mh = metrics(rph)
        wv_only = arm_return(ret, wv, weekly)
        mv_only = metrics(wv_only)
        hybrids.append({'rebal': '周' if weekly else '日',
                        'sharpe': mh['sharpe'], 'mdd': mh['mdd'],
                        'uplift_vs_bh': round(mh['sharpe'] - mb['sharpe'], 3),
                        'uplift_vs_pure_vol': round(mh['sharpe'] - mv_only['sharpe'], 3)})

    # ---- 现金敏感性(非判据) ----
    rp_cash = arm_return(ret, w, weekly=False, cash=CASH)
    cash_sens = {'sharpe': metrics(rp_cash)['sharpe'],
                 'uplift_vs_bh': round(metrics(rp_cash)['sharpe'] - metrics(ret.loc[rp_cash.index])['sharpe'], 3)}

    res = {'prereg_id': prereg['id'], 'fingerprint': fp,
           'grid_variants': rows,
           'P1': {'variants_uplift_ge005': n_good, 'median_uplift': round(float(ups.median()), 3),
                  'primary_uplift': prow['uplift'], 'primary': prow.to_dict(), 'pass': p1},
           'P2': {'yearly_uplift': yearly, 'pos_year_ratio': round(pos_years, 2),
                  'halves': [round(h1, 3), round(h2, 3)], 'pass': p2},
           'hybrid_arms_descriptive': hybrids,
           'cash_sensitivity_descriptive': cash_sens,
           'verdict': verdict}
    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"网格24变体: 增量≥+0.05占 {n_good}/24, 中位增量={ups.median():.3f}")
    print(f"primary(21/252/[0.5,1]/日): uplift={prow['uplift']} sharpe={prow['sharpe']} mdd={prow['mdd']} vs bh={prow['bh_mdd']}")
    print(f"分年增量: {yearly} (正占比{pos_years:.0%}), 两半窗: [{h1:.3f}, {h2:.3f}]")
    print('混合臂(趋势门控):', hybrids)
    print('现金敏感性:', cash_sens)
    print(f"VERDICT = {verdict}")
    print(f'结果已写: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
