# -*- coding: utf-8 -*-
"""入场画像数据库 builder + query(交割单全家族 × qfq) 2026-10-10
数据源: 六家交割单台账(data/ec_*) = 只核/黄金外卖员/小蟒夫/丨木丨/02IM/神明不响
       (博股通金/zgzl/冰川/A里寻无逐笔台账, 498人matchall为截面非逐笔, 均不可纳入)
episode构造: 快照近似口径, 复用 entry_zone_stats_20261008 方法(同code持仓日期缺口>6自然日切段)
特征: 入场日走势(pos20/60/250, bias5/10/20/60, ret1/5/10/20/60, 涨停确认/连板数/量能/新高/多头排列/市场涨停家数)
     + 出场日特征(断板/涨停/量能/乖离/盈亏状态) + 前瞻结果(f1/f3/f5/f10/f20/runup20/dd20) + 段收益
存储: data/entry_portrait.db (episodes + dim_stats + meta, 幂等全量重建)
用法: python analysis/entry_portrait_db_builder.py build
      python analysis/entry_portrait_db_builder.py stats [--dim pos60_band]
      python analysis/entry_portrait_db_builder.py query --code 600519 --date 20261010
边界: 研究层零交易权; 入场价锚=段首日qfq收盘(快照近似); 接入选股权重须A/B+主人批
"""
import argparse
import csv
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')

SOURCES = [
    ('只核', r'D:\zcodesj\data\ec_zhihe_spmatch_20261007.csv'),
    ('黄金', r'D:\zcodesj\data\ec_gold_delivery_spmatch_20261007.csv'),
    ('小蟒夫', r'D:\zcodesj\data\ec_xiaomangfu_spmatch_20261007.csv'),
    ('丨木丨', r'D:\zcodesj\data\ec_mumu_hold_20261008.csv'),
    ('02IM', r'D:\zcodesj\data\ec_02im_hold_20261008.csv'),
    ('神明', r'D:\zcodesj\data\ec_shenming_hold_20261008.csv'),
]
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ZT_DB = r'D:\zcodesj\data\zt_pool_history.db'
INDUSTRY_DB = r'D:\workbuddyspace\databases\fundamental_data.db'  # industry_map_ths(code,name,industry) 现映射非PIT
OUT_DB = r'D:\zcodesj\data\entry_portrait.db'
EXCLUDE_CODE = 'sz300033'  # 同花顺占位行


def load_industry():
    try:
        con = sqlite3.connect(f'file:{INDUSTRY_DB}?mode=ro', uri=True)
        m = {c: ind for c, ind in con.execute('SELECT code, industry FROM industry_map_ths')}
        con.close()
        return m
    except sqlite3.Error as e:
        print(f'结构化不可用: 行业映射加载失败({e})，industry列置N/A')
        return {}


def load_episodes():
    """六家台账 → (player, fullCode) 的持仓日期 → 缺口>6自然日切段"""
    by_pc = defaultdict(set)  # (player, code) -> set(date)
    names = {}
    for player, path in SOURCES:
        for r in csv.DictReader(open(path, encoding='utf-8'), delimiter='|'):
            fc = (r.get('fullCode') or '').strip()
            d = (r.get('dateNum') or '').strip()
            if not fc or fc == EXCLUDE_CODE or not (len(d) == 8 and d.isdigit()):
                continue
            by_pc[(player, fc)].add(d)
            if r.get('stockName'):
                names[(player, fc)] = r['stockName'].strip()
    episodes = []
    for (player, code), dts in by_pc.items():
        ds = sorted(dts)
        seg = [ds[0]]
        out = []
        for a, b in zip(ds, ds[1:]):
            gap = (int(b[:4]) - int(a[:4])) * 372 + (int(b[4:6]) - int(a[4:6])) * 31 + (int(b[6:]) - int(a[6:]))
            if gap > 6:
                out.append(seg)
                seg = [b]
            else:
                seg.append(b)
        out.append(seg)
        for s in out:
            episodes.append({'player': player, 'code': code, 'name': names.get((player, code), ''),
                             'd0': s[0], 'dL': s[-1], 'hold_days': len(s) - 1})
    episodes.sort(key=lambda e: (e['player'], e['code'], e['d0']))
    return episodes


