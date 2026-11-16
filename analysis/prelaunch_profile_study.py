# -*- coding: utf-8 -*-
"""启动前画像研究 2026-10-10 (主人令: 涨停买不进, 启动前长什么样? 怎么找启动前特征?)
Part A 家族层: 每笔交割单买入定位其启动日(连续板链首板日), 回算启动前 T-1/T-5/T-20 特征分布
  类型: 首板启动(consec_zt=1, d0即首板) / 连板加速(consec_zt>=2, 链起点s回溯) / 非板入场(d0无板, 含5日内是否出板)
Part B 全市场层: 2024+2025 全市场逐日事件, T日状态格 → T+1涨停率 lift 矩阵 + 连板率矩阵 + 蓄势组合格
  宇宙/口径与 entry_portrait 系列逐字对齐(剔科创68/北交8x4x92/次新<60bar; 涨停阈值9.85/19.85/4.85ST)
边界: 研究层零交易权; qfq无退市股=幸存者偏差; 涨停预测基率极低, lift≈1 的格如实呈报
"""
import csv
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
PORTRAIT_DB = r'D:\zcodesj\data\entry_portrait.db'
ZT_DB = r'D:\zcodesj\data\zt_pool_history.db'
NAME_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
YEARS = ('2024', '2025')


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


def load_series(codes, start_date):
    """code -> [dates, open, high, low, close, volume, change_pct]"""
    q6 = sorted(codes)
    px = {}
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    B = 100
    for i in range(0, len(q6), B):
        chunk = q6[i:i + B]
        q = ("SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
             "WHERE code IN (%s) AND date>=? ORDER BY code,date" % ','.join('?' * len(chunk)))
        for code, d, o, h, lo, c, v, p in con.execute(q, [*chunk, start_date]):
            px.setdefault(code, [[], [], [], [], [], [], []])
            px[code][0].append(str(d))
            px[code][1].append(o)
            px[code][2].append(h)
            px[code][3].append(lo)
            px[code][4].append(c)
            px[code][5].append(v)
            px[code][6].append(p)
    con.close()
    return px


def pre_feats(series, i, thr):
    """T日(或T-k日)特征, 与画像库同口径"""
    dates, o, h, lo, c, v, p = series
    if i < 59:
        return None
    cl = c[i]
    w60 = c[max(0, i - 59):i + 1]
    hi_, lo_ = max(w60), min(w60)
    pos60 = 100.0 * (cl - lo_) / (hi_ - lo_) if hi_ > lo_ else None
    ma20 = st.mean(c[i - 19:i + 1])
    bias20 = 100.0 * (cl / ma20 - 1)
    ret5 = 100.0 * (cl / c[i - 5] - 1) if i >= 5 else None
    ret20 = 100.0 * (cl / c[i - 20] - 1) if i >= 20 else None
    vol5 = st.mean([x for x in v[i - 5:i] if x is not None]) if i >= 5 else None
    vr = round(v[i] / vol5, 2) if vol5 and vol5 > 0 else None
    zt5 = sum(1 for x in p[i - 4:i + 1] if x is not None and x >= thr)
    nh60 = int(i >= 60 and cl >= max(c[i - 60:i]))
    mab = lambda n: st.mean(c[i - n + 1:i + 1])
    ma5, ma10, ma20v = mab(5), mab(10), mab(20)
    mabull = int(ma5 > ma10 > ma20v)
    return {'pos60': pos60, 'bias20': bias20, 'ret5': ret5, 'ret20': ret20,
            'vol_ratio': vr, 'zt_cnt5': zt5, 'nh60': nh60, 'ma_bull': mabull,
            'pct': p[i], 'vol_ratio_5d': round(st.mean([x for x in v[max(0, i - 4):i + 1] if x is not None]) /
                                                st.mean([x for x in v[max(0, i - 24):i - 4] if x is not None]), 2)
            if i >= 24 and st.mean([x for x in v[max(0, i - 24):i - 4] if x is not None]) else None}


