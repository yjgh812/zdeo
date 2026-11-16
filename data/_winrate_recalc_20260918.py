# -*- coding: utf-8 -*-
"""胜率独立重算 2026-09-18：不复用报告统计代码，从原始库/CSV重新聚合，用于与已有分析对比。
A. verification_trades 分月/分通道 T+5 胜率（attribution.db 独立复算）
B. 8-9月241笔逐笔 json 重切片（对齐 selection_deep_analysis_20260916 口径交叉验证）
C. 追涨度阶梯独立复现（qfq 库独立取买入日当日涨幅）
D. 回测产物独立统计（bp_ep/ml 全笔 vs 退出段胜率, exit_reason 分解）
输出: data/winrate_recalc_20260918.json
"""
import sqlite3, json, csv, statistics, sys, os
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\zcodesj'
OUT = {}

def wr(n, wins, rets):
    return {'n': n, 'win%': round(100*wins/n, 1) if n else None,
            'mean_pp': round(statistics.mean(rets), 2) if rets else None,
            'median_pp': round(statistics.median(rets), 2) if rets else None}

# ---------- A. verification_trades 独立复算 ----------
conn = sqlite3.connect(os.path.join(ROOT, 'attribution', 'attribution.db'))
cur = conn.cursor()
cur.execute("SELECT trade_date, stock_code, stock_name, strategy_id, actual_ret_t5 FROM verification_trades")
rows = cur.fetchall()
conn.close()

by_month = defaultdict(lambda: [0, 0, []])   # n, wins, rets(已到期)
by_chan = defaultdict(lambda: [0, 0, []])
chan_alias = {}
for sid in {r[3] for r in rows}:
    s = sid or ''
    if s.startswith('ungated'):
        chan_alias[sid] = s  # 细分保留
    elif 'manual' in s:
        chan_alias[sid] = 'manual'
    elif 'executor' in s or 'gated' in s:
        chan_alias[sid] = 'gated(trade_executor)'
    else:
        chan_alias[sid] = s or '(空)'

total_due = 0
for td, code, name, sid, r5 in rows:
    if r5 is None:
        continue
    total_due += 1
    m = td[:6]
    for bucket, key in ((by_month, m), (by_chan, chan_alias.get(sid, sid))):
        b = bucket[key]; b[0] += 1; b[1] += (r5 > 0); b[2].append(r5*100)

OUT['A_verification_trades'] = {
    '总笔数': len(rows), 'T5已到期': total_due,
    '分月_T5': {k: wr(*v) for k, v in sorted(by_month.items())},
    '分通道_T5': {k: wr(*v) for k, v in sorted(by_chan.items())},
}

# ---------- B+C. 241笔逐笔重切片 + 追涨度阶梯(qfq独立取当日涨幅) ----------
recs = json.load(open(os.path.join(ROOT, 'attribution', 'selection_deep_analysis_20260916.json'), encoding='utf-8'))
qconn = sqlite3.connect(os.path.join(ROOT, 'data', 'dragon_quant_qfq.db'))
qcur = qconn.cursor()
qcur.execute("PRAGMA table_info(stock_daily_meta_qfq)")
qcols = [r[1] for r in qcur.fetchall()]
# 自适应找列
c_code = 'code' if 'code' in qcols else ('stock_code' if 'stock_code' in qcols else qcols[0])
c_date = 'date' if 'date' in qcols else ('trade_date' if 'trade_date' in qcols else [c for c in qcols if 'date' in c][0])
c_chg = 'change_pct' if 'change_pct' in qcols else None
c_close = 'close' if 'close' in qcols else None
OUT['_qfq_cols'] = qcols[:15]

def qfq_chg(code, date):
    """返回当日涨幅(百分数)。优先 change_pct，否则 close/prev_close。"""
    code6 = code.zfill(6)
    q = f"SELECT {c_date},{c_chg if c_chg else c_close} FROM stock_daily_meta_qfq WHERE {c_code}=? AND {c_date}<=? ORDER BY {c_date} DESC LIMIT 2"
    r = qcur.execute(q, (code6, date)).fetchall()
    if not r:
        return None
    if c_chg:
        return float(r[0][1])
    if len(r) >= 2 and r[1][1]:
        return round((float(r[0][1])/float(r[1][1]) - 1) * 100, 3)
    return None

