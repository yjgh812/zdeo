# -*- coding: utf-8 -*-
"""PREREG_BATCH_CONC_MA20_20260903 两规则回测验证(只读库,产物=reports json+md)。

规则A 批次集中度: 按日聚合买入,批次占比=当日买入金额/前一日收盘持仓市值(流水重放,qfq收盘计价);
        分组 >40% vs 40%以下, 对比批次内各笔 T+3 收益(qfq当日收盘基准) 金额加权均值。
规则B MA20偏离: 每笔买入 偏离=qfq收盘(T)/MA20(T-1收盘)-1 (无前视); 分组 >15% vs 15%以下,
        对比 T+3/T+5 收益均值与胜率; 附 signal_type 分层(板块强度层数据缺失,降级声明)。
判定阈值 = PREREG 预注册, 不可事后调。
"""
import sqlite3, json, datetime
from collections import defaultdict

ATTR = 'D:/zcodesj/attribution/attribution.db'
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
TODAY = '20260903'
END = '20260902'   # qfq写库窗口暂态风险规避: 可结算样本自然截止, 9/3买入无T+3剔除

def norm_date(s):
    s = str(s).replace('-', '')
    return s[:8]

acon = sqlite3.connect(ATTR)
qcon = sqlite3.connect(QFQ)

rows = acon.execute("select trade_date, code, name, action, price, volume, amount, signal_type, sector from trade_records order by trade_date").fetchall()
buys, sells = [], []
for d, code, name, act, price, vol, amt, sig, sector in rows:
    d = norm_date(d)
    if d > END:
        continue
    rec = dict(date=d, code=str(code).zfill(6), name=name, price=price, vol=vol or 0, amt=amt or 0, sig=sig or '', sector=sector or '')
    if str(act).lower() in ('buy', 'b', '买入'):
        buys.append(rec)
    elif str(act).lower() in ('sell', 's', '卖出'):
        sells.append(rec)
print(f"trade_records: buys={len(buys)} sells={len(sells)} (<= {END})")

# qfq 收盘序列缓存
codes = sorted({r['code'] for r in buys} | {r['code'] for r in sells})
px = {}
for c in codes:
    rs = qcon.execute("select date, close from stock_daily_meta_qfq where code=? and date<=? order by date", (c, END)).fetchall()
    px[c] = {d: cl for d, cl in rs}

def fwd_ret(code, d, k):
    """T+k 收益, 基准=T日qfq收盘; 不足k日返回None"""
    ser = px.get(code, {})
    if d not in ser:
        return None
    dates = sorted(ser)
    i = dates.index(d)
    if i + k >= len(dates):
        return None
    return ser[dates[i + k]] / ser[d] - 1

def ma20_prev(code, d):
    """T-1收盘MA20 (无前视)"""
    ser = px.get(code, {})
    dates = sorted(ser)
    if d not in dates:
        return None
    i = dates.index(d)
    if i < 20:
        return None
    return sum(ser[dt] for dt in dates[i - 20:i]) / 20

# ---------- 规则B ----------
b_res = []
for r in buys:
    m = ma20_prev(r['code'], r['date'])
    if not m or m <= 0:
        continue
    close_t = px.get(r['code'], {}).get(r['date'])
    if not close_t:
        continue
    dev = close_t / m - 1
    t3, t5 = fwd_ret(r['code'], r['date'], 3), fwd_ret(r['code'], r['date'], 5)
    if t3 is None:
        continue
    b_res.append(dict(date=r['date'], code=r['code'], name=r['name'], sig=r['sig'],
                      dev=dev, t3=t3, t5=t5))