def board_threshold(code, name):
    if 'ST' in (name or '').upper():
        return 4.85
    c6 = code[-6:]
    return 19.85 if c6[:3] in ('300', '301') else 9.85


def load_prices(codes, start_date):
    """qfq库批量拉: code -> (dates, high, low, close, volume, change_pct)"""
    q6 = sorted(set(c[2:] if len(c) == 8 else c for c in codes))
    px = {}
    cq = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    B = 100
    for i in range(0, len(q6), B):
        chunk = q6[i:i + B]
        q = ("SELECT code,date,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
             "WHERE code IN (%s) AND date>=? ORDER BY code,date" % ','.join('?' * len(chunk)))
        for code, d, hi, lo, cl, vo, pc in cq.execute(q, [*chunk, start_date]):
            px.setdefault(code, [[], [], [], [], [], []])
            px[code][0].append(str(d))
            px[code][1].append(hi)
            px[code][2].append(lo)
            px[code][3].append(cl)
            px[code][4].append(vo)
            px[code][5].append(pc)
    cq.close()
    return px


def load_mkt_zt():
    z = sqlite3.connect(f'file:{ZT_DB}?mode=ro', uri=True)
    m = {str(d): n for d, n in z.execute('SELECT date, COUNT(*) FROM zt_pool GROUP BY date')}
    z.close()
    return m


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def calc_features(code, name, day, series, mkt_zt):
    """入场日+出场日特征; series=[dates,high,low,close,volume,change_pct]"""
    dates, highs, lows, closes, vols, pcts = series
    i = next((k for k, d in enumerate(dates) if d == day), None)
    if i is None or i < 59:
        return None
    thr = board_threshold(code, name)
    c = closes[i]

    def win(k0, k1, arr):  # [k0,k1] 闭区间
        return arr[max(0, k0):k1 + 1]

    def pos_of(n):
        if i < n - 1:
            return None
        w = win(i - n + 1, i, closes)
        hi_, lo_ = max(w), min(w)
        return round(100.0 * (c - lo_) / (hi_ - lo_), 1) if hi_ > lo_ else None

    def bias(n):
        if i < n - 1:
            return None
        return round(100.0 * (c / mean(win(i - n + 1, i, closes)) - 1), 2)

    def ret(n):
        if i < n:
            return None
        return round(100.0 * (c / closes[i - n] - 1), 2)

    vol5 = mean(win(i - 5, i - 1, vols))
    vol3 = mean(win(i - 2, i, vols))
    vol20 = mean(win(i - 22, i - 3, vols))
    newhigh = lambda n: int(i >= n and c >= max(win(i - n, i - 1, closes)))
    mab = lambda n: mean(win(i - n + 1, i, closes))
    ma5, ma10, ma20 = mab(5), mab(10), mab(20)
    ma_bull = int(ma5 is not None and ma10 is not None and ma20 is not None and ma5 > ma10 > ma20)

    zt_cnt5 = sum(1 for p in win(i - 4, i, pcts) if p is not None and p >= thr)
    consec_zt = 0
    for k in range(i, -1, -1):
        if pcts[k] is not None and pcts[k] >= thr:
            consec_zt += 1
        else:
            break
    cz_band = ('非板' if consec_zt == 0 else '首板' if consec_zt == 1 else '2板' if consec_zt == 2
               else '3-5板' if consec_zt <= 5 else '6板+')
    consec = 0
    for k in range(i, 0, -1):
        if pcts[k] is not None and pcts[k] > 0:
            consec += 1
        else:
            break

    def band_pos60(v):
        return 'N/A' if v is None else ('深位0-25' if v <= 25 else '灰带25-40' if v <= 40 else '中带40-80' if v <= 80 else '高区80-100')

    def band_bias(v):
        return 'N/A' if v is None else ('<-10' if v < -10 else '-10~0' if v < 0 else '0~+10' if v < 10 else '+10~+20' if v < 20 else '>+20')

    def band_ret5(v):
        return 'N/A' if v is None else ('<-10%' if v < -10 else '-10~0%' if v < 0 else '0~10%' if v < 10 else '10~20%' if v < 20 else '>+20%')

    def band_vol(v):
        return 'N/A' if v is None else ('<0.8缩量' if v < 0.8 else '0.8-1.2平量' if v < 1.2 else '1.2-2放量' if v < 2 else '>=2爆量')

    if pcts[i] is None:
        confirm = 'N/A'
    elif pcts[i] >= thr:
        confirm = '涨停确认'
    elif pcts[i] >= 5:
        confirm = '强势阳线5-板'
    elif pcts[i] >= 0:
        confirm = '温和阳0-5'
    else:
        confirm = '阴线<0'

    return {
        'pos20': pos_of(20), 'pos60': pos_of(60), 'pos250': pos_of(250),
        'bias5': bias(5), 'bias10': bias(10), 'bias20': bias(20), 'bias60': bias(60),
        'ret1': ret(1), 'ret5': ret(5), 'ret10': ret(10), 'ret20': ret(20), 'ret60': ret(60),
        'entry_pct': pcts[i], 'zt_entry': int(pcts[i] is not None and pcts[i] >= thr),
        'zt_cnt5': zt_cnt5, 'consec_up': consec, 'consec_zt': consec_zt, 'consec_zt_band': cz_band,
        'vol_ratio': round(vols[i] / vol5, 2) if vol5 else None,
        'vol3_ratio': round(vol3 / vol20, 2) if vol3 and vol20 else None,
        'newhigh20': newhigh(20), 'newhigh60': newhigh(60), 'ma_bull': ma_bull,
        'mkt_zt_cnt': mkt_zt.get(day),
        'pos60_band': band_pos60(pos_of(60)), 'bias20_band': band_bias(bias(20)),
        'ret5_band': band_ret5(ret(5)),
        'zt_cnt5_band': '0次' if zt_cnt5 == 0 else '1次' if zt_cnt5 == 1 else '2次+',
        'vol_band': band_vol(round(vols[i] / vol5, 2) if vol5 else None),
        'confirm_band': confirm,
        'nh60_band': '创60日新高' if newhigh(60) else '非新高',
        'mabull_band': '多头排列' if ma_bull else '非多头',
        '_idx': i,
    }


