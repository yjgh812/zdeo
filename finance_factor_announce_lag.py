# -*- coding: utf-8 -*-
"""
finance_factor_announce_lag — 财务因子法定披露截止口径重构与重估
================================================================
修正 finance-lookahead-bias-found: 因子可用时间从 report_date 改为法定披露截止
  Q1(03-31)→04-30 | 中报(06-30)→08-31 | 三季报(09-30)→10-31 | 年报(12-31)→次年04-30
重估内容:
  [1] 四因子月度截面IC(rank spearman vs 次月收益): 无滞后 vs 法定截止
  [2] 组合真实成色(向量真实执行,月末close+0.4%/次):
      bp+ep Top20行业≤4(生产口径) | droe+roa Top50(WF-icir口径)
运行: python D:/zcodesj/finance_factor_announce_lag.py
"""
import sqlite3
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

FIN_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
START, END = 20180101, 20260731
TOPN_BP, TOPN_RD = 20, 50

t0 = time.time()


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


def deadline(rd):
    """report_date(YYYYMMDD int) → 法定披露截止(YYYYMMDD int)"""
    y, md = rd // 10000, rd % 10000
    if md == 331:
        return y * 10000 + 430
    if md == 630:
        return y * 10000 + 831
    if md == 930:
        return y * 10000 + 1031
    if md == 1231:
        return (y + 1) * 10000 + 430
    return rd   # 非标准报告期: 原样(极少)


# ── 1. 财务数据+因子(公告滞后版) ──
log('加载 finance_daily...')
con = sqlite3.connect('file:%s?mode=ro' % FIN_DB, uri=True)
fin = pd.read_sql_query(
    'SELECT code, report_date, basic_eps, bps, net_profit, total_equity, total_assets '
    'FROM finance_daily ORDER BY code, report_date', con)
con.close()
fin['report_date'] = fin['report_date'].astype(int)
fin['avail'] = fin['report_date'].map(deadline)
fin = fin.sort_values(['code', 'report_date'])
fin['roe'] = fin['net_profit'] / fin['total_equity']
fin['roe_lag4'] = fin.groupby('code')['roe'].shift(4)
fin['droe'] = fin['roe'] - fin['roe_lag4']
fin['roa'] = fin['net_profit'] / fin['total_assets']
# 年化EPS(同生产口径)
m = fin['report_date'] % 10000 // 100
fin['ann_eps'] = fin['basic_eps'] * m.map({3: 4, 6: 2, 9: 4 / 3, 12: 1}).fillna(4)
fin = fin.dropna(subset=['droe', 'roa', 'bps', 'ann_eps'])
log('财报行=%d (含droe可比)' % len(fin))

# ── 2. 月末交易日+价格 ──
con = sqlite3.connect(META_DB)
dates = [r[0] for r in con.execute(
    'SELECT DISTINCT date FROM stock_daily_meta WHERE date BETWEEN ? AND ? ORDER BY date',
    (START, END))]
df = pd.DataFrame({'d': dates})
df['ym'] = df['d'].astype(str).str[:6]
me_dates = [int(x) for x in df.groupby('ym')['d'].max().tolist()]
ph = ','.join('?' * len(me_dates))
snap = pd.read_sql_query(
    'SELECT date, code, close FROM stock_daily_meta WHERE date IN (%s)' % ph,
    con, params=me_dates)
ind_map = {}
for c2, sec in con.execute('SELECT code, sector_name FROM stock_sector_sw1'):
    ind_map[c2[-6:]] = sec
con.close()
pxs = snap.set_index(['date', 'code'])['close']
log('月末信号日=%d' % len(me_dates))


def cross_section(d, lag):
    """公告滞后版因子截面: avail<=d(-lag) 的最近报告期。lag=0 即法定截止口径。"""
    cutoff = int((pd.Timestamp(str(d)) - pd.Timedelta(days=lag)).strftime('%Y%m%d')) if lag else d
    recs = {}
    day_px = snap[snap['date'] == d].set_index('code')['close']
    for code, g in fin.groupby('code'):
        gg = g[g['avail'] <= cutoff]
        if gg.empty:
            continue
        row = gg.iloc[-1]
        close = day_px.get(code)
        if not close or close != close or close <= 0 or close > 30:
            continue
        recs[code] = dict(droe=row['droe'], roa=row['roa'],
                          bp=row['bps'] / close, ep=0.0)
        if 0 < close / row['ann_eps'] < 500:
            recs[code]['ep'] = 1.0 / (close / row['ann_eps'])
    return recs


# ── 3. IC对比 ──
log('[1] 月度截面IC对比(rank spearman vs 次月收益)...')
me_arr = me_dates