hi = [x for x in b_res if x['dev'] > 0.15]
lo = [x for x in b_res if x['dev'] <= 0.15]
def stat(g, k):
    v = [x[k] for x in g if x[k] is not None]
    if not v:
        return dict(n=0)
    v.sort()
    return dict(n=len(v), mean=sum(v) / len(v), win=sum(1 for x in v if x > 0) / len(v),
                med=v[len(v) // 2], worst=min(v), best=max(v))
B = dict(hi_t3=stat(hi, 't3'), lo_t3=stat(lo, 't3'), hi_t5=stat(hi, 't5'), lo_t5=stat(lo, 't5'),
         n_hi=len(hi), n_lo=len(lo))

# signal_type 分层(板块强度降级声明)
sig_layer = defaultdict(lambda: dict(hi=[0], lo=[0]))
layers = defaultdict(lambda: {'hi': [], 'lo': []})
for x in b_res:
    key = (x['sig'] or 'unknown').split('_')[0][:16]
    layers[key]['hi' if x['dev'] > 0.15 else 'lo'].append(x['t3'])
sig_table = {k: dict(n_hi=len(v['hi']), hi_t3=round(sum(v['hi'])/len(v['hi']), 4) if v['hi'] else None,
                     n_lo=len(v['lo']), lo_t3=round(sum(v['lo'])/len(v['lo']), 4) if v['lo'] else None)
             for k, v in sorted(layers.items(), key=lambda kv: -(len(kv[1]['hi']) + len(kv[1]['lo'])))}

# ---------- 规则A: 按日重放持仓市值 ----------
days = sorted({r['date'] for r in buys} | {r['date'] for r in sells})
hold = defaultdict(float)   # code -> 股数
prev_mv = {}
all_dates = sorted({d for c in codes for d in px.get(c, {})})
for d in all_dates:
    buys_d = [r for r in buys if r['date'] == d]
    sells_d = [r for r in sells if r['date'] == d]
    for r in buys_d:
        hold[r['code']] += r['vol']
    for r in sells_d:
        hold[r['code']] -= r['vol']
    mv = sum(hold[c] * px.get(c, {}).get(d, 0) for c in hold if hold[c] > 0 and d in px.get(c, {}))
    prev_mv[d] = mv   # 当日收盘市值(重放后)

a_res = []
by_day = defaultdict(list)
for r in buys:
    by_day[r['date']].append(r)
for d, rs in sorted(by_day.items()):
    dates_sorted = sorted(prev_mv)
    prior = [x for x in dates_sorted if x < d]
    if not prior:
        continue
    base = prev_mv[prior[-1]]
    amt = sum(r['amt'] or (r['price'] * r['vol']) for r in rs)
    if base <= 10000:   # 空仓/建仓期分母无意义, 剔除并计数
        continue
    conc = amt / base
    t3s = [(r, fwd_ret(r['code'], d, 3)) for r in rs]
    t3s = [(r, t) for r, t in t3s if t is not None]
    if not t3s:
        continue
    w = sum((r['amt'] or r['price'] * r['vol']) for r, _ in t3s) or 1
    t3_w = sum(t * (r['amt'] or r['price'] * r['vol']) for r, t in t3s) / w
    a_res.append(dict(date=d, conc=conc, n=len(rs), amt=round(amt), t3_w=t3_w))

hi_a = [x for x in a_res if x['conc'] > 0.40]
lo_a = [x for x in a_res if x['conc'] <= 0.40]
def stat_a(g):
    if not g:
        return dict(n=0)
    v = [x['t3_w'] for x in g]
    v.sort()
    return dict(n=len(v), mean=sum(v) / len(v), win=sum(1 for x in v if x > 0) / len(v), med=v[len(v) // 2])
A = dict(hi=stat_a(hi_a), lo=stat_a(lo_a), hi_days=[(x['date'], round(x['conc'], 2), round(x['t3_w'], 4)) for x in sorted(hi_a, key=lambda x: -x['conc'])])

# ---------- 判定 (PREREG 预注册阈值) ----------
def pct(x): return round(x * 100, 2) if x is not None else None
verdict = {}
# 规则A: 高集中组T3均值 < 低集中组 且差>=1.0pp 且 高集中批次数>=10
if A['hi']['n'] >= 1 and A['lo']['n'] >= 1:
    diff_a = (A['lo']['mean'] - A['hi']['mean']) * 100
    verdict['A'] = dict(diff_lo_minus_hi_pp=round(diff_a, 2), n_hi=A['hi']['n'],
                        evidence=bool(diff_a >= 1.0 and A['hi']['n'] >= 10),
                        note='PASS需 diff>=1.0pp 且 n_hi>=10')
    # 上行收益对照
    verdict['A']['hi_best'] = pct(A['hi']['best'] if 'best' in A['hi'] else None)
else:
    verdict['A'] = dict(evidence=False, note='样本不足')
# 规则B: 高偏离组T5均值比低偏离组低>=1.5pp 且 n>=30
if B['hi_t5'].get('n') and B['lo_t5'].get('n'):
    diff_b = (B['lo_t5']['mean'] - B['hi_t5']['mean']) * 100
    verdict['B'] = dict(diff_lo_minus_hi_t5_pp=round(diff_b, 2), n_hi=B['n_hi'],
                        evidence=bool(diff_b >= 1.5 and B['n_hi'] >= 30),
                        note='PASS需 diff>=1.5pp 且 n_hi>=30')
else:
    verdict['B'] = dict(evidence=False, note='样本不足')

out = dict(generated=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           sample=dict(buys=len(buys), sells=len(sells), range=[buys[0]['date'], buys[-1]['date']] if buys else None,
                       b_res=len(b_res), a_days=len(a_res)),
           ruleB=dict(hi=B['hi_t3'], lo=B['lo_t3'], hi5=B['hi_t5'], lo5=B['lo_t5'], sig_layer=sig_table),
           ruleA=dict(hi=A['hi'], lo=A['lo'], hi_days=A['hi_days'][:15]),
           verdict=verdict,
           caveat='qfq写库窗口内9/3当日行未用于T+k结算(样本自然截止); 板块强度层降级为signal_type分层(个股行业映射缺失)')
json.dump(out, open(r'D:\zcodesj\reports\BATCH_CONC_MA20_BACKTEST_20260903.json', 'w', encoding='utf8'), ensure_ascii=False, indent=1, default=str)

print('=== 规则B MA20偏离 ===')
print('高偏离>15%:', json.dumps(B['hi_t3'], default=str), '| T5:', json.dumps(B['hi_t5'], default=str), f"n={B['n_hi']}")
print('低偏离<=15%:', json.dumps(B['lo_t3'], default=str), '| T5:', json.dumps(B['lo_t5'], default=str), f"n={B['n_lo']}")
print('signal分层:', json.dumps(sig_table, ensure_ascii=False))
print('=== 规则A 批次集中度 ===')
print('高集中>40%:', json.dumps(A['hi'], default=str))
print('低集中<=40%:', json.dumps(A['lo'], default=str))
print('高集中日明细:', A['hi_days'][:10])
print('=== 判定 ===')
print(json.dumps(verdict, ensure_ascii=False, indent=1))
acon.close(); qcon.close()
