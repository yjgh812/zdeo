# -*- coding: utf-8 -*-
"""PREREG-VOLTARGET-V1 (指纹 688a0017c003c5cf) 一次跑执行器.

用途: 市场层波动率目标仓位机制影子测算(择时线P0'), 二阶矩仓位语境首次本地检验.
纪律: prereg先冻结(688a0017c003c5cf), 本脚本只执行不调参; 唯一写入=结果JSON;
      qfq只读; 代理宇宙=全池等权(小盘倾斜), 组合C/实盘接线若过闸另行立项.
用法: python -u voltarget_v1_20260914.py
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
PREREG_PATH = r'D:\zcodesj\data\prereg_voltarget_v1_20260914.json'
OUT = r'D:\zcodesj\data\voltarget_v1_results_20260914.json'
COST = 0.0015  # 单边0.15%(冻结)


def metrics(r: pd.Series):
    r = r.dropna()
    ann_ret = r.mean() * 252
    ann_vol = r.std(ddof=1) * math.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else float('nan')
    eq = (1 + r).cumprod()
    mdd = float((eq / eq.cummax() - 1).min())
    return {'ann_ret': round(ann_ret * 100, 2), 'ann_vol': round(ann_vol * 100, 2),
            'sharpe': round(float(sharpe), 3), 'mdd': round(mdd * 100, 2), 'n': int(len(r))}


def main() -> int:
    prereg = json.load(open(PREREG_PATH, encoding='utf-8'))
    fp = prereg['fingerprint']
    body = {k: v for k, v in prereg.items() if k != 'fingerprint'}
    fp_check = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:16]
    assert fp == fp_check, f'指纹不符: {fp} vs {fp_check}'
    print(f'[prereg] VOLTARGET-V1 指纹校验通过 {fp}')

    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    df = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>='20200101' GROUP BY date ORDER BY date", con)
    con.close()
    df['date'] = df['date'].astype(str)
    ret = df.set_index(pd.to_datetime(df['date'], format='%Y%m%d'))['ret'].astype(float)
    print(f'[data] {ret.index[0].date()}~{ret.index[-1].date()} 共{len(ret)}日')

    # 机制(冻结): sigma21 → target252中位 → w clip(0.3,1.0) → 次日生效
    sigma = ret.rolling(21).std() * math.sqrt(252)
    sigma_star = sigma.rolling(252).median()
    w = (sigma_star / sigma).clip(0.3, 1.0)
    w_exec = w.shift(1)
    cost = (w_exec - w_exec.shift(1)).abs() * COST
    rp = w_exec * ret - cost
    valid = rp.dropna().index
    eval_r = rp.loc[valid]
    bh_r = ret.loc[valid]
    print(f'[eval] 评估期 {valid[0].date()}~{valid[-1].date()} 共{len(valid)}日 '
          f'(成本拖累累计={cost.loc[valid].sum() * 100:.2f}%)')

    res = {'prereg_id': prereg['id'], 'fingerprint': fp,
           'window': [str(valid[0].date()), str(valid[-1].date())], 'eval_days': int(len(valid))}
    m_t, m_b = metrics(eval_r), metrics(bh_r)
    res['target_arm'] = m_t
    res['buyhold_arm'] = m_b
    p0 = bool(len(valid) >= 800)
    d_sharpe = m_t['sharpe'] - m_b['sharpe']
    p1 = bool(d_sharpe >= 0.10 and m_t['mdd'] < m_b['mdd'])
    mid = valid[len(valid) // 2]
    halves = {}
    for tag, sel in (('前半', valid[valid <= mid]), ('后半', valid[valid > mid])):
        mt, mb = metrics(eval_r.loc[sel]), metrics(bh_r.loc[sel])
        halves[tag] = {'sharpe_target': mt['sharpe'], 'sharpe_bh': mb['sharpe'],
                       'diff': round(mt['sharpe'] - mb['sharpe'], 3), 'n': mt['n']}
    p2 = bool(all(h['diff'] > 0 for h in halves.values()))
    verdict = 'PASS' if (p0 and p1 and p2) else ('INSUFFICIENT_POWER' if not p0 else 'FAIL')
    res['gates'] = {'P0_power': {'eval_days': len(valid), 'pass': p0},
                    'P1': {'sharpe_diff': round(d_sharpe, 3),
                           'mdd_target': m_t['mdd'], 'mdd_bh': m_b['mdd'], 'pass': p1},
                    'P2': {'halves': halves, 'pass': p2}}
    res['verdict'] = verdict

    # 敏感性1: 周频调仓(周五决策, 次日生效, 其余日沿用)
    weekday = ret.index.dayofweek
    dec_fri = w.where(weekday == 4)
    w_exec_w = dec_fri.ffill().shift(1)
    rp_w = (w_exec_w * ret - (w_exec_w - w_exec_w.shift(1)).abs() * COST).loc[valid]
    mw = metrics(rp_w)
    res['sensitivity_weekly'] = {**mw, 'sharpe_diff_vs_bh': round(mw['sharpe'] - m_b['sharpe'], 3)}

    # 敏感性2: 波动三分位条件Sharpe(滞后σ分桶, 机理前提检验)
    sig_lag = sigma.shift(1).loc[valid]
    ter = pd.qcut(sig_lag, 3, labels=['低波', '中波', '高波'])
    cond = {}
    for lab in ['低波', '中波', '高波']:
        cond[lab] = metrics(bh_r[ter == lab])['sharpe']
    res['sensitivity_vol_tercile_cond_sharpe_bh'] = cond

    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n===== 判定结果 =====')
    print('目标臂:', m_t)
    print('持有臂:', m_b)
    print('P1:', res['gates']['P1'])
    print('P2:', res['gates']['P2'])
    print('周频变体:', res['sensitivity_weekly'])
    print('波动三分位条件Sharpe(持有臂):', cond)
    print(f"VERDICT = {verdict}")
    print(f'结果已写: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
