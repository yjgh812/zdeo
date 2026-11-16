# -*- coding: utf-8 -*-
"""UP桶历史同类条件fwd验证 — prereg侦察(2026-09-19)
主人方案 winrate_lift_engineering_plan_20260919.md P2-1。
口径: 月度首个交易日抽样时点 × direction_census.classify 复刻(TOP/UP/DOWN 分支逐行对照)
     × fwd20/60(收盘口径 + T+1开盘入场口径) × 同日全市场中位基线超额
     × 等权市场指数 250 日窗口分位分桶 × 同票60交易日事件去重(UP/DOWN 各自独立锚)。
实现校验(双跑一致): 对 20260918 单点重扫, 生产 census json(20260919, 数据截至0918)的 UP 明细
     须全部命中复刻版 UP 集合且 off_start 一致(容差0.15pp)。
产出: reports/up_bucket_hist_recon_YYYYMMDD.md + data/up_bucket_hist_recon_YYYYMMDD.json
铁律: 只读研究, 不触碰生产闸门; 侦察FAIL即登记, 禁复活。
"""
import itertools
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name  # noqa: E402

DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
DATA_START = '20200801'
T0 = '20210701'
FWDS = (20, 60)


def classify_recon(closes, vols):
    """逐行复刻 direction_census.classify 的 TOP/UP/DOWN 分支; UP 附带 ps(段起后20日量/250日量)。
    注意: 生产版在 classify 内用 shrink_cap 挡 ps; 本复刻带出 ps 由调用方按档过滤。"""
    n = len(closes)
    if n < 320:
        return None, None
    c = closes[0]
    ma20 = sum(closes[:20]) / 20
    ma60 = sum(closes[:60]) / 60
    yr = closes[:250]
    hi, lo = max(yr), min(yr)
    pos250 = 100 * (c - lo) / (hi - lo) if hi > lo else 50
    r20 = (c / closes[20] - 1) * 100 if closes[20] else 0
    off = (c / ma20 - 1) * 100
    v250 = sum(vols[:250]) / 250
    v20 = sum(vols[:20]) / 20 if v250 else 0
    shrink = v20 / v250 if v250 else 1
    yr_dd = (lo / hi - 1) * 100 if hi else 0
    out = {'pos250': pos250, 'r20': r20, 'off': off, 'shrink': shrink, 'yr_dd': yr_dd}
    off_lo250 = (c / lo - 1) * 100 if lo else 0
    top_score = (off_lo250 >= 40) + (n > 5 and closes[5] and c / closes[5] - 1 >= 0.12) + (off >= 10) + \
                (v250 and vols[0] >= max(vols[:250]) and (closes[1] and (c / closes[1] - 1) <= 0.01))
    if top_score >= 3 and off_lo250 >= 40:
        return 'TOP', out
    if ma20 > ma60 and pos250 < 50:
        lo_i = yr.index(lo)
        start_i = None
        for i in range(lo_i, -1, -1):
            w = vols[i:i + 20]
            if len(w) < 20:
                break
            if i + 1 < n and closes[i + 1] and vols[i] >= 2 * (sum(w) / 20) and (closes[i] / closes[i + 1] - 1) >= 0.03:
                start_i = i
                break
        if start_i is not None and 2 <= start_i <= 40:
            off_s = (c / closes[start_i] - 1) * 100
            if -3 <= off_s <= 17:
                pre60 = closes[start_i + 1:start_i + 61]
                if len(pre60) >= 40 and (min(pre60) / max(pre60) - 1) * 100 <= -25:
                    p20 = sum(vols[start_i + 1:start_i + 21]) / 20 if start_i + 21 <= n else v250
                    ps = p20 / v250 if v250 else 1
                    yr3 = closes[:750] if n >= 750 else closes
                    hi3, lo3 = max(yr3), min(yr3)
                    p3 = 100 * (c - lo3) / (hi3 - lo3) if hi3 > lo3 else 50
                    if p3 < 55 and (c / lo3 - 1) * 100 <= 80:
                        return 'UP', {**out, 'ps': ps, 'off_start': off_s, 'days': start_i, 'p3': p3}
    if ma20 < ma60 and r20 < 0:
        if yr_dd > -25 or shrink > 0.9:
            return 'DOWN', out
    return 'MID', out


