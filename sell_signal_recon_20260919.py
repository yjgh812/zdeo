# -*- coding: utf-8 -*-
"""卖出侧双信号 prereg 侦察(2026-09-19) — 方案 winrate_lift_engineering_plan P2-3/P2-4
信号A 峰顶涨停聚集(swing-full-study: 峰顶涨停聚集5.71x跨年最稳健→交易化=高位涨停密集=见顶回避/卖出信号, 预期fwd负超额)
  事件: pos250>=60 且 5日内涨停>=2次; 信号日收盘判定(回避/卖出用途, 无买入可执行性问题)
信号B 破MA20不收回(winner-wave-anatomy 观察级"卖点=破MA20不收回"→交易化验证)
  事件: 首次跌破MA20(b日 close<ma20 且 b-1日 close>=ma20) 后 b+1/b+2 两日收盘均<各自ma20(不收回);
        确认日=b+2, fwd 从 b+2 收盘起算(无前视, 模拟确认后卖出)
共同口径: 2018-01~2026-09 每日全市场扫描 × fwd20/60(qfq收盘) × 同日全市场20%随机子样本中位超额
        × 月度聚合t × 分年 × 大盘env三桶 × 同票60交易日去重。
局限声明: 超额口径含选择效应(破位票本弱), 侦察只回答"持有是否跑输市场=卖出优于持有"。
涨停判定: qfq日ret>=9.7%(主板) / >=19.7%(创业板300/301且date>=20200824); ST(5%板)自然不触发9.7阈值, 无需过滤; 剔688/北交所。
产出: data/sell_signal_recon_YYYYMMDD.json
"""
import itertools
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict, deque
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
DATA_START = '20160601'
T0 = '20180101'
FWDS = (20, 60)


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


def sliding_max(xs, w):
    dq, out = deque(), [None] * len(xs)
    for i, x in enumerate(xs):
        while dq and xs[dq[-1]] <= x:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - w:
            dq.popleft()
        if i >= w - 1:
            out[i] = xs[dq[0]]
    return out


