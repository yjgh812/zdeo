# -*- coding: utf-8 -*-
"""sold_rebuy_shadow.py — 深贴-8%回补·影子前向跟踪 (2026-09-29, PREREG_SOLD_REBUY_DEEP8 三门全过后影子化).

规则(与prereg指纹c52fdf8b888c8b32一致): 锚=空仓日收盘; 次日起20bar内 low<=anchor*0.92 首触成交
(成交价=min(anchor*0.92, open)); 成交起第10bar收盘退出; 单股同时至多一笔。
影子实现=每晚全量重算最近60个交易日的完整规则轨迹(无状态文件, 确定性重建), 与台账做
"窗口内替换式"幂等同步(重跑不重复不漂移)。产出:
  data/sold_rebuy_shadow_ledger.jsonl  (kind=fill/exit, day, code, price, ref, ret_at_exit)
汇报: 昨日新成交/新退出 + 当前在场纸面仓位 + 影子累计胜率/均值(滚动全窗)。
铁律: 零交易零信号权——影子信号禁止接任何真实委托; 生产接线须主人裁决+宪法§10流程。
用法: python sold_rebuy_shadow.py
"""
import os, sys, json, sqlite3
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
LEDGER = r'D:\zcodesj\data\sold_rebuy_shadow_ledger.jsonl'
HOLD, VALID, ZONE = 10, 20, 0.92
WINDOW = 60           # 重算窗口(交易日)
PREFIX = ('600', '601', '603', '605', '000', '001', '002', '003', '300', '301', '302')


def load_ledger(path):
    if not os.path.exists(path):
        return []
    out = []
    for l in open(path, encoding='utf-8'):
        l = l.strip()
        if not l:
            continue
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            continue
    return out


def main():
    qc = sqlite3.connect(QFQ_URI, uri=True, timeout=120)
    all_dates = [r[0] for r in qc.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
    win_dates = all_dates[-WINDOW:]
    d0 = win_dates[0]
    d_last = win_dates[-1]
    codes = [r[0] for r in qc.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>=?", (d0,)).fetchall()
             if r[0][:3] in PREFIX]
    print(f'[shadow] 重算窗口 {d0}~{d_last} ({len(win_dates)}交易日) 宇宙 {len(codes)} 只')

    events = []   # fill/exit 事件流(窗口内)
    open_pos = []  # 当前在场纸面仓
    for ci, code in enumerate(codes):
        if ci % 800 == 0:
            print(f'  {ci}/{len(codes)}', flush=True)
        px = pd.read_sql_query(
            "SELECT date,open,close,low FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date",
            qc, params=(code, d0))
        if len(px) < 3:
            continue
        o = px['open'].astype(float).to_numpy()
        c = px['close'].astype(float).to_numpy()
        lo = px['low'].astype(float).to_numpy()
        dd = px['date'].astype(str).to_numpy()
        n = len(c)
        t = 0
        while t < n - 1:
            limit = c[t] * ZONE
            end_scan = min(t + 1 + VALID, n)
            seg = lo[t + 1:end_scan]
            if len(seg) == 0 or seg.min() > limit:
                t += 1
                continue
            hit = int(np.nonzero(seg <= limit)[0][0]) + t + 1
            fill = min(limit, o[hit])
            ei = hit + HOLD - 1
            if ei < n:
                events.append({'kind': 'fill', 'day': dd[hit], 'code': code, 'price': round(fill, 3),
                               'anchor': round(c[t], 3), 'exit_day': dd[ei], 'exit_price': round(c[ei], 3),
                               'ret': round(c[ei] / fill - 1, 4)})
                t = ei + 1
            else:
                # 在场未平(窗口尾部)
                open_pos.append({'code': code, 'entry_day': dd[hit], 'entry_price': round(fill, 3),
                                 'anchor': round(c[t], 3), 'bars_held': n - 1 - hit,
                                 'last_close': round(c[-1], 3),
                                 'unreal': round(c[-1] / fill - 1, 4), 'bars_to_exit': HOLD - (n - hit)})
                break
    qc.close()

    # 幂等同步: 窗口内(d>=d0)按(kind,day,code)替换
    led = load_ledger(LEDGER)
    kept = [l for l in led if str(l.get('day', '')) < d0]
    keys_new = {(e['kind'], e['day'], e['code']) for e in events}
    kept = [l for l in kept if (l.get('kind'), l.get('day'), l.get('code')) not in keys_new]
    kept.extend(events)
    kept.sort(key=lambda l: (l.get('day', ''), l.get('code', ''), l.get('kind', '')))
    with open(LEDGER, 'w', encoding='utf-8') as f:
        for l in kept:
            f.write(json.dumps(l, ensure_ascii=False) + '\n')

    done = [e for e in events]
    wins = [e['ret'] for e in done]
    yesterday = win_dates[-2] if len(win_dates) >= 2 else d_last
    new_fill_y = [e for e in done if e['day'] == d_last]
    exit_y = [e for e in done if e.get('exit_day') == d_last]
    print(f'\n[shadow] 窗口内成交 {len(done)} 笔 | 滚动胜率 '
          f'{round(sum(1 for x in wins if x > 0) / len(wins) * 100, 1) if wins else "NA"}% | '
          f'滚动均值 {round(float(np.mean(wins)) * 100, 2) if wins else "NA"}%')
    print(f'最新日 {d_last}: 新成交 {len(new_fill_y)} 笔 / 到期退出 {len(exit_y)} 笔 | '
          f'当前在场纸面仓 {len(open_pos)} 笔')
    for e in new_fill_y[:8]:
        print(f"  FILL {e['code']} @{e['price']} (锚{e['anchor']})")
    for p in open_pos[:8]:
        print(f"  OPEN {p['code']} 入{p['entry_day']} @{p['entry_price']} 现价{p['last_close']} "
              f"浮动{p['unreal']*100:+.1f}% 距退出{p['bars_to_exit']}bar")
    print(f'台账同步 {LEDGER} (窗口替换式幂等, 共{len(kept)}行)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