def describe(xs):
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return None
    n = len(xs)
    med = xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])
    return {'n': n, 'mean': round(sum(xs) / n, 2), 'med': round(med, 2),
            'win': round(sum(1 for x in xs if x > 0) / n * 100, 1),
            'p10': round(xs[int(0.10 * n)], 2), 'p90': round(xs[min(n - 1, int(0.90 * n))], 2)}


def monthly_t(pairs):
    g = defaultdict(list)
    for m, x in pairs:
        if x is not None and math.isfinite(x):
            g[m].append(x)
    ms = [sum(v) / len(v) for v in g.values() if v]
    if len(ms) < 5:
        return None
    mu = sum(ms) / len(ms)
    sd = math.sqrt(sum((x - mu) ** 2 for x in ms) / (len(ms) - 1))
    return {'months': len(ms), 'mu': round(mu, 2),
            't': round(mu / (sd / math.sqrt(len(ms))), 2) if sd else None}


def clean_series(rows):
    """ffill close; 头部无效丢弃; vols None->0。返回(dates,closes,opens,vols) 或 None。"""
    dates = [r[1] for r in rows]
    cl, vl, op = [], [], []
    last = None
    ok = False
    for r in rows:
        x = r[3]
        if x is not None and x > 0:
            last = x
            ok = True
        elif not ok:
            return None
        cl.append(last)
        op.append(r[2])
        vl.append(r[4] or 0)
    return dates, cl, op, vl


