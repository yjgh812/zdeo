# -*- coding: utf-8 -*-
"""
wf_volume_price_factor — A+B: 量价因子真实口径全景(bp为法定截止口径)
====================================================================
因子池(全部无财报前视; bp为唯一财务项且已过法定披露截止映射):
  turn_low = -(amt/amt_20d)   低换手(成交额比率)
  mom20 / mom60               动量
  rev20 = -mom20              短期反转
  vol_low = -std(ret,20d)     低波动
  bp = bps/close(法定截止)     终审唯一残存财务因子
流程: 月末截面IC(rank spearman vs 次月收益) → 单因子+两两组合
      → Top20/Top50真实执行(月末close,0.4%/次,≤30元,无行业约束=纯因子)
运行: python D:/zcodesj/wf_volume_price_factor.py
"""
import itertools
import sqlite3
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
FIN_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
START, END = 20180101, 20260731
PRE = 20170701          # 预留动量/滚动窗口
COST = 0.004

t0 = time.time()


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


# ── 1. 月末交易日与价格/成交额 ──
log('加载 meta 日线(原始,用于≤30过滤/bp/流动性) + qfq(用于收益因子)...')
con = sqlite3.connect(META_DB)
dates = [r[0] for r in con.execute(
    'SELECT DISTINCT date FROM stock_daily_meta WHERE date BETWEEN ? AND ? ORDER BY date',
    (PRE, END))]
df = pd.DataFrame({'d': dates})
df['ym'] = df['d'].astype(str).str[:6]
me_dates = [int(x) for x in df.groupby('ym')['d'].max().tolist()]
me_dates = [d for d in me_dates if d >= 20180131]
keep = set(me_dates)
raw = pd.read_sql_query(
    'SELECT code, date, close, amount FROM stock_daily_meta '
    'WHERE date BETWEEN ? AND ? AND close > 0', con, params=(PRE, END))
con.close()
qfq_db = "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
con = sqlite3.connect('file:%s?mode=ro' % qfq_db, uri=True)
qfq = pd.read_sql_query(
    'SELECT code, date, close FROM stock_daily_meta_qfq '
    'WHERE date BETWEEN ? AND ? AND close > 0', con, params=(PRE, END))
con.close()
log('原始行=%d | qfq行=%d' % (len(raw), len(qfq)))

raw['date'] = raw['date'].astype(int)
raw = raw.sort_values(['code', 'date'])
qfq['date'] = qfq['date'].astype(int)
qfq = qfq.sort_values(['code', 'date'])
log('构建因子矩阵(qfq收益因子+清洁v2)...')
close_p = qfq.pivot(index='date', columns='code', values='close').sort_index()
amt_p = raw.pivot(index='date', columns='code', values='amount').sort_index()
qfq_me_raw = qfq[qfq['date'].isin(keep)]
pxs_qfq = qfq_me_raw.set_index(['date', 'code'])['close']   # qfq月末价(收益计算用)
del qfq, qfq_me_raw
# 清洁v2: 滚动中位数±3倍锚定(退市仙股天价脏行/连续脏段)
_med = close_p.rolling(21, min_periods=5).median()
_ratio = close_p / _med
_bad = (_ratio > 3) | (close_p.pct_change() > 0.21)   # 只杀上行:天价脏行/涨幅异常;真实暴跌保留
close_p = close_p.mask(_bad).ffill()
del _med, _ratio, _bad
mom20 = close_p.pct_change(20)
mom60 = close_p.pct_change(60)
ret1 = close_p.pct_change()
vol20 = ret1.rolling(20).std()
turn = amt_p / amt_p.rolling(20).mean()
# 仅保留月末行
FAC = {
    'turn_low': -turn.loc[turn.index.isin(keep)],
    'mom20': mom20.loc[mom20.index.isin(keep)],
    'mom60': mom60.loc[mom60.index.isin(keep)],
    'rev20': -mom20.loc[mom20.index.isin(keep)],
    'vol_low': -vol20.loc[vol20.index.isin(keep)],
}
_me_rows = close_p.loc[close_p.index.isin(keep)].copy()
amt20_me = amt_p.rolling(20).mean()
amt20_me = amt20_me.loc[amt20_me.index.isin(keep)]
del mom20, mom60, ret1, vol20, turn, close_p, amt_p
log('月末截面: %d个月' % len(next(iter(FAC.values()))))

