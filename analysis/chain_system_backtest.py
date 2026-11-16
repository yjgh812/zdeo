# -*- coding: utf-8 -*-
"""连板链体系组合回测 2026-10-10 (主人令「回测试一下」)
策略: 扫板(触板前一分钟确定成交) + 退出(隔日兑现/骑链断板) × 过滤(早板/胚胎资格/30元铁律)
版本: A无差别+隔日 / B早板+隔日 / C早板×胚胎+隔日 / D终版(早板×胚胎×骑链×30元)
组合: 等权1/5槽, 槽满跳过先到先得(信号按首触时刻排序), 双边成本0.2%, 骑链上限10日
断言: 基准初值1.0 / n>=30 / 无差别当日均值与scan_fill_replay决策树复现(免费断言) / 买卖同锁完整配对
边界: qfq无退市股=幸存偏差; 扫板成交为回放口径(实盘滑点另计); 2026年8.5个月单段含牛市
"""
import json
import sqlite3
import struct
import statistics as st
import sys
from collections import defaultdict
from datetime import date, timedelta
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
COST = 0.002
K_SLOTS = 5
RIDE_CAP = 10
PRICE_CAP = 30.0


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


def build_signals(names, qfq):
    """lc1回放 → 信号: {code,date,k,entry,tcat,day_ret,embryo,ride_exit_k}(k=qfq idx of 触板日)"""
    sigs = []
    n_files = 0
    for code in sorted(qfq):
        mkt = 'sh' if code.startswith('6') else 'sz'
        p = ROOT / mkt / 'minline' / f'{mkt}{code}.lc1'
        if not p.is_file():
            continue
        n_files += 1
        if n_files % 600 == 0:
            print(f'...replay {n_files}只/信号{len(sigs)}', flush=True)
        thr = thr_of(code, names.get(code, ''))
        ds, cs, ps = qfq[code]
        qidx = {d: i for i, d in enumerate(ds)}
        n = p.stat().st_size // RECORD_SIZE
        with open(p, 'rb') as f:
            buf = f.read(n * RECORD_SIZE)
        prev_close = None
        prev_day_close = None
        cur_day = None
        bars = []

        def flush(day, pclose):
            if not bars or not pclose or not (D_MIN <= day <= D_MAX):
                return
            limit = round(pclose * (1 + thr / 100) + 1e-9, 2)
            touch = next((k for k, (t, h, l, c) in enumerate(bars) if h >= limit - EPS), None)
            if touch is None:
                return
            entry = bars[0][1] if touch == 0 else bars[touch - 1][3]
            if not entry:
                return
            k = qidx.get(str(day))
            if k is None or k < 61 or k + 1 >= len(cs) or cs[k] is None or cs[k + 1] is None:
                return
            last_c = bars[-1][3]
            t0 = bars[touch][0]
            # 胚胎资格(T-1, screener同参)
            cl = cs[k - 1]
            w = cs[k - 60:k]
            hi_, lo_ = max(w), min(w)
            pos60 = 100.0 * (cl - lo_) / (hi_ - lo_) if hi_ > lo_ else None
            ret20 = 100.0 * (cl / cs[k - 21] - 1) if cs[k - 21] else None
            zt60 = sum(1 for x in ps[k - 60:k] if x is not None and x >= thr)
            embryo = bool(pos60 is not None and pos60 >= 60 and ret20 is not None and 0 < ret20 < 20 and zt60 >= 2)
            # 骑链退出k: k+1起首个未板日(其收盘卖); 上限RIDE_CAP; 数据尾强平
            m = k + 1
            cnt = 0
            while m < len(ps) and ps[m] is not None and ps[m] >= thr and cnt < RIDE_CAP:
                m += 1
                cnt += 1
            sigs.append({'code': code, 'date': day, 'k': k, 'entry': entry,
                         't': bars[touch][0], 'tcat': 'early' if t0 < 1000 else 'late',
                         'day_ret': 100.0 * (last_c / entry - 1),
                         'embryo': embryo, 'ride_k': m, 'entry_le30': entry <= PRICE_CAP})

        for b in struct.iter_unpack(_MIN_FMT, buf):
            dnum, tmin, o, h, l, c, amt, vol, _ = b
            if dnum == 0 or o <= 0:
                continue
            year = dnum // 2048 + 2004
            md = dnum % 2048
            day = year * 10000 + md // 100 * 100 + md % 100
            if day != cur_day:
                if cur_day is not None:
                    flush(cur_day, prev_day_close)
                prev_day_close = prev_close
                bars = []
                cur_day = day
            hh, mm = divmod(tmin, 60)
            bars.append((hh * 100 + mm, h, l, c))
            prev_close = c
        if cur_day is not None:
            flush(cur_day, prev_day_close)
    print(f'replay完成: {n_files}只, 信号{len(sigs)}')
    return sigs