def main():
    t0 = time.time()
    db = sqlite3.connect(DB, uri=True)
    all_dates = sorted(r[0] for r in db.execute(
        f"SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>='{DATA_START}'"))
    didx = {d: i for i, d in enumerate(all_dates)}
    seen, sample_pts = set(), []
    for d in all_dates:
        if d < T0:
            continue
        if d[:6] not in seen:
            seen.add(d[:6])
            sample_pts.append(d)
    max_i = len(all_dates) - 1 - 61
    sample_pts = [d for d in sample_pts if didx[d] <= max_i]
    print(f'[CAL] {len(all_dates)} days {all_dates[0]}~{all_dates[-1]}, {len(sample_pts)} monthly pts', flush=True)

    base_f = {h: defaultdict(list) for h in FWDS}
    ret_sum, ret_cnt = defaultdict(float), defaultdict(int)
    events = []
    n_codes = 0

    def flush(rows):
        cs = clean_series(rows)
        if cs is None:
            return
        dates, closes, opens, vols = cs
        pos = {d: k for k, d in enumerate(dates)}
        for k in range(1, len(dates)):
            c0, c1 = closes[k - 1], closes[k]
            if c0 and c0 > 0 and c1 and c1 > 0:
                ret_sum[dates[k]] += c1 / c0 - 1
                ret_cnt[dates[k]] += 1
        kept = {'UP': -10 ** 9, 'DOWN': -10 ** 9}
        for d in sample_pts:
            i = pos.get(d)
            if i is None or i < 320:
                continue
            f = {('c', 20): None, ('c', 60): None, ('o', 20): None, ('o', 60): None}
            if closes[i] is None or closes[i] <= 0:
                continue
            for h in FWDS:
                if i + h < len(closes) and closes[i + h]:
                    f[('c', h)] = (closes[i + h] / closes[i] - 1) * 100
                    if opens[i + 1] is not None and opens[i + 1] > 0:
                        f[('o', h)] = (closes[i + h] / opens[i + 1] - 1) * 100
            for h in FWDS:
                if f[('c', h)] is not None:
                    base_f[h][d].append(f[('c', h)])
            s = max(0, i - 779)
            cr = closes[i::-1] if s == 0 else closes[i:s:-1]
            vr = vols[i::-1] if s == 0 else vols[i:s:-1]
            bucket, det = classify_recon(cr, vr)
            if bucket in ('UP', 'DOWN'):
                if didx[d] - kept[bucket] < 60:
                    continue
                kept[bucket] = didx[d]
                events.append({'date': d, 'code': code, 'bucket': bucket,
                               'ps': det.get('ps'), 'shrink': round(det['shrink'], 3),
                               'off_start': det.get('off_start'), 'days': det.get('days'),
                               'f20c': f[('c', 20)], 'f60c': f[('c', 60)],
                               'f20o': f[('o', 20)], 'f60o': f[('o', 60)]})

    cur = db.execute(
        f"SELECT code,date,open,close,volume FROM stock_daily_meta_qfq WHERE date>='{DATA_START}' ORDER BY code,date")
    code = None
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        n_codes += 1
        if n_codes % 800 == 0:
            print(f'  ..{n_codes} codes, {len(events)} events, {time.time()-t0:.0f}s', flush=True)
        flush(list(grp))
    db.close()
    print(f'[SCAN] codes={n_codes} events={len(events)} in {time.time()-t0:.0f}s', flush=True)

    mdates = sorted(ret_cnt)
    idx, cum = [], 1.0
    for d in mdates:
        cum *= 1 + ret_sum[d] / ret_cnt[d]
        idx.append(cum)
    mpos = {d: k for k, d in enumerate(mdates)}
    env_of = {}
    for d in sample_pts:
        k = mpos.get(d)
        if k is None:
            continue
        w = idx[max(0, k - 249):k + 1]
        env_of[d] = round(100 * (idx[k] - min(w)) / (max(w) - min(w))) if max(w) > min(w) else 50

    base_med = {}
    for h in FWDS:
        base_med[h] = {d: sorted(xs)[len(xs) // 2] for d, xs in base_f[h].items() if xs}
    for e in events:
        e['year'] = e['date'][:4]
        e['env'] = env_of.get(e['date'])
        for h in FWDS:
            bm = base_med[h].get(e['date'])
            e[f'x{h}'] = (e[f'f{h}c'] - bm) if (bm is not None and e[f'f{h}c'] is not None) else None

    R = {'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
         'points': len(sample_pts), 'codes': n_codes, 'events': len(events),
         'note': 'UP@ps阈值分档; 超额=个票fwd收盘口径-同日全市场中位; t=月度聚合t; '
                 'f20o/f60o=T+1开盘入场口径(超额近似用收盘基线)'}

    up_all = [e for e in events if e['bucket'] == 'UP']
    groups = {'UP@ps<=0.95': [e for e in up_all if e['ps'] is not None and e['ps'] <= 0.95],
              'UP@ps<=1.56': [e for e in up_all if e['ps'] is not None and e['ps'] <= 1.56],
              'DOWN': [e for e in events if e['bucket'] == 'DOWN']}
    for h in FWDS:
        sec = {}
        for tag, evs in groups.items():
            sec[tag] = {'n': len(evs),
                        f'f{h}c': describe([e[f'f{h}c'] for e in evs]),
                        f'f{h}o': describe([e[f'f{h}o'] for e in evs]),
                        f'x{h}': describe([e[f'x{h}'] for e in evs]),
                        f'x{h}_t': monthly_t([(e['date'][:6], e[f'x{h}']) for e in evs])}
        R[f'H{h}'] = sec
        print(f'--- H{h} ---')
        for tag in sec:
            s = sec[tag]
            print(f"  {tag}: n={s['n']} f{h}c={s[f'f{h}c']} x{h}={s[f'x{h}']} t={s[f'x{h}_t']}")

    up_loose = groups['UP@ps<=1.56']
    by_year, by_env = defaultdict(list), defaultdict(list)
    for e in up_loose:
        by_year[e['year']].append(e)
        if e['env'] is not None:
            by_env[e['env'] // 20 * 20].append(e)
    R['UP_year'] = {y: {'n': len(v),
                        'f20_med': describe([e['f20c'] for e in v])['med'] if v else None,
                        'x20_med': describe([e['x20'] for e in v])['med'] if v else None,
                        'win60': round(100 * sum(1 for e in v if (e['f60c'] or 0) > 0) / len(v), 1)}
                    for y, v in sorted(by_year.items())}
    R['UP_env'] = {f'{k}~{k+19}': {'n': len(v),
                                   'x20_med': describe([e['x20'] for e in v])['med'] if v else None,
                                   'f60_med': describe([e['f60c'] for e in v])['med'] if v else None}
                   for k, v in sorted(by_env.items()) if k >= 0}
    print('[YEAR]', json.dumps(R['UP_year'], ensure_ascii=False))
    print('[ENV]', json.dumps(R['UP_env'], ensure_ascii=False))
    # 分年×env 交叉(门控版跨期稳健性; prereg 冻结用)
    ye = defaultdict(list)
    for e in up_loose:
        if e['env'] is None:
            continue
        b = e['env'] // 20 * 20
        ye[f"{e['year']}|env{b}~{b+19}"].append(e)
    R['UP_year_env'] = {k: {'n': len(v),
                            'x20_med': describe([e['x20'] for e in v])['med'] if v else None,
                            'f60_med': describe([e['f60c'] for e in v])['med'] if v else None,
                            'win60': round(100 * sum(1 for e in v if (e['f60c'] or 0) > 0) / len(v), 1)}
                        for k, v in sorted(ye.items())}
    print('[YEARxENV]', json.dumps(R['UP_year_env'], ensure_ascii=False))

    # 实现校验: 20260918 单点重扫 vs 生产 census json
    chk = {'status': 'SKIP'}
    try:
        cj = json.load(open(os.path.join(ROOT, 'data', 'direction_census_20260919.json'), encoding='utf-8'))
        prod_up = {x['code']: x.get('off_start') for x in cj['buckets']['UP']}
        ms = '?'
        try:
            ms = json.load(open(os.path.join(ROOT, 'data', 'market_state_probe.json'), encoding='utf-8')).get('state', '?')
        except Exception:
            pass
        cap = 1.56 if ms == 'LOW_VOL' else 0.95
        db = sqlite3.connect(DB, uri=True)
        codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20260901'")]
        recon_up = {}
        for ci, c2 in enumerate(codes):
            if c2.startswith('688'):
                continue
            nm = get_name(c2) or ''
            if 'ST' in nm.upper() or '退' in nm:
                continue
            h = db.execute("SELECT close,volume FROM stock_daily_meta_qfq WHERE code=? AND date<='20260918' "
                           "ORDER BY date DESC LIMIT 780", (c2,)).fetchall()
            rows = [(a, b) for a, b in h]
            if len(rows) < 320 or rows[0][0] is None or not (2 <= rows[0][0] <= 500):
                continue
            cl = [x[0] for x in rows]
            vl = [x[1] or 0 for x in rows]
            b, det = classify_recon(cl, vl)
            if b == 'UP' and det.get('ps') is not None and det['ps'] <= cap:
                recon_up[c2] = det.get('off_start')
        db.close()
        miss = [(c3, prod_up[c3], recon_up.get(c3)) for c3 in prod_up
                if c3 not in recon_up or (recon_up[c3] is not None and prod_up[c3] is not None
                                          and abs(recon_up[c3] - prod_up[c3]) > 0.15)]
        chk = {'status': 'PASS' if not miss else 'FAIL', 'prod_n': len(prod_up),
               'recon_n': len(recon_up), 'cap': cap, 'ms': ms, 'mismatch': miss[:20]}
    except Exception as ex:
        chk = {'status': 'ERROR', 'err': str(ex)}
    R['impl_check'] = chk
    print('[IMPL-CHECK]', json.dumps(chk, ensure_ascii=False))

    stamp = datetime.now().strftime('%Y%m%d')
    json.dump(R, open(os.path.join(ROOT, 'data', f'up_bucket_hist_recon_{stamp}.json'), 'w',
                      encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    print(f'[DONE] {time.time()-t0:.0f}s -> data/up_bucket_hist_recon_{stamp}.json')


if __name__ == '__main__':
    main()