# bp(法定截止口径)
log('bp因子(法定披露截止)...')
con = sqlite3.connect('file:%s?mode=ro' % FIN_DB, uri=True)
fin = pd.read_sql_query(
    'SELECT code, report_date, bps FROM finance_daily '
    'WHERE bps > 0 ORDER BY code, report_date', con)
con.close()
fin['report_date'] = fin['report_date'].astype(int)


def deadline(rd):
    y, md = rd // 10000, rd % 10000
    return {331: y * 10000 + 430, 630: y * 10000 + 831,
            930: y * 10000 + 1031, 1231: (y + 1) * 10000 + 430}.get(md, rd)


fin['avail'] = fin['report_date'].map(deadline)
fin = fin.sort_values(['code', 'avail'])
bp_rows = []
for d in me_dates:
    day = fin[fin['avail'] <= d].groupby('code').tail(1)
    day = day.set_index('code')['bps']
    bp_rows.append(pd.Series(day, name=d))
bp_panel = pd.concat(bp_rows, axis=1).T
bp_panel.index = bp_panel.index.astype(int)

con = sqlite3.connect(META_DB)
ph = ','.join('?' * len(me_dates))
snap = pd.read_sql_query(
    'SELECT date, code, close FROM stock_daily_meta WHERE date IN (%s)' % ph,
    con, params=me_dates)
con.close()
pxs_raw = snap.set_index(['date', 'code'])['close']       # 原始月末价(退市判定)
last_raw = snap.reset_index().groupby('code')['date'].max().to_dict()  # 原始最后交易日
pxs = snap.set_index(['date', 'code'])['close']
# 收益端=清洁qfq月末价(_me_rows来自清洁后的close_p=qfq)
_pxs_d = {}
for d in _me_rows.index:
    row = _me_rows.loc[d].dropna()
    for c, v in row.items():
        _pxs_d[(int(d), c)] = v
pxs = pd.Series(_pxs_d)          # 清洁qfq月末价(收益计算)
pxs_clean2 = _pxs_d
close_me = _me_rows.copy()
close_me.index = close_me.index.astype(int)
del _me_rows
close_me = snap.pivot(index='date', columns='code', values='close')
close_me.index = close_me.index.astype(int)
bp_me = (bp_panel / close_me).loc[bp_panel.index.intersection(close_me.index)]
FAC['bp'] = bp_me
log('全部因子就绪: %s' % list(FAC.keys()))

# ── 2. IC(单因子+两两组合) ──
log('月度截面IC...')


def ic_of(fs, me):
    ics = []
    for i, d in enumerate(me[:-1]):
        d1 = me[i + 1]
        try:
            f = fs.loc[d].dropna()
        except KeyError:
            continue
        fwd = {}
        for c in f.index:
            try:
                p0, p1 = pxs.loc[(d, c)], pxs.loc[(d1, c)]
                if p0 > 0 and p1 > 0:
                    fwd[c] = p1 / p0 - 1
            except KeyError:
                pass
        if len(fwd) < 300:
            continue
        s = pd.Series(fwd)
        common = f.index.intersection(s.index)
        if len(common) < 300:
            continue
        ic = spearmanr(f.loc[common], s.loc[common])[0]
        if ic == ic:
            ics.append(ic)
    a = np.array(ics)
    return (a.mean(), a.mean() / (a.std() + 1e-9), (a > 0).mean(), len(a))


me = sorted(set(FAC['turn_low'].index) & set(FAC['bp'].index.dropna(how='all').index) if False else set(FAC['turn_low'].index))


def combo_frame(names):
    """等权pct-rank组合的月末截面DataFrame"""
    ranks = [FAC[n].rank(axis=1, pct=True) for n in names]
    out = ranks[0]
    for r in ranks[1:]:
        out = out.add(r, fill_value=np.nan)
    return out / len(names)


