# -*- coding: utf-8 -*-
"""连板链启动前体系研究 2026-10-10 (主人令: 最终连板的票启动前什么样? 什么时候介入? 形成体系)
定义: 链=连续收盘涨停序列(pct>=thr), 链高H=链长, 首板日s(s-1必非板), maximal run
Part1 H分层判别: H=1(一日游)/H=2/H=3-4/H=5+ 的首板日前 T-1/T-5/T-20 特征对照
Part2 介入点收益结构 × H分层:
  A=首板前一日收盘(埋伏)  B=首板日收盘(排板,成交率未知如实标注)
  D=首板日开盘(追启动,可执行)  C=先导异动日收盘(首板前5日内放量阳pct∈[5,thr))
Part3 可执行决策面: T-1状态格 × 首板日开盘gap → P(当日收板) lift (当天开盘的唯一决策信息)
宇宙/口径与 entry_portrait 系列对齐; 边界: 研究层零交易权; 排板成交率不可测=上限口径
"""
import sqlite3
import statistics as st
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
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


def state_feats(D, O, H, C, V, P, i, thr):
    """i日状态特征(需i>=59)"""
    if i < 59:
        return None
    cl = C[i]
    w = C[i - 59:i + 1]
    hi_, lo_ = max(w), min(w)
    pos60 = 100.0 * (cl - lo_) / (hi_ - lo_) if hi_ > lo_ else None
    ma20 = st.mean(C[i - 19:i + 1])
    ret5 = 100.0 * (cl / C[i - 5] - 1)
    ret10 = 100.0 * (cl / C[i - 10] - 1)
    ret20 = 100.0 * (cl / C[i - 20] - 1)
    v5 = st.mean([x for x in V[i - 5:i] if x])
    vr = V[i] / v5 if v5 else None
    amp = 100.0 * (H[i] - min(O[i], cl)) / C[i - 1] if C[i - 1] else None  # 当日振幅代理
    zt10 = sum(1 for x in P[max(0, i - 9):i] if x is not None and x >= thr)
    zt60 = sum(1 for x in P[max(0, i - 59):i] if x is not None and x >= thr)
    mab = lambda n: st.mean(C[i - n + 1:i + 1])
    mabull = int(mab(5) > mab(10) > mab(20))
    return {'pos60': pos60, 'bias20': 100.0 * (cl / ma20 - 1), 'ret5': ret5, 'ret10': ret10,
            'ret20': ret20, 'vol_ratio': vr, 'amp': amp, 'zt10': zt10, 'zt60': zt60, 'ma_bull': mabull}


