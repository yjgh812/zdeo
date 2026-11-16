# -*- coding: utf-8 -*-
"""入场画像全市场泛化验证 2026-10-10 (主人「继续」令)
问题: 家族段收益含其卖出技能, 画像格能否用于选股须看【全市场同格前瞻收益】。
设计: qfq全市场 2024(家族期外OOS)+2025(同期非家族票), 逐日事件按画像口径分类:
  confirm_band(涨停/强势阳5-板/温和阳/阴线) × vol_band(量比=vol/前5日均量, 0.8/1.2/2分档)
  × 涨停日可成交代理: 封死(high==close, 疑不可买) vs 开板过(high>close, 盘中有成交机会)
  × consec_zt(入场日起连板数) 口径与 entry_portrait_db_builder 逐字对齐(9.85/19.85/4.85ST)
宇宙: 剔科创68/北交8x4x92/次新(<60bar, 与builder一致); ST按名称阈值单独分层
边界: 研究层零交易权; f1=入场日收盘锚(封死=不可成交幻觉), f1n=次日开盘→次日收盘(封死日现实追买代理)
      qfq无退市股=幸存者偏差; 停牌股f1取下一有bar日
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


def band_vol(v):
    if v is None:
        return None
    return '<0.8缩量' if v < 0.8 else '0.8-1.2平量' if v < 1.2 else '1.2-2放量' if v < 2 else '>=2爆量'


def main():
    names = load_names()
    print(f'名称映射: {len(names)}只(含ST历史名)')

    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    cur = con.execute("SELECT code,date,open,high,close,volume,change_pct FROM stock_daily_meta_qfq "
                      "WHERE date>='20230601' ORDER BY code,date")
    # cells: (year, confirm, vol_band, sealed) -> [f1...]; zt另存 consec_zt 分层与 f1n
    cells = defaultdict(list)
    zt_cz = defaultdict(list)      # (year, consec_zt_band, sealed) -> [(f1, f1n)]
    n_events = 0
    code_now, dates = None, []
    buf = []

    def flush(code):
        nonlocal n_events
        if not buf:
            return
        nm = names.get(code, '')
        thr = thr_of(code, nm)
        is_st = 'ST' in nm.upper()
        D, O, H, C, V, P = buf
        n = len(D)
        # vol5 前缀和滚动
        pre = [0.0]
        for v in V:
            pre.append(pre[-1] + (v or 0.0))
        for i in range(59, n):
            d = D[i]
            if d[:4] not in YEARS:
                continue
            p = P[i]
            if p is None:
                continue
            v5 = (pre[i] - pre[i - 5]) / 5 if i >= 5 else None
            vr = round(V[i] / v5, 2) if v5 and v5 > 0 else None
            if p >= thr:
                confirm = '涨停'
                sealed = int(H[i] == C[i])
            elif p >= 5:
                confirm, sealed = '强势阳线5-板', 0
            elif p >= 0:
                confirm, sealed = '温和阳0-5', 0
            else:
                confirm, sealed = '阴线<0', 0
            f1 = round(100.0 * (C[i + 1] / C[i] - 1), 2) if i + 1 < n else None
            f5 = round(100.0 * (C[i + 5] / C[i] - 1), 2) if i + 5 < n else None
            # 封死日现实追买代理: 次日开盘买→次日收盘卖
            f1n = round(100.0 * (C[i + 1] / O[i + 1] - 1), 2) if i + 1 < n and O[i + 1] else None
            cz = 0
            for k in range(i, -1, -1):
                if P[k] is not None and P[k] >= thr:
                    cz += 1
                else:
                    break
            czb = ('首板' if cz == 1 else '2板' if cz == 2 else '3-5板' if cz <= 5 else '6板+' if cz >= 6 else '非板')
            n_events += 1
            yr = d[:4]
            vb = band_vol(vr)
            uni = 'ST' if is_st else ('20cm' if code[:3] in ('300', '301') else 'main')
            if f1 is not None:
                cells[(yr, confirm, vb, sealed, uni)].append(f1)
            if confirm == '涨停' and f1 is not None and f5 is not None:
                zt_cz[(yr, czb, sealed)].append((f1, f5, f1n))

    for code, d, o, h, c, v, p in cur:
        if code[:2] not in ('60', '00', '30'):
            continue
        if code != code_now:
            flush(code_now)
            code_now, buf = code, ([], [], [], [], [], [])
            dates = buf[0]
        dates.append(str(d))
        buf[1].append(o)
        buf[2].append(h)
        buf[3].append(c)
        buf[4].append(v)
        buf[5].append(p)
    flush(code_now)
    con.close()
    print(f'事件总数: {n_events}')

    def row(yr, confirm, vb, sealed, uni='main'):
        a = cells.get((yr, confirm, vb, sealed, uni), [])
        if not a:
            return None
        return (len(a), 100 * sum(1 for x in a if x > 0) / len(a), st.median(a), st.mean(a))

    print('\n== 涨停日 × 量能 × 可成交代理 (main板, f1=收盘锚) ==')
    print(f"{'年':<5}{'量能':<12}{'封死':<4}{'n':>7}{'F1胜率':>8}{'medF1':>8}{'均F1':>7}")
    for yr in YEARS:
        for vb in ('<0.8缩量', '0.8-1.2平量', '1.2-2放量', '>=2爆量'):
            for sd in (0, 1):
                r = row(yr, '涨停', vb, sd)
                if r and r[0] >= 100:
                    print(f'{yr:<5}{vb:<12}{"封死" if sd else "开板过":<4}{r[0]:>7}{r[1]:>7.1f}%{r[2]:>+8.2f}{r[3]:>+7.2f}')
        a_all = [x for (y, cf, vb, sd, u), xs in cells.items() if y == yr and cf == '涨停' and u == 'main' for x in xs]
        if a_all:
            print(f'{yr:<5}{"涨停全量能":<12}{"—":<4}{len(a_all):>7}{100*sum(1 for x in a_all if x>0)/len(a_all):>7.1f}%{st.median(a_all):>+8.2f}{st.mean(a_all):>+7.2f}')

    print('\n== 涨停日 × 连板高度 (main板, 合并两年) ==')
    agg = defaultdict(list)
    for (yr, czb, sd), xs in zt_cz.items():
        agg[(czb, sd)] += xs
    print(f"{'板数':<8}{'封死':<4}{'n':>7}{'F1胜率':>8}{'medF1':>8}{'medF5':>8}{'medF1n次日开盘买':>14}")
    for czb in ('首板', '2板', '3-5板', '6板+'):
        for sd in (0, 1):
            xs = agg.get((czb, sd), [])
            if len(xs) >= 100:
                f1s = [a for a, b, c2 in xs]
                f5s = [b for a, b, c2 in xs]
                f1ns = [c2 for a, b, c2 in xs if c2 is not None]
                med_f1n = st.median(f1ns) if f1ns else None
                print(f'{czb:<8}{"封死" if sd else "开板过":<4}{len(xs):>7}{100*sum(1 for x in f1s if x>0)/len(f1s):>7.1f}%{st.median(f1s):>+8.2f}{st.median(f5s):>+8.2f}'
                      f'{(f"{med_f1n:+.2f}" if med_f1n is not None else "N/A"):>14}')

    print('\n== 非涨停格 × 量能 (main板, 合并两年) ==')
    agg2 = defaultdict(list)
    for (yr, cf, vb, sd, u), xs in cells.items():
        if cf != '涨停' and u == 'main':
            agg2[(cf, vb)] += xs
    print(f"{'确认':<14}{'量能':<12}{'n':>7}{'F1胜率':>8}{'medF1':>8}{'均F1':>7}")
    for cf in ('强势阳线5-板', '温和阳0-5', '阴线<0'):
        for vb in ('<0.8缩量', '0.8-1.2平量', '1.2-2放量', '>=2爆量'):
            a = agg2.get((cf, vb), [])
            if len(a) >= 200:
                print(f'{cf:<14}{vb:<12}{len(a):>7}{100*sum(1 for x in a if x>0)/len(a):>7.1f}%{st.median(a):>+8.2f}{st.mean(a):>+7.2f}')

    print('\n== 20cm(创业板) 涨停日合并量能 ==')
    for yr in YEARS:
        a = [x for (y, cf, vb, sd, u), xs in cells.items() if y == yr and cf == '涨停' and u == '20cm' for x in xs]
        if a:
            print(f'{yr}: n={len(a)} F1胜率{100*sum(1 for x in a if x>0)/len(a):.1f}% medF1{st.median(a):+.2f} 均F1{st.mean(a):+.2f}')

    import json
    out = {str(k): {'n': len(v), 'med': st.median(v), 'mean': round(st.mean(v), 2),
                    'win': round(100 * sum(1 for x in v if x > 0) / len(v), 1)} for k, v in cells.items() if len(v) >= 50}
    json.dump(out, open(r'D:\zcodesj\tmp\entry_portrait_market_validate_20261010.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n已存 tmp/entry_portrait_market_validate_20261010.json')


if __name__ == '__main__':
    main()
