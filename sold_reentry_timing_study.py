# -*- coding: utf-8 -*-
"""sold_reentry_timing_study.py — 卖后回补买点研究 (2026-09-28, 主人令「找到什么时候该买,买点」).

预注册四规则(先定死再跑数据, 防数据窥探; 全部在 231 笔全历史卖单上检验, 禁只在卖飞子集评——
卖对事件里回补会亏, 规则必须两侧都活):
  R-A 缩量企稳: 当日量<卖日量×0.5 且 |收盘/参考-1|<=4% 且 近2日未创新低 → 收盘买回
  R-B 折扣首阳: 收盘<=参考×0.95 且 当日收阳            → 收盘买回
  R-C 放量启动: 当日量>前3日均量×2 且 当日涨幅>3% 且 收盘>参考 → 收盘买回
  R-D 对照组:   固定 T+3 收盘买回(无信息基线)
基准=卖出价立即回补(持有20日)。产出=各规则 触发覆盖率/入场后20日收益(均值/中位/胜率)/
按事件终局(卖飞vs卖对)分层。卖后20日内首次触发, 每事件每规则至多一次入场。
结论判读纪律: n=231 单年度单regime周期, 侦察级——任何过线规则只进影子/prereg管线, 禁直连生产。
只读两库。用法: python sold_reentry_timing_study.py [--json OUT]
"""
import os, sys, json, sqlite3, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

ATTR_URI = r'file:D:\zcodesj\attribution\attribution.db?mode=ro'
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
HOLD = 20