def calc_outcome_exit(series, idx0, idxL, name, code):
    """前瞻结果 + 出场日特征; 返回dict"""
    dates, highs, lows, closes, vols, pcts = series
    c0 = closes[idx0]
    thr = board_threshold(code, name)

    def fwd(n):
        return round(100.0 * (closes[idx0 + n] / c0 - 1), 2) if idx0 + n < len(closes) else None

    runup = round(max((highs[idx0 + k] / c0 - 1) * 100 for k in range(1, 21) if idx0 + k < len(highs)), 2) if idx0 + 1 < len(highs) else None
    dd = round(min((lows[idx0 + k] / c0 - 1) * 100 for k in range(1, 21) if idx0 + k < len(lows)), 2) if idx0 + 1 < len(lows) else None

    pc_L = pcts[idxL]
    zt_exit = int(pc_L is not None and pc_L >= thr)
    zt_prev = pcts[idxL - 1] is not None and pcts[idxL - 1] >= thr
    duanban = int(zt_prev and not zt_exit)
    vol5 = mean(vols[idxL - 5:idxL])
    if pc_L is None:
        ec = 'N/A'
    elif duanban:
        ec = '断板卖出'
    elif zt_exit:
        ec = '涨停卖出'
    elif pc_L >= 5:
        ec = '大阳卖出'
    elif pc_L >= 0:
        ec = '小阳卖出'
    else:
        ec = '阴线卖出'

    return {'f1': fwd(1), 'f3': fwd(3), 'f5': fwd(5), 'f10': fwd(10), 'f20': fwd(20),
            'runup20': runup, 'dd20': dd,
            'exit_pct': pc_L, 'zt_exit': zt_exit, 'duanban_exit': duanban,
            'vol_ratio_exit': round(vols[idxL] / vol5, 2) if vol5 else None,
            'exit_confirm': ec}


