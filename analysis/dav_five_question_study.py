# -*- coding: utf-8 -*-
"""大V交割单五问逐笔拆解 2026-10-11 (主人令: 对典型交割单做全量完整逐笔拆解)
五问框架(主人体系文档): Q1大盘可操作窗口 / Q2主线板块共振 / Q3个股形态量能均线(左侧vs右侧)
                        / Q4分批(台账近似) / Q5止损结构(结果反推)
样本: dav_merged_pairtrades_v2 全部131笔配对(9家族2023-2026)
量化口径:
  Q1 大盘三条件(满足>=2=窗口): a上证收盘>MA20 b近10日不创20日新低 c全市场近5日均上涨占比>=50%
  Q3 个股: 站上MA20/MA20拐头(>5日前)/多头排列/底部抬高/量比; 分层:
     右侧回踩=近10日有板且近3日回调且close>MA20*0.97; 左侧埋伏=近10日无板且|bias20|<5%且量比<1.2;
     追高=买入日涨停或chg>=7; 其余=其他
  Q4 分批近似=同case+code 5日内>=2条买入(trades表); 台账无逐笔分批比例=如实N/A
  Q5 亏损单亏损幅度分布(金额止损-2~-3%集中度检验); 盈亏比=盈利单均值/亏损单均值
输出: data/dav_five_question_per_trade_20261011.csv(131行逐笔) + stdout汇总
边界: 研究层零交易权; Q2主线强度用挖掘件gear字段(人工标注)+行业动量代理; 分批为近似
"""
import csv
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')

from tdx_reader import read_day

PAIRS = r'D:\zcodesj\data\dav_merged_pairtrades_v2_20261007.csv'
TRADES = r'D:\zcodesj\data\dav_merged_trades_20261007.csv'
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
IND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
OUT_CSV = r'D:\zcodesj\data\dav_five_question_per_trade_20261011.csv'


def build_market_env():
    """Q1: 上证MA20/新低 + 全市场上涨占比/跌停家数 → {date: (sh>ma20, 不创新低, 涨占比5日均, 跌停5日均)}"""
    # 上证 (read_day元组: date,o,h,l,c,amt,vol)
    sh = read_day('sh', '000001')
    sh_d = [r[0] for r in sh]
    sh_c = [r[4] for r in sh]
    sh_l = [r[3] for r in sh]
    env = {}
    for i in range(20, len(sh_d)):
        d = sh_d[i]
        ma20 = st.mean(sh_c[i - 19:i + 1])
        above = sh_c[i] > ma20
        lo10 = min(sh_l[i - 9:i + 1])
        lo_prev = min(sh_l[i - 19:i - 9]) if i >= 20 else None
        nolow = lo_prev is not None and lo10 > lo_prev
        env[str(d)] = [above, nolow]
    # 全市场宽度(2023+)
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    cur = con.execute("SELECT date, SUM(change_pct>0), SUM(change_pct<=-9.7), COUNT(*) "
                      "FROM stock_daily_meta_qfq WHERE date>='20230101' AND change_pct IS NOT NULL "
                      "GROUP BY date ORDER BY date")
    breadth = {}
    for d, up, dn, tot in cur:
        breadth[str(d)] = (up / tot if tot else 0, dn)
    con.close()
    # 近5日均
    bd = sorted(breadth)
    env2 = {}
    for k, d in enumerate(bd):
        w = bd[max(0, k - 4):k + 1]
        up5 = st.mean([breadth[x][0] for x in w])
        dn5 = st.mean([breadth[x][1] for x in w])
        env2[d] = (env.get(d, [False, False])[0], env.get(d, [False, False])[1], up5, dn5)
    return env2


def build_ind_momentum():
    """行业20日动量代理: ths行业指数(industry_kline_ths)最新期滚动 → 简化用fund库industry日K不可得时None"""
    try:
        con = sqlite3.connect(r'file:D:\workbuddyspace\databases\sw_industry.db?mode=ro', uri=True)
        rows = con.execute("SELECT industry_name, trade_date, close FROM industry_kline_ths ORDER BY industry_name, trade_date").fetchall()
        con.close()
        by = defaultdict(list)
        for nm, d, c in rows:
            by[nm].append((str(d), c))
        mom = {}
        for nm, ser in by.items():
            if len(ser) < 21:
                continue
            c0 = ser[-1][1]
            c20 = ser[-21][1]
            mom[nm] = 100.0 * (c0 / c20 - 1) if c20 else None
        return mom
    except sqlite3.Error:
        return {}


def stock_series(code):
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    rows = con.execute("SELECT date,close,volume,change_pct FROM stock_daily_meta_qfq WHERE code=? AND date>='20220101' ORDER BY date",
                       (code,)).fetchall()
    con.close()
    return rows


