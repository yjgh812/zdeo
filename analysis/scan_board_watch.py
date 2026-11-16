# -*- coding: utf-8 -*-
"""盘中扫板哨 2026-10-10 (主人「继续」令: 体系最后一块=盘中执行工具; 提请制同strong_sell_watch族)
监测对象: data/chain_relay_pool_latest.json 胚胎池(未板观察触板) + 确认池(已板状态)
逻辑: 腾讯批量快照 → 现价/最高触及涨停价(prev*(1+thr)) → 首触时间记录 → toast提请(每票每日一次)
      确认池: 仍封/已炸状态入brief(2板+炸板toast)
调度: schtasks ScanBoardWatch_5min (09:35-14:55每5分钟); 非交易日/非盘中静默(腾讯快照日期!=今日即退)
边界: 提示层零交易权——弹清单待主人裁决, 系统不自动买入; 排板已被分钟回放判死(scan_fill_replay),
      提示语义=扫板确认域(早板优先); 数据源=qt.gtimg.cn(seal_snapshot同族)
输出: data/scan_board_watch_{state.json,ledger.jsonl,brief_latest.txt}
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(r'D:\zcodesj')
POOL = ROOT / 'data' / 'chain_relay_pool_latest.json'
STATE = ROOT / 'data' / 'scan_board_watch_state.json'
LEDGER = ROOT / 'data' / 'scan_board_watch_ledger.jsonl'
BRIEF = ROOT / 'data' / 'scan_board_watch_brief_latest.txt'
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'


def thr_of(code, name):
    if 'ST' in (name or '').upper():
        return 4.85
    return 19.85 if code[:3] in ('300', '301') else 9.85


def fetch_batch(codes):
    """腾讯批量快照 → {code6: {name,price,prev,high,low,quote_time}}"""
    q = ','.join(('sh' if c.startswith(('6', '9')) else 'sz') + c for c in codes)
    url = f'https://qt.gtimg.cn/q={q}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'})
    raw = urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='replace')
    out = {}
    for seg in raw.strip().split(';'):
        seg = seg.strip()
        if '=' not in seg:
            continue
        f = seg.split('=', 1)[1].strip('"').split('~')
        if len(f) < 40 or not f[2]:
            continue
        try:
            out[f[2]] = {'name': f[1], 'price': float(f[3] or 0), 'prev': float(f[4] or 0),
                         'high': float(f[33] or 0), 'low': float(f[34] or 0), 'quote_time': f[30]}
        except (ValueError, IndexError):
            continue
    return out


def latest_trade_date():
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    r = con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0]
    con.close()
    return int(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='', help='覆盖池日期(调试用)')
    ap.add_argument('--no-toast', action='store_true')
    a = ap.parse_args()

    now = datetime.now()
    today = int(now.strftime('%Y%m%d'))
    hhmm = now.hour * 100 + now.minute
    if not (925 <= hhmm <= 1508):
        print('[scan_board_watch] 非盘中时段静默退出')
        return
    if not POOL.is_file():
        print('[scan_board_watch] 结构化不可用: 接力池文件缺失(夜链chain_pool未跑?)')
        return
    pool = json.load(open(POOL, encoding='utf-8'))
    pool_date = int(a.date) if a.date else int(pool.get('date', 0))
    if pool_date == 0:
        print('[scan_board_watch] 结构化不可用: 池日期字段缺失')
        return
    latest = latest_trade_date()
    if pool_date > latest:
        print(f'[scan_board_watch] 池日期{pool_date}超前于最新交易日{latest} → 数据异常, 静默退出')
        return
    if pool_date < latest:
        print(f'[scan_board_watch] 🔴池日期{pool_date}落后于最新交易日{latest} → 夜链chain_pool断供, 提示不静默(日志可见)')
        return
    embryo = pool.get('embryo_pool', [])
    confirm = pool.get('confirm_pool', [])
    codes = [r['code'] for r in embryo] + [r['code'] for r in confirm]
    if not codes:
        print('[scan_board_watch] 池为空, 退出')
        return

    quotes = {}
    B = 60
    for i in range(0, len(codes), B):
        quotes.update(fetch_batch(codes[i:i + B]))
        time.sleep(0.15)
    qt_dates = {q['quote_time'][:8] for q in quotes.values() if q['quote_time']}
    qt_today = any(d == str(today) for d in qt_dates)
    if not qt_today:
        print(f'[scan_board_watch] 快照日期{sorted(qt_dates)[:1]}≠今日{today} → 非交易日, 静默退出')
        return

    state = {}
    if STATE.is_file():
        try:
            state = json.load(open(STATE, encoding='utf-8'))
        except Exception:
            state = {}
    if state.get('date') != today:
        state = {'date': today, 'touched': {}, 'alerted': []}
    touched = state.setdefault('touched', {})
    alerted = set(state.setdefault('alerted', []))

    done_keys = set()
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                if r.get('date') == today:
                    done_keys.add((r['code'], r['event']))
            except Exception:
                pass

    hits, new_rows = [], []
    confirm_status = []
    for r in confirm:
        q = quotes.get(r['code'])
        if not q or not q['prev']:
            continue
        limit = round(q['prev'] * (1 + thr_of(r['code'], r['name']) / 100) + 1e-9, 2)
        st_ = '仍封' if q['price'] >= limit - 0.001 else ('曾触回落' if q['high'] >= limit - 0.001 else '未触')
        confirm_status.append(f"{r['name']}({r['code']}) {r['boards']}板 {st_}")
        if r['boards'] >= 2 and st_ == '曾触回落' and (r['code'], 'zha_2p') not in done_keys:
            hits.append((r['code'], r['name'], 'zha_2p',
                         f"{r['boards']}板炸板 现价{q['price']:.2f}/板价{limit:.2f} 持有者注意断板纪律"))
            new_rows.append({'date': today, 'code': r['code'], 'name': r['name'], 'event': 'zha_2p',
                             'detail': f"{r['boards']}板炸板 现价{q['price']:.2f}", 'ts': time.strftime('%Y-%m-%d %H:%M:%S')})
    for r in embryo:
        q = quotes.get(r['code'])
        if not q or not q['prev']:
            continue
        limit = round(q['prev'] * (1 + thr_of(r['code'], r['name']) / 100) + 1e-9, 2)
        touched_now = q['high'] >= limit - 0.001 or q['price'] >= limit - 0.001
        if touched_now and r['code'] not in touched:
            touched[r['code']] = now.strftime('%H:%M')
        if touched_now and r['code'] not in alerted:
            t0 = touched[r['code']]
            hits.append((r['code'], r['name'], 'touch',
                         f"胚胎池触板 现价{q['price']:.2f}/板价{limit:.2f} 首触{t0} pos60={r.get('pos60')} 板史{r.get('zt60')}"
                         + (' | 早板(<10:00)' if hhmm < 1000 else '')))
            alerted.add(r['code'])
            new_rows.append({'date': today, 'code': r['code'], 'name': r['name'], 'event': 'touch',
                             'detail': f"首触{t0} 现价{q['price']:.2f}", 'ts': time.strftime('%Y-%m-%d %H:%M:%S')})

    if new_rows:
        with open(LEDGER, 'a', encoding='utf-8') as fp:
            for r in new_rows:
                fp.write(json.dumps(r, ensure_ascii=False) + '\n')
    state['alerted'] = sorted(alerted)
    json.dump(state, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False)

    lines = [f'[scan_board_watch] {today} {now.strftime("%H:%M:%S")} 池日={pool_date} 胚胎{len(embryo)}+确认{len(confirm)} 触板命中={len(hits)}']
    for c, n, ev, dt in hits:
        lines.append(f'  {n}({c}) {ev}: {dt}')
    lines.append('  -- 确认池状态 --')
    lines += ['  ' + s for s in confirm_status[:30]]
    lines.append('  注: 提请制零交易权——扫板/介入由主人盘中裁决, 系统不自动买入; 排板已被分钟回放判死')
    BRIEF.write_text('\n'.join(lines), encoding='utf-8')
    print('\n'.join(lines))
    if hits and not a.no_toast:
        try:
            import subprocess
            r = subprocess.run(
                [sys.executable, str(ROOT / 'notify.py'), 'send',
                 '--title', f'扫板哨: {len(hits)}笔触板(提请制)',
                 '--content', '\n'.join(f'{n}({c}) {dt}' for c, n, ev, dt in hits) +
                 '\n\n提请制: 是否介入由主人盘中裁决,系统不自动买入',
                 '--level', 'warn', '--channel', 'toast'],
                capture_output=True, text=True, timeout=60, cwd=str(ROOT))
            print(f'[toast rc={r.returncode}]')
        except Exception as e:
            print(f'[toast失败:{e}]')


if __name__ == '__main__':
    main()
