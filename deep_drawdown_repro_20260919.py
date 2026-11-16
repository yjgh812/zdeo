# -*- coding: utf-8 -*-
"""深回撤组fwd60独立口径复现 — prereg侦察(2026-09-19)
主人方案 winrate_lift_engineering_plan_20260919.md P2-2。
背景: 9/19涨跌波段全维度学习中"深回撤组fwd60 +15.05% > 浅 +8.89%"待prereg;
     同日另一路独立口径未复现被降级禁引用。本脚本=第三口径正式复现判定。
独立口径设计(与原线索的差异显式化):
  a) 窗口: 2018-01~2026-09(月度抽样), 原线索窗口更短;
  b) 回撤定义: dd250 = close/250日最高收盘 - 1(五档分组), 不用zigzag段回撤;
  c) 基线: 同日全市场fwd60中位超额(消除市场漂移);
  d) 判定: 越深档越高的单调性 + 深档(<=-40%)对浅档(>=-10%)的超额差与月度t。
产出: data/deep_drawdown_repro_YYYYMMDD.json
铁律: 只读研究; 若复现失败(不单调或差<0), 登记禁引用, 禁复活。
"""
import itertools
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
DATA_START = '20160601'
T0 = '20180101'
H = 60
BUCKETS = [(0, -10), (-10, -20), (-20, -30), (-30, -40), (-40, -100)]  # dd250 分档(%)


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
    sample_pts = [d for d in sample_pts if didx[d] <= len(all_dates) - 1 - H]
    print(f'[CAL] {len(all_dates)} days, {len(sample_pts)} monthly pts', flush=True)

    base = defaultdict(list)      # 同日全市场 fwd60
    recs = []                     # (date, year, dd_bucket, fwd60, excess)
    n_codes = 0
    cur = db.execute(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='{DATA_START}' ORDER BY code,date")

    def flush(rows):
        dates = [r[1] for r in rows]
        cl = []
        last = None
        ok = False
        for r in rows:
            x = r[2]
            if x is not None and x > 0:
                last = x
                ok = True
            elif not ok:
                return
            cl.append(last)
        pos = {d: k for k, d in enumerate(dates)}
        n = len(cl)
        for d in sample_pts:
            i = pos.get(d)
            if i is None or i < 250 or i + H >= n:
                continue
            w = cl[i - 249:i + 1]
            hi = max(w)
            if hi <= 0:
                continue
            dd = (cl[i] / hi - 1) * 100
            f = (cl[i + H] / cl[i] - 1) * 100
            base[d].append(f)
            bi = next((k for k, (a, b) in enumerate(BUCKETS) if b <= dd < a or (k == len(BUCKETS) - 1 and dd <= b)), None)
            if bi is None:
                bi = len(BUCKETS) - 1 if dd <= -40 else None
            if bi is None:
                bi = 0 if dd > -10 else bi
            recs.append([d, d[:4], bi, f, None])

    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        n_codes += 1
        if n_codes % 1000 == 0:
            print(f'  ..{n_codes} codes, {len(recs)} pts, {time.time()-t0:.0f}s', flush=True)
        flush(list(grp))
    db.close()

    med = {d: sorted(xs)[len(xs) // 2] for d, xs in base.items() if xs}
    for r in recs:
        bm = med.get(r[0])
        r[4] = (r[3] - bm) if bm is not None else None

    R = {'generated': datetime.now().strftime('%Y-%m-%d %H:%M'), 'points': len(sample_pts),
         'codes': n_codes, 'n': len(recs), 'H': H,
         'buckets': [f'{a}~{b}%' for a, b in BUCKETS]}
    by_b = defaultdict(list)
    for r in recs:
        by_b[r[2]].append(r)
    print('=== dd250 分档 fwd60(独立口径 2018-2026) ===')
    rows_out = {}
    for k in range(len(BUCKETS)):
        v = by_b.get(k, [])
        f60 = describe([r[3] for r in v])
        x60 = describe([r[4] for r in v])
        xt = monthly_t([(r[0][:6], r[4]) for r in v])
        rows_out[f'{BUCKETS[k][0]}~{BUCKETS[k][1]}%'] = {'f60': f60, 'x60': x60, 'x60_t': xt}
        print(f"  {BUCKETS[k]}: n={len(v)} f60={f60} x60={x60} t={xt}")
    R['by_bucket'] = rows_out
    # 判定: 最深档 vs 最浅档 超额差
    deep = by_b.get(4, []); shallow = by_b.get(0, [])
    dx = [r[4] for r in deep if r[4] is not None]
    sx = [r[4] for r in shallow if r[4] is not None]
    if dx and sx:
        diff = sum(dx) / len(dx) - sum(sx) / len(sx)
        R['verdict'] = {'deep_x60_mean': round(sum(dx)/len(dx), 2), 'shallow_x60_mean': round(sum(sx)/len(sx), 2),
                        'diff_pp': round(diff, 2),
                        'monotonic': all((rows_out[f'{BUCKETS[k][0]}~{BUCKETS[k][1]}%']['x60'] or {}).get('mean', -99) <=
                                         (rows_out[f'{BUCKETS[k+1][0]}~{BUCKETS[k+1][1]}%']['x60'] or {}).get('mean', -99) + 0.5
                                         for k in range(len(BUCKETS)-1)),
                        'note': '复现成立需 diff_pp>0 且大体单调; 否则登记禁引用'}
    print('[VERDICT]', json.dumps(R.get('verdict'), ensure_ascii=False))

    stamp = datetime.now().strftime('%Y%m%d')
    json.dump(R, open(f'{ROOT}/data/deep_drawdown_repro_{stamp}.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=str)
    print(f'[DONE] {time.time()-t0:.0f}s -> data/deep_drawdown_repro_{stamp}.json')


if __name__ == '__main__':
    main()