def five_q(rows, buy_i, vol_ratio):
    """Q3量化: 返回 dict"""
    ds = [r[0] for r in rows]
    cs = [r[1] for r in rows]
    vs = [r[2] or 0 for r in rows]
    ps = [r[3] for r in rows]
    i = buy_i
    c = cs[i]
    ma20 = st.mean(cs[i - 19:i + 1])
    ma20_prev = st.mean(cs[i - 24:i - 4])
    ma5 = st.mean(cs[i - 4:i + 1])
    ma10 = st.mean(cs[i - 9:i + 1])
    q = {
        'above_ma20': int(c > ma20),
        'ma20_up': int(ma20 > ma20_prev),
        'bull_align': int(ma5 > ma10 > ma20),
        'bias20': round(100.0 * (c / ma20 - 1), 2),
        'vol_ratio': round(vol_ratio, 2),
        'pct': ps[i],
    }
    # 底部抬高: 近10日最低close > 前10日最低close
    q['low_rise'] = int(min(cs[i - 9:i + 1]) > min(cs[i - 19:i - 9]))
    # 近10日有板
    zt10 = sum(1 for k in range(max(0, i - 9), i + 1) if ps[k] is not None and ps[k] >= 9.7)
    ret3 = 100.0 * (c / cs[i - 3] - 1) if i >= 3 else None
    if ps[i] is not None and ps[i] >= 9.5:
        q['style'] = '追高(涨停买入)'
    elif q['pct'] is not None and q['pct'] >= 7:
        q['style'] = '追高(≥7%)'
    elif zt10 > 0 and ret3 is not None and ret3 < 0 and c > ma20 * 0.97:
        q['style'] = '右侧回踩'
    elif zt10 == 0 and abs(q['bias20']) < 5 and vol_ratio < 1.2:
        q['style'] = '左侧埋伏'
    else:
        q['style'] = '其他'
    q['zt10'] = zt10
    return q


