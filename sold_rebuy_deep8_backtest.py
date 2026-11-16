# -*- coding: utf-8 -*-
"""sold_rebuy_deep8_backtest.py — PREREG_SOLD_REBUY_DEEP8_20260929 十三年泛化回测 (指纹 c52fdf8b888c8b32).

规则(与prereg逐字一致, 只执行不调参):
  宇宙=60/00/30前缀全市场(剔688/689/北交所), 20130101起;
  锚=该股空仓时任一日收盘; 入场=次日起20bar内 low<=anchor*0.92 首触, 成交价=min(anchor*0.92, open);
  退出=成交日起第10bar收盘; 成本双边0.3%; 单股同时至多一笔; 平仓次bar方可立新锚。
验收门(LOCKED): G1 全期均值>0且t>=2.0 | G2 13年度>=8年正 | G3 2026子样本均值与+2.33%偏差<=1.5pp。
唯一写入=结果JSON; qfq只读。
"""
import os, sys, json, sqlite3, math, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
PREREG = r'D:\zcodesj\data\prereg_sold_rebuy_deep8_20260929.json'
OUT = r'D:\zcodesj\data\sold_rebuy_deep8_backtest_20260929.json'
START = '20130101'
HOLD = 10
VALID = 20
ZONE = 0.92
COST = 0.003
PREFIX = ('600', '601', '603', '605', '000', '001', '002', '003', '300', '301', '302')


def main():
    prereg = json.load(open(PREREG, encoding='utf-8'))
    fp = prereg['fingerprint']
    body = {k: v for k, v in prereg.items() if k != 'fingerprint'}
    assert fp == hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False)
                                .encode('utf-8')).hexdigest()[:16], '指纹不符'
    print(f'[prereg] SOLD-REBUY-DEEP8 指纹校验通过 {fp}')

    qc = sqlite3.connect(QFQ_URI, uri=True, timeout=120)
    codes = [r[0] for r in qc.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>=? ", (START,)).fetchall()
        if r[0][:3] in PREFIX]
    print(f'[data] 宇宙 {len(codes)} 只')

    trades = []
    for ci, code in enumerate(codes):
        if ci % 500 == 0:
            print(f'  {ci}/{len(codes)}', flush=True)
        px = pd.read_sql_query(
            "SELECT date,open,close,low FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date",
            qc, params=(code, START))
        if len(px) < HOLD + VALID:
            continue
        o = px['open'].astype(float).to_numpy()
        c = px['close'].astype(float).to_numpy()
        lo = px['low'].astype(float).to_numpy()
        d = px['date'].astype(str).to_numpy()
        n = len(c)
        t = 0
        while t < n - 1:
            anchor = c[t]
            limit = anchor * ZONE
            # 快筛: 未来VALID bar的最低low都高于limit→无成交
            end_scan = min(t + 1 + VALID, n)
            if end_scan <= t + 1:
                break
            seg_lo = lo[t + 1:end_scan]
            if seg_lo.min() > limit:
                t += 1
                continue
            # 首触bar
            hit = np.nonzero(seg_lo <= limit)[0][0] + t + 1
            fill = min(limit, o[hit])
            ei = hit + HOLD - 1
            if ei >= n:
                break  # 持有期超出数据尾, 该锚作废且后续无新锚
            gross = c[ei] / fill - 1
            trades.append({'code': code, 'entry_d': d[hit], 'exit_d': d[ei],
                           'ret': gross - COST, 'year': d[hit][:4]})
            t = ei + 1  # 平仓次bar方可立新锚
    qc.close()

    df = pd.DataFrame(trades)
    a = df['ret'].to_numpy()
    tstat = float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a)))) if len(a) > 2 else None
    by_year = {y: {'n': len(g), 'mean': round(float(g['ret'].mean()) * 100, 2),
                   'win': round(float((g['ret'] > 0).mean()) * 100, 1)}
               for y, g in df.groupby('year')}
    sub26 = df[df['year'] == '2026']['ret']
    res = {'fingerprint': fp, 'n_trades': len(df), 'n_stocks': int(df['code'].nunique()),
           'full': {'mean': round(float(a.mean()) * 100, 3), 'median': round(float(np.median(a)) * 100, 2),
                    'win': round(float((a > 0).mean()) * 100, 1), 't': round(tstat, 2) if tstat else None},
           'by_year': by_year,
           'sub2026': {'n': len(sub26), 'mean': round(float(sub26.mean()) * 100, 2) if len(sub26) else None},
           'gates': {}}
    g1 = bool(a.mean() > 0 and tstat and tstat >= 2.0)
    pos_years = sum(1 for y in by_year.values() if y['mean'] > 0)
    g2 = bool(pos_years >= 8)
    g3 = bool(len(sub26) and abs(sub26.mean() * 100 - 2.33) <= 1.5)
    res['gates'] = {'G1_full_t': g1, 'G2_pos_years': f'{pos_years}/{len(by_year)}', 'G2_pass': g2,
                    'G3_2026': res['sub2026'], 'G3_pass': g3, 'ALL_PASS': bool(g1 and g2 and g3)}
    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    print('\n===== 十三年泛化回测判定 =====')
    print(f"交易 {len(df)} 笔 / {res['n_stocks']} 只 | 全期均值 {res['full']['mean']}% "
          f"中位 {res['full']['median']}% 胜率 {res['full']['win']}% t={res['full']['t']}")
    print(f"分年: {json.dumps(by_year, ensure_ascii=False)}")
    print(f"G1(t>=2): {g1} | G2(>=8年正): {pos_years}/{len(by_year)}={g2} | "
          f"G3(2026≈+2.33±1.5): {res['sub2026']}={g3}")
    print(f"VERDICT = {'三门全过→提请主人裁决' if res['gates']['ALL_PASS'] else '有门未过→候选判死'}")
    print(f'结果已写: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