def ic_table(lag, label):
    ics = {k: [] for k in ('droe', 'roa', 'bp', 'ep', 'bp_ep')}
    for i, d in enumerate(me_arr[:-1]):
        d1 = me_arr[i + 1]
        recs = cross_section(d, lag)
        if len(recs) < 200:
            continue
        fwd = []
        for c, f in recs.items():
            try:
                p0, p1 = pxs.loc[(d, c)], pxs.loc[(d1, c)]
                if p0 > 0 and p1 > 0:
                    fwd.append((c, f, p1 / p0 - 1))
            except KeyError:
                pass
        if len(fwd) < 150:
            continue
        df2 = pd.DataFrame([(c, f['droe'], f['roa'], f['bp'], f['ep'])
                            for c, f, _ in fwd],
                           columns=['code', 'droe', 'roa', 'bp', 'ep'])
        df2['fwd'] = [r for _, _, r in fwd]
        df2['bp_ep'] = (df2['bp'].rank(pct=True) + df2['ep'].rank(pct=True)) / 2
        for k in ics:
            v = df2[k].dropna()
            sub = df2.loc[v.index]
            ic = spearmanr(sub[k], sub['fwd'])[0]
            if ic == ic:
                ics[k].append(ic)
    out = []
    for k, arr in ics.items():
        a = np.array(arr)
        out.append('%s:%+.4f(%+.2f,正率%.0f%%)' % (k, a.mean(), a.mean() / (a.std() + 1e-9), (a > 0).mean() * 100))
    print('  %-14s n=%d  %s' % (label, len(ics['droe']), ' | '.join(out)))


ic_table(0, '法定截止口径')
ic_table(60, '再延60天(极保守)')

# ── 4. 组合真实成色对比 ──
log('[2] 组合真实成色(月末close,成本0.4%/次)...')


def portfolio(lag, mode):
    rets = []
    for i, d in enumerate(me_arr[:-1]):
        d1 = me_arr[i + 1]
        recs = cross_section(d, lag)
        if len(recs) < 200:
            continue
        dfx = pd.DataFrame.from_dict(recs, orient='index')
        if mode == 'bp_ep':
            dfx = dfx[(dfx['ep'] > 0)]
            dfx['combo'] = (dfx['ep'].rank(pct=True) + dfx['bp'].rank(pct=True)) / 2
            n_top = TOPN_BP
        else:
            dfx['combo'] = (dfx['droe'].rank(pct=True) + dfx['roa'].rank(pct=True)) / 2
            n_top = TOPN_RD
        dfx = dfx.sort_values('combo', ascending=False)
        cap = 4 if mode == 'bp_ep' else 999
        picks, used = [], {}
        for code in dfx.index:
            if len(picks) >= n_top:
                break
            ind = ind_map.get(code, 'UNKNOWN')
            if used.get(ind, 0) >= cap:
                continue
            used[ind] = used.get(ind, 0) + 1
            picks.append(code)
        rs = []
        for c in picks:
            try:
                p0, p1 = pxs.loc[(d, c)], pxs.loc[(d1, c)]
                if p0 > 0 and p1 > 0:
                    rs.append(p1 / p0 - 1)
            except KeyError:
                pass
        if rs:
            rets.append(np.mean(rs) - 0.004)
    r = np.array(rets)
    cum = np.cumprod(1 + r)
    n = len(r)
    return (cum[-1] ** (12 / n) - 1, r.mean() / (r.std() + 1e-12) * np.sqrt(12),
            (cum / np.maximum.accumulate(cum) - 1).min(), n)


print('\n' + '=' * 78)
print('财务因子法定披露截止口径 重估 (2018-01~2026-07, ≤30元)')
print('=' * 78)
for mode, name in (('bp_ep', 'bp+ep Top20行业≤4(生产口径)'),
                   ('rd', 'droe+roa Top50(WF-icir口径)')):
    a0 = portfolio(0, mode)
    a1 = portfolio(60, mode)
    print('  %s' % name)
    print('    法定截止口径:   CAGR %+7.2f%%  Sharpe %5.2f  MDD %7.2f%%  (%d月)'
          % (a0[0] * 100, a0[1], a0[2] * 100, a0[3]))
    print('    再延60天(极保守): CAGR %+7.2f%%  Sharpe %5.2f  MDD %7.2f%%  (%d月)'
          % (a1[0] * 100, a1[1], a1[2] * 100, a1[3]))
print('\n对照(前视污染版): bp+ep无滞后+8.80%%/滞后60天-1.55%%/滞后120天-6.11%%; '
      'droe+roa无滞后+34.51%%/滞后60天+5.77%%/滞后120天-2.39%%')
print('耗时%.0f秒' % (time.time() - t0))