def main():
    env = build_market_env()
    ind_mom = build_ind_momentum()
    pairs = list(csv.DictReader(open(PAIRS, encoding='utf-8-sig')))
    trades = list(csv.DictReader(open(TRADES, encoding='utf-8-sig')))
    # Q4 分批近似: case+code 5日内多次买入
    buys_by_key = defaultdict(list)
    for r in trades:
        if r['act'] in ('买入', '加仓') and r['date']:
            d = r['date'].split('.')[0]
            buys_by_key[(r['case'], r['code'])].append(d)
    # 行业映射
    ind_map = {}
    try:
        con = sqlite3.connect(f'file:{IND_DB}?mode=ro', uri=True)
        for code, ind in con.execute('SELECT code, industry FROM industry_map_ths'):
            ind_map[code] = ind
        con.close()
    except sqlite3.Error:
        pass

    out_rows = []
    cache = {}
    for r in pairs:
        code = r['code']
        buy_d = r['buy_d']
        if not buy_d or len(buy_d) != 8:
            continue
        if code not in cache:
            cache[code] = stock_series(code)
        rows = cache[code]
        if not rows:
            continue
        ds = [x[0] for x in rows]
        i = next((k for k, d in enumerate(ds) if d == buy_d), None)
        if i is None or i < 25:
            continue
        v5 = st.mean([x[2] or 0 for x in rows[i - 5:i]])
        vr = (rows[i][2] or 0) / v5 if v5 else None
        q = five_q(rows, i, vr if vr else 0)
        # Q1
        e = env.get(buy_d)
        if e:
            cond = sum([e[0], e[1], e[2] >= 0.5])
            q1 = f'窗口{cond}/3' + ('✓' if cond >= 2 else '✗')
            q1_detail = f"MA20={'上' if e[0] else '下'} 不创新低={'是' if e[1] else '否'} 涨占比5日均={e[2]*100:.0f}%"
        else:
            q1, q1_detail = 'N/A', ''
        # Q2
        ind = ind_map.get(code, '')
        imom = ind_mom.get(ind)
        q2 = f"gear={r['gear']}" + (f' 行业动量={imom:+.1f}%' if imom is not None else '')
        # Q4
        blist = sorted(buys_by_key.get((r['case'], code), []))
        batches = 1
        for a, b in zip(blist, blist[1:]):
            gap = (int(b[:4]) - int(a[:4])) * 10000 + (int(b[4:6]) - int(a[4:6])) * 100 + (int(b[6:]) - int(a[6:]))
            if gap > 5:
                batches += 1
        q4 = f'{batches}批' + ('(近似)' if batches > 1 else '')
        # Q5
        ret = float(r['ret'])
        q5 = f'止损{ret:+.1f}%' if ret < 0 else f'止盈{ret:+.1f}%(持有{int(r["hold"])}日)'
        out_rows.append({
            'case': r['case'], 'code': code, 'buy_d': buy_d, 'sell_d': r['sell_d'], 'ret': ret, 'hold': int(r['hold']),
            'Q1_窗口': q1, 'Q1_明细': q1_detail, 'Q2_主线': q2, 'Q3_style': q['style'],
            'Q3_站上MA20': q['above_ma20'], 'Q3_MA20拐头': q['ma20_up'], 'Q3_多头排列': q['bull_align'],
            'Q3_底部抬高': q['low_rise'], 'Q3_bias20': q['bias20'], 'Q3_量比': q['vol_ratio'],
            'Q4_分批': q4, 'Q5_止损止盈': q5,
        })
    keys = list(out_rows[0].keys())
    with open(OUT_CSV, 'w', encoding='utf-8-sig', newline='') as fp:
        w = csv.DictWriter(fp, fieldnames=keys)
        w.writeheader()
        w.writerows(out_rows)
    print(f'逐笔拆解落盘: {OUT_CSV} ({len(out_rows)}笔)')

    # 汇总验证
    n = len(out_rows)
    win = [r for r in out_rows if r['ret'] > 0]
    lose = [r for r in out_rows if r['ret'] <= 0]
    print(f'\n== 汇总验证 (n={n}) ==')
    c1 = sum(1 for r in out_rows if '✓' in r['Q1_窗口'])
    print(f'Q1 大盘窗口命中率: {100*c1/n:.0f}% (盈单{100*sum(1 for r in win if "✓" in r["Q1_窗口"])/max(1,len(win)):.0f}% vs 亏单{100*sum(1 for r in lose if "✓" in r["Q1_窗口"])/max(1,len(lose)):.0f}%)')
    from collections import Counter
    sc = Counter(r['Q3_style'] for r in out_rows)
    print(f'Q3 风格分布: {dict(sc)}')
    print(f"{'风格':<14}{'n':>4}{'ret中位':>9}{'胜率':>6}")
    for s_ in ('右侧回踩', '左侧埋伏', '追高(涨停买入)', '追高(≥7%)', '其他'):
        g = [r for r in out_rows if r['Q3_style'] == s_]
        if len(g) < 5:
            continue
        print(f"{s_:<14}{len(g):>4}{st.median([r['ret'] for r in g]):>+9.2f}{100*sum(1 for r in g if r['ret']>0)/len(g):>5.0f}%")
    # 风格×家族交叉(策略×风格交互原则)
    print('风格×家族交叉(n>=5的格):')
    cc = defaultdict(list)
    for r in out_rows:
        cc[(r['case'], r['Q3_style'])].append(r)
    for (case, s_), g in sorted(cc.items(), key=lambda x: -len(x[1])):
        if len(g) < 5:
            continue
        print(f"  {case:<10}{s_:<12} n={len(g):>3} ret中位={st.median([r['ret'] for r in g]):>+7.2f}% 胜率={100*sum(1 for r in g if r['ret']>0)/len(g):.0f}%")
    # 三层共振
    res3 = [r for r in out_rows if '✓' in r['Q1_窗口'] and r['Q3_站上MA20'] and r['Q2_主线'].startswith('gear=强')]
    print(f'三层共振(大盘✓+站MA20+gear强): n={len(res3)} ret中位={st.median([r["ret"] for r in res3]):+.2f}% '
          f'胜率={100*sum(1 for r in res3 if r["ret"]>0)/len(res3):.0f}%' if res3 else '三层共振: n=0')
    non3 = [r for r in out_rows if r not in res3]
    if non3:
        print(f'非共振: n={len(non3)} ret中位={st.median([r["ret"] for r in non3]):+.2f}% 胜率={100*sum(1 for r in non3 if r["ret"]>0)/len(non3):.0f}%')
    # Q5 止损结构
    loss_vals = sorted(r['ret'] for r in lose)
    if loss_vals:
        print(f'Q5 止损结构: 亏损单n={len(loss_vals)} 中位={st.median(loss_vals):+.2f}% '
              f'-3%内占比={100*sum(1 for x in loss_vals if x>=-3)/len(loss_vals):.0f}% '
              f'-5%内={100*sum(1 for x in loss_vals if x>=-5)/len(loss_vals):.0f}%')
    if win and lose:
        aw = st.mean([r['ret'] for r in win])
        al = abs(st.mean([r['ret'] for r in lose]))
        print(f'盈亏比: 盈单均值{aw:+.2f}%/亏单均值-{al:.2f}% = {aw/al:.2f}:1')


if __name__ == '__main__':
    main()