def build():
    episodes = load_episodes()
    by_player = defaultdict(int)
    for e in episodes:
        by_player[e['player']] += 1
    print('== episode段(六家, 缺口>6日切段) ==')
    for p, n in by_player.items():
        print(f'  {p}: {n}')
    print(f'  合计: {len(episodes)}')

    min_d0 = min(e['d0'] for e in episodes)
    yy, mm = int(min_d0[:4]), int(min_d0[4:6])
    mm -= 22  # pos250需~250交易日回看
    if mm <= 0:
        yy, mm = yy + (mm - 1) // 12, (mm - 1) % 12 + 1
    start_date = f'{yy}{mm:02d}01'
    px = load_prices([e['code'] for e in episodes], start_date)
    mkt = load_mkt_zt()
    ind = load_industry()
    print(f'qfq加载: {len(px)}只/回看起点{start_date}/市场涨停家数{len(mkt)}日/行业映射{len(ind)}只')

    rows = []
    miss_px = miss_d0 = 0
    for e in episodes:
        series = px.get(e['code'][2:])
        if series is None:
            miss_px += 1
            continue
        f = calc_features(e['code'], e['name'], e['d0'], series, mkt)
        if f is None:
            miss_d0 += 1
            continue
        idx0 = f.pop('_idx')
        idxL = next((k for k, d in enumerate(series[0]) if d == e['dL']), None)
        seg = None
        if idxL is not None and idxL > idx0 and series[3][idx0]:
            seg = round(100.0 * (series[3][idxL] / series[3][idx0] - 1), 2)
        ex = calc_outcome_exit(series, idx0, idxL, e['name'], e['code']) if idxL is not None else {}
        if seg is None:
            pfb = 'N/A'
        elif seg < -8:
            pfb = '<-8止损域'
        elif seg < 0:
            pfb = '-8~0'
        elif seg < 10:
            pfb = '0~10'
        else:
            pfb = '>10止盈域'
        board = 'ST' if 'ST' in (e['name'] or '').upper() else ('20cm' if e['code'][-6:][:3] in ('300', '301') else 'main')
        rows.append({**e, **f, **ex, 'board_type': board,
                     'industry': ind.get(e['code'][2:], 'N/A'),
                     'seg_ret': seg,
                     'is_winner': int(seg is not None and seg > 5),
                     'is_loser': int(seg is not None and seg < -5),
                     'profit_exit_band': pfb})
    print(f'特征完整段: {len(rows)} (qfq缺股{miss_px}/入场日缺bar{miss_d0})')

    dims = ['pos60_band', 'bias20_band', 'ret5_band', 'zt_cnt5_band', 'vol_band', 'consec_zt_band',
            'confirm_band', 'nh60_band', 'mabull_band', 'exit_confirm', 'profit_exit_band']

    def agg(player_filter):
        out = []
        for dim in dims:
            g = defaultdict(list)
            for r in rows:
                if player_filter != 'ALL' and r['player'] != player_filter:
                    continue
                k = r.get(dim)
                if k and k != 'N/A' and r['seg_ret'] is not None:
                    g[k].append(r)
            for k, items in g.items():
                segs = [x['seg_ret'] for x in items]
                wins = [x['seg_ret'] for x in items if x['seg_ret'] > 0]
                losses = [x['seg_ret'] for x in items if x['seg_ret'] < 0]
                f5s = [x['f5'] for x in items if x['f5'] is not None]
                f1s = [x['f1'] for x in items if x['f1'] is not None]
                f20s = [x['f20'] for x in items if x['f20'] is not None]
                out.append((dim, k, player_filter, len(items),
                            round(100.0 * sum(1 for s in segs if s > 0) / len(segs), 1),
                            round(st.median(f1s), 2) if f1s else None,
                            round(st.median(f5s), 2) if f5s else None,
                            round(st.median(f20s), 2) if f20s else None,
                            round(st.median(segs), 2), round(st.mean(segs), 2),
                            round(st.mean(wins) / abs(st.mean(losses)), 2) if wins and losses else None))
        return out

    stat_rows = agg('ALL')
    for p in by_player:
        stat_rows += agg(p)

    con = sqlite3.connect(OUT_DB)
    con.execute('DROP TABLE IF EXISTS episodes')
    con.execute('DROP TABLE IF EXISTS dim_stats')
    con.execute('DROP TABLE IF EXISTS meta')
    con.execute('''CREATE TABLE episodes (
        player TEXT, code TEXT, name TEXT, d0 TEXT, dL TEXT, hold_days INTEGER, board_type TEXT,
        pos20 REAL, pos60 REAL, pos250 REAL, bias5 REAL, bias10 REAL, bias20 REAL, bias60 REAL,
        ret1 REAL, ret5 REAL, ret10 REAL, ret20 REAL, ret60 REAL,
        entry_pct REAL, zt_entry INTEGER, zt_cnt5 INTEGER, consec_up INTEGER, consec_zt INTEGER, consec_zt_band TEXT,
        vol_ratio REAL, vol3_ratio REAL, newhigh20 INTEGER, newhigh60 INTEGER, ma_bull INTEGER,
        mkt_zt_cnt INTEGER, f1 REAL, f3 REAL, f5 REAL, f10 REAL, f20 REAL,
        runup20 REAL, dd20 REAL, seg_ret REAL, is_winner INTEGER, is_loser INTEGER,
        exit_pct REAL, zt_exit INTEGER, duanban_exit INTEGER, vol_ratio_exit REAL, exit_confirm TEXT,
        pos60_band TEXT, bias20_band TEXT, ret5_band TEXT, zt_cnt5_band TEXT, vol_band TEXT,
        confirm_band TEXT, nh60_band TEXT, mabull_band TEXT, profit_exit_band TEXT, industry TEXT,
        PRIMARY KEY(player, code, d0))''')
    con.execute('''CREATE TABLE dim_stats (
        dim_name TEXT, bucket TEXT, player TEXT, n INTEGER, win5 REAL,
        med_f1 REAL, med_f5 REAL, med_f20 REAL, med_seg REAL, mean_seg REAL, pl_ratio REAL,
        PRIMARY KEY(dim_name, bucket, player))''')
    con.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
    con.executemany('INSERT INTO episodes VALUES (%s)' % ','.join('?' * len(EP_COLS)),
                    [tuple(r.get(c) for c in EP_COLS) for r in rows])
    con.executemany('INSERT INTO dim_stats VALUES (?,?,?,?,?,?,?,?,?,?,?)', stat_rows)
    con.executemany('INSERT INTO meta VALUES (?,?)', [
        ('built_at', str(date.today())),
        ('sources', json.dumps([p for p, _ in SOURCES], ensure_ascii=False)),
        ('method', 'snapshot episode gap>6d; entry anchor=d0 qfq close; zt thr=9.85/19.85(30x/301x)/4.85(ST); '
                   'consec_zt=入场日起连续涨停板数; industry=industry_map_ths现映射非PIT'),
        ('n_episodes', str(len(rows))),
        ('boundary', 'research-only zero-trade-authority; qfq库不含已退市股=幸存者偏差; 接入选股权重须A/B+主人批'),
    ])
    con.commit()
    ic = con.execute('PRAGMA integrity_check').fetchone()[0]
    n1 = con.execute('SELECT COUNT(*) FROM episodes').fetchone()[0]
    n2 = con.execute('SELECT COUNT(*) FROM dim_stats').fetchone()[0]
    con.close()
    print(f'落库: data/entry_portrait.db episodes={n1} dim_stats={n2} integrity={ic}')

    # 校验: 五家子集(剔小蟒夫)赢家/输家入场画像 vs entry_zone_stats基线
    five = [r for r in rows if r['player'] != '小蟒夫']
    for tag, key in (('赢家段(seg>+5%)', 'is_winner'), ('输家段(seg<-5%)', 'is_loser')):
        g = [r for r in five if r[key]]
        if not g:
            continue
        zt = 100 * sum(r['zt_entry'] for r in g) / len(g)
        print(f'校验五家{tag}: n={len(g)} pos60中位={st.median([r["pos60"] for r in g if r["pos60"] is not None]):.0f} '
              f'bias20中位={st.median([r["bias20"] for r in g]):+.1f}% 涨停入场率={zt:.0f}%')
    print('基线(entry_zone_stats_20261008): 赢家n=257 pos60中位=100 bias20中位+26.4% 涨停率72% / 输家n=155 +27.1% 45%')


