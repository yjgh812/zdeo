# -*- coding: utf-8 -*-
"""本周(20260824-20260828)买入52笔逐笔FIFO配对结局分析(只读研究件)
目的: 主人指令"查找本周选股胜率低的原因"
数据源: attribution/attribution.db trade_records(时间线顺序) + qfq库最新收盘价
配对规则: 同code FIFO,卖出时间必须晚于买入(防未来配对,同日按id顺序)
"""
import sqlite3
from collections import defaultdict

ATTR = r"D:\zcodesj\attribution\attribution.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

conn = sqlite3.connect(ATTR)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 本周全量买卖(含id保证时间线)
cur.execute("""
    SELECT id, trade_date, code, name, action, price, volume, subsystems, list_membership, notes
    FROM trade_records WHERE trade_date >= '20260824' ORDER BY id
""")
week_rows = [dict(r) for r in cur.fetchall()]

# ---------- 1. 需要更早的买入来解释本周卖出(旧仓退出) ----------
cur.execute("""
    SELECT id, trade_date, code, name, action, price, volume
    FROM trade_records WHERE trade_date < '20260824' ORDER BY id
""")
old_rows = [dict(r) for r in cur.fetchall()]

# FIFO账本: code -> [ [buy_date, price, vol, source, list_mem, buy_id], ... ]
ledger = defaultdict(list)
for r in old_rows:
    if r['action'] == 'BUY':
        ledger[r['code']].append([r['trade_date'], r['price'], r['volume'], 'pre_week', 'old', r['id']])

results = []  # 本周买入的每笔结局
sell_log = []  # 本周卖出对应的买入来源

for r in week_rows:
    code = r['code']
    if r['action'] == 'BUY':
        ledger[code].append([r['trade_date'], r['price'], r['volume'],
                             r['subsystems'] or '', r['list_membership'] or '', r['id']])
    else:  # SELL
        remaining = r['volume']
        while remaining > 0 and ledger[code]:
            lot = ledger[code][0]
            take = min(remaining, lot[2])
            if take > 0:
                pnl_pct = (r['price'] / lot[1] - 1) * 100
                sell_log.append({
                    'sell_date': r['trade_date'], 'code': code, 'name': r['name'],
                    'buy_date': lot[0], 'buy_price': lot[1], 'sell_price': r['price'],
                    'vol': take, 'pnl_pct': pnl_pct, 'buy_source': lot[3],
                    'list_mem': lot[4], 'sell_note': (r['notes'] or '')[:40],
                })
                lot[2] -= take
                remaining -= take
            if ledger[code] and ledger[code][0][2] <= 0:
                ledger[code].pop(0)

# ---------- 2. qfq最新价(8/28收盘)算未平仓浮盈亏 ----------
qconn = sqlite3.connect(QFQ)
qconn.row_factory = sqlite3.Row
qcur = qconn.cursor()

