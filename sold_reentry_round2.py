# -*- coding: utf-8 -*-
"""sold_reentry_round2.py — 卖后回补买点·第二轮深挖 (2026-09-28 深夜, 主人令「没找对买点,继续挖掘」).

对第一轮的方法论修正(预注册, 跑前声明):
  H1 regime闸: 以qfq等权市场指数MA20为regime闸——仅卖出时市场在MA20上方才回补(强市卖飞81.8%的正面利用);
  H2 价位限价单: 挂 ref×0.95 限价, 窗口内 low 触及即成交@ref×0.95, 持有至T+20(底=价位非形态);
  H3 地量日: 首个"量<此前每日量min"且 k>=2 的bar收盘买入(A股地量底经典假设);
  H4 oracle天花板: 事后最低点当日买入的收益上限(给所有规则定标尺);
  H5 幸存规则时间外检验: R-B折扣首阳在7-8月(开发段)与9月(验证段)分开算;
  H6 卖出理由分层: S-5恐慌止损 vs 趋势卖 vs 其他—— capitulation 卖点是否更近底(飞率/底距描述)。
纪律: 全样本+时间外双报; n=214/分月更小, 一切结果侦察级, 禁直连生产。
只读两库。用法: python sold_reentry_round2.py [--json OUT]
"""
import os, sys, json, sqlite3, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

ATTR_URI = r'file:D:\zcodesj\attribution\attribution.db?mode=ro'
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
HOLD = 20
ZONE = 0.95  # H2 限价位


