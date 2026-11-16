# -*- coding: utf-8 -*-
"""趋势/主线低吸/波段族 启动前画像与介入体系研究 2026-10-11 (主人令)
与连板链(打板/隔日族)姊妹篇: 主角=淘股吧趋势/低吸/波段大V (素衣/二池/陈小炮/踏雪无痕/名字被你们等)
Part A: dav_merged_pairtrades_v2 131笔完整配对 → 按持有期分层(短持≤2接力/中持3-9波段/长持≥10趋势)
        × 买入特征(pos60/chg买入日涨幅/gear主线强度) × ret
Part B: dav_merged_trades 239笔买入(含f5/f20前瞻) → 买入特征分桶×前向收益(样本加厚)
Part C: 全市场 2024-2025 逐日事件: 胚胎状态(pos60≥60∧0<ret20<20∧近10日无板)×未板温和入场
        → f5/f10/f20 前向 vs 全体基线 (低吸/波段版介入的市场级期望, 对照启动确认体系)
边界: 研究层零交易权; dav台账为交割单挖掘件(样例量级中等); ret已是百分数; 全市场段无成本
"""
import csv
import sqlite3
import statistics as st
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

PAIRS = r'D:\zcodesj\data\dav_merged_pairtrades_v2_20261007.csv'
TRADES = r'D:\zcodesj\data\dav_merged_trades_20261007.csv'
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
YEARS = ('2024', '2025')


def cohort(h):
    return '短持≤2(接力)' if h <= 2 else '中持3-9(波段)' if h <= 9 else '长持≥10(趋势)'


def part_a():
    rows = list(csv.DictReader(open(PAIRS, encoding='utf-8-sig')))
    for r in rows:
        r['hold'] = float(r['hold'])
        r['ret'] = float(r['ret'])
        r['pos60v'] = float(r['pos60']) if r['pos60'] else None
        r['chgv'] = float(r['chg']) if r['chg'] else None
    print('== Part A: 131笔配对 持有期分层 ==')
    print(f"{'层':<12}{'n':>4}{'ret中位':>9}{'ret均值':>9}{'胜率':>6}{'买入pos60':>10}{'买入日chg':>10}")
    for co in ('短持≤2(接力)', '中持3-9(波段)', '长持≥10(趋势)'):
        g = [r for r in rows if cohort(r['hold']) == co]
        if not g:
            continue
        rets = [r['ret'] for r in g]
        p60 = [r['pos60v'] for r in g if r['pos60v'] is not None]
        chg = [r['chgv'] for r in g if r['chgv'] is not None]
        print(f"{co:<12}{len(g):>4}{st.median(rets):>+9.2f}{st.mean(rets):>+9.2f}"
              f"{100*sum(1 for x in rets if x>0)/len(rets):>5.0f}%"
              f"{(st.median(p60) if p60 else 0):>10.0f}{(st.median(chg) if chg else 0):>+10.2f}")
    print('\n-- 长持+中持(波段/趋势族)买入日状态分布 --')
    g = [r for r in rows if r['hold'] >= 3]
    chg = [r['chgv'] for r in g if r['chgv'] is not None]
    p60 = [r['pos60v'] for r in g if r['pos60v'] is not None]
    gear = defaultdict(int)
    for r in g:
        gear[r['gear']] += 1
    print(f'n={len(g)} 买入日chg: 阴线{100*sum(1 for x in chg if x<0)/len(chg):.0f}% 0-5%{100*sum(1 for x in chg if 0<=x<5)/len(chg):.0f}% '
          f'5-板{100*sum(1 for x in chg if 5<=x<9.5)/len(chg):.0f}% 涨停≥9.5{100*sum(1 for x in chg if x>=9.5)/len(chg):.0f}%(近似)')
    print(f'  pos60三分位: {st.quantiles(p60, n=3)} 中位={st.median(p60):.0f}')
    print(f'  gear分布: {dict(gear)}')
    print('\n-- 波段/趋势族 分case战绩 --')
    by = defaultdict(list)
    for r in g:
        by[r['case']].append(r)
    for k, gg in sorted(by.items(), key=lambda x: -len(x[1])):
        rets = [r['ret'] for r in gg]
        print(f'  {k:<10} n={len(gg):>2} 持有中位={st.median([r["hold"] for r in gg]):>3.0f}日 '
              f'ret中位={st.median(rets):>+7.2f}% 胜率={100*sum(1 for x in rets if x>0)/len(rets):.0f}%')


