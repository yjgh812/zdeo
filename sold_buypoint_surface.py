# -*- coding: utf-8 -*-
"""sold_buypoint_surface.py — 卖后窗口买点状态曲面 (2026-09-28 深夜, 主人令「找准买点,继续找找」).

方法(第三种挖法, 与二值规则互补): 231卖单事件×卖后每日bar=候选买点日全集;
每个bar按当日可观测状态分桶, 算 E[fwd10](该状态入场后10日收益)——买点=期望收益曲面上的高地带。
状态维度(全部当日收盘可知): ①量能比(当日量/卖日量) ②相对卖价折扣 ③卖后天数
  ④连续阴线数 ⑤当日收盘强度(收/当日高) ⑥市场regime(MA20上下) ⑦距卖后滚动高点回撤。
纪律: 全桶输出(不挑报); 只有 n>=30 且方向单调 且与基准差>=1.5pp 的格子才标记候选;
  单年数据侦察级, 候选只进影子/prereg。只读两库。用法: python sold_buypoint_surface.py [--json OUT]
"""
import os, sys, json, sqlite3, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

ATTR_URI = r'file:D:\zcodesj\attribution\attribution.db?mode=ro'
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
HOLD = 20
FWD = 10
BARS = 50
MAXK = 20


def norm_date(s):
    s = str(s).replace('-', '').strip()
    return s[:8] if len(s) >= 8 else ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=None)
    a = ap.parse_args()
    con = sqlite3.connect(ATTR_URI, uri=True)
    sells = pd.read_sql_query(
        "SELECT code,name,trade_date,action,price FROM trade_records WHERE action IN ('SELL','PARTIAL_SELL')", con)
    con.close()
    sells['code'] = sells['code'].astype(str).str.replace(r'^(sh|sz|bj)', '', regex=True).str.zfill(6)
    sells['d'] = sells['trade_date'].map(norm_date)
    sells = sells.dropna(subset=['price'])
    sells = sells[(sells['d'] >= '20260101') & (sells['price'] > 0)]
    sells = sells.drop_duplicates(subset=['code', 'd'], keep='first')

    qc = sqlite3.connect(QFQ_URI, uri=True, timeout=60)
    mkt = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>='20260101' GROUP BY date ORDER BY date", qc)
    mkt['date'] = mkt['date'].astype(str)
    mkt['idx'] = (1 + mkt['ret'].astype(float)).cumprod()
    mkt['ma20'] = mkt['idx'].rolling(20).mean()
    regime = dict(zip(mkt['date'], (mkt['idx'] > mkt['ma20']).astype(int)))
    all_dates = [r[0] for r in qc.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
    idx = {d: i for i, d in enumerate(all_dates)}

    rows = []
    for _, s in sells.iterrows():
        code, d = s['code'], s['d']
        i0 = idx.get(d)
        if i0 is None:
            nxt = [x for x in all_dates if x > d][:1]
            if not nxt:
                continue
            i0 = idx[nxt[0]] - 1
        tds = all_dates[i0 + 1:i0 + 1 + BARS]
        if len(tds) < HOLD:
            continue
        ph = ','.join('?' * len(tds))
        px = pd.read_sql_query(
            f"SELECT date,close,low,high,volume FROM stock_daily_meta_qfq WHERE code=? AND date IN ({ph}) ORDER BY date",
            qc, params=[code] + tds)
        if len(px) < HOLD:
            continue
        ref_row = pd.read_sql_query("SELECT close,volume FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                                    qc, params=[code, all_dates[i0]])
        if ref_row.empty or not ref_row['close'].iloc[0]:
            continue
        ref = float(ref_row['close'].iloc[0])
        vol_sell = float(ref_row['volume'].iloc[0] or 0)
        c = px['close'].astype(float).to_numpy()
        hi = px['high'].astype(float).to_numpy()
        v = px['volume'].astype(float).to_numpy()
        for k in range(1, min(MAXK, len(c) - FWD)):
            fwd = c[k + FWD - 1] / c[k] - 1
            vr = v[k] / vol_sell if vol_sell > 0 else None
            disc = c[k] / ref - 1
            consec_dn = 0
            j = k
            while j >= 1 and c[j] < c[j - 1]:
                consec_dn += 1
                j -= 1
            rows.append({'code': code, 'd': d, 'k': k, 'fwd': fwd,
                         'vr': vr, 'disc': disc, 'consec_dn': consec_dn,
                         'cls': c[k] / hi[k] if hi[k] > 0 else None,
                         'strong': regime.get(tds[k - 1], 0),
                         'dd': (max(c[:k + 1]) / c[k] - 1) if k else 0.0})
    qc.close()
    df = pd.DataFrame(rows)
    print(f'[surface] 候选买点日 {len(df)} 个(事件去重前bar数) | 全体 fwd10 中位 {df["fwd"].median()*100:+.2f}%')

    def bucketize(name, series, edges, labels):
        out = []
        b = pd.cut(series, bins=[-np.inf] + edges + [np.inf], labels=labels)
        for lab in labels:
            g = df[b == lab]['fwd']
            if len(g) == 0:
                out.append({'bucket': lab, 'n': 0})
                continue
            out.append({'bucket': lab, 'n': len(g),
                        'win': round(float((g > 0).mean()) * 100, 1),
                        'median': round(float(g.median()) * 100, 2),
                        'mean': round(float(g.mean()) * 100, 2)})
        return {'dim': name, 'buckets': out}

    dims = [
        bucketize('量能比(vs卖日)', df['vr'], [0.4, 0.7, 1.0], ['<0.4地量', '0.4-0.7缩量', '0.7-1.0常量', '>1.0放量']),
        bucketize('相对卖价折扣', df['disc'] * 100, [-8, -5, -2], ['深贴<-8%', '-8~-5%', '-5~-2%', '>-2%(贴/升)']),
        bucketize('卖后天数', df['k'], [3, 7, 14], ['1-3日', '4-7日', '8-14日', '15-20日']),
        bucketize('连续阴线', df['consec_dn'], [0, 2], ['0-1阳/平', '2-3连阴', '>=4连阴']),
        bucketize('收盘强度(收/高)', df['cls'], [0.3, 0.7], ['弱收<0.3', '中0.3-0.7', '强收>0.7']),
        bucketize('市场regime', df['strong'].astype(float), [0.5], ['弱市(MA20下)', '强市(MA20上)']),
        bucketize('距滚动高点回撤', df['dd'] * 100, [3, 8], ['<3%', '3-8%', '>8%']),
    ]
    allmap = {'<0.4地量': 0, '0.4-0.7缩量': 1, '0.7-1.0常量': 2, '>1.0放量': 3,
              '深贴<-8%': 0, '-8~-5%': 1, '-5~-2%': 2, '>-2%(贴/升)': 3,
              '1-3日': 0, '4-7日': 1, '8-14日': 2, '15-20日': 3,
              '0-1阳/平': 0, '2-3连阴': 1, '>=4连阴': 2,
              '弱收<0.3': 0, '中0.3-0.7': 1, '强收>0.7': 2,
              '弱市(MA20下)': 0, '强市(MA20上)': 1, '<3%': 0, '3-8%': 1, '>8%': 2}
    base_med = float(df['fwd'].median()) * 100
    print(f"\n===== 买点状态曲面 (基准: 全体fwd10中位 {base_med:+.2f}%) =====")
    cands = []
    for dim in dims:
        print(f"\n--- {dim['dim']} ---")
        print(f"{'桶':<14}{'n':>6}{'胜率':>7}{'中位%':>8}{'均值%':>8}")
        for b in dim['buckets']:
            if b['n'] == 0:
                print(f"{b['bucket']:<14}{0:>6}")
                continue
            mark = ''
            if b['n'] >= 30 and b['median'] >= base_med + 1.5:
                mark = ' ←候选'
                cands.append({**b, 'dim': dim['dim']})
            print(f"{b['bucket']:<14}{b['n']:>6}{b['win']:>7}{b['median']:>8}{b['mean']:>8}{mark}")
    print(f"\n候选格子(n>=30且中位超基准>=1.5pp): {len(cands)} 个")
    for c_ in cands:
        print(f"  [{c_['dim']}] {c_['bucket']}: n={c_['n']} 胜率{c_['win']}% 中位{c_['median']}%")
    if a.json:
        json.dump({'baseline_median': base_med, 'dims': dims, 'candidates': cands},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print(f'落盘 {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