EP_COLS = ['player', 'code', 'name', 'd0', 'dL', 'hold_days', 'board_type',
           'pos20', 'pos60', 'pos250', 'bias5', 'bias10', 'bias20', 'bias60',
           'ret1', 'ret5', 'ret10', 'ret20', 'ret60',
           'entry_pct', 'zt_entry', 'zt_cnt5', 'consec_up', 'consec_zt', 'consec_zt_band',
           'vol_ratio', 'vol3_ratio', 'newhigh20', 'newhigh60', 'ma_bull',
           'mkt_zt_cnt', 'f1', 'f3', 'f5', 'f10', 'f20',
           'runup20', 'dd20', 'seg_ret', 'is_winner', 'is_loser',
           'exit_pct', 'zt_exit', 'duanban_exit', 'vol_ratio_exit', 'exit_confirm',
           'pos60_band', 'bias20_band', 'ret5_band', 'zt_cnt5_band', 'vol_band',
           'confirm_band', 'nh60_band', 'mabull_band', 'profit_exit_band', 'industry']


def stats(dim=None):
    con = sqlite3.connect(f'file:{OUT_DB}?mode=ro', uri=True)
    where, params = '', []
    if dim:
        where, params = 'WHERE dim_name=?', [dim]
    print(f"{'维度':<14}{'桶':<12}{'主体':<8}{'n':>5}{'胜率':>7}{'medF1':>7}{'medF5':>7}{'medF20':>8}{'med段':>8}{'均段':>7}{'盈亏比':>7}")
    for row in con.execute(f'SELECT * FROM dim_stats {where} ORDER BY dim_name, player, bucket', params):
        print(f'{row[0]:<14}{row[1]:<12}{row[2]:<8}{row[3]:>5}{row[4]:>6.1f}%'
              f'{(row[5] if row[5] is not None else 0):>+7.2f}{(row[6] if row[6] is not None else 0):>+7.2f}'
              f'{(row[7] if row[7] is not None else 0):>+8.2f}{(row[8] if row[8] is not None else 0):>+8.2f}'
              f'{(row[9] if row[9] is not None else 0):>+7.2f}{(row[10] if row[10] is not None else 0):>7.2f}')
    con.close()