def main():
    names = load_names()
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    cur = con.execute("SELECT code,date,open,high,close,volume,change_pct FROM stock_daily_meta_qfq "
                      "WHERE date>='20230601' ORDER BY code,date")
    chains = []          # (code, s, H, board) maximal run 起点
    decision = defaultdict(lambda: [0, 0])   # (pre-state, gap档) -> [收板数, n]  Part3
    dec_f1 = defaultdict(list)               # (pre-state, gap档) -> open→close 收益
    code_now, buf = None, []

    def flush(code):
        if not buf:
            return
        thr = thr_of(code, names.get(code, ''))
        D, O, H, C, V, P = buf
        n = len(D)
        i = 59
        # 提链: 扫描 maximal consecutive 涨停 run
        while i < n:
            if P[i] is not None and P[i] >= thr and not (i > 0 and P[i - 1] is not None and P[i - 1] >= thr):
                j = i
                while j + 1 < n and P[j + 1] is not None and P[j + 1] >= thr:
                    j += 1
                hh = j - i + 1
                if D[i][:4] in YEARS:
                    chains.append((code, i, hh, j))
                i = j + 1
            else:
                i += 1
        # Part3 决策面: 任意日 T-1状态(高位缓升格) × 当日开盘gap → 当日收板?
        for i in range(60, n):
            if D[i][:4] not in YEARS or P[i] is None or P[i - 1] is None or not O[i] or not C[i - 1]:
                continue
            sf = state_feats(D, O, H, C, V, P, i - 1, thr)
            if sf is None:
                continue
            gap = 100.0 * (O[i] / C[i - 1] - 1)
            gk = '<-2' if gap < -2 else '-2~+2' if gap < 2 else '+2~+5' if gap < 5 else '>+5'
            hi_trend = int(sf['pos60'] > 60 and 0 < sf['ret20'] < 20 and sf['zt10'] == 0)
            zt_today = int(P[i] >= thr)
            key_all = ('全体', gk)
            decision[key_all][1] += 1
            decision[key_all][0] += zt_today
            dec_f1[key_all].append(100.0 * (C[i] / O[i] - 1))
            if hi_trend:
                key = ('高位缓升无板', gk)
                decision[key][1] += 1
                decision[key][0] += zt_today
                dec_f1[key].append(100.0 * (C[i] / O[i] - 1))
    for code, d, o, h, c, v, p in cur:
        if code[:2] not in ('60', '00', '30'):
            continue
        if code != code_now:
            flush(code_now)
            code_now, buf = code, ([], [], [], [], [], [])
        buf[0].append(str(d))
        buf[1].append(o)
        buf[2].append(h)
        buf[3].append(c)
        buf[4].append(v)
        buf[5].append(p)
    flush(code_now)
    con.close()
    print(f'链总数(2024-2025): {len(chains)}')

    # Part1/2 需要回读各链首板日特征 → 按code聚合再读一次(只读链涉及code窗口)
    by_code = defaultdict(list)
    for code, s, hh, j in chains:
        by_code[code].append((s, hh, j))
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    px = {}
    codes6 = sorted(by_code)
    B = 100
    for k in range(0, len(codes6), B):
        chunk = codes6[k:k + B]
        q = ("SELECT code,date,open,high,close,volume,change_pct FROM stock_daily_meta_qfq "
             "WHERE code IN (%s) AND date>='20230601' ORDER BY code,date" % ','.join('?' * len(chunk)))
        for code, d, o, h, c, v, p in con.execute(q, chunk):
            px.setdefault(code, ([], [], [], [], [], []))
            for arr, val in zip(px[code], (str(d), o, h, c, v, p)):
                arr.append(val)
    con.close()

    hb = lambda H: 'H=1' if H == 1 else 'H=2' if H == 2 else 'H=3-4' if H <= 4 else 'H=5+'
    feats = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))   # H层 -> offset -> feat -> vals
    entries = defaultdict(list)                                          # (H层, 介入点) -> dict
    leader_share = defaultdict(lambda: [0, 0])                           # H层 -> [有先导, 链数]
    for code, lst in by_code.items():
        series = px.get(code)
        if series is None:
            continue
        D, O, Hh, C, V, P = series
        n = len(D)
        thr = thr_of(code, names.get(code, ''))
        for s, hh, j in lst:
            if s < 60:
                continue
            layer = hb(hh)
            for off in (1, 5, 20):
                sf = state_feats(D, O, Hh, C, V, P, s - off, thr)
                if sf:
                    for kk, vv in sf.items():
                        if vv is not None:
                            feats[layer][f'T-{off}'][kk].append(vv)
            # 介入点收益
            eA = C[s - 1]
            eB = C[s]
            eD = O[s]
            f2 = C[s + 2] if s + 2 < n else None
            f5 = C[s + 5] if s + 5 < n else None
            top = max(Hh[s:min(j + 1, n)]) if j < n else Hh[s]
            rec = {'layer': layer}
            if eA:
                rec['A_day'] = 100.0 * (C[s] / eA - 1)
                if f2:
                    rec['A_f2'] = 100.0 * (f2 / eA - 1)
                if f5:
                    rec['A_f5'] = 100.0 * (f5 / eA - 1)
            if eB:
                if f2:
                    rec['B_f2'] = 100.0 * (f2 / eB - 1)
                if f5:
                    rec['B_f5'] = 100.0 * (f5 / eB - 1)
            if eD:
                rec['D_day'] = 100.0 * (C[s] / eD - 1)
                rec['D_win'] = int(P[s] >= thr)
                if f2:
                    rec['D_f2'] = 100.0 * (f2 / eD - 1)
                if f5:
                    rec['D_f5'] = 100.0 * (f5 / eD - 1)
            entries[layer].append(rec)
            # 先导异动: s前5日内 放量阳 pct∈[5,thr)
            has_leader = 0
            for k in range(max(60, s - 5), s):
                pk = P[k]
                if pk is not None and 5 <= pk < thr:
                    v5 = st.mean([x for x in V[k - 5:k] if x]) if k >= 5 else 0
                    if v5 and V[k] / v5 >= 1.5:
                        has_leader = 1
                        break
            leader_share[layer][1] += 1
            leader_share[layer][0] += has_leader

    print('\n== Part1 链高分层 × 启动前特征 (中位数) ==')
    layers = ['H=1', 'H=2', 'H=3-4', 'H=5+']
    for off in ('T-1', 'T-5', 'T-20'):
        print(f'\n-- {off} --')
        print(f"{'层':<7}{'n':>6}{'pos60':>7}{'bias20':>8}{'ret5':>7}{'ret10':>7}{'ret20':>8}{'量比':>6}{'振幅%':>7}{'10日板':>7}{'60日板':>7}{'多头%':>7}")
        for layer in layers:
            g = feats.get(layer, {}).get(off)
            if not g or not g['pos60']:
                continue
            mb = 100 * sum(g['ma_bull']) / len(g['ma_bull'])
            print(f"{layer:<7}{len(g['pos60']):>6}{st.median(g['pos60']):>7.0f}{st.median(g['bias20']):>+8.1f}"
                  f"{st.median(g['ret5']):>+7.1f}{st.median(g['ret10']):>+7.1f}{st.median(g['ret20']):>+8.1f}"
                  f"{st.median(g['vol_ratio']):>6.2f}{st.median(g['amp']):>7.1f}{st.median(g['zt10']):>7.0f}"
                  f"{st.median(g['zt60']):>7.0f}{mb:>7.0f}")

    print('\n== Part2 介入点收益结构 × 链高分层 ==')
    print(f"{'层':<7}{'n':>6}{'A埋伏当日':>10}{'A_f2':>8}{'A_f5':>8}{'B排板_f2':>10}{'B排板_f5':>10}{'D开盘当日':>11}{'D胜率':>7}{'D_f2':>8}{'D_f5':>8}{'先导占比':>8}")
    for layer in layers:
        rs = entries.get(layer, [])
        if len(rs) < 30:
            continue
        def med(key):
            a = [r[key] for r in rs if key in r]
            return st.median(a) if a else None
        dw = 100 * sum(r['D_win'] for r in rs) / len(rs)
        ls = leader_share[layer]
        row = [layer, len(rs)]
        for key in ('A_day', 'A_f2', 'A_f5', 'B_f2', 'B_f5', 'D_day'):
            m = med(key)
            row.append(f'{m:+.2f}' if m is not None else '—')
        row.append(f'{dw:.0f}%')
        for key in ('D_f2', 'D_f5'):
            m = med(key)
            row.append(f'{m:+.2f}' if m is not None else '—')
        row.append(f'{100*ls[0]/ls[1]:.0f}%')
        print(f"{row[0]:<7}{row[1]:>6}{row[2]:>10}{row[3]:>8}{row[4]:>8}{row[5]:>10}{row[6]:>10}{row[7]:>11}{row[8]:>7}{row[9]:>8}{row[10]:>8}{row[11]:>8}")

    print('\n== Part3 决策面: T-1状态 × 当日开盘gap → P(当日收板) ==')
    tot = decision.get(('全体', '-2~+2'), [0, 1])[1] + decision.get(('全体', '<-2'), [0, 1])[1] + \
        decision.get(('全体', '+2~+5'), [0, 1])[1] + decision.get(('全体', '>+5'), [0, 1])[1]
    hit = sum(decision.get(('全体', g), [0, 0])[0] for g in ('<-2', '-2~+2', '+2~+5', '>+5'))
    bl = hit / tot if tot else 0
    print(f'基线: 全体日 P(当日收板)={100*bl:.2f}% (n={tot})')
    for pre in ('高位缓升无板', '全体'):
        for gk in ('<-2', '-2~+2', '+2~+5', '>+5'):
            hz, nz = decision.get((pre, gk), [0, 0])
            if nz < 2000:
                continue
            r = hz / nz
            f1s = dec_f1.get((pre, gk), [])
            mf = st.median(f1s) if f1s else 0
            print(f"{pre:<8} gap{gk:<7} n={nz:>8} P收板={100*r:>5.2f}% lift={r/bl:>5.2f}x 开盘买入当日med={mf:+.2f}%")


if __name__ == '__main__':
    main()