def sliding_min(xs, w):
    dq, out = deque(), [None] * len(xs)
    for i, x in enumerate(xs):
        while dq and xs[dq[-1]] >= x:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - w:
            dq.popleft()
        if i >= w - 1:
            out[i] = xs[dq[0]]
    return out


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
    sample_pts = set(d for d in sample_pts if didx[d] <= len(all_dates) - 1 - 61)
    print(f'[CAL] {len(all_dates)} days', flush=True)

    base = {h: defaultdict(list) for h in FWDS}     # 同日全市场 20% 子样本 fwd
    ret_sum, ret_cnt = defaultdict(float), defaultdict(int)
    ev_peak, ev_break = [], []
    n_codes = 0

    def flush(rows):
        dates = [r[1] for r in rows]
        cl, last, ok = [], None, False
        for r in rows:
            x = r[2]
            if x is not None and x > 0:
                last = x
                ok = True
            elif not ok:
                return
            cl.append(last)
        n = len(cl)
        posmap = {d: k for k, d in enumerate(dates)}
        for k in range(1, n):
            c0, c1 = cl[k - 1], cl[k]
            if c0 and c0 > 0 and c1 and c1 > 0:
                ret_sum[dates[k]] += c1 / c0 - 1
                ret_cnt[dates[k]] += 1
        # 涨停布尔
        lim = [False] * n
        is_cyb = code.startswith(('300', '301'))
        for k in range(1, n):
            if cl[k - 1] and cl[k - 1] > 0:
                r = cl[k] / cl[k - 1] - 1
                if is_cyb and dates[k] >= '20200824':
                    lim[k] = r >= 0.197
                else:
                    lim[k] = r >= 0.097
        # 5日涨停计数(滑动)
        pc, cnt5 = [0] * (n + 1), [0] * n
        for k in range(n):
            pc[k + 1] = pc[k] + (1 if lim[k] else 0)
        for k in range(n):
            j = max(0, k - 4)
            cnt5[k] = pc[k + 1] - pc[j]
        # MA20 前缀和
        ps = [0.0] * (n + 1)
        for k in range(n):
            ps[k + 1] = ps[k] + cl[k]
        ma20 = [None] * n
        for k in range(19, n):
            ma20[k] = ps[k + 1] - ps[k - 19]
            ma20[k] /= 20
        hi250 = sliding_max(cl, 250)
        lo250 = sliding_min(cl, 250)
        kept = {'PEAK': -10 ** 9, 'BREAK': -10 ** 9}
        for i in range(249, n):
            d = dates[i]
            hi, lo = hi250[i], lo250[i]
            pos250 = 100 * (cl[i] - lo) / (hi - lo) if hi and lo and hi > lo else None
            # 基线子样本(hash稳定抽样20%) + 事件日必入
            h_sub = (hash((code, d)) % 5 == 0) or (d in sample_pts)
            if h_sub:
                for h in FWDS:
                    if i + h < n and cl[i + h]:
                        base[h][d].append((cl[i + h] / cl[i] - 1) * 100)
            # 信号A: 峰顶涨停聚集
            if pos250 is not None and pos250 >= 60 and cnt5[i] >= 2:
                if didx[d] - kept['PEAK'] >= 60:
                    if all(i + h < n and cl[i + h] for h in FWDS):
                        kept['PEAK'] = didx[d]
                        ev_peak.append({'d': d, 'y': d[:4], 'pos': round(pos250),
                                        **{f'f{h}': (cl[i + h] / cl[i] - 1) * 100 for h in FWDS}})
            # 信号B: 破MA20不收回(首破b, 确认b+2)
            b = i - 2
            if b >= 20 and ma20[b] and ma20[i] and ma20[i - 1] and ma20[i - 2]:
                if cl[b] < ma20[b] and cl[b - 1] >= ma20[b - 1] and cl[i - 1] < ma20[i - 1] and cl[i] < ma20[i]:
                    if didx[d] - kept['BREAK'] >= 60:
                        if all(i + h < n and cl[i + h] for h in FWDS):
                            kept['BREAK'] = didx[d]
                            ev_break.append({'d': d, 'y': d[:4],
                                             **{f'f{h}': (cl[i + h] / cl[i] - 1) * 100 for h in FWDS}})

    cur = db.execute(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='{DATA_START}' ORDER BY code,date")
    code = None
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        if code.startswith(('688', '4', '8')):
            continue
        n_codes += 1
        if n_codes % 1000 == 0:
            print(f'  ..{n_codes} codes, peak={len(ev_peak)} break={len(ev_break)}, {time.time()-t0:.0f}s', flush=True)
        flush(list(grp))
    db.close()
    print(f'[SCAN] codes={n_codes} peak={len(ev_peak)} break={len(ev_break)} in {time.time()-t0:.0f}s', flush=True)

    mdates = sorted(ret_cnt)
    idx, cum = [], 1.0
    for d in mdates:
        cum *= 1 + ret_sum[d] / ret_cnt[d]
        idx.append(cum)
    mpos = {d: k for k, d in enumerate(mdates)}
    env_of = {}
    for d in set([e['d'] for e in ev_peak] + [e['d'] for e in ev_break]):
        k = mpos.get(d)
        if k is None:
            continue
        w = idx[max(0, k - 249):k + 1]
        env_of[d] = 100 * (idx[k] - min(w)) / (max(w) - min(w)) if max(w) > min(w) else 50
    med = {}
    for h in FWDS:
        med[h] = {d: sorted(xs)[len(xs) // 2] for d, xs in base[h].items() if len(xs) >= 30}
    for tag, evs in (('PEAK', ev_peak), ('BREAK', ev_break)):
        for e in evs:
            for h in FWDS:
                bm = med[h].get(e['d'])
                e[f'x{h}'] = (e[f'f{h}'] - bm) if bm is not None else None
            e['env'] = env_of.get(e['d'])

    R = {'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
         'codes': n_codes, 'n_peak': len(ev_peak), 'n_break': len(ev_break),
         'note': '峰顶=pos250>=60且5日2板; 破位=首破MA20后2日不收回,确认日收盘起算fwd; '
                 '超额=同日全市场20%子样本中位; 卖出信号预期负超额(持有跑输市场=卖出优于持有)'}
    for tag, evs in (('PEAK', ev_peak), ('BREAK', ev_break)):
        sec = {}
        for h in FWDS:
            sec[f'H{h}'] = {'f': describe([e[f'f{h}'] for e in evs]),
                            'x': describe([e[f'x{h}'] for e in evs]),
                            'x_t': monthly_t([(e['d'][:6], e[f'x{h}']) for e in evs])}
        by_y = defaultdict(list)
        by_env = defaultdict(list)
        for e in evs:
            by_y[e['y']].append(e)
            if e['env'] is not None:
                by_env['<40' if e['env'] < 40 else ('40-79' if e['env'] < 80 else '80+')].append(e)
        sec['year_x60med'] = {y: describe([e['x60'] for e in v])['med'] for y, v in sorted(by_y.items())}
        sec['year_x60win'] = {y: describe([e['x60'] for e in v])['win'] for y, v in sorted(by_y.items())}
        sec['env_x60'] = {k: {'n': len(v), 'x60': describe([e['x60'] for e in v]),
                              'x20_t': monthly_t([(e['d'][:6], e['x20']) for e in v])}
                          for k, v in sorted(by_env.items())}
        R[tag] = sec
        print(f'--- {tag} ---')
        for h in FWDS:
            print(f"  H{h}: f={sec[f'H{h}']['f']} x={sec[f'H{h}']['x']} t={sec[f'H{h}']['x_t']}")
        print('  year_x60med:', json.dumps(sec['year_x60med'], ensure_ascii=False))
        print('  env_x60:', json.dumps(sec['env_x60'], ensure_ascii=False))

    stamp = datetime.now().strftime('%Y%m%d')
    json.dump(R, open(f'{ROOT}/data/sell_signal_recon_{stamp}.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=str)
    print(f'[DONE] {time.time()-t0:.0f}s -> data/sell_signal_recon_{stamp}.json')


if __name__ == '__main__':
    main()