def norm_date(s):
    s = str(s).replace('-', '').strip()
    return s[:8] if len(s) >= 8 else ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=None)
    a = ap.parse_args()
    con = sqlite3.connect(ATTR_URI, uri=True)
    sells = pd.read_sql_query(
        "SELECT code,name,trade_date,action,price,subsystems,notes,strategy_version,signal_type "
        "FROM trade_records WHERE action IN ('SELL','PARTIAL_SELL')", con)
    con.close()
    sells['code'] = sells['code'].astype(str).str.replace(r'^(sh|sz|bj)', '', regex=True).str.zfill(6)
    sells['d'] = sells['trade_date'].map(norm_date)
    sells = sells.dropna(subset=['price'])
    sells = sells[(sells['d'] >= '20260101') & (sells['price'] > 0)]
    sells = sells.drop_duplicates(subset=['code', 'd'], keep='first')

    qc = sqlite3.connect(QFQ_URI, uri=True, timeout=60)
    # H1: qfq 等权市场指数(全池日均change_pct) → MA20 regime
    mkt = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq "
        "WHERE date>='20260101' GROUP BY date ORDER BY date", qc)
    mkt['date'] = mkt['date'].astype(str)
    mkt['idx'] = (1 + mkt['ret'].astype(float)).cumprod()
    mkt['ma20'] = mkt['idx'].rolling(20).mean()
    regime = dict(zip(mkt['date'], mkt['idx'] > mkt['ma20']))  # True=强(线上)

    all_dates = [r[0] for r in qc.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
    idx = {d: i for i, d in enumerate(all_dates)}

    events = []
    for _, s in sells.iterrows():
        code, d = s['code'], s['d']
        i0 = idx.get(d)
        if i0 is None:
            nxt = [x for x in all_dates if x > d][:1]
            if not nxt:
                continue
            i0 = idx[nxt[0]] - 1
        tds = all_dates[i0 + 1:i0 + 1 + HOLD + 3]
        if len(tds) < 5:
            continue
        ph = ','.join('?' * len(tds))
        px = pd.read_sql_query(
            f"SELECT date,close,low,high,volume FROM stock_daily_meta_qfq WHERE code=? AND date IN ({ph}) ORDER BY date",
            qc, params=[code] + tds)
        if px.empty:
            continue
        ref_row = pd.read_sql_query("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                                    qc, params=[code, all_dates[i0]])
        if ref_row.empty or not ref_row['close'].iloc[0]:
            continue
        ref = float(ref_row['close'].iloc[0])
        c = px['close'].astype(float).to_numpy()
        lo = px['low'].astype(float).to_numpy()
        v = px['volume'].astype(float).to_numpy()
        strong = bool(regime.get(d, False))
        reason = 'S5' if ('S-5' in str(s['notes']) or '止损' in str(s['notes'])) else (
            'trend' if ('走坏' in str(s['notes']) or '趋势' in str(s['notes'])) else 'other')
        events.append({'code': code, 'name': s['name'], 'd': d, 'ref': ref,
                       'c': c.tolist(), 'lo': lo.tolist(), 'v': v.tolist(),
                       'strong': strong, 'reason': reason, 'month': d[:6]})
    qc.close()
    print(f'[round2] 可模拟事件 {len(events)} 笔 (强市卖出 {sum(1 for e in events if e["strong"])} / '
          f'弱市 {sum(1 for e in events if not e["strong"])})')

    def hold20(c, k0):
        """从bar k0收盘入场持20日; 返回(收益, full)"""
        if k0 >= len(c):
            return None, False
        oi = min(k0 + HOLD, len(c)) - 1
        return c[oi] / c[k0] - 1, (k0 + HOLD) <= len(c)

    # ---- H1 regime闸: 仅强市卖出→立即回补 ----
    h1 = {'strong': [], 'weak': []}
    for e in events:
        r, full = hold20(e['c'], 0)
        if r is None or not full:
            continue
        (h1['strong'] if e['strong'] else h1['weak']).append(r)
    # ---- H4 oracle: 事后最低日买入 ----
    h4 = []
    for e in events:
        k_low = int(np.argmin(e['lo'][:min(HOLD, len(e['lo']))]))
        r, full = hold20(e['c'], k_low)
        if r is not None and full:
            h4.append(r)
    # ---- H2 限价单 ref×0.95 ----
    h2_hits, h2 = 0, []
    for e in events:
        lo, c, ref = e['lo'], e['c'], e['ref']
        touch = [k for k in range(min(HOLD, len(lo))) if lo[k] <= ref * ZONE]
        if not touch:
            continue
        k = touch[0]
        fill = min(ref * ZONE, c[k])  # 触及日若收盘已低于限价按收盘更保守? 取更保守=收盘
        r, full = hold20(c, k)
        if r is None or not full:
            continue
        # 用实际成交价计收益: (c[oi]/fill -1) 但持有期从成交bar起
        oi = min(k + HOLD, len(c)) - 1
        h2_hits += 1
        h2.append(c[oi] / fill - 1)
    # ---- H3 地量日 ----
    h3 = []
    for e in events:
        v, c = e['v'], e['c']
        run_min = v[0]
        for k in range(2, min(HOLD, len(v))):
            if v[k] < run_min:
                run_min = v[k]
                r, full = hold20(c, k)
                if r is not None and full:
                    h3.append(r)
                break
    # ---- H5 R-B时间外: 开发段(7-8月) vs 验证段(9月) ----
    h5 = {'dev': [], 'oos': []}
    for e in events:
        c, ref = e['c'], e['ref']
        for k in range(3, min(len(c), HOLD)):
            if c[k] <= ref * 0.95 and c[k] / c[k - 1] - 1 > 0:
                r, full = hold20(c, k)
                if r is not None and full:
                    (h5['dev'] if e['month'] in ('202607', '202608') else h5['oos']).append(r)
                break

    def pack(rs):
        if not rs:
            return {'n': 0}
        a = np.array(rs)
        return {'n': len(a), 'win': round(float((a > 0).mean()) * 100, 1),
                'median': round(float(np.median(a)) * 100, 2), 'mean': round(float(a.mean()) * 100, 2)}

    res = {'n_events': len(events), 'n_strong': sum(1 for e in events if e['strong'])}
    res['H1_regime'] = {'strong_rebuy': pack(h1['strong']), 'weak_rebuy': pack(h1['weak'])}
    res['H2_limit95'] = {'fill_rate': round(h2_hits / len(events) * 100, 1), 'perf': pack(h2)}
    res['H3_dryvol'] = {'trigger_rate': round(len(h3) / len(events) * 100, 1), 'perf': pack(h3)}
    res['H4_oracle'] = pack(h4)
    res['H5_B_holdout'] = {'dev_julaug': pack(h5['dev']), 'oos_sep': pack(h5['oos'])}
    # ---- H6 卖出理由分层(底距+飞率描述) ----
    h6 = {}
    for e in events:
        r, full = hold20(e['c'], 0)
        if r is None:
            continue
        bd = (e['ref'] - min(e['lo'][:min(HOLD, len(e['lo']))])) / e['ref'] * 100
        h6.setdefault(e['reason'], []).append({'fly': r > 0.01, 'bd': bd})
    h6o = {}
    for k2, lst in h6.items():
        flyr = sum(1 for x in lst if x['fly']) / len(lst) * 100
        bds = sorted(x['bd'] for x in lst)
        h6o[k2] = {'n': len(lst), 'fly_rate': round(flyr, 1),
                   'bd_median': round(bds[len(bds)//2], 2)}
    res['H6_reason'] = h6o

    print(f"\n===== 回补买点·第二轮 (n={len(events)}) =====")
    s1, w1 = res['H1_regime']['strong_rebuy'], res['H1_regime']['weak_rebuy']
    print(f"H1 regime闸: 强市卖出回补(n={s1['n']}) 胜率{s1['win']}% 中位{s1['median']}% || "
          f"弱市(n={w1['n']}) 胜率{w1['win']}% 中位{w1['median']}% → 闸的价值={round(s1['median']-w1['median'],2)}pp")
    p2 = res['H2_limit95']
    print(f"H2 限价{ZONE}价位单: 触发率{p2['fill_rate']}% 成交后(n={p2['perf']['n']}) 胜率{p2['perf']['win']}% 中位{p2['perf']['median']}%")
    p3 = res['H3_dryvol']
    print(f"H3 地量日: 触发率{p3['trigger_rate']}% (n={p3['perf']['n']}) 胜率{p3['perf']['win']}% 中位{p3['perf']['median']}%")
    p4 = res['H4_oracle']
    print(f"H4 oracle天花板(事后最低日买): 胜率{p4['win']}% 中位{p4['median']}% ← 所有规则的上限标尺")
    p5d, p5o = res['H5_B_holdout']['dev_julaug'], res['H5_B_holdout']['oos_sep']
    print(f"H5 R-B时间外: 开发段7-8月(n={p5d['n']}) 中位{p5d.get('median','NA')}% || 验证段9月(n={p5o['n']}) 中位{p5o.get('median','NA')}%")
    print(f"H6 卖出理由: {json.dumps(h6o, ensure_ascii=False)}")
    if a.json:
        json.dump(res, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print(f'落盘 {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
