# -*- coding: utf-8 -*-
"""百亚深挖StepD: 遗漏维度总扫(只读研究件, 20260915)
对938笔bp_ep历史交易补算4个未测维度并6切片检验:
  新维度: amt_ratio(自身成交额地量分位), repeat(60日内重复入选次数),
          mkt_pos250(上证250bar位置=市场状态), fwd40/fwd60长窗
  切片: S1指纹长窗延续 S2上修剂量效应 S3完整百亚组合(R∧刚披露∧上修)
        S4地量×指纹 S5市场状态×指纹 S6重复入选
边界: 多重切片探索性检验, 方向一致+样本量足者才算候选发现, 全部仅观察。
"""
import sys, csv, bisect, sqlite3, statistics
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
from datetime import datetime

qfq = sqlite3.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)
pit = sqlite3.connect(r'file:D:\zcodesj\factor_pit.db?mode=ro', uri=True)
_hc, _pc = {}, {}
def hist(code):
    if code not in _hc:
        _hc[code] = qfq.execute('SELECT date, close, high, amount FROM stock_daily_meta_qfq WHERE code=? ORDER BY date', (code,)).fetchall()
    return _hc[code]
def pit_rows(code):
    if code not in _pc:
        _pc[code] = pit.execute('SELECT pit_avail_date, ep FROM stock_factors_fundamental_pit_v2 WHERE code=? ORDER BY pit_avail_date', (code,)).fetchall()
    return _pc[code]

# 上证指数
idx = [(r[0].replace('-',''), float(r[1])) for r in list(csv.reader(open(r'data/_sh_index_daily.csv', encoding='utf-8')))[1:] if r and r[0] != 'date']
idx_d = [x[0] for x in idx]; idx_c = [x[1] for x in idx]
def mkt_pos250(d):
    i = bisect.bisect_left(idx_d, d)
    if i >= len(idx_d) or idx_d[i] != d or i < 249: return None
    seg = idx_c[i-249:i+1]
    lo, hi = min(seg), max(seg)
    return (seg[-1]-lo)/(hi-lo)*100 if hi > lo else None

def features(code, d):
    hh = hist(code); dates = [x[0] for x in hh]
    i0 = bisect.bisect_left(dates, d)
    if i0 >= len(dates) or dates[i0] != d or i0 < 61: return None
    seg = hh[max(0, i0-249):i0+1]
    c = np.array([x[1] for x in seg]); hiv = np.array([x[2] for x in seg]); amt = np.array([float(x[3]) for x in seg])
    last = c[-1]; lo, hi = c.min(), hiv.max()
    if hi <= lo: return None
    ret = c[1:]/c[:-1]-1
    amt20 = np.array([amt[max(0,j-19):j+1].mean() for j in range(len(amt))])
    f = {}
    f['pos250'] = (last-lo)/(hi-lo)*100
    f['dist_hi'] = (last/hi-1)*100
    f['ret20'] = (last/c[-21]-1)*100
    f['ret60'] = (last/c[-61]-1)*100
    f['vol20'] = np.std(ret[-20:], ddof=1)*100
    f['amt_ratio'] = float(amt20[-1]/amt20[-250:].max()) if amt20[-250:].max() > 0 else None
    f['R'] = (f['pos250']<=45 and f['dist_hi']<=-30 and f['ret60']<=0 and f['ret20']>=-12 and f['vol20']<=4)
    for nd in (20, 40, 60):
        j = i0+nd
        f[f'fwd{nd}'] = round((hh[j][1]/last-1)*100, 2) if j < len(hh) else None
    # 披露
    rows = [r for r in pit_rows(code) if r[0] <= int(d)]
    if rows:
        days = (datetime.strptime(d, '%Y%m%d') - datetime.strptime(str(rows[-1][0]), '%Y%m%d')).days
        prev_ep = rows[-2][1] if len(rows) >= 2 else None
        jump = (rows[-1][1]/prev_ep-1) if (prev_ep and prev_ep > 0 and rows[-1][1]) else None
    else:
        days, jump = None, None
    f['days_disc'] = days; f['ep_jump'] = jump
    f['mkt'] = mkt_pos250(d)
    return f

