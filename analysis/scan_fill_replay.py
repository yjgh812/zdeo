# -*- coding: utf-8 -*-
"""扫板全量触板日回放 2026-10-10 (修正 chain_minute_replay 的成功日幸存偏差)
策略: 触板前一分钟买入(确定成交), 样本=2026年全部盘中触涨停日(含炸板收阴失败日)
真期望 = P(封死|触板)×E[收益|封死] + P(炸板收阴|触板)×E[收益|失败]
同口径: lc1自含raw昨收定涨停价; 前瞻f1=当日raw段×qfq比值段; 排板乐观上界(开板即成交)并列
宇宙: 剔科创68/北交8x4x92; ST按名称5%板
"""
import sqlite3
import struct
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')

from tdx_reader import _MIN_FMT, RECORD_SIZE, DEFAULT_TDX_ROOT

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ZT_DB = r'D:\zcodesj\data\zt_pool_history.db'
NAME_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
ROOT = Path(DEFAULT_TDX_ROOT)
D_MIN, D_MAX = 20260201, 20261009
EPS = 0.001


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


def main():
    names = load_names()
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    qfq = {}
    for code, d, c, p in con.execute("SELECT code,date,close,change_pct FROM stock_daily_meta_qfq "
                                     "WHERE date>='20251101' ORDER BY code,date"):
        if code[0] in '684' or code[:2] == '92':
            continue
        qfq.setdefault(code, ([], [], []))
        qfq[code][0].append(str(d))
        qfq[code][1].append(c)
        qfq[code][2].append(p)
    con.close()

    stocks = sorted(qfq)
    rows = []
    n_files = 0
    for si, code in enumerate(stocks):
        m = 'sh' if code.startswith('6') else 'sz'
        p = ROOT / m / 'minline' / f'{m}{code}.lc1'
        if not p.is_file():
            continue
        n_files += 1
        if n_files % 800 == 0:
            print(f'...已读{n_files}只/触板日{len(rows)}', flush=True)
        thr = thr_of(code, names.get(code, ''))
        n = p.stat().st_size // RECORD_SIZE
        with open(p, 'rb') as f:
            buf = f.read(n * RECORD_SIZE)
        prev_close = None
        prev_day_close = None
        cur_day = None
        bars = []
        ds, cs, ps = qfq[code]
        qidx = {d: k for k, d in enumerate(ds)}

        def flush_day(day, pclose):
            if not bars or not pclose or not (D_MIN <= day <= D_MAX):
                return
            limit = round(pclose * (1 + thr / 100) + 1e-9, 2)
            touch = next((k for k, (t, h, l, c) in enumerate(bars) if h >= limit - EPS), None)
            if touch is None:
                return
            last_c = bars[-1][3]
            sealed = last_c >= limit - EPS
            entry = bars[0][1] if touch == 0 else bars[touch - 1][3]
            if not entry:
                return
            day_ret = 100.0 * (last_c / entry - 1)
            t0 = bars[touch][0]
            tcat = '早板<10:00' if t0 < 1000 else '上午10-11:30' if t0 < 1131 else '午后13-14' if t0 < 1400 else '尾盘>=14:00'
            k = qidx.get(str(day))
            if k is None or k + 1 >= len(cs) or cs[k] is None or cs[k + 1] is None:
                return
            f1 = day_ret + 100.0 * (cs[k + 1] / cs[k] - 1)  # 近似合成(除权日误差<当日振幅, 注记)
            reopens = 0
            on = True
            for (t, h, l, c) in bars[touch:]:
                if c >= limit - EPS and not on:
                    on = True
                elif c < limit - EPS and on:
                    on = False
                    reopens += 1
            rows.append({'date': day, 'sealed': int(sealed), 'tcat': tcat, 'reopens': reopens,
                         'day_ret': day_ret, 'f1': f1,
                         'pa_filled': int(reopens >= 1),
                         'pa_f1': (100.0 * (last_c / limit - 1)) + 100.0 * (cs[k + 1] / cs[k] - 1) if reopens >= 1 else None})

        for b in struct.iter_unpack(_MIN_FMT, buf):
            dnum, tmin, o, h, l, c, amt, vol, _ = b
            if dnum == 0 or o <= 0:
                continue
            year = dnum // 2048 + 2004
            md = dnum % 2048
            day = year * 10000 + md // 100 * 100 + md % 100
            if day != cur_day:
                if cur_day is not None:
                    flush_day(cur_day, prev_day_close)  # 昨收=上一日末分钟收盘
                prev_day_close = prev_close  # 当日收盘=次日昨收
                bars = []
                cur_day = day
            hh, mm = divmod(tmin, 60)
            bars.append((hh * 100 + mm, h, l, c))
            prev_close = c
        if cur_day is not None:
            flush_day(cur_day, prev_close)
    print(f'完成: 读{n_files}只, 触板日{len(rows)}')

    sealed_g = [r for r in rows if r['sealed']]
    fail_g = [r for r in rows if not r['sealed']]
    print(f'\n== 触板日决策树 (n={len(rows)}, 202602~202610) ==')
    print(f'封死率: {100*len(sealed_g)/len(rows):.1f}%  炸板收阴率: {100*len(fail_g)/len(rows):.1f}%')
    print(f"分支       {'n':>7}{'当日med':>9}{'当日均值':>9}{'当日胜率':>9}{'f1med':>8}{'f1均值':>8}{'f1胜率':>8}")
    for tag, g in ('封死分支', sealed_g), ('炸板失败分支', fail_g), ('全量扫板策略', rows):
        dr = [r['day_ret'] for r in g]
        f1 = [r['f1'] for r in g]
        print(f"{tag:<9}{len(g):>7}{st.median(dr):>+9.2f}{st.mean(dr):>+9.2f}"
              f"{100*sum(1 for x in dr if x>0)/len(dr):>8.0f}%{st.median(f1):>+8.2f}{st.mean(f1):>+8.2f}"
              f"{100*sum(1 for x in f1 if x>0)/len(f1):>7.0f}%")

    print('\n== 封死分支 × 封板时段: 当日/f1 与次段结构 ==')
    for tcat in ('早板<10:00', '上午10-11:30', '午后13-14', '尾盘>=14:00'):
        g = [r for r in sealed_g if r['tcat'] == tcat]
        if len(g) < 100:
            continue
        f1 = [r['f1'] for r in g]
        print(f'{tcat:<12} n={len(g):>5} 当日med={st.median([r["day_ret"] for r in g]):+6.2f}% f1med={st.median(f1):+6.2f}% f1均值={st.mean(f1):+6.2f}%')

    print('\n== 排板乐观上界对照 ==')
    pa = [r for r in rows if r['pa_filled']]
    paf = [r['pa_f1'] for r in pa]
    print(f'成交率={100*len(pa)/len(rows):.0f}%  成交日f1 med={st.median(paf):+.2f}% 均值={st.mean(paf):+.2f}%  组合期望={st.mean(paf)*len(pa)/len(rows):+.2f}%/信号')
    f1a = [r['f1'] for r in rows]
    print(f'扫板组合期望(f1均值)={st.mean(f1a):+.2f}%/信号 (对比排板上界, 扫板无miss)')

    print('\n== 封死 vs 失败 的当日形态(执行者盘口差异) ==')
    print(f"失败分支炸板日当日med={st.median([r['day_ret'] for r in fail_g]):+.2f}% — 即扫板失败的代价")
    import json
    json.dump(rows, open(r'D:\zcodesj\tmp\scan_fill_replay_20261010.json', 'w', encoding='utf-8'), ensure_ascii=False)
    print('已存 tmp/scan_fill_replay_20261010.json')


if __name__ == '__main__':
    main()
