# -*- coding: utf-8 -*-
"""排板/扫板分钟回放 2026-10-10 (主人「干,继续」令: 执行域前置——成交率与真期望)
数据: vipdoc lc1 1分钟(20260107~20261009, 181交易日, 唯一owner=tdx_reader格式语义)
链: qfq日线2026年maximal连续涨停run(口径同chain_prelaunch_study), 首板日s回放, s>=20260201保证s-1在lc1
涨停价: limit=round(前日分钟末收盘*(1+thr),2) — lc1自含raw昨收, 无复权混源
前瞻: 当日段用raw, s→s+k段用qfq比值(除权正确), 总收益=两者乘积
模型(如实标注):
  扫板=首次触板前一分钟收盘买入(确定成交); 排板=涨停价排队, 乐观模型=当日开板>=1次即成交(真实成交率≤此上界),
       纯封死(零开板)=大概率不成交不建仓
输出: 封板时间分布/纯封死占比/扫板与排板当日及+f1收益 × 链高H分层 + 封板时段×次日连板率
边界: 研究层零交易权; 排板为上界口径; 剔除s或s+1..s+5缺qfq/缺分钟样本并计数
"""
import sqlite3
import struct
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')

from tdx_reader import _MIN_FMT, RECORD_SIZE, DEFAULT_TDX_ROOT  # 格式唯一owner=tdx_reader

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ZT_DB = r'D:\zcodesj\data\zt_pool_history.db'
NAME_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
ROOT = Path(DEFAULT_TDX_ROOT)
D0_MIN, D0_MAX = 20260201, 20261009


def load_names():
    names = defaultdict(set)
    z = sqlite3.connect(f'file:{ZT_DB}?mode=ro', uri=True)
    for code, nm in z.execute('SELECT code, name FROM zt_pool'):
        if nm:
            names[code].add(nm)
    z.close()
    try:
        con = sqlite3.connect(f'file:{NAME_DB}?mode=ro', uri=True)
        for code, nm in con.execute('SELECT code, name FROM spot_daily'):
            if nm:
                names[code].add(nm)
        con.close()
    except sqlite3.Error:
        pass
    return {c: max(s, key=len) for c, s in names.items()}


def thr_of(code, name):
    if 'ST' in (name or '').upper():
        return 4.85
    return 19.85 if code[:3] in ('300', '301') else 9.85


def read_min1_day(code6, day):
    """单股某日1分钟bar列表[(time_hhmm,h,l,c)], 内联过滤, 复用tdx_reader格式常量"""
    m = 'sh' if code6.startswith(('6', '9')) else ('bj' if code6.startswith(('4', '8', '92')) else 'sz')
    p = ROOT / m / 'minline' / f'{m}{code6}.lc1'
    if not p.is_file():
        return None, None
    n = p.stat().st_size // RECORD_SIZE
    bars = []
    prev_close = None
    with open(p, 'rb') as f:
        buf = f.read(n * RECORD_SIZE)
    for b in struct.iter_unpack(_MIN_FMT, buf):
        dnum, tmin, o, h, l, c, amt, vol, _ = b
        if dnum == 0 or o <= 0:
            continue
        year = dnum // 2048 + 2004
        md = dnum % 2048
        d = year * 10000 + md // 100 * 100 + md % 100
        if d < day:
            prev_close = c  # 持续跟踪最近一日末分钟收盘
        elif d == day:
            hh, mm = divmod(tmin, 60)
            bars.append((hh * 100 + mm, h, l, c))
        else:
            break
    return prev_close, bars