def part_a():
    con = sqlite3.connect(f'file:{PORTRAIT_DB}?mode=ro', uri=True)
    eps = con.execute('SELECT player, code, name, d0, consec_zt, seg_ret FROM episodes').fetchall()
    con.close()
    px = load_series(sorted(set(e[1][2:] for e in eps)), '20230601')
    print(f'Part A 家族层: episodes={len(eps)} 涉及{len(px)}只')
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # type -> offset -> feat -> vals
    type_n = defaultdict(int)
    nonboard_5d = []
    for player, fullcode, name, d0, cz, seg in eps:
        code = fullcode[2:]
        series = px.get(code)
        if series is None:
            continue
        dates = series[0]
        i = next((k for k, d in enumerate(dates) if d == d0), None)
        if i is None:
            continue
        thr = thr_of(code, name)
        if cz >= 2:
            s = i
            while s - 1 >= 0 and series[6][s - 1] is not None and series[6][s - 1] >= thr:
                s -= 1
            typ = '连板加速'
        elif cz == 1:
            s = i
            typ = '首板启动'
        else:
            s = i
            typ = '非板入场'
            hit = any(series[6][k] is not None and series[6][k] >= thr for k in range(i + 1, min(i + 6, len(dates))))
            f5 = None
            if i + 5 < len(dates) and series[4][i]:
                f5 = round(100.0 * (series[4][i + 5] / series[4][i] - 1), 2)
            nonboard_5d.append((hit, f5))
        type_n[typ] += 1
        for off in (1, 5, 20):
            f = pre_feats(series, s - off, thr) if s - off >= 0 else None
            if f is None:
                continue
            for k, v in f.items():
                if v is not None:
                    out[typ][f'T-{off}'][k].append(v)
    print('类型分布:', dict(type_n), '| 非板入场样本:', len(nonboard_5d))
    if nonboard_5d:
        hits = [h for h, _ in nonboard_5d]
        f5s = [x for _, x in nonboard_5d if x is not None]
        print(f'非板入场后5日内出板率: {100*sum(hits)/len(hits):.1f}%  其f5中位={st.median(f5s):+.2f}% vs 未出板f5中位='
              f'{st.median([x for (h, x) in nonboard_5d if x is not None and not h]):+.2f}%')
    for typ in ('首板启动', '连板加速', '非板入场'):
        print(f'\n== {typ} (n={type_n[typ]}) 启动前特征 ==')
        print(f"{'时点':<6}{'n':>5}{'pos60':>7}{'bias20':>8}{'ret5':>7}{'ret20':>7}{'量比':>6}{'前5日板':>7}{'缩量%':>7}{'高区%':>7}{'创60高%':>8}{'多头%':>7}{'阴线%':>7}")
        for off in ('T-1', 'T-5', 'T-20'):
            g = out[typ].get(off)
            if not g:
                continue
            vr = g['vol_ratio']
            shrink = 100 * sum(1 for x in vr if x < 0.8) / len(vr) if vr else 0
            pos = g['pos60']
            highz = 100 * sum(1 for x in pos if x > 80) / len(pos) if pos else 0
            nh = 100 * sum(g['nh60']) / len(g['nh60']) if g['nh60'] else 0
            mb = 100 * sum(g['ma_bull']) / len(g['ma_bull']) if g['ma_bull'] else 0
            pct = g['pct']
            yin = 100 * sum(1 for x in pct if x < 0) / len(pct) if pct else 0
            print(f"{off:<6}{len(g['pos60']):>5}{st.median(g['pos60']):>7.0f}{st.median(g['bias20']):>+8.1f}"
                  f"{(st.median(g['ret5']) if g['ret5'] else 0):>+7.1f}{(st.median(g['ret20']) if g['ret20'] else 0):>+7.1f}"
                  f"{(st.median(vr) if vr else 0):>6.2f}{(st.median(g['zt_cnt5']) if g['zt_cnt5'] else 0):>7.0f}"
                  f"{shrink:>7.0f}{highz:>7.0f}{nh:>8.0f}{mb:>7.0f}{yin:>7.0f}")
    return out


