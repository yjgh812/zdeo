# -*- coding: utf-8 -*-
"""卖飞成本量化(最低持有期证据基础) 2026-09-18
问题: 四路杠杆之一「最低持有期」至今无逐笔量化——早卖到底损失多少?
设计: trade_records 全部 SELL 记录, 按卖出理由分组(止损S-5/趋势走坏/其他),
  qfq 算卖后 T+5/T+10/T+20 反事实收益(卖出价→未来收盘)。
  持有天数 = 卖出日 - 同code最近一次BUY日。
预声明判定:
  若「非止损卖出」组卖后T+10/T+20均值显著为正(>+2%且中位>0),=早卖系统性卖飞证据,最低持有期提请有弹药;
  若≈0或为负,=卖出时机无罪,最低持有期降级。
偏差声明: 反事实不含持有期风险;-8%止损的铁律价值在截断左尾,卖后反弹不构成否定止损的证据(单列观察)。
"""
import sqlite3, json, statistics, sys, os, re
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\zcodesj'

conn = sqlite3.connect(os.path.join(ROOT, 'attribution', 'attribution.db'))
cur = conn.cursor()
cur.execute("SELECT code, name, trade_date, action, price, volume, notes FROM trade_records ORDER BY trade_date, id")
rows = cur.fetchall()
conn.close()

# code 规范化: sz300025 -> 300025
def norm(c):
    return c[-6:] if c and len(c) >= 6 else c

# 建持仓账本: 顺序处理, SELL 匹配最近 BUY
last_buy = {}
sells = []
for code, name, td, action, price, vol, notes in rows:
    c = norm(code)
    if action == 'BUY':
        last_buy[c] = td
    elif action == 'SELL':
        buy_td = last_buy.get(c)
        # 理由分类
        ns = notes or ''
        if ('止损' in ns) or ('S-5' in ns):
            reason = '止损S-5(-8%铁律)'
        elif '走坏' in ns:
            reason = '趋势走坏'
        elif '清仓' in ns or '全清' in ns:
            reason = '止损S-5(-8%铁律)' if '止损' in ns else '清仓/其他'
        else:
            reason = '其他/未标注'
        sells.append({'code': c, 'name': name, 'sell_date': td.replace('-', ''), 'sell_price': price,
                      'buy_date': (buy_td.replace('-', '') if buy_td else None), 'reason': reason})

qconn = sqlite3.connect(os.path.join(ROOT, 'data', 'dragon_quant_qfq.db'))
qcur = qconn.cursor()

def fwd(code, date, n):
    """卖后第n个交易日收盘价相对卖出价收益%"""
    r = qcur.execute("SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>? ORDER BY date LIMIT ?",
                     (code.zfill(6), date, n)).fetchall()
    if len(r) < n or not r[-1][1]:
        return None
    return round((float(r[-1][1]) / 1) , 4), None  # placeholder

def fwd_ret(code, date, sellpx, n):
    r = qcur.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date>? ORDER BY date LIMIT ?",
                     (code.zfill(6), date, n)).fetchall()
    if len(r) < n or r[-1][0] is None:
        return None
    return round((float(r[-1][0]) / sellpx - 1) * 100, 2)

groups = defaultdict(lambda: defaultdict(lambda: [0, 0, [], []]))  # reason -> horizon -> [n, wins, rets, hold_days]
detail = []
for s in sells:
    if not s['sell_price'] or s['sell_price'] <= 0:
        continue  # 价格为0的脏记录跳过
    hold = None
    if s['buy_date'] and s['sell_date'] >= s['buy_date']:
        try:
            import datetime as dt
            d0 = dt.datetime.strptime(s['buy_date'], '%Y%m%d')
            d1 = dt.datetime.strptime(s['sell_date'], '%Y%m%d')
            hold = (d1 - d0).days
        except Exception:
            pass
    for n in (5, 10, 20):
        r = fwd_ret(s['code'], s['sell_date'], s['sell_price'], n)
        if r is not None:
            b = groups[s['reason']][f'T+{n}']
            b[0] += 1; b[1] += (r > 0); b[2].append(r)
            if hold is not None: b[3].append(hold)
    detail.append({**s, 'hold_days': hold})

qconn.close()

OUT = {'卖出总笔数': len(sells)}
for reason, hz in groups.items():
    OUT[reason] = {}
    for h, (n, w, rets, holds) in sorted(hz.items()):
        OUT[reason][h] = {'n': n, '卖后为正占比%': round(100*w/n, 1),
                          '均值%': round(statistics.mean(rets), 2), '中位%': round(statistics.median(rets), 2),
                          '平均持有天数': round(statistics.mean(holds), 1) if holds else None}

# 非止损卖出按持有天数分桶(T+10口径) —— 在循环中补存T+10
qconn = sqlite3.connect(os.path.join(ROOT, 'data', 'dragon_quant_qfq.db'))
qcur2 = qconn.cursor()
buckets = defaultdict(lambda: [0, 0, []])
for s in detail:
    if s['reason'] == '止损S-5(-8%铁律)' or not s.get('hold_days'):
        continue
    r = qcur2.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date>? ORDER BY date LIMIT 10",
                      (s['code'].zfill(6), s['sell_date'])).fetchall()
    if len(r) < 10 or r[-1][0] is None:
        continue
    ret = (float(r[-1][0]) / s['sell_price'] - 1) * 100
    hb = '≤3日' if s['hold_days'] <= 3 else ('4-7日' if s['hold_days'] <= 7 else ('8-15日' if s['hold_days'] <= 15 else '>15日'))
    b = buckets[hb]; b[0] += 1; b[1] += (ret > 0); b[2].append(ret)
qconn.close()
OUT['非止损_按持有天数_T+10卖后'] = {k: {'n': v[0], '卖后为正%': round(100*v[1]/v[0], 1),
    '均值%': round(statistics.mean(v[2]), 2), '中位%': round(statistics.median(v[2]), 2)} for k, v in sorted(buckets.items())}

for s in detail[:3]:
    pass
OUT['_detail_head'] = detail[:3]
json.dump(OUT, open(os.path.join(ROOT, 'data', 'early_sell_cost_20260918.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print(json.dumps(OUT, ensure_ascii=False, indent=1, default=str))
