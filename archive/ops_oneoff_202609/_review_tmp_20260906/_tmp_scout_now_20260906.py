# -*- coding: utf-8 -*-
"""一次性筛选(临时v2): 当前蓄势中+刚二波清单 (感知层观察清单,非买信号)
v2修正: 量缩比=近3日均量/首板后峰值量(旧版偏移一天); 新增蓄势交易日数; 剔除ST/科创/北交所/>30元"""
import json
import sqlite3
import numpy as np

DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
recs = [json.loads(l) for l in open('data/breakout_scout_track.jsonl', encoding='utf-8') if l.strip()]
tracking = [r for r in recs if r['status'] == 'tracking']
breakouts = [r for r in recs if r['status'] == 'breakout']
con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)


def tail(code, since, n=30):
    rows = con.execute("SELECT date,close,volume FROM stock_daily_meta_qfq "
                       "WHERE code=? AND date>=? ORDER BY date", (code, since)).fetchall()
    return rows[-n:]


def in_domain(r):
    name = r.get('name', '')
    code = r['code']
    if name.startswith(('ST', '*ST')) or 'ST' in name[:4]:
        return False
    if code.startswith(('68', '92', '82', '83', '87', '88', '43')):
        return False
    return True


A = []
for r in tracking:
    if not in_domain(r):
        continue
    t = tail(r['code'], r['board_date'])
    if not t or int(t[-1][0]) < 20260904:
        continue
    last_c = float(t[-1][1])
    if last_c > 30.0:
        continue
    drift = (last_c / r['board_close'] - 1) * 100
    if not (-6 <= drift <= 10):
        continue
    peak_v = max(float(x[2]) for x in t)
    shrink = float(np.mean([float(x[2]) for x in t[-3:]])) / max(1e-9, peak_v)
    A.append({'code': r['code'], 'name': r.get('name', ''), 'bd': r['board_date'],
              'board_c': r['board_close'], 'last': last_c, 'drift': round(drift, 1),
              'shrink': round(shrink, 2), 'days': len(t) - 1})

B = []
for r in breakouts:
    if not in_domain(r):
        continue
    ev = r['events'][-1] if r['events'] else {}
    bd = ev.get('date')
    if bd and bd >= 20260828:
        last_c = con.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=20260904",
                             (r['code'],)).fetchone()
        B.append({'code': r['code'], 'name': r.get('name', ''), 'bkd': bd,
                  'last': float(last_c[0]) if last_c else None})

print(f"=== A组: 蓄势中且强势(距首板-6%~+10%, 研究域, <30元) 共{len(A)}只, 按缩量+贴首板排序 Top25 ===")
A.sort(key=lambda x: (abs(x['drift'] - 2), x['shrink']))
print(f"{'代码':<8}{'名称':<10}{'首板日':<10}{'蓄势日数':>7}{'首板收':>7}{'现价':>7}{'距首板%':>8}{'量缩比':>7}")
for x in A[:25]:
    print(f"{x['code']:<8}{x['name']:<10}{x['bd']:<10}{x['days']:>7}{x['board_c']:>7.2f}{x['last']:>7.2f}{x['drift']:>7.1f}%{x['shrink']:>7.2f}")

print(f"\n=== B组: 8/28后刚二波(研究域) {len(B)}只, 按二波日新->旧 ===")
B.sort(key=lambda x: x['bkd'], reverse=True)
print(f"{'代码':<8}{'名称':<10}{'二波日':<10}{'9/4收':>7}")
for x in B:
    print(f"{x['code']:<8}{x['name']:<10}{x['bkd']:<10}{(x['last'] or 0):>7.2f}")
con.close()