def norm_date(s):
    s = str(s).replace('-', '').strip()
    return s[:8] if len(s) >= 8 else ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=None)
    a = ap.parse_args()
    con = sqlite3.connect(ATTR_URI, uri=True)
    sells = pd.read_sql_query(
        "SELECT code,name,trade_date,action,price,subsystems FROM trade_records "
        "WHERE action IN ('SELL','PARTIAL_SELL')", con)
    con.close()
    sells['code'] = sells['code'].astype(str).str.replace(r'^(sh|sz|bj)', '', regex=True).str.zfill(6)
    sells['d'] = sells['trade_date'].map(norm_date)
    sells = sells.dropna(subset=['price'])
    sells = sells[(sells['d'] >= '20260101') & (sells['price'] > 0)]
    sells = sells.drop_duplicates(subset=['code', 'd'], keep='first')

    qc = sqlite3.connect(QFQ_URI, uri=True, timeout=60)
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
        ref_row = pd.read_sql_query("SELECT close,volume FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                                    qc, params=[code, all_dates[i0]])
        if ref_row.empty or not ref_row['close'].iloc[0]:
            continue
        ref = float(ref_row['close'].iloc[0])
        vol_sell = float(ref_row['volume'].iloc[0] or 0)
        c = px['close'].astype(float).to_numpy()
        lo = px['low'].astype(float).to_numpy()
        v = px['volume'].astype(float).to_numpy()
        events.append({'code': code, 'name': s['name'], 'd': d, 'ref': ref, 'vol_sell': vol_sell,
                       'c': c.tolist(), 'lo': lo.tolist(), 'v': v.tolist()})
    qc.close()
    print(f'[study] 可模拟事件 {len(events)} 笔')

    RULES = ('A_缩量企稳', 'B_折扣首阳', 'C_放量启动', 'D_T3对照')
    # baseline: 立即回补
    base = []
    for e in events:
        c = e['c']
        if len(c) >= HOLD:
            base.append(c[HOLD - 1] / e['ref'] - 1)
    base = np.array(base)

    per_rule = {r: [] for r in RULES}
    cov = {r: 0 for r in RULES}
    for e in events:
        c, lo, v, ref, vs = e['c'], e['lo'], e['v'], e['ref'], e['vol_sell']
        n = min(len(c), HOLD)
        fired_k = {}
        # 事件终局(以参考价持有20日定义, 供分层)
        end20 = c[HOLD - 1] if len(c) >= HOLD else c[-1]
        e['fly_event'] = bool(end20 / ref - 1 > 0.01)
        for k in range(3, n):  # 从第4根bar起(需3日历史)
            disc = c[k] / ref - 1
            day_gain = c[k] / c[k - 1] - 1
            vr = v[k] / vs if vs > 0 else None
            vref3 = float(np.mean(v[max(0, k - 3):k]))
            prior_low_min = min(lo[:k - 1]) if k >= 2 else lo[0]
            no_new_low_2d = min(lo[k - 1], lo[k]) > prior_low_min
            if 'A_缩量企稳' not in fired_k and vr is not None and vr < 0.5 and abs(disc) <= 0.04 and no_new_low_2d:
                fired_k['A_缩量企稳'] = k
            if 'B_折扣首阳' not in fired_k and c[k] <= ref * 0.95 and day_gain > 0:
                fired_k['B_折扣首阳'] = k
            if 'C_放量启动' not in fired_k and vref3 > 0 and v[k] > vref3 * 2 and day_gain > 0.03 and c[k] > ref:
                fired_k['C_放量启动'] = k
            if 'D_T3对照' not in fired_k and k == 3:
                fired_k['D_T3对照'] = k
            if len(fired_k) == len(RULES):
                break
        for r, kk in fired_k.items():
            entry = c[kk]
            out_idx = min(kk + HOLD, len(c)) - 1
            ret = c[out_idx] / entry - 1
            full = (kk + HOLD) <= len(c)
            per_rule[r].append({'ret': ret, 'fly': e['fly_event'], 'full': full})
            cov[r] += 1

    def pack(rs):
        if not rs:
            return {'n': 0}
        rets = np.array([x['ret'] for x in rs])
        fly = [x for x in rs if x['fly']]
        right = [x for x in rs if not x['fly']]
        fulln = sum(1 for x in rs if x['full'])
        return {'n': len(rs), 'win_rate': round(float((rets > 0).mean()) * 100, 1),
                'mean': round(float(rets.mean()) * 100, 2), 'median': round(float(np.median(rets)) * 100, 2),
                'full20_n': fulln,
                'on_fly_events': {'n': len(fly),
                                  'win': round(float(np.mean([x['ret'] > 0 for x in fly])) * 100, 1) if fly else None,
                                  'median': round(float(np.median([x['ret'] for x in fly])) * 100, 2) if fly else None},
                'on_right_events': {'n': len(right),
                                    'win': round(float(np.mean([x['ret'] > 0 for x in right])) * 100, 1) if right else None,
                                    'median': round(float(np.median([x['ret'] for x in right])) * 100, 2) if right else None}}

    res = {'n_events': len(events),
           'baseline_immediate': {'n': len(base), 'win_rate': round(float((base > 0).mean()) * 100, 1),
                                  'mean': round(float(base.mean()) * 100, 2),
                                  'median': round(float(np.median(base)) * 100, 2)},
           'rules': {}}
    print(f"\n===== 卖后回补买点研究 (n={len(events)}, 持有20日) =====")
    print(f"基准(卖出价立即回补): 胜率 {res['baseline_immediate']['win_rate']}% | "
          f"中位 {res['baseline_immediate']['median']}% | 均值 {res['baseline_immediate']['mean']}%")
    for r in RULES:
        res['rules'][r] = pack(per_rule[r])
        p = res['rules'][r]
        if p['n']:
            print(f"{r}: 触发{p['n']}笔(覆盖{round(p['n']/len(events)*100)}%) 胜率{p['win_rate']}% "
                  f"中位{p['median']}% 均值{p['mean']}% [卖飞事件n{p['on_fly_events']['n']}中位{p['on_fly_events']['median']}% | "
                  f"卖对事件n{p['on_right_events']['n']}中位{p['on_right_events']['median']}%]")
        else:
            print(f"{r}: 零触发")
    if a.json:
        json.dump(res, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print(f'落盘 {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
