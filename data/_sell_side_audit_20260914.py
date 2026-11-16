# -*- coding: utf-8 -*-
"""卖出侧反事实验证 20260914
对每笔 SELL 成交: 卖出价基准 -> fwd5/10/20 (卖后走势)。
卖对=卖后跌(fwd<0), 卖飞=卖后涨>5%。按 signal_type/notes 分组。
"""
import sqlite3, re, statistics, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PRE = 'file:D:/zcodesj/attribution/attribution.db?mode=ro'
QFQ = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'

con = sqlite3.connect(PRE, uri=True)
con.row_factory = sqlite3.Row
sells = con.execute(
    "SELECT code, name, trade_date, price, signal_type, notes, subsystems "
    "FROM trade_records WHERE action='SELL' ORDER BY trade_date").fetchall()
con.close()
print("卖出成交:", len(sells))

con = sqlite3.connect(QFQ, uri=True)
px = {}
import re as _re
def norm6(c):
    m = _re.search(r'(\d{6})', str(c))
    return m.group(1) if m else str(c)
for c in {norm6(r['code']) for r in sells}:
    px[c] = con.execute("SELECT date, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (c,)).fetchall()
con.close()

def fwd(code, td, n, base):
    code = norm6(code)
    ds = [d for d, _ in px.get(code, [])]
    td8 = td.replace('-', '')
    if td8 not in ds: return None
    i = ds.index(td8)
    if i + n >= len(ds): return None
    return (px[code][i+n][1] / base - 1) * 100

def classify(r):
    notes = (r['notes'] or '') + (r['signal_type'] or '')
    if re.search(r'-8%|止损|S-5|S5', notes): return 'S-5止损'
    if re.search(r'走坏|trend_break|趋势', notes): return '趋势走坏'
    if re.search(r'owner|主人|指令', notes): return '主人指令'
    if re.search(r'调仓|轮动|换仓', notes): return '调仓'
    return '其他'

groups = {}
for r in sells:
    g = classify(r)
    groups.setdefault(g, []).append(r)

print("\n=== 卖出分组反事实（卖对=卖后跌; 卖飞阈值: fwd5>+5%） ===")
print("分组 | n | fwd5均 | fwd5中位 | 卖对率(fwd5<0) | 卖飞率(fwd5>5%) | fwd10均")
for g, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    r5 = [(r['code'], r['trade_date'], r['price'] or 0, fwd(r['code'], r['trade_date'], 5, r['price'])) for r in rs]
    r5 = [(c, t, p, x) for c, t, p, x in r5 if x is not None and p > 0]
    r10 = [fwd(c, t, 10, p) for c, t, p, _ in r5]
    r10 = [x for x in r10 if x is not None]
    if not r5:
        print("%s | %d | 窗口不足" % (g, len(rs))); continue
    vals = [x for *_, x in r5]
    sell_right = 100 * sum(1 for x in vals if x < 0) / len(vals)
    sell_late = 100 * sum(1 for x in vals if x > 5) / len(vals)
    print("%s | %d | %+.2f%% | %+.2f%% | %.0f%% | %.0f%% | %s" % (
        g, len(rs), statistics.mean(vals), statistics.median(vals), sell_right, sell_late,
        ("%+.2f%%(n=%d)" % (statistics.mean(r10), len(r10))) if r10 else "-"))

# 卖飞Top明细(任何组)
print("\n=== 卖飞Top10 (fwd5>5% 全部分组) ===")
flown = []
for r in sells:
    x = fwd(r['code'], r['trade_date'], 5, r['price'])
    if x is not None and x > 5:
        flown.append((x, r['trade_date'], r['code'], r['name'], r['price'], classify(r)))
flown.sort(reverse=True)
for x, t, c, n, p, g in flown[:10]:
    print("  %s %s(%s) @%.2f [%s] 卖后5日%+.1f%%" % (t, c, n, p, g, x))

# 止损单卖后继续跌比例(止损有效性)
print("\n=== S-5止损单明细 ===")
for r in groups.get('S-5止损', []):
    x5 = fwd(r['code'], r['trade_date'], 5, r['price'])
    x10 = fwd(r['code'], r['trade_date'], 10, r['price'])
    print("  %s %s(%s) @%.2f 卖后5日=%s 10日=%s" % (
        r['trade_date'], r['code'], r['name'], r['price'] or 0,
        ("%+.1f%%" % x5) if x5 is not None else "-",
        ("%+.1f%%" % x10) if x10 is not None else "-"))
