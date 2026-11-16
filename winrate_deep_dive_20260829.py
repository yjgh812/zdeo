# -*- coding: utf-8 -*-
"""深挖四问(只读研究件, 回应主人'全面么深入么'):
A. 8期名单全样本模拟+逐期银行占比 -> 银行化进程时间线
B. 0824文件结构核实(全市场模式?)
C. 银行板块8月日度位置(8/21因子截面时银行在高位还是低位)
D. 大盘条件交叉 + E 生产口径重算 + F 执行价质量
"""
import csv
import sqlite3
import glob
import json
from collections import defaultdict

BASE = r"D:\zcodesj"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ATTR = BASE + r"\attribution\attribution.db"

qconn = sqlite3.connect(QFQ)
qconn.row_factory = sqlite3.Row
qcur = qconn.cursor()

# 交易日历与行情
qcur.execute("SELECT code, date, open, high, low, close FROM stock_daily_meta_qfq WHERE date >= '20260801'")
px = defaultdict(dict)
cals = set()
for r in qcur.fetchall():
    px[r['code']][r['date']] = (r['open'], r['high'], r['low'], r['close'])
    cals.add(r['date'])
cal = sorted(cals)
cal_idx = {d: i for i, d in enumerate(cal)}

def next_trading_day(d):
    for x in cal:
        if x > d:
            return x
    return None

def daily_mkt_ret():
    """每日市场等权涨跌%"""
    out = {}
    for i in range(1, len(cal)):
        d0, d1 = cal[i-1], cal[i]
        rets = []
        for code, dd in px.items():
            if d0 in dd and d1 in dd and dd[d0][3] and dd[d1][3]:
                rets.append((dd[d1][3] / dd[d0][3] - 1) * 100)
        out[d1] = sum(rets) / len(rets) if rets else 0.0
    return out

mkt_daily = daily_mkt_ret()

def sim_list(rows, exec_date, label):
    """名单模拟: exec_date开盘等权买入, 持有到8/28"""
    rets, banks, det = [], 0, []
    n = 0
    for r in rows:
        n += 1
        name = r.get('name') or ''
        if '银行' in name:
            banks += 1
        if n > 15:
            continue
        code = r['code']
        d = px.get(code, {})
        if exec_date not in d or '20260828' not in d:
            det.append((name, None))
            continue
        ret = (d['20260828'][3] / d[exec_date][0] - 1) * 100
        rets.append(ret)
        det.append((name, ret))
    avg = sum(rets) / len(rets) if rets else None
    pos = sum(1 for v in rets if v > 0)
    return dict(label=label, exec_date=exec_date, n_total=n, banks_in_list=banks,
                banks_top15=sum(1 for nm, _ in det[:15] if nm and '银行' in nm),
                avg=avg, pos=pos, nn=len(rets), det=det)

print('===== A. 8期名单银行化进程 + 全样本模拟(持有到8/28) =====')
files = sorted(glob.glob(BASE + r"\multifactor\reports\daily_top20\*.csv"))
sims = []
for f in files:
    fname = f.split('\\')[-1].replace('.csv', '')
    parts = fname.split('_')
    sig, eff = parts[-2], parts[-1]
    with open(f, encoding='utf-8-sig') as fh:
        rdr = csv.DictReader(fh)
        cols = rdr.fieldnames
        rows = []
        for r in rdr:
            if r.get('rank') and r.get('code'):
                try:
                    rk = int(float(r['rank']))
                    rows.append((rk, r))
                except Exception:
                    pass
        rows.sort(key=lambda x: x[0])
        ordered = [r for _, r in rows]
    ex = next_trading_day(sig) if eff == sig else (eff if eff in cal_idx else next_trading_day(eff))
    s = sim_list(ordered, ex, fname)
    sims.append(s)
    avg_s = f"{s['avg']:+.2f}%" if s['avg'] is not None else '--'
    mkt = None
    # 市场对照: 同窗口等权
    i0, i1 = cal_idx.get(ex), cal_idx.get('20260828')
    if i0 is not None and i1 > i0:
        rets = []
        for code, dd in px.items():
            if ex in dd and '20260828' in dd:
                rets.append((dd['20260828'][3] / dd[ex][0] - 1) * 100)
        mkt = sum(rets) / len(rets)
    exc_s = f"{s['avg']-mkt:+.2f}pp" if (s['avg'] is not None and mkt is not None) else '--'
    print(f"{fname}: 文件{len(ordered)}行 | Top15银行{s['banks_top15']}/15 | 模拟({ex}→8/28) {avg_s} 超额{exc_s} 上涨{s['pos']}/{s['nn']}")

