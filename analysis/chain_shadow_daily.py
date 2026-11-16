# -*- coding: utf-8 -*-
"""连板链影子组合日更 2026-10-10 (「继续」令: 哨vs回测口径对账 + 终版D影子逐日记账 = 造OOS测试集)
三件事(每交易日盘后, 挂夜链[chain_shadow]):
  1) 对账: 扫板哨台账(data/scan_board_watch_ledger.jsonl)今日touch事件 vs lc1重放当日触板全集
     → 哨漏抓/多抓计数(口径=chain_system_backtest.build_signals单日同参)
  2) 影子开仓: 终版D过滤(首触<10:00 × 胚胎资格 × entry<=30元) 按首触时刻填空槽(等权1/5)
  3) 持仓推进: qfq日线逐日, 收板续持, 首个未板日收盘退出(骑链cap10日), 双边0.2%
状态: data/chain_shadow_state_latest.json + ledger jsonl + brief; 幂等(last_date推进, 重复跑SKIP)
边界: 影子零交易权; entry=触板前一分钟lc1 raw(与回测同口径上限); qfq无退市股幸存偏差
"""
import json
import sqlite3
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')

from tdx_reader import _MIN_FMT, RECORD_SIZE, DEFAULT_TDX_ROOT

ROOT = Path(r'D:\zcodesj')
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'  # 2026-10-13 切wb唯一真源(zc副本停更致夜链SKIP, SOP一百三十二)
ZT_DB = r'D:\zcodesj\data\zt_pool_history.db'
NAME_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
WATCH_LEDGER = ROOT / 'data' / 'scan_board_watch_ledger.jsonl'
STATE = ROOT / 'data' / 'chain_shadow_state_latest.json'
SHADOW_LEDGER = ROOT / 'data' / 'chain_shadow_ledger.jsonl'
BRIEF = ROOT / 'data' / 'chain_shadow_brief_latest.txt'
K_SLOTS, RIDE_CAP, COST, PRICE_CAP, EPS = 5, 10, 0.002, 30.0, 0.001


def thr_of(code, name):
    if 'ST' in (name or '').upper():
        return 4.85
    return 19.85 if code[:3] in ('300', '301') else 9.85


def day_signal(code, day, cs, ps, thr, names_nm):
    """单股单日lc1重放 → 触板信号(与chain_system_backtest.build_signals同参) 或 None"""
    mkt = 'sh' if code.startswith('6') else 'sz'
    p = Path(DEFAULT_TDX_ROOT) / mkt / 'minline' / f'{mkt}{code}.lc1'
    if not p.is_file():
        return None
    n = p.stat().st_size // RECORD_SIZE
    with open(p, 'rb') as f:
        buf = f.read(n * RECORD_SIZE)
    prev_close = None
    prev_day_close = None
    cur_day = None
    bars = []
    for b in struct.iter_unpack(_MIN_FMT, buf):
        dnum, tmin, o, h, l, c, amt, vol, _ = b
        if dnum == 0 or o <= 0:
            continue
        year = dnum // 2048 + 2004
        md = dnum % 2048
        d = year * 10000 + md // 100 * 100 + md % 100
        if d != cur_day:
            prev_day_close = prev_close
            bars = []
            cur_day = d
        hh, mm = divmod(tmin, 60)
        bars.append((hh * 100 + mm, h, l, c))
        prev_close = c
        if d > day:
            break
    if cur_day != day or not bars or not prev_day_close:
        return None
    limit = round(prev_day_close * (1 + thr / 100) + 1e-9, 2)
    touch = next((k for k, (t, h, l, c) in enumerate(bars) if h >= limit - EPS), None)
    if touch is None:
        return None
    entry = bars[0][1] if touch == 0 else bars[touch - 1][3]
    if not entry:
        return None
    return {'code': code, 'date': day, 'entry': entry, 'limit': limit,
            't': bars[touch][0], 'early': int(bars[touch][0] < 1000),
            'le30': int(entry <= PRICE_CAP)}


def embryo_check(code, day, qfq, thr):
    """T-1胚胎资格(qfq口径, 与backtest同参)"""
    ds, cs, ps = qfq.get(code, ([], [], []))
    k = next((i for i, d in enumerate(ds) if d == str(day)), None)
    if k is None or k < 61:
        return False
    cl = cs[k - 1]
    w = cs[k - 60:k]
    hi_, lo_ = max(w), min(w)
    pos60 = 100.0 * (cl - lo_) / (hi_ - lo_) if hi_ > lo_ else None
    ret20 = 100.0 * (cl / cs[k - 21] - 1) if cs[k - 21] else None
    zt60 = sum(1 for x in ps[k - 60:k] if x is not None and x >= thr)
    return bool(pos60 is not None and pos60 >= 60 and ret20 is not None and 0 < ret20 < 20 and zt60 >= 2)


