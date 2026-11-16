# -*- coding: utf-8 -*-
"""胜率独立重算v2：修月份清洗 + 追涨度改用买入价/前收盘价口径（对齐9/17诊断定义）。
追涨度定义核对：9/17诊断=「买入日当日涨>=6%后追入」，原始计算用的是买入时点日内涨幅。
本脚本两种口径都算：(1)当日收盘涨幅(qfq change_pct) (2)买入价/前收-1（更接近"追入时点已涨多少"）
"""
import sqlite3, json, statistics, sys, os
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\zcodesj'
OUT = {}

def wr(n, wins, rets):
    return {'n': n, 'win%': round(100*wins/n, 1) if n else None,
            'mean_pp': round(statistics.mean(rets), 2) if rets else None,
            'median_pp': round(statistics.median(rets), 2) if rets else None}

def norm_date(td):
    return td.replace('-', '') if td else td

# ---------- A2. 月份清洗后的分月/分通道 ----------
conn = sqlite3.connect(os.path.join(ROOT, 'attribution', 'attribution.db'))
cur = conn.cursor()
cur.execute("SELECT trade_date, strategy_id, actual_ret_t5 FROM verification_trades")
rows = cur.fetchall()
conn.close()

by_month = defaultdict(lambda: [0, 0, []])
by_month_chan = defaultdict(lambda: defaultdict(lambda: [0, 0, []]))  # 月->通道
chan_alias = {}
for sid in {r[1] for r in rows}:
    s = sid or ''
    if s.startswith('ungated'):
        chan_alias[sid] = s
    elif 'manual' in s:
        chan_alias[sid] = 'manual'
    elif 'executor' in s or 'gated' in s:
        chan_alias[sid] = 'gated'
    else:
        chan_alias[sid] = s or '(空)'

for td, sid, r5 in rows:
    if r5 is None:
        continue
    m = norm_date(td)[:6]
    ch = chan_alias.get(sid, sid)
    for b in (by_month[m], by_month_chan[m][ch]):
        b[0] += 1; b[1] += (r5 > 0); b[2].append(r5*100)

OUT['A2_分月清洗后'] = {k: wr(*v) for k, v in sorted(by_month.items())}
OUT['A2_分月x通道'] = {m: {c: wr(*v) for c, v in sorted(chs.items())} for m, chs in sorted(by_month_chan.items())}

# ---------- C2. 追涨度双口径（用241笔已实现盈亏样本） ----------
recs = json.load(open(os.path.join(ROOT, 'attribution', 'selection_deep_analysis_20260916.json'), encoding='utf-8'))
qconn = sqlite3.connect(os.path.join(ROOT, 'data', 'dragon_quant_qfq.db'))
qcur = qconn.cursor()

def qfq_prev_close(code, date):
    code6 = code.zfill(6)
    r = qcur.execute("SELECT date, close, change_pct FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 2",
                     (code6, date)).fetchall()
    if not r:
        return None, None, None
    day_chg = float(r[0][2]) if r[0][2] is not None else None
    prev_close = float(r[1][1]) if len(r) >= 2 else None
    return float(r[0][1]), day_chg, prev_close

res = {'口径1_当日收盘涨幅': defaultdict(lambda: [0,0,0.0]),
       '口径2_买入价/前收(日内追涨度)': defaultdict(lambda: [0,0,0.0])}
miss = 0
detail_v2 = []
for r in recs:
    close, day_chg, prev_close = qfq_prev_close(r['code'], r['date'])
    if close is None or prev_close is None or not prev_close:
        miss += 1; continue
    intraday = (r['price']/prev_close - 1) * 100  # 买入时点日内涨幅
    def band(x):
        return '跌/平(<0)' if x < 0 else ('0-3%' if x < 3 else ('3-6%' if x < 6 else '>=6%'))
    for key, x in (('口径1_当日收盘涨幅', day_chg), ('口径2_买入价/前收(日内追涨度)', intraday)):
        if x is None: continue
        b = res[key][band(x)]; b[0] += 1; b[1] += (r['pnl'] > 0); b[2] += r['pnl']
    detail_v2.append({'code': r['code'], 'name': r['name'], 'date': r['date'], 'pnl': r['pnl'], 'intraday': round(intraday, 2)})

OUT['C2_追涨度双口径'] = {'缺失': miss}
for k, bd in res.items():
    OUT['C2_追涨度双口径'][k] = {b: {'n': v[0], 'win%': round(100*v[1]/v[0], 1) if v[0] else None, 'sum_pnl': round(v[2])}
                                 for b, v in sorted(bd.items())}
qconn.close()

# >=6%组明细（日内口径）
hi = sorted([d for d in detail_v2 if d['intraday'] >= 6], key=lambda x: x['intraday'])
OUT['C2_日内>=6%明细'] = hi
OUT['C2_日内追涨度分布'] = {
    'mean': round(statistics.mean(d['intraday'] for d in detail_v2), 2),
    'median': round(statistics.median(d['intraday'] for d in detail_v2), 2),
    '>=6%笔数': len(hi), '>=6%合计pnl': round(sum(d['pnl'] for d in hi)),
}

json.dump(OUT, open(os.path.join(ROOT, 'data', 'winrate_recalc_v2_20260918.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in OUT.items() if k != 'C2_日内>=6%明细'}, ensure_ascii=False, indent=1))
print('\n=== 日内>=6%明细(前12) ===')
for d in hi[:12]:
    print(d['date'], d['code'], d['name'], '日内+%.1f%%' % d['intraday'], 'pnl', d['pnl'])