def last_close(code, before_date):
    qcode = code[2:] if code[:2] in ('sz', 'sh', 'bj') else code
    qdate = before_date.replace('-', '')
    qcur.execute("SELECT close, date FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                 (qcode, qdate))
    row = qcur.fetchone()
    return (row['close'], row['date']) if row else (None, None)

# qfq code格式校验
qcur.execute("SELECT code FROM stock_daily_meta_qfq LIMIT 3")
sample_codes = [r['code'] for r in qcur.fetchall()]
print('# qfq code样例:', sample_codes)

holding_rows = []
for code, lots in ledger.items():
    for lot in lots:
        if lot[2] > 0 and lot[3] != 'pre_week':  # 本周买入且未平仓
            lc, ld = last_close(code, '2026-08-28')
            if lc:
                pnl_pct = (lc / lot[1] - 1) * 100
                holding_rows.append({
                    'code': code, 'buy_date': lot[0], 'buy_price': lot[1],
                    'vol': lot[2], 'last_close': lc, 'last_date': ld,
                    'pnl_pct': pnl_pct, 'buy_source': lot[3], 'list_mem': lot[4],
                })
            else:
                holding_rows.append({
                    'code': code, 'buy_date': lot[0], 'buy_price': lot[1],
                    'vol': lot[2], 'last_close': None, 'last_date': None,
                    'pnl_pct': None, 'buy_source': lot[3], 'list_mem': lot[4],
                })

# ---------- 3. 汇总输出 ----------
def src_group(src, lm):
    s = src or ''
    if lm == 'bp_ep_top20':
        return '名单内(bp_ep_top20)'
    if lm == 'outside_list' or lm == '':
        if s in ('', 'ungated_待标注', 'manual_confirmed'):
            return '名单外(ungated/manual)'
        return f'过闸-{s}'
    if s.startswith('trade_executor'):
        return '过闸-async'
    return f'过闸-{s}'

print('\n===== A. 本周买入→本周已平仓(含旧仓参与配对仅计本周买) =====')
week_closed = [s for s in sell_log if s['buy_source'] != 'pre_week']
week_open = holding_rows

tot_pnl_w = sum(s['pnl_pct'] / 100 * s['vol'] * s['buy_price'] for s in week_closed if s['buy_source'] != 'pre_week')
for s in sorted(week_closed, key=lambda x: x['buy_date']):
    print(f"买{s['buy_date']} @{s['buy_price']:>7} → 卖{s['sell_date']} @{s['sell_price']:>7} {s['name']:<6} {s['pnl_pct']:+6.2f}% x{s['vol']} | {src_group(s['buy_source'], s['list_mem'])} | {s['sell_note'][:30]}")

print(f"\n本周买→已平仓 {len(week_closed)}笔 胜{sum(1 for s in week_closed if s['pnl_pct']>0)} 亏{sum(1 for s in week_closed if s['pnl_pct']<=0)} 金额加权盈亏 {tot_pnl_w:+.0f}元")

print('\n===== B. 本周买入→仍持仓(8/28收盘浮盈亏) =====')
for h in sorted(week_open, key=lambda x: (x['pnl_pct'] if x['pnl_pct'] is not None else -99)):
    p = f"{h['pnl_pct']:+6.2f}%" if h['pnl_pct'] is not None else '  N/A '
    print(f"买{h['buy_date']} @{h['buy_price']:>7} 现{h['last_close'] or 0:>7} {h['code']:<10} {p} x{h['vol']} | {src_group(h['buy_source'], h['list_mem'])}")

print(f"\n本周买→持仓 {len(week_open)}笔 浮盈{sum(1 for h in week_open if (h['pnl_pct'] or -1)>0)} 浮亏{sum(1 for h in week_open if (h['pnl_pct'] or 1)<=0)}")

# 合并口径: 本周全部52笔买入的当前结局
all_outcomes = [s['pnl_pct'] for s in week_closed] + [h['pnl_pct'] for h in week_open if h['pnl_pct'] is not None]
na_cnt = sum(1 for h in week_open if h['pnl_pct'] is None)
print(f"\n===== C. 合计口径: 本周买入 {len(all_outcomes)+na_cnt} 笔可配对 {len(all_outcomes)} =====")
print(f"盈利(含浮盈) {sum(1 for p in all_outcomes if p>0)} 笔 | 亏损(含浮亏) {sum(1 for p in all_outcomes if p<=0)} 笔 | 胜率 {sum(1 for p in all_outcomes if p>0)/len(all_outcomes)*100:.1f}%")

# 按来源分组
print('\n===== D. 按来源分组胜率 =====')
grp = defaultdict(list)
for s in week_closed:
    grp[src_group(s['buy_source'], s['list_mem'])].append(s['pnl_pct'])
for h in week_open:
    if h['pnl_pct'] is not None:
        grp[src_group(h['buy_source'], h['list_mem'])].append(h['pnl_pct'])
for g, ps in sorted(grp.items(), key=lambda kv: sum(kv[1])/len(kv[1])):
    win = sum(1 for p in ps if p > 0)
    print(f"{g:<24} n={len(ps):>2} 胜率{win/len(ps)*100:5.1f}% 均值{sum(ps)/len(ps):+6.2f}% 最差{min(ps):+7.2f}% 最好{max(ps):+7.2f}%")

# 按买入日分组
print('\n===== E. 按买入日分组 =====')
gd = defaultdict(list)
for s in week_closed:
    gd[s['buy_date']].append(s['pnl_pct'])
for h in week_open:
    if h['pnl_pct'] is not None:
        gd[h['buy_date']].append(h['pnl_pct'])
for d, ps in sorted(gd.items()):
    win = sum(1 for p in ps if p > 0)
    print(f"{d} n={len(ps):>2} 胜率{win/len(ps)*100:5.1f}% 均值{sum(ps)/len(ps):+6.2f}%")

# 本周卖出的旧仓(本周前买入)结局——主人感知的"被迫止损/趋势走坏"
print('\n===== F. 本周卖出的旧仓(非本周买入) =====')
old_closed = [s for s in sell_log if s['buy_source'] == 'pre_week']
for s in sorted(old_closed, key=lambda x: x['pnl_pct']):
    print(f"卖{s['sell_date']} @{s['sell_price']:>7} {s['name']:<6} {s['pnl_pct']:+6.2f}% x{s['vol']} | {s['sell_note'][:36]}")
tot_old = sum(s['pnl_pct'] / 100 * s['vol'] * s['buy_price'] for s in old_closed)
print(f"旧仓退出 {len(old_closed)}笔 胜{sum(1 for s in old_closed if s['pnl_pct']>0)} 亏{sum(1 for s in old_closed if s['pnl_pct']<=0)} 金额加权盈亏 {tot_old:+.0f}元")