def query(code, day):
    series = load_prices([code], '20000101').get(code[2:] if len(code) == 8 else code)
    if series is None:
        print(f'结构化不可用: qfq库无 {code} 日线')
        return 2
    mkt = load_mkt_zt()
    f = calc_features(code, '', day, series, mkt)
    if f is None:
        print(f'结构化不可用: {code} 在 {day} 无bar或历史不足60日')
        return 2
    f.pop('_idx')
    print(f'== {code} {day} 入场画像特征 ==')
    for k, v in f.items():
        print(f'  {k}: {v}')
    con = sqlite3.connect(f'file:{OUT_DB}?mode=ro', uri=True)
    print('\n== 匹配历史桶(全家族ALL) ==')
    for dim in ['pos60_band', 'bias20_band', 'ret5_band', 'zt_cnt5_band', 'vol_band', 'consec_zt_band',
                'confirm_band', 'nh60_band', 'mabull_band']:
        b = f.get(dim)
        if not b or b == 'N/A':
            print(f'  {dim}: N/A')
            continue
        row = con.execute('SELECT n,win5,med_f1,med_f5,med_seg,pl_ratio FROM dim_stats WHERE dim_name=? AND bucket=? AND player="ALL"',
                          (dim, b)).fetchone()
        if row:
            print(f'  {dim}={b}: n={row[0]} 段胜率{row[1]}% medF1{row[2]} medF5{row[3]} med段收益{row[4]}% 盈亏比{row[5]}')
        else:
            print(f'  {dim}={b}: 库内无此桶')
    con.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', nargs='?', default='build', choices=['build', 'stats', 'query'])
    ap.add_argument('--dim')
    ap.add_argument('--code')
    ap.add_argument('--date')
    a = ap.parse_args()
    if a.cmd == 'build':
        build()
    elif a.cmd == 'stats':
        stats(a.dim)
    elif a.cmd == 'query':
        if not (a.code and a.date):
            print('query 需要 --code 与 --date')
            sys.exit(2)
        sys.exit(query(a.code, a.date))


if __name__ == '__main__':
    main()
