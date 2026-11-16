# -*- coding: utf-8 -*-
"""
bpep_audit_crosscheck — 用晚间illiq测试自己的持仓CSV在我方向量引擎上重放，
判定两引擎25pp分歧来自"选股"还是"收益计算"。
持仓来源: reports/_illiq_increment_holdings.csv (A=≤30元域, A0=全域, 月末调仓)
"""
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ_DB = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
GHOST = {20181231, 20260627}


def load_holdings():
    df = pd.read_csv(r'D:\zcodesj\reports\_illiq_increment_holdings.csv',
                     dtype={'date': int, 'code': str})
    out = {}
    for port, g in df.groupby('port'):
        sig = {d: sorted(set(gg['code'])) for d, gg in g.groupby('date')}
        out[port] = sig
    return out


def load_qfq_close(codes):
    con = sqlite3.connect(QFQ_DB, uri=True)
    frames = []
    codes = list(codes)
    for i in range(0, len(codes), 400):
        chunk = codes[i:i + 400]
        ph = ','.join('?' * len(chunk))
        frames.append(pd.read_sql_query(
            'SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN (%s) '
            'AND date BETWEEN 20181001 AND 20260814' % ph, con, params=chunk))
    con.close()
    df = pd.concat(frames, ignore_index=True)
    piv = df.pivot_table(index='date', columns='code', values='close')
    piv.index = piv.index.astype(int)
    return piv


def vector(piv, signals, cost_rate):
    """月末收盘等权重置, 期间漂移, 成本=换手x(双边). 无22%跳过/无MDD."""
    dates = [d for d in sorted(piv.index) if d not in GHOST]
    rows = {d: piv.loc[d].dropna() for d in dates}
    cur, prev = {}, None
    nav = 1.0
    navs, turns = [], []
    for d in dates:
        pt = rows[d]
        pr = 0.0
        if cur and prev is not None:
            valid = {}
            for c, w in cur.items():
                if c in pt.index and c in prev.index:
                    p0, p1 = prev[c], pt[c]
                    if p0 > 0 and p1 > 0:
                        r = p1 / p0 - 1
                        pr += w * r
                        valid[c] = w
            if valid:
                drift, tot = {}, 0.0
                for c, w in valid.items():
                    drift[c] = w * (pt[c] / prev[c])
                    tot += drift[c]
                if tot > 0:
                    orig = sum(cur.values())
                    cur = {c: w / tot * orig for c, w in drift.items()}
        nav *= (1 + pr)
        navs.append(nav)
        if d in signals:
            new = signals[d]
            nh = {c: 1.0 / len(new) for c in new if c in piv.columns}
            old, ns = set(cur), set(nh)
            bw = sum(nh[c] for c in ns - old)
            sw = sum(cur[c] for c in old - ns)
            turn = (bw + sw) / 2
            nav *= (1 - turn * cost_rate)
            cur = nh
            turns.append(turn)
        prev = pt
    r = pd.Series(navs, index=dates)
    rets = r.pct_change().dropna()
    yr = (1 + rets).groupby(rets.index.astype(str).str[:4]).prod() - 1
    return {
        'ann': float(r.iloc[-1] ** (252 / len(rets)) - 1),
        'sharpe': float(rets.mean() / rets.std() * np.sqrt(252)),
        'mdd': float((r / r.cummax() - 1).min()),
        'avg_turn': float(np.mean(turns)),
        'final': float(r.iloc[-1]),
        'yearly': {k: round(float(v), 3) for k, v in yr.items()},
    }


def main():
    holdings = load_holdings()
    codes = sorted({c for port in holdings.values()
                    for lst in port.values() for c in lst})
    print('持仓union: %d只 | 组合: %s' % (len(codes), list(holdings)))
    piv = load_qfq_close(codes)
    print('qfq close矩阵: %s' % (piv.shape,))

    for port in ['A', 'A0(全域)']:
        sig = holdings[port]
        print('\n[%s] %d个月 (illiq报告口径: %s)'
              % (port, len(sig), 'A: 扣费+0.89%/毛+1.98%' if port == 'A'
                 else 'A0: 扣费-2.65%/毛-1.54%'))
        for rate, tag in [(0.0, '零成本(毛)'), (0.003, '换手x0.30%(≈单边0.15%)')]:
            r = vector(piv, sig, rate)
            print('  %-24s 年化%+8.2f%%  Sharpe%6.2f  MDD%8.2f%%  月换手%5.1f%%'
                  % (tag, r['ann'] * 100, r['sharpe'], r['mdd'] * 100,
                     r['avg_turn'] * 100))
            if rate == 0.0:
                print('    分年: %s' % r['yearly'])

    # 与我方tab_all信号的重合度(按持有月对齐: 其月末D ↔ 我次月M-FIRST)
    import json
    with open(r'D:\zcodesj\reports\_bpep_audit_rerun_results_20260815.json',
              encoding='utf-8') as f:
        pass  # 若存在则后补


if __name__ == '__main__':
    main()