def main():
    names = load_names()
    # 1) qfq提2026链(口径同chain_prelaunch_study)
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    qfq = {}
    cur = con.execute("SELECT code,date,close,change_pct FROM stock_daily_meta_qfq "
                      "WHERE date>='20251101' ORDER BY code,date")
    for code, d, c, p in cur:
        if code[0] in '684' or code[:2] == '92':
            continue
        qfq.setdefault(code, ([], [], []))
        qfq[code][0].append(str(d))
        qfq[code][1].append(c)
        qfq[code][2].append(p)
    con.close()
    chains = []
    for code, (ds, cs, ps) in qfq.items():
        n = len(ds)
        i = 1
        while i < n:
            pi, pj = ps[i - 1], ps[i]
            if pj is not None and pi is not None and pj >= thr_of(code, names.get(code, '')) and not (pi >= thr_of(code, names.get(code, ''))):
                j = i
                while j + 1 < n and ps[j + 1] is not None and ps[j + 1] >= thr_of(code, names.get(code, '')):
                    j += 1
                if D0_MIN <= int(ds[i]) <= D0_MAX:
                    chains.append((code, i, j - i + 1))
                i = j + 1
            else:
                i += 1
    print(f'2026链数(首板日≥20260201): {len(chains)}')

    hb = lambda H: 'H=1' if H == 1 else 'H=2' if H == 2 else 'H=3-4' if H <= 4 else 'H=5+'
    rows = []
    miss_min = miss_qfq = 0
    for code, s, H in chains:
        ds, cs, ps = qfq[code]
        day = int(ds[s])
        prev_close, bars = read_min1_day(code, day)
        if not bars or not prev_close:
            miss_min += 1
            continue
        thr = thr_of(code, names.get(code, ''))
        limit = round(prev_close * (1 + thr / 100) + 1e-9, 2)
        eps = 0.001
        touch = next((k for k, (t, h, l, c) in enumerate(bars) if h >= limit - eps), None)
        if touch is None:
            miss_min += 1
            continue
        sealed_end = bars[-1][3] >= limit - eps
        reopens = 0
        on = True
        for (t, h, l, c) in bars[touch:]:
            if c >= limit - eps and not on:
                on = True
            elif c < limit - eps and on:
                on = False
                reopens += 1
        t0 = bars[touch][0]
        tcat = ('早板<10:00' if t0 < 1000 else '上午10-11:30' if t0 < 1131 else '午后13-14' if t0 < 1400 else '尾盘>=14:00')
        # 当日段收益
        last_c = bars[-1][3]
        scan_entry = bars[0][1] if touch == 0 else bars[touch - 1][3]  # 首分钟触板用开盘价
        scan_day = 100.0 * (last_c / scan_entry - 1)
        # 前瞻(qfq比值)
        q0 = cs[s]
        if q0 is None or s + 5 >= len(ds) or cs[s + 1] is None:
            miss_qfq += 1
            continue
        r1 = cs[s + 1] / q0
        r2 = cs[s + 2] / q0 if cs[s + 2] is not None else None
        r5 = cs[s + 5] / q0 if cs[s + 5] is not None else None
        rows.append({'code': code, 'H': H, 'layer': hb(H), 'tcat': tcat, 'sealed': int(sealed_end),
                     'reopens': reopens, 'scan_day': scan_day,
                     'scan_f1': 100.0 * (r1 * last_c / scan_entry - 1),
                     'pa_filled': int(reopens >= 1),
                     'pa_day': 100.0 * (last_c / limit - 1) if reopens >= 1 else None,
                     'pa_f1': 100.0 * (r1 * last_c / limit - 1) if reopens >= 1 else None,
                     'next_zt': int(ps[s + 1] is not None and ps[s + 1] >= thr)})
    print(f'回放完成: {len(rows)} (缺分钟{miss_min}/缺qfq前瞻{miss_qfq})')

    print('\n== 首板日微观结构 × 链高 ==')
    print(f"{'层':<7}{'n':>6}{'封死率':>7}{'纯封死占比':>10}{'开板中位':>8}{'扫板当日med':>11}{'扫板当日胜率':>11}{'扫板f1med':>10}{'排板成交率':>10}{'排板成交日当日':>13}{'排板成交日f1':>12}")
    for layer in ('H=1', 'H=2', 'H=3-4', 'H=5+'):
        g = [r for r in rows if r['layer'] == layer]
        if len(g) < 30:
            continue
        sealed = 100 * sum(r['sealed'] for r in g) / len(g)
        pure = 100 * sum(1 for r in g if r['sealed'] and r['reopens'] == 0) / len(g)
        ro = st.median([r['reopens'] for r in g])
        sd = [r['scan_day'] for r in g]
        sf = [r['scan_f1'] for r in g]
        pa = [r for r in g if r['pa_filled']]
        pa_rate = 100 * len(pa) / len(g)
        print(f"{layer:<7}{len(g):>6}{sealed:>6.0f}%{pure:>9.0f}%{ro:>8.0f}{st.median(sd):>+11.2f}"
              f"{100*sum(1 for x in sd if x>0)/len(sd):>10.0f}%{st.median(sf):>+10.2f}"
              f"{pa_rate:>9.0f}%{(st.median([r['pa_day'] for r in pa]) if pa else 0):>+13.2f}"
              f"{(st.median([r['pa_f1'] for r in pa]) if pa else 0):>+12.2f}")

    print('\n== 封板时段 × 次日连板率 (全层) ==')
    by_t = defaultdict(list)
    for r in rows:
        by_t[r['tcat']].append(r)
    for tcat in ('早板<10:00', '上午10-11:30', '午后13-14', '尾盘>=14:00'):
        g = by_t.get(tcat, [])
        if len(g) < 100:
            continue
        nz = 100 * sum(r['next_zt'] for r in g) / len(g)
        sf = st.median([r['scan_f1'] for r in g])
        hi = 100 * sum(1 for r in g if r['layer'] in ('H=3-4', 'H=5+')) / len(g)
        print(f'{tcat:<12} n={len(g):>5} 次日连板率={nz:>5.1f}% 扫板f1med={sf:>+6.2f}% 高链占比={hi:.0f}%')

    print('\n== 扫板 vs 排板 组合期望 (全层加权, f1口径) ==')
    sf = [r['scan_f1'] for r in rows]
    print(f'扫板无条件: n={len(sf)} med={st.median(sf):+.2f}% 均值={st.mean(sf):+.2f}% 胜率={100*sum(1 for x in sf if x>0)/len(sf):.0f}%')
    pa = [r for r in rows if r['pa_filled']]
    paf = [r['pa_f1'] for r in pa]
    if paf:
        print(f'排板乐观上界(成交日): n={len(paf)} med={st.median(paf):+.2f}% 均值={st.mean(paf):+.2f}% 胜率={100*sum(1 for x in paf if x>0)/len(paf):.0f}%  成交率={100*len(pa)/len(rows):.0f}%')
    import json
    json.dump([{k: v for k, v in r.items() if k != 'code'} for r in rows],
              open(r'D:\zcodesj\tmp\chain_minute_replay_20261010.json', 'w', encoding='utf-8'), ensure_ascii=False)
    print('\n已存 tmp/chain_minute_replay_20261010.json')


if __name__ == '__main__':
    main()
