# -*- coding: utf-8 -*-
"""liquidity_watch.py · 持仓流动性日更巡检 (P0 防护网, 20260913 自省轮产物)

功能: 每日扫描全部持仓, 检查近 5 日 vs 近 20 日成交额, 识别流动性恶化持仓.
     CRITICAL(<3000万日额) / WARN(<5000万或缩量<40%) → toast + 日志.
用法: python liquidity_watch.py
"""
import os, sys, json, sqlite3, subprocess, datetime
import pandas as pd
import numpy as np
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
BASE = os.path.dirname(os.path.abspath(__file__))
QFQ = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
SNAP = os.path.join(BASE, 'data', 'positions_latest.json')
LOG = os.path.join(BASE, 'data', 'liquidity_watch_log.txt')
CRIT, WARN, SHRINK_TH = 3000, 5000, 0.40

def notify(title, content, level='warning'):
    try:
        subprocess.run([sys.executable, os.path.join(BASE, 'notify.py'), 'send',
                        '--title', title, '--content', content[:400], '--level', level], timeout=30)
    except Exception:
        pass

def load_positions():
    if not os.path.exists(SNAP):
        return []
    d = json.load(open(SNAP, encoding='utf-8'))
    items = d if isinstance(d, list) else d.get('positions') or []
    out = []
    for x in items:
        c = str(x.get('code') or '').replace('sz','').replace('sh','').zfill(6)[-6:]
        q = int(x.get('quantity', 0))
        px = float(x.get('current_price', x.get('price', 0)) or 0)
        if q > 0:
            out.append({'code': c, 'name': x.get('name','?'), 'qty': q, 'mkt_val': q * px})
    return out

def main():
    now = datetime.datetime.now()
    if not os.path.exists(SNAP):
        print('[liq] 无持仓快照, 跳过'); return 0
    pos = load_positions()
    if not pos:
        print('[liq] 无有效持仓'); return 0
    codes = [p['code'] for p in pos]
    con = sqlite3.connect(QFQ, uri=True, timeout=60)
    ph = ','.join('?' * len(codes))
    df = pd.read_sql_query(
        f"SELECT code, date, amount FROM stock_daily_meta_qfq WHERE code IN ({ph}) "
        "AND date >= date('now','-45 days') ORDER BY code, date", con, params=codes)
    con.close()
    df['date'] = df['date'].astype(str)
    df['amt_wan'] = pd.to_numeric(df['amount'], errors='coerce') / 1e4

    lines = [f'[liq_watch {now:%m-%d %H:%M}] 持仓{len(pos)}只']
    alerts = []
    for p in pos:
        c, name = p['code'], p['name']
        g = df[df['code'] == c].dropna(subset=['amt_wan'])
        if len(g) < 20:
            continue
        a5 = g['amt_wan'].tail(5).mean()
        a20 = g['amt_wan'].tail(20).mean()
        shrink = a5 / a20 if a20 > 0 else 1.0
        mkt_val_wan = p.get('mkt_val', 0) / 1e4
        lvl = 'OK'
        if a5 < CRIT:
            lvl = 'CRIT'; alerts.append(f'{c} {name} 日均额{a5:.0f}万(CRIT)')
        elif a5 < WARN:
            lvl = 'WARN'; alerts.append(f'{c} {name} 日均额{a5:.0f}万(WARN)')
        if shrink < SHRINK_TH:
            lvl = 'SHRINK'; alerts.append(f'{c} {name} 缩量比{shrink:.0%}')
        print(f'  {c} {name}: 5日均额={a5:.0f}万 缩量比={shrink:.0%} → {lvl}')

    print(f'\n[liq_watch] 异常 {len(alerts)} 项' + (':\n  ' + '\n  '.join(alerts) if alerts else ' — 全部健康'))
    # 全部异常时 toast
    if alerts:
        notify('持仓流动性巡检', f'{len(alerts)}项异常:\n' + '\n'.join(alerts[:6]), 'warning')
    # 日志
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f'\n[{now:%Y-%m-%d %H:%M}] {len(pos)}只 巡检完成\n')
        for ln in lines[1:]:
            f.write(ln + '\n')
    return 0

if __name__ == '__main__':
    main()
