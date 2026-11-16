# -*- coding: utf-8 -*-
"""选股alpha直接检验: 买入后到底涨没涨(只读研究件)
主人质疑: '选好股买入后不涨反跌,说明选的股不行' -> 用forward return直接回答
口径: trade_records 8/11以来全部买入, 成交价为基准, qfq真库算D+1/D+3/D+5收盘收益
      + 期间最高价涨幅(是否给过浮盈机会) + 期间最低价跌幅(最大浮亏)
基准: 同期全市场等权平均(每笔买入日为锚, 市场所有股票同窗口收益均值) = '随机选股'对照
"""
import sqlite3
import json
from collections import defaultdict

ATTR = r"D:\zcodesj\attribution\attribution.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

conn = sqlite3.connect(ATTR)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("""SELECT trade_date, code, name, price, volume, subsystems, list_membership
FROM trade_records WHERE trade_date >= '20260811' AND action='BUY' ORDER BY trade_date, id""")
buys = [dict(r) for r in cur.fetchall()]
print(f'样本: 8/11以来买入 {len(buys)} 笔')

qconn = sqlite3.connect(QFQ)
qconn.row_factory = sqlite3.Row
qcur = qconn.cursor()

# 预载: 全市场日收盘 {code: {date: close}} 与交易日历(只用股票行,排除可能的指数行: volume>0过滤)
qcur.execute("SELECT code, date, close, high, low FROM stock_daily_meta_qfq WHERE date >= '20260801'")
px = defaultdict(dict)
days_set = set()
for r in qcur.fetchall():
    px[r['code']][r['date']] = (r['close'], r['high'], r['low'])
    days_set.add(r['date'])
cal = sorted(days_set)
print(f'qfq日历: {cal[0]}~{cal[-1]}, 共{len(cal)}日, 股票数{len(px)}')

def fwd(code, buy_date, buy_price, n_days):
    """买入日后第n个交易日的收盘收益%与期间最高/最低"""
    d = px.get(code, {})
    if buy_date not in d:
        return None
    i = cal.index(buy_date)
    j = min(i + n_days, len(cal) - 1)
    if j <= i:
        return None
    end = cal[j]
    closes = [d[dt][0] for dt in cal[i:j+1] if dt in d]
    highs = [d[dt][1] for dt in cal[i:j+1] if dt in d]
    lows = [d[dt][2] for dt in cal[i:j+1] if dt in d]
    if not closes:
        return None
    return {
        'ret': (closes[-1] / buy_price - 1) * 100,
        'max_up': (max(highs) / buy_price - 1) * 100,
        'max_dn': (min(lows) / buy_price - 1) * 100,
        'actual_days': j - i,
    }

def market_fwd(buy_date, n_days):
    """市场等权基准: 所有股票同窗口收益均值"""
    i = cal.index(buy_date)
    j = min(i + n_days, len(cal) - 1)
    if j <= i:
        return None
    rets = []
    for code, d in px.items():
        if buy_date in d and cal[j] in d:
            rets.append((d[cal[j]][0] / d[buy_date][0] - 1) * 100)
    return sum(rets) / len(rets) if rets else None

def grp(b):
    s = b['subsystems'] or ''; lm = b['list_membership'] or ''
    if lm == 'bp_ep_top20' or s == 'bp_ep_core': return 'bp_ep系'
    if 'master' in s or 'hist' in s: return 'master_hist系'
    if 'smc' in s: return 'smc系'
    if 'hotspot' in s: return 'hotspot系'
    if s.startswith('trade_executor'): return 'executor_async未标源'
    if s == 'manual_confirmed': return 'manual特批'
    if s in ('ungated_待标注', ''): return '名单外/未登记'
    return 'other:' + s[:12]

results = []
skipped = 0
for b in buys:
    qc = b['code'][2:] if b['code'][:2] in ('sz', 'sh', 'bj') else b['code']
    if b['trade_date'] not in cal:
        skipped += 1
        continue
    row = {'date': b['trade_date'], 'code': b['code'], 'name': b['name'], 'buy': b['price'], 'src': grp(b)}
    for n, tag in [(1, 'd1'), (3, 'd3'), (5, 'd5')]:
        f = fwd(qc, b['trade_date'], b['price'], n)
        m = market_fwd(b['trade_date'], n)
        if f and f['actual_days'] == n:
            row[tag] = round(f['ret'], 2)
            row[tag + '_mkt'] = round(m, 2) if m is not None else None
            row[tag + '_excess'] = round(f['ret'] - m, 2) if m is not None else None
            row[tag + '_maxup'] = round(f['max_up'], 2)
            row[tag + '_maxdn'] = round(f['max_dn'], 2)
    results.append(row)
if skipped:
    print(f'跳过非交易日记录 {skipped} 笔(如20260815周末补录)')