trades = list(csv.DictReader(open(r'reports/bp_ep_monthly_production/bp_ep_monthly_trades_full.csv', encoding='utf-8-sig')))
recs = []
for t in trades:
    code = t['股票代码'].strip().zfill(6); d = t['买入日期'].replace('-','')
    fm = features(code, d)
    if fm is None: continue
    fm['code'] = code; fm['date'] = d
    recs.append(fm)
recs.sort(key=lambda r: (r['code'], r['date']))
for i, r in enumerate(recs):
    prior = [x for x in recs[:i] if x['code'] == r['code']
             and 0 < (datetime.strptime(r['date'],'%Y%m%d')-datetime.strptime(x['date'],'%Y%m%d')).days <= 60]
    r['repeat'] = len(prior)
print(f'可算交易 {len(recs)} 笔')

def st(grp, label, key):
    vals = [g[key] for g in grp if g.get(key) is not None]
    if not vals: print(f'{label}: {key} 无样本'); return
    win = sum(1 for v in vals if v > 0)
    print(f'{label}: n={len(vals)} {key}均值{statistics.mean(vals):+.2f}% 中位{statistics.median(vals):+.2f}% 胜率{win/len(vals)*100:.0f}%')

R = [r for r in recs if r['R']]
NR = [r for r in recs if not r['R']]
print('\n== S1 指纹长窗延续 ==')
st(R, 'R通过', 'fwd20'); st(NR, 'R不通过', 'fwd20')
st(R, 'R通过', 'fwd40'); st(NR, 'R不通过', 'fwd40')
st(R, 'R通过', 'fwd60'); st(NR, 'R不通过', 'fwd60')

print('\n== S2 上修剂量效应(仅刚披露<=10天) ==')
fresh = [r for r in recs if r['days_disc'] is not None and r['days_disc'] <= 10]
for lo, hi_, lab in [(0.0, 0.1, '上修0~10%'), (0.1, 0.3, '上修10~30%'), (0.3, 9.9, '上修>30%')]:
    g = [r for r in fresh if r['ep_jump'] is not None and lo < r['ep_jump'] <= hi_]
    st(g, lab, 'fwd20')

print('\n== S3 完整百亚组合: R ∧ 刚披露<=10 ∧ ep_jump>10% ==')
full = [r for r in recs if r['R'] and r['days_disc'] is not None and r['days_disc'] <= 10
        and r['ep_jump'] is not None and r['ep_jump'] > 0.10]
st(full, '完整组合', 'fwd20'); st(full, '完整组合', 'fwd40'); st(full, '完整组合', 'fwd60')
if full:
    yrs = sorted({r['date'][:4] for r in full})
    print(f'  年份分布: {yrs}')

print('\n== S4 地量×指纹 ==')
rp = [r for r in R if r['amt_ratio'] is not None]
g_low = [r for r in rp if r['amt_ratio'] < 0.35]
g_hi = [r for r in rp if r['amt_ratio'] >= 0.35]
st(g_low, 'R∧地量(amt_ratio<0.35)', 'fwd20'); st(g_hi, 'R∧非地量', 'fwd20')

print('\n== S5 市场状态×指纹(fwd20) ==')
ms = [r for r in recs if r['mkt'] is not None]
for lo, hi_, lab in [(0, 33, '市场LOW(<33)'), (33, 67, '市场MID'), (67, 101, '市场HIGH(>67)')]:
    g_all = [r for r in ms if lo <= r['mkt'] < hi_]
    g_R = [r for r in g_all if r['R']]
    if g_R and g_all:
        print(f'  {lab}: 全体n={len(g_all)} fwd20={statistics.mean([x["fwd20"] for x in g_all if x["fwd20"] is not None]):+.2f}% | '
              f'R通过n={len(g_R)} fwd20={statistics.mean([x["fwd20"] for x in g_R if x["fwd20"] is not None]):+.2f}%')

print('\n== S6 重复入选(60日内) ==')
rep2 = [r for r in recs if r['repeat'] >= 1]
rep1 = [r for r in recs if r['repeat'] == 0]
st(rep2, '重复入选', 'fwd20'); st(rep1, '首次入选', 'fwd20')
qfq.close(); pit.close()
