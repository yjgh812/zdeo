# -*- coding: utf-8 -*-
"""sold_reentry_round3.py — 卖后回补买点·终审版 (2026-09-28 深夜).

对前两轮的测量修正(本轮为终审):
  BUG=前两轮bar窗口仅载 HOLD+3 根, 入场k>3的触发持有期被截断(round1混入短窗收益/round2被full门槛剔除);
  修正=每事件载 45 根bar, 全部规则仅计"完整20日持有"入场(k<=25), 截断事件如实计数丢弃。
规则全集(两轮预注册合集, 零新增): 基准k=0 / A缩量企稳 / B折扣首阳 / C放量启动 / D_T3对照 /
  H2限价0.95价位单(触及成交, 从成交bar持20日) / H3地量日 / H1强市分层(基准按regime分) / H4 oracle。
样本=完整20日前向窗的事件(卖出日晚于样本尾~25交易日的自然丢弃, 如实报n)。
只读两库。用法: python sold_reentry_round3.py [--json OUT]
"""
import os, sys, json, sqlite3, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

ATTR_URI = r'file:D:\zcodesj\attribution\attribution.db?mode=ro'
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
HOLD = 20
BARS = 50          # 载入bar数(入场k<=25 + 持有20 + 余量)
MAXK = 25          # 允许入场的最晚bar


def norm_date(s):
    s = str(s).replace('-', '').strip()
    return s[:8] if len(s) >= 8 else ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=None)
    a = ap.parse_args()
    con = sqlite3.connect(ATTR_URI, uri=True)
    sells = pd.read_sql_query(
        "SELECT code,name,trade_date,action,price,subsystems,notes FROM trade_records "
        "WHERE action IN ('SELL','PARTIAL_SELL')", con)
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
    regime = dict(zip(mkt['date'], mkt['idx'] > mkt['ma20']))
    all_dates = [r[0] for r in qc.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
    idx = {d: i for i, d in enumerate(all_dates)}

    events, dropped = [], 0
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
            dropped += 1
            continue
        ph = ','.join('?' * len(tds))
        px = pd.read_sql_query(
            f"SELECT date,close,low,volume FROM stock_daily_meta_qfq WHERE code=? AND date IN ({ph}) ORDER BY date",
            qc, params=[code] + tds)
        if px.empty or len(px) < HOLD:
            dropped += 1
            continue
        ref_row = pd.read_sql_query("SELECT close,volume FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                                    qc, params=[code, all_dates[i0]])
        if ref_row.empty or not ref_row['close'].iloc[0]:
            dropped += 1
            continue
        c = px['close'].astype(float).to_numpy()
        lo = px['low'].astype(float).to_numpy()
        v = px['volume'].astype(float).to_numpy()
        events.append({'code': code, 'name': s['name'], 'd': d, 'ref': float(ref_row['close'].iloc[0]),
                       'vol_sell': float(ref_row['volume'].iloc[0] or 0),
                       'c': c.tolist(), 'lo': lo.tolist(), 'v': v.tolist(),
                       'strong': bool(regime.get(d, False)),
                       'reason': 'S5' if ('S-5' in str(s['notes']) or '止损' in str(s['notes'])) else (
                           'trend' if ('走坏' in str(s['notes']) or '趋势' in str(s['notes'])) else 'other')})
    qc.close()
    print(f'[round3] 完整前向窗事件 {len(events)} 笔 (丢弃短窗 {dropped} 笔)')

    def ret_from(c, k):
        if k >= len(c) or k + HOLD > len(c):
            return None
        return c[k + HOLD - 1] / c[k] - 1

    R = {k: [] for k in ('BASE', 'A', 'B', 'C', 'D', 'H2', 'H3', 'ORACLE')}
    R_strong, R_weak = [], []
    n_fly = 0
    for e in events:
        c, lo, v, ref, vs = e['c'], e['lo'], e['v'], e['ref'], e['vol_sell']
        r0 = ret_from(c, 0)
        if r0 is None:
            continue
        fly_ev = r0 > 0.01
        n_fly += fly_ev
        (R_strong if e['strong'] else R_weak).append(r0)
        R['BASE'].append(r0)
        fired = {}
        run_min = v[0]
        for k in range(3, min(MAXK, len(c))):
            disc = c[k] / ref - 1
            day_gain = c[k] / c[k - 1] - 1
            vr = v[k] / vs if vs > 0 else None
            vref3 = float(np.mean(v[max(0, k - 3):k]))
            prior_low_min = min(lo[:k - 1]) if k >= 2 else lo[0]
            no_new_low_2d = min(lo[k - 1], lo[k]) > prior_low_min
            if 'A' not in fired and vr is not None and vr < 0.5 and abs(disc) <= 0.04 and no_new_low_2d:
                fired['A'] = k
            if 'B' not in fired and c[k] <= ref * 0.95 and day_gain > 0:
                fired['B'] = k
            if 'C' not in fired and vref3 > 0 and v[k] > vref3 * 2 and day_gain > 0.03 and c[k] > ref:
                fired['C'] = k
            if 'H3' not in fired and v[k] < run_min:
                fired['H3'] = k
                run_min = v[k]
            if 'D' not in fired and k == 3:
                fired['D'] = 3
        for r, k in fired.items():
            rr = ret_from(c, k)
            if rr is not None:
                R[r].append(rr)
        # H2 限价: 触及ref*0.95的bar成交(成交价=min(限价,当日收盘保守), 从该bar持20日)
        touch = [k for k in range(min(MAXK, len(lo))) if lo[k] <= ref * 0.95]
        if touch:
            k = touch[0]
            rr = ret_from(c, k)
            fill_adj = (c[k] / (ref * 0.95) - 1)  # 成交优于限价时增益(保守用收盘计)
            if rr is not None:
                R['H2'].append(rr + fill_adj)
        k_low = int(np.argmin(lo[:min(MAXK + HOLD, len(lo))]))
        rr = ret_from(c, k_low)
        if rr is not None:
            R['ORACLE'].append(rr)

    def pack(rs):
        if not rs:
            return {'n': 0}
        a = np.array(rs)
        return {'n': len(a), 'win': round(float((a > 0).mean()) * 100, 1),
                'median': round(float(np.median(a)) * 100, 2), 'mean': round(float(a.mean()) * 100, 2)}

    res = {'n_events': len(events), 'fly_rate': round(n_fly / len(events) * 100, 1)}
    names = {'BASE': '基准(立即回补)', 'A': 'A缩量企稳', 'B': 'B折扣首阳', 'C': 'C放量启动',
             'D': 'D_T3对照', 'H2': 'H2限价0.95', 'H3': 'H3地量日', 'ORACLE': 'H4 oracle天花板'}
    print(f"\n===== 回补买点·终审 (n={len(events)}, 事件飞率{res['fly_rate']}%, 全部完整20日持有) =====")
    for k in ('BASE', 'A', 'B', 'C', 'D', 'H2', 'H3', 'ORACLE'):
        p = pack(R[k])
        res[names[k]] = p
        print(f"{names[k]}: n={p['n']} 胜率{p['win']}% 中位{p['median']}% 均值{p['mean']}%")
    ps, pw = pack(R_strong), pack(R_weak)
    res['H1_strong'] = ps
    res['H1_weak'] = pw
    print(f"H1 强市卖出基准回补(n={ps['n']}): 中位{ps['median']}% || 弱市(n={pw['n']}): 中位{pw['median']}%")
    if a.json:
        json.dump(res, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print(f'落盘 {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