def main():
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    latest = int(con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0])
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

    state = json.load(open(STATE, encoding='utf-8')) if STATE.is_file() else {
        'equity': 1.0, 'last_date': 0, 'positions': [], 'n_trades': 0, 'wins': 0}
    if state['last_date'] >= latest:
        print(f'[chain_shadow] last_date={state["last_date"]}>=qfq最新{latest} → 无新交易日, SKIP')
        return
    day = latest
    t0 = time.time()

    # 1) 当日触板全集重放(全池宇宙=有qfq的票)
    names = {}
    try:
        c2 = sqlite3.connect(f'file:{NAME_DB}?mode=ro', uri=True)
        names = {c: nm for c, nm in c2.execute('SELECT code, name FROM spot_daily') if nm}
        c2.close()
    except sqlite3.Error:
        pass
    sigs = []
    for code, (ds, cs, ps) in qfq.items():
        if day not in {int(x) for x in ds[-3:]}:
            continue
        thr = thr_of(code, names.get(code, ''))
        s = day_signal(code, day, cs, ps, thr, names.get(code, ''))
        if s:
            s['embryo'] = embryo_check(code, day, qfq, thr)
            sigs.append(s)
    sig_set = {(s['code']) for s in sigs}

    # 覆盖池: 处理T日须用T-1晚归档池(=哨当日可见全集, 胚胎∪确认); 缺失则保守不开仓(防前视)
    all_ds = sorted({int(d) for ds, _, _ in qfq.values() for d in ds if int(d) < day})
    prev_td = all_ds[-1] if all_ds else 0
    pool_path = ROOT / 'data' / f'chain_relay_pool_{prev_td}.json'
    pool_codes = set()
    pool_note = ''
    if pool_path.is_file():
        poolj = json.load(open(pool_path, encoding='utf-8'))
        pool_codes = {r['code'] for r in poolj.get('embryo_pool', [])} | {r['code'] for r in poolj.get('confirm_pool', [])}
        pool_note = f'覆盖池={prev_td}晚(胚胎{len(poolj.get("embryo_pool", []))}+确认{len(poolj.get("confirm_pool", []))})'
    else:
        pool_note = f'🔴覆盖池{prev_td}缺失 → 保守不开仓(防前视)'

    # 2) 对账: 哨touch事件 vs 重放
    watch = set()
    if WATCH_LEDGER.is_file():
        for line in WATCH_LEDGER.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                if int(r.get('date', 0)) == day and r.get('event') == 'touch':
                    watch.add(r['code'])
            except Exception:
                pass
    missed = sorted(watch - sig_set)
    extra = sorted(sig_set - watch)
    # 3) 影子开仓(终版D过滤 × 池内成员=哨可见全集) — 池缺失=零开仓(保守防前视), 池外=覆盖缺口仅统计
    positions = state['positions']
    opens = []
    gap_outside = 0
    d_filter = [s for s in sigs if s['early'] and s['embryo'] and s['le30']]
    if not pool_codes:
        gap_outside = len(d_filter)
    else:
        for s in sorted(d_filter, key=lambda x: x['t']):
            if s['code'] not in pool_codes:
                gap_outside += 1
                continue
            if len(positions) + len(opens) >= K_SLOTS:
                break
            opens.append({'code': s['code'], 'entry_date': day, 'entry': s['entry'],
                          'ride_days': 0, 't': s['t']})
    positions += opens

    # 4) 持仓推进(qfq日线): 收板续持, 首个未板日收盘退出; cap10
    closed = []
    still = []
    for pos in positions:
        ds, cs, ps = qfq.get(pos['code'], ([], [], []))
        k = next((i for i, d in enumerate(ds) if d == str(day)), None)
        if k is None:  # 停牌等, 保留
            still.append(pos)
            continue
        pos['ride_days'] += 1
        thr = thr_of(pos['code'], names.get(pos['code'], ''))
        chained = ps[k] is not None and ps[k] >= thr
        if (not chained) or pos['ride_days'] >= RIDE_CAP:
            # 退出于当日收盘; entry是raw, 收益分解=(当日raw段已含在?)/影子用qfq重算:
            k0 = next((i for i, d in enumerate(ds) if d == str(pos['entry_date'])), None)
            if k0 is None or cs[k0] is None or cs[k] is None:
                still.append(pos)
                continue
            raw_leg = 100.0 * (cs[k0] / pos['entry'] - 1) if pos['entry'] else 0.0
            qfq_leg = 100.0 * (cs[k] / cs[k0] - 1)
            total = (1 + raw_leg / 100) * (1 + qfq_leg / 100) - 1
            total *= 100
            total -= COST * 100
            state['n_trades'] += 1
            state['wins'] += int(total > 0)
            state['equity'] *= (1 + total / 100 / K_SLOTS)
            closed.append({'code': pos['code'], 'entry_date': pos['entry_date'], 'exit_date': day,
                           'entry': pos['entry'], 'total': round(total, 2),
                           'ride_days': pos['ride_days'], 'ts': time.strftime('%Y-%m-%d %H:%M:%S')})
        else:
            still.append(pos)
    positions = still

    state['positions'] = positions
    state['last_date'] = day
    json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False)
    if closed or opens:
        with open(SHADOW_LEDGER, 'a', encoding='utf-8') as fp:
            for c in closed:
                fp.write(json.dumps({'type': 'close', **c}, ensure_ascii=False) + '\n')
            for o in opens:
                fp.write(json.dumps({'type': 'open', 'ts': time.strftime('%Y-%m-%d %H:%M:%S'), **o}, ensure_ascii=False) + '\n')

    lines = [f'[chain_shadow] {day} 重放触板{len(sigs)} 哨抓{len(watch)} 漏{len(missed)} 多{len(extra)}',
             f'  {pool_note} 池外可开缺口={gap_outside}',
             f'  新开{len(opens)} 平仓{len(closed)} 在仓{len(positions)} 净值={state["equity"]:.4f} '
             f'交易{state["n_trades"]}笔 胜率={100*state["wins"]/max(1,state["n_trades"]):.0f}%']
    if missed:
        lines.append(f'  🔴哨漏抓: {missed[:10]}')
    if extra:
        lines.append(f'  🟡重放有哨无(池外触板, 正常): {extra[:10]}')
    lines.append(f'  耗时{time.time()-t0:.0f}s; 影子零交易权, entry=回放上限口径')
    BRIEF.write_text('\n'.join(lines), encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