singles = ['turn_low', 'rev20', 'mom20', 'mom60', 'vol_low', 'bp']
print('\n[单因子IC] (月末截面→次月收益, 2018-2026)')
rows = []
for n in singles:
    mu, ir, pr, cnt = ic_of(FAC[n], me)
    rows.append((n, mu, ir, pr, cnt))
    print('  %-10s IC %+.4f  ICIR %+5.2f  正率%3.0f%%  n=%d' % (n, mu, ir, pr * 100, cnt))

print('\n[两两组合IC] (等权rank)')
combos = []
for a, b in itertools.combinations(singles, 2):
    cf = combo_frame([a, b])
    mu, ir, pr, cnt = ic_of(cf, me)
    combos.append(('%s+%s' % (a, b), mu, ir, pr, cnt))
    print('  %-22s IC %+.4f  ICIR %+5.2f  正率%3.0f%%' % (a + '+' + b, mu, ir, pr * 100))

# ── 3. 组合真实成色(Top20/Top50, 月末close, 0.4%/次, ≤30元) ──
log('组合真实成色扫描...')


def portfolio(fs, n_top):
    rets = []
    for i, d in enumerate(me[:-1]):
        d1 = me[i + 1]
        try:
            f = fs.loc[d].dropna()
        except KeyError:
            continue
        day_px = close_me.loc[d]
        liq = amt20_me.loc[d] if d in amt20_me.index else None
        f = f[[c for c in f.index if c in day_px.index
               and day_px[c] == day_px[c] and 0 < day_px[c] <= 30
               and (liq is None or (c in liq.index and liq[c] == liq[c] and liq[c] > 2e7))]]
        if len(f) < n_top * 3:
            continue
        picks = f.nlargest(n_top).index.tolist()
        rs = []
        for c in picks:
            p0 = pxs.get((d, c))
            if p0 is None or p0 != p0 or p0 <= 0:
                p0 = pxs_clean2.get((d, c))
            p1 = pxs.get((d1, c))
            if p1 is None or p1 != p1 or p1 <= 0:
                p1 = pxs_clean2.get((d1, c))
            if p0 and p1 and p0 > 0 and p1 > 0:
                rs.append(p1 / p0 - 1)
        if rs:
            rets.append(np.mean(rs) - COST)
    r = np.array(rets)
    if not len(r):
        return None
    cum = np.cumprod(1 + r)
    n = len(r)
    return (cum[-1] ** (12 / n) - 1, r.mean() / (r.std() + 1e-12) * np.sqrt(12),
            (cum / np.maximum.accumulate(cum) - 1).min(), n)


print('\n[组合真实成色] Top20 | Top50 (无行业约束,纯因子)')
best = []
for n in singles:
    for nt in (20, 50):
        r = portfolio(FAC[n], nt)
        if r:
            best.append(('%s@T%d' % (n, nt),) + r)
MIRROR = {frozenset(('rev20', 'mom20')), frozenset(('rev20', 'mom60'))}
for a, b in itertools.combinations(singles, 2):
    if frozenset((a, b)) in MIRROR:
        continue
    cf = combo_frame([a, b])
    for nt in (20, 50):
        r = portfolio(cf, nt)
        if r:
            best.append(('%s+%s@T%d' % (a, b, nt),) + r)
best.sort(key=lambda x: -x[1])
for name, cagr, sh, mdd, n in best[:15]:
    print('  %-26s CAGR %+7.2f%%  Sharpe %5.2f  MDD %7.2f%%  (%d月)'
          % (name, cagr * 100, sh, mdd * 100, n))

out = r'D:\zcodesj\reports\WF_VOLUME_PRICE_FACTOR_20260815.md'
with open(out, 'w', encoding='utf-8') as f:
    f.write('# 量价因子真实口径全景 (2026-08-15)\n\n```\n见控制台输出\n```\n')
print('\n报告占位: %s | 耗时%.0f秒' % (out, time.time() - t0))