# B: 重切片
def bucket_stat(key, lo, hi):
    sel = [r for r in recs if r.get(key) is not None and lo <= r[key] < hi]
    n = len(sel); wins = sum(1 for r in sel if r['pnl'] > 0)
    s = sum(r['pnl'] for r in sel)
    return {'n': n, 'win%': round(100*wins/n, 1) if n else None, 'sum': round(s)}

B = {'all': {'n': len(recs), 'win%': round(100*sum(1 for r in recs if r['pnl'] > 0)/len(recs), 1),
             'sum': round(sum(r['pnl'] for r in recs))}}
for lo, hi in [(0,20),(20,40),(40,60),(60,80),(80,101)]:
    B[f'pos_{lo}-{hi if hi<101 else "100"}'] = bucket_stat('pos250', lo, hi)
    B[f'turn_{lo}-{hi if hi<101 else "100"}'] = bucket_stat('turn_pctl', lo, hi)
for lo, hi in [(0,20),(20,40),(40,60),(60,80),(80,101)]:
    pass
B['turn_>=80'] = bucket_stat('turn_pctl', 80, 1e9)
B['pos_60-80'] = bucket_stat('pos250', 60, 80)
OUT['B_rebucket_241'] = B

# C: 追涨度阶梯（当日涨幅分档 × 胜率/盈亏）
ladder = defaultdict(lambda: [0, 0, 0.0])  # n, wins, sum_pnl
miss = 0
for r in recs:
    chg = qfq_chg(r['code'], r['date'])
    if chg is None:
        miss += 1; continue
    band = '跌/平(<0)' if chg < 0 else ('0-3%' if chg < 3 else ('3-6%' if chg < 6 else '>=6%'))
    b = ladder[band]; b[0] += 1; b[1] += (r['pnl'] > 0); b[2] += r['pnl']
OUT['C_chase_ladder'] = {'qfq缺失笔数': miss,
    'bands': {k: {'n': v[0], 'win%': round(100*v[1]/v[0], 1) if v[0] else None, 'sum_pnl': round(v[2])}
              for k, v in ladder.items()}}
qconn.close()

# ---------- D. 回测产物独立统计 ----------
def bt_stats(path):
    with open(path, encoding='utf-8-sig') as f:
        rd = list(csv.DictReader(f))
    def g(t, *names):
        for nm in names:
            if nm in t:
                return t[nm]
        raise KeyError(names)
    allwins = sum(1 for t in rd if float(g(t, 'pnl', '盈亏金额')) > 0)
    byreason = defaultdict(lambda: [0, 0])
    byyear = defaultdict(lambda: [0, 0])
    for t in rd:
        w = float(g(t, 'pnl', '盈亏金额')) > 0
        reason = g(t, 'exit_reason', '卖出触发理由')
        byreason[reason][0] += 1; byreason[reason][1] += w
        y = g(t, 'exit_date', '卖出日期')[:4]
        byyear[y][0] += 1; byyear[y][1] += w
    return {'n': len(rd), 'win%': round(100*allwins/len(rd), 1),
            'exit_reason': {k: {'n': v[0], 'win%': round(100*v[1]/v[0], 1)} for k, v in sorted(byreason.items())},
            '分年win%': {k: round(100*v[1]/v[0], 1) for k, v in sorted(byyear.items())}}

base = os.path.join(ROOT, 'reports', 'drift_check_20260918a_0917end')
OUT['D_backtest'] = {
    'bp_ep_core': bt_stats(os.path.join(base, 'bp_ep_core_trades_full.csv')),
    'ml_signal': bt_stats(os.path.join(base, 'ml_signal_trades_full.csv')),
    'breakout': bt_stats(os.path.join(base, 'breakout_signal_trades_full.csv')),
}

json.dump(OUT, open(os.path.join(ROOT, 'data', 'winrate_recalc_20260918.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(OUT, ensure_ascii=False, indent=1))