json.dump(results, open('reports/winrate_fwd_returns_20260829.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

# ===== 分源统计 =====
def stat(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    ex = [r[key + '_excess'] for r in rows if r.get(key + '_excess') is not None]
    mu = [r[key + '_maxup'] for r in rows if r.get(key + '_maxup') is not None]
    if not vals:
        return None
    pos = sum(1 for v in vals if v > 0)
    return dict(n=len(vals), win=pos, winrate=round(pos / len(vals) * 100, 1),
                avg=round(sum(vals) / len(vals), 2),
                med=round(sorted(vals)[len(vals)//2], 2),
                exc=round(sum(ex) / len(ex), 2) if ex else None,
                avg_maxup=round(sum(mu) / len(mu), 2) if mu else None,
                never_up=sum(1 for v in mu if v <= 0))

print('\n===== 总体: 买入后D+1/D+3/D+5 收盘收益(%) =====')
for tag, label in [('d1', 'D+1'), ('d3', 'D+3'), ('d5', 'D+5')]:
    s = stat(results, tag)
    if s:
        print(f"{label}: n={s['n']} 上涨比例{s['winrate']}% 均值{s['avg']:+.2f}% 中位{s['med']:+.2f}% "
              f"超额(对市场等权){s['exc']:+.2f}% 期间最高曾涨{s['avg_maxup']:+.2f}% 从未给过浮盈{s['never_up']}笔")

print('\n===== 分信号源 (D+3口径主表) =====')
groups = defaultdict(list)
for r in results:
    groups[r['src']].append(r)
hdr = f"{'来源':<18}{'n':>3} {'胜率':>6} {'均值':>7} {'中位':>7} {'超额':>7} {'曾涨均':>7} {'从未涨':>5} | D+1均值   D+5均值"
print(hdr)
for src in sorted(groups, key=lambda s: -len(groups[s])):
    rows = groups[src]
    s3 = stat(rows, 'd3'); s1 = stat(rows, 'd1'); s5 = stat(rows, 'd5')
    if not s3:
        print(f"{src:<18}{len(rows):>3}  (D+3窗口不足)")
        continue
    d1s = f"{s1['avg']:+.2f}%" if s1 else '--'
    d5s = f"{s5['avg']:+.2f}%" if s5 else '--'
    print(f"{src:<18}{s3['n']:>3} {s3['winrate']:>5.1f}% {s3['avg']:>+7.2f} {s3['med']:>+7.2f} {s3['exc']:>+7.2f} "
          f"{s3['avg_maxup']:>+7.2f} {s3['never_up']:>5} | {d1s:>8} {d5s:>8}")

print('\n===== 逐笔明细(D+3, 按收益排序) =====')
def fmt(v):
    return f"{v:+7.2f}" if isinstance(v, (int, float)) else '    -- '
det = sorted([r for r in results if r.get('d3') is not None], key=lambda r: r['d3'])
for r in det:
    print(f"{r['date']} {r['name']:<6} 买@{r['buy']:>7} D+1{fmt(r.get('d1'))} D+3{fmt(r['d3'])} D+5{fmt(r.get('d5'))} "
          f"超额{fmt(r.get('d3_excess'))} 最高{fmt(r.get('d3_maxup'))} | {r['src']}")

# ===== 时间分段: 负alpha是持续还是某几周造成 =====
print('\n===== 按买入周分段 (D+3) =====')
wk = defaultdict(list)
for r in results:
    d = r['date']
    w = 'W1(8/11-14)' if d <= '20260814' else ('W2(8/17-21)' if d <= '20260821' else ('W3(8/24-28)' if d <= '20260828' else '?'))
    wk[w].append(r)
for w in sorted(wk):
    s3 = stat(wk[w], 'd3')
    if s3:
        print(f"{w}: n={s3['n']} 胜率{s3['winrate']}% 均值{s3['avg']:+.2f}% 超额{s3['exc']:+.2f}% 曾涨均{s3['avg_maxup']:+.2f}% 从未涨{s3['never_up']}笔")
    else:
        s1 = stat(wk[w], 'd1')
        print(f"{w}: (D+3不足) D+1: n={s1['n']} 胜率{s1['winrate']}% 均值{s1['avg']:+.2f}% 超额{s1['exc']:+.2f}%")

# ===== 涨跌结构: 买入后给过浮盈机会但没拿住 vs 从未给过机会 =====
print('\n===== D+3窗口内结构: 曾涨幅度分布(是否有浮盈出场机会) =====')
maxups = sorted(r['d3_maxup'] for r in results if r.get('d3_maxup') is not None)
buckets = [('从未涨(<=0%)', 0, 0.01), ('曾涨0~2%', 0.01, 2), ('曾涨2~5%', 2, 5), ('曾涨>5%', 5, 999)]
for label, lo, hi in buckets:
    n = sum(1 for v in maxups if lo <= v < hi)
    print(f"{label}: {n}笔 ({n/len(maxups)*100:.0f}%)")