def trade_returns(sig, qfq, mode):
    """单笔逐日收益序列(百分比/日, 除权正确, 买卖同锁): mode='next'|'ride'"""
    code = sig['code']
    ds, cs, ps = qfq[code]
    k0 = sig['k']
    if mode == 'next':
        exit_k = k0 + 1
    else:
        exit_k = min(sig['ride_k'], k0 + RIDE_CAP)
        if exit_k >= len(cs):
            exit_k = len(cs) - 1
    day_rets = [sig['day_ret']]                                # 触板日(raw段, 已含当日收盘)
    for k in range(k0 + 1, exit_k + 1):
        if cs[k] is None or cs[k - 1] is None:
            return None
        day_rets.append(100.0 * (cs[k] / cs[k - 1] - 1))
    return day_rets


def simulate(sigs, qfq, mode, filters):
    """按日历事件驱动组合模拟; filters=dict(early,embryo,le30)"""
    sel = [s for s in sigs
           if (not filters.get('early') or s['tcat'] == 'early')
           and (not filters.get('embryo') or s['embryo'])
           and (not filters.get('le30') or s['entry_le30'])]
    by_day = defaultdict(list)
    for s in sel:
        by_day[s['date']].append(s)
    ret_cache = {}

    def sig_rets(s):
        key = (s['code'], s['date'], mode)
        if key not in ret_cache:
            ret_cache[key] = trade_returns(s, qfq, mode)
        return ret_cache[key]

    d = date(2026, 2, 1)
    end = date(2026, 10, 9)
    equity = 1.0
    curve = []
    trades = []
    open_pos = []  # {'rets':[...], 'idx':int}
    while d <= end:
        dk = int(d.strftime('%Y%m%d'))
        day_ret = 0.0
        still = []
        for pos in open_pos:
            if pos['idx'] < len(pos['rets']):
                day_ret += pos['rets'][pos['idx']]
                pos['idx'] += 1
            if pos['idx'] < len(pos['rets']):
                still.append(pos)
            else:
                prod = 1.0
                for x in pos['rets']:
                    prod *= (1 + x / 100)
                trades.append((prod - 1) * 100 - COST * 100)
        open_pos = still
        for s in sorted(by_day.get(dk, []), key=lambda x: x['t']):
            if len(open_pos) >= K_SLOTS:
                break
            r = sig_rets(s)
            if r:
                open_pos.append({'rets': r, 'idx': 0})
        equity *= (1 + day_ret / K_SLOTS / 100)
        curve.append(equity)
        d += timedelta(days=1)
    for pos in open_pos:
        prod = 1.0
        for x in pos['rets']:
            prod *= (1 + x / 100)
        trades.append((prod - 1) * 100 - COST * 100)
    return {'n_sel': len(sel), 'n': len(trades), 'equity': equity, 'curve': curve, 'trades': trades}


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
    sigs = build_signals(names, qfq)
    assert sigs, '信号为空=数据/口径崩'
    # 免费断言: 无差别当日均值复现 scan_fill_replay 决策树(+0.21%)
    m_day = st.mean([s['day_ret'] for s in sigs])
    print(f'免费断言: 无差别当日均值={m_day:+.2f}% (scan_fill_replay决策树=+0.21%)')
    assert abs(m_day - 0.21) < 0.10, f'口径漂移! {m_day}'

    versions = [
        ('A 无差别+隔日', 'next', {}),
        ('B 早板+隔日', 'next', {'early': True}),
        ('C 早板×胚胎+隔日', 'next', {'early': True, 'embryo': True}),
        ('D 终版(早板×胚胎×骑链×30元)', 'ride', {'early': True, 'embryo': True, 'le30': True}),
    ]
    print(f'\n== 组合回测 20260201-20261009 等权1/{K_SLOTS}槽 双边{COST*100:.1f}% ==')
    print(f"{'版本':<26}{'信号':>6}{'成交':>6}{'笔胜率':>8}{'笔中位':>8}{'笔均值':>8}{'组合净值':>9}{'总收益':>8}{'MaxDD':>8}")
    results = {}
    for nm_, mode, flt in versions:
        r = simulate(sigs, qfq, mode, flt)
        assert r['n'] >= 30, f'{nm_} 样本不足 n={r["n"]}'
        tr = r['trades']
        dd = 0.0
        peak = 1.0
        for e in r['curve']:
            peak = max(peak, e)
            dd = min(dd, e / peak - 1)
        results[nm_] = r
        print(f"{nm_:<26}{r['n_sel']:>6}{r['n']:>6}{100*sum(1 for t in tr if t>0)/len(tr):>7.0f}%"
              f"{st.median(tr):>+8.2f}{st.mean(tr):>+8.2f}"
              f"{r['equity']:>9.3f}{100*(r['equity']-1):>+8.1f}%{100*dd:>+8.1f}%")
    json.dump({k: {'n_sel': v['n_sel'], 'n': v['n'], 'equity': round(v['equity'], 4), 'mean': round(st.mean(v['trades']), 2),
                   'med': round(st.median(v['trades']), 2),
                   'win': round(100*sum(1 for t in v['trades'] if t>0)/len(v['trades']), 1)}
               for k, v in results.items()},
              open(r'D:\zcodesj\tmp\chain_system_backtest_20261010.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('已存 tmp/chain_system_backtest_20261010.json')


if __name__ == '__main__':
    main()