def part_b():
    names = load_names()
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    cur = con.execute("SELECT code,date,high,close,volume,change_pct FROM stock_daily_meta_qfq "
                      "WHERE date>='20230601' ORDER BY code,date")
    zt_cells = defaultdict(list)     # (cz_band, sealed) -> zt_next bool
    nz_cells = defaultdict(list)     # (高区?, vol_band, 阴阳) -> [zt_next, f1]
    xs_cells = defaultdict(list)     # 蓄势格: (5日缩量, 高区, 无板10日, 阴阳) -> zt_next
    base = defaultdict(list)         # year -> zt_next bool
    code_now, buf = None, []

    def flush(code):
        if not buf:
            return
        thr = thr_of(code, names.get(code, ''))
        D, H, C, V, P = buf
        n = len(D)
        pre = [0.0]
        for v in V:
            pre.append(pre[-1] + (v or 0.0))
        for i in range(59, n - 1):
            d = D[i]
            if d[:4] not in YEARS:
                continue
            p = P[i]
            pn = P[i + 1]
            if p is None or pn is None:
                continue
            zt_next = int(pn >= thr)
            yr = d[:4]
            base[yr].append(zt_next)
            v5 = (pre[i] - pre[i - 5]) / 5 if i >= 5 else 0
            vr = V[i] / v5 if v5 > 0 else None
            if p >= thr:
                sealed = int(H[i] == C[i])
                cz = 0
                for k in range(i, -1, -1):
                    if P[k] is not None and P[k] >= thr:
                        cz += 1
                    else:
                        break
                czb = '首板' if cz == 1 else '2板' if cz == 2 else '3-5板' if cz <= 5 else '6板+'
                zt_cells[(czb, sealed)].append(zt_next)
            else:
                w60 = C[max(0, i - 59):i + 1]
                hi_, lo_ = max(w60), min(w60)
                pos_high = int(hi_ > lo_ and 100.0 * (C[i] - lo_) / (hi_ - lo_) > 80)
                vb = None
                if vr is not None:
                    vb = '<0.8缩量' if vr < 0.8 else '0.8-1.2平量' if vr < 1.2 else '1.2-2放量' if vr < 2 else '>=2爆量'
                if vb:
                    yin = '阴' if p < 0 else '阳'
                    f1 = 100.0 * (C[i + 1] / C[i] - 1)
                    nz_cells[(pos_high, vb, yin)].append((zt_next, f1))
                if i >= 24 and v5 > 0:
                    v20 = (pre[i - 4] - pre[i - 24]) / 20
                    shrink5 = int(v20 > 0 and (pre[i + 1] - pre[i - 4]) / 5 / v20 < 0.8)
                    noboard10 = int(not any(P[k] is not None and P[k] >= thr for k in range(max(0, i - 9), i + 1)))
                    if shrink5 and noboard10:
                        xs_cells[(pos_high, '阴' if p < 0 else '阳')].append(zt_next)
    for code, d, h, c, v, p in cur:
        if code[:2] not in ('60', '00', '30'):
            continue
        if code != code_now:
            flush(code_now)
            code_now, buf = code, ([], [], [], [], [])
        buf[0].append(str(d))
        buf[1].append(h)
        buf[2].append(c)
        buf[3].append(v)
        buf[4].append(p)
    flush(code_now)
    con.close()

    print('\n== Part B 全市场: T+1涨停率基线 ==')
    for yr, xs in base.items():
        print(f'{yr}: 事件{len(xs)} 次日涨停率{100*sum(xs)/len(xs):.2f}%')
    allb = [x for xs in base.values() for x in xs]
    bl = sum(allb) / len(allb)
    print(f'合并基线: {100*bl:.2f}%')

    print('\n== 连板率矩阵: T日板数×封死 → T+1涨停率 ==')
    print(f"{'T日板数':<8}{'封死':<4}{'n':>8}{'T+1涨停率':>10}{'lift':>7}")
    for czb in ('首板', '2板', '3-5板', '6板+'):
        for sd in (1, 0):
            xs = zt_cells.get((czb, sd), [])
            if len(xs) >= 50:
                r = sum(xs) / len(xs)
                print(f'{czb:<8}{"封死" if sd else "炸板":<4}{len(xs):>8}{100*r:>9.1f}%{r/bl:>6.1f}x')

    print('\n== 非板日 lift 矩阵: 位置×量能×阴阳 → T+1涨停率 ==')
    print(f"{'高区':<4}{'量能':<12}{'阴阳':<4}{'n':>9}{'T+1涨停率':>10}{'lift':>7}{'medF1':>8}")
    rows = []
    for (ph, vb, yin), xs in nz_cells.items():
        rows.append((ph, vb, yin, xs))
    rows.sort(key=lambda r: -sum(x[0] for x in r[3]) / len(r[3]) if r[3] else 0)
    for ph, vb, yin, xs in rows:
        if len(xs) < 2000:
            continue
        zr = sum(x[0] for x in xs) / len(xs)
        f1s = [x[1] for x in xs]
        print(f"{'高区' if ph else '非高区':<4}{vb:<12}{yin:<4}{len(xs):>9}{100*zr:>9.2f}%{zr/bl:>6.2f}x{st.median(f1s):>+8.2f}")

    print('\n== 蓄势格: 5日缩量×无板10日 → T+1涨停率 ==')
    for (ph, yin), xs in sorted(xs_cells.items()):
        if len(xs) < 500:
            continue
        zr = sum(xs) / len(xs)
        print(f"{'高区' if ph else '非高区':<4}{'阴' if yin=='阴' else '阳':<4}n={len(xs):>7} T+1涨停率{100*zr:.2f}% lift={zr/bl:.2f}x")
    return bl


if __name__ == '__main__':
    part_a()
    part_b()