def part_b():
    rows = list(csv.DictReader(open(TRADES, encoding='utf-8-sig')))
    buys = [r for r in rows if r['act'] == '买入' and r['f5'] and r['f20']]
    for r in buys:
        r['f5v'] = float(r['f5']) * 100
        r['f20v'] = float(r['f20']) * 100
        r['pos60v'] = float(r['pos60']) if r['pos60'] else None
        r['chgv'] = float(r['chg']) if r['chg'] else None
    print(f'\n== Part B: 大V买入事件×前瞻 (n={len(buys)}, f5/f20已×100转百分数) ==')
    print(f'全样本: f5中位={st.median([r["f5v"] for r in buys]):+.2f}% f20中位={st.median([r["f20v"] for r in buys]):+.2f}%')
    print(f"{'分桶':<22}{'n':>5}{'f5中位':>8}{'f20中位':>8}{'f20胜率':>7}")
    def bucket(name, fn):
        g = [r for r in buys if fn(r)]
        if len(g) < 15:
            return
        print(f"{name:<22}{len(g):>5}{st.median([r['f5v'] for r in g]):>+8.2f}{st.median([r['f20v'] for r in g]):>+8.2f}"
              f"{100*sum(1 for r in g if r['f20v']>0)/len(g):>6.0f}%")
    bucket('pos60<40(深位)', lambda r: r['pos60v'] is not None and r['pos60v'] < 40)
    bucket('pos60 40-80(中带)', lambda r: r['pos60v'] is not None and 40 <= r['pos60v'] < 80)
    bucket('pos60>=80(高区)', lambda r: r['pos60v'] is not None and r['pos60v'] >= 80)
    bucket('买入日阴线', lambda r: r['chgv'] is not None and r['chgv'] < 0)
    bucket('买入日温和阳0-5', lambda r: r['chgv'] is not None and 0 <= r['chgv'] < 5)
    bucket('买入日强阳5-板', lambda r: r['chgv'] is not None and 5 <= r['chgv'] < 9.5)
    bucket('买入日涨停', lambda r: r['chgv'] is not None and r['chgv'] >= 9.5)
    bucket('gear=强', lambda r: r['gear'] == '强')
    bucket('gear=中', lambda r: r['gear'] == '中')
    bucket('gear=弱', lambda r: r['gear'] == '弱')


def part_c():
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    cur = con.execute("SELECT code,date,close,volume,change_pct FROM stock_daily_meta_qfq "
                      "WHERE date>='20230601' ORDER BY code,date")
    cells = defaultdict(list)   # (格) -> [f5,f10,f20]
    base = defaultdict(list)
    code_now, buf = None, []

    def flush(code):
        if not buf:
            return
        D, C, V, P = buf
        n = len(D)
        pre = [0.0]
        for v in V:
            pre.append(pre[-1] + (v or 0.0))
        for i in range(60, n - 20):
            d = D[i]
            if d[:4] not in YEARS:
                continue
            p = P[i]
            if p is None:
                continue
            need = [C[i + 5], C[i + 10], C[i + 20]]
            if any(x is None or C[i] is None for x in need):
                continue
            w = C[i - 59:i + 1]
            hi_, lo_ = max(w), min(w)
            pos60 = 100.0 * (C[i] - lo_) / (hi_ - lo_) if hi_ > lo_ else None
            ret20 = 100.0 * (C[i] / C[i - 20] - 1)
            zt10 = sum(1 for x in P[max(0, i - 9):i + 1] if x is not None and x >= 9.7)
            if pos60 is None or ret20 is None:
                continue
            embryo = int(pos60 >= 60 and 0 < ret20 < 20 and zt10 == 0)
            mild = int(-3 <= p < 5)
            f5 = 100.0 * (need[0] / C[i] - 1)
            f10 = 100.0 * (need[1] / C[i] - 1)
            f20 = 100.0 * (need[2] / C[i] - 1)
            yr = d[:4]
            base[yr].append(f20)
            if embryo and mild:
                cells[('胚胎×温和', yr)].append((f5, f10, f20))
            if embryo:
                cells[('胚胎全体', yr)].append((f5, f10, f20))

    for code, d, c, v, p in cur:
        if code[0] in '684' or code[:2] == '92':
            continue
        if code != code_now:
            flush(code_now)
            code_now, buf = code, ([], [], [], [])
        buf[0].append(str(d))
        buf[1].append(c)
        buf[2].append(v)
        buf[3].append(p)
    flush(code_now)
    con.close()

    allf = [x for xs in base.values() for x in xs]
    print(f'\n== Part C: 全市场启动前(胚胎)波段持有事件研究 2024-2025 ==')
    print(f'全体基线: n={len(allf)} f20中位={st.median(allf):+.2f}% f20均值={st.mean(allf):+.2f}% 胜率={100*sum(1 for x in allf if x>0)/len(allf):.0f}%')
    print(f"{'格':<10}{'年':<5}{'n':>8}{'f5中位':>8}{'f10中位':>8}{'f20中位':>8}{'f20均值':>8}{'f20胜率':>7}")
    for key in (('胚胎×温和', '2024'), ('胚胎×温和', '2025'), ('胚胎全体', '2024'), ('胚胎全体', '2025')):
        g = cells.get(key, [])
        if len(g) < 500:
            continue
        f5 = [a for a, b, c2 in g]
        f10 = [b for a, b, c2 in g]
        f20 = [c2 for a, b, c2 in g]
        print(f"{key[0]:<10}{key[1]:<5}{len(g):>8}{st.median(f5):>+8.2f}{st.median(f10):>+8.2f}"
              f"{st.median(f20):>+8.2f}{st.mean(f20):>+8.2f}{100*sum(1 for x in f20 if x>0)/len(f20):>6.0f}%")


if __name__ == '__main__':
    part_a()
    part_b()
    part_c()