print()
print('===== B. 0824文件结构核实 =====')
f824 = BASE + r"\multifactor\reports\daily_top20\bp_ep_monthly_20260821_20260824.csv"
with open(f824, encoding='utf-8-sig') as fh:
    all_rows = list(csv.DictReader(fh))
print(f"0824文件总行数: {len(all_rows)} (其他期约20行)")
bank_all = sum(1 for r in all_rows if '银行' in (r.get('name') or ''))
print(f"全文件银行行数: {bank_all}")
top50 = [r for r in all_rows if r.get('rank') and float(r['rank']) <= 50]
print(f"rank<=50里银行: {sum(1 for r in top50 if '银行' in (r.get('name') or ''))}/50")

print()
print('===== C. 银行板块8月日度位置(实买12银行等权, 8/3=100) =====')
bank_codes = ['002936', '601916', '601169', '601997', '600926', '601229', '600928', '601009',
              '601328', '000001', '600016', '601818']
eq = {}
base_val = None
for d in cal:
    rets = [px[c][d][3] / px[c]['20260803'][3] * 100 for c in bank_codes if c in px and d in px[c] and '20260803' in px[c]]
    if rets:
        eq[d] = sum(rets) / len(rets)
ks = sorted(eq)
print('  ' + ' '.join(f"{d[4:]}:{eq[d]:.1f}" for d in ks))

print()
print('===== D. 大盘条件交叉: 买入日市场等权涨跌 vs D+3 (170笔) =====')
conn = sqlite3.connect(ATTR)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("""SELECT trade_date, code, name, price, subsystems, list_membership FROM trade_records
WHERE trade_date >= '20260811' AND action='BUY' ORDER BY trade_date, id""")
buys = [dict(r) for r in cur.fetchall()]

def fwd3(code, bd, bp_):
    qc = code[2:] if code[:2] in ('sz', 'sh', 'bj') else code
    d = px.get(qc, {})
    if bd not in d or bd not in cal_idx:
        return None
    j = cal_idx[bd] + 3
    if j >= len(cal):
        return None
    end = cal[j]
    if end not in d:
        return None
    return (d[end][3] / bp_ - 1) * 100

down_day, up_day = [], []
for b in buys:
    if b['trade_date'] not in mkt_daily:
        continue
    f3 = fwd3(b['code'], b['trade_date'], b['price'])
    if f3 is None:
        continue
    (down_day if mkt_daily[b['trade_date']] < 0 else up_day).append(f3)
for label, lst in [('市场跌日买入', down_day), ('市场涨日买入', up_day)]:
    if lst:
        print(f"{label}: n={len(lst)} D+3均值{sum(lst)/len(lst):+.2f}% 中位{sorted(lst)[len(lst)//2]:+.2f}% 胜率{sum(1 for v in lst if v>0)/len(lst)*100:.0f}%")

print()
print('===== E. 生产口径重算(剔除8/11-13实验批+8/26验证批) =====')
EXPERIMENT_DAYS = {'20260811', '20260812', '20260813', '20260826'}
prod = []
for b in buys:
    if b['trade_date'] in EXPERIMENT_DAYS:
        continue
    f3 = fwd3(b['code'], b['trade_date'], b['price'])
    if f3 is not None:
        prod.append(f3)
if prod:
    print(f"生产口径: n={len(prod)} D+3均值{sum(prod)/len(prod):+.2f}% 中位{sorted(prod)[len(prod)//2]:+.2f}% 胜率{sum(1 for v in prod if v>0)/len(prod)*100:.0f}%")

print()
print('===== F. 执行价质量: 实盘买价 vs 当日OHLC均价 =====')
diffs = []
for b in buys:
    qc = b['code'][2:] if b['code'][:2] in ('sz', 'sh', 'bj') else b['code']
    d = px.get(qc, {})
    if b['trade_date'] in d:
        o, h, l, c = d[b['trade_date']]
        vwap_proxy = (o + h + l + c) / 4
        if vwap_proxy > 0:
            diffs.append((b['price'] / vwap_proxy - 1) * 100)
if diffs:
    import statistics as st
    print(f"n={len(diffs)} 实盘价vs当日OHLC均值: 均值{st.mean(diffs):+.2f}% 中位{st.median(diffs):+.2f}% 买贵(>0)占比{sum(1 for v in diffs if v>0)/len(diffs)*100:.0f}% 最贵{max(diffs):+.2f}%")

json.dump([{k: v for k, v in s.items() if k != 'det'} for s in sims],
          open(BASE + r'\reports\winrate_deep_dive_20260829.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\nsaved reports/winrate_deep_dive_20260829.json')
