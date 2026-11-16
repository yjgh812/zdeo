# -*- coding: utf-8 -*-
"""
wficir_top50_evaluation — WF-icir Top50 全面评估(选型前)
======================================================
四块:
  [A] 分年度表现 vs 沪深300(Alpha一致性/环境分布)
  [B] 参数邻域敏感性: TopN{30,50,80} × train{250,500,750} × 调仓{月,季}
  [C] 因子消融: droe单/roa单/等权权重/icir权重
  [D] 落地要素: 当前Top50名单(名称经stock_info核实) + 与bp+ep持仓重合度
执行模型: 向量真实执行(与backtrader全约束已双引擎闭合,误差0.35pp)
运行: python D:/zcodesj/wficir_top50_evaluation.py
"""
import sqlite3
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd
import factor_combo_walkforward as fcw

t0 = time.time()


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


REPORT = []
def rep(s=''):
    print(s)
    REPORT.append(s)


# ── 信号管线(加载一次) ──
log('加载信号管线...')
src = open(r'D:\zcodesj\bt_wficir_realistic_recheck.py',
           encoding='utf-8').read().splitlines(keepends=True)
cut = next(i for i, l in enumerate(src)
           if l.startswith("# ------------------------------------------------ L1 "))
g = {'__name__': 'm'}
exec(compile(''.join(src[:cut]), 'sig', 'exec'), g)
reb_dates = g['reb_dates']
prices = g['prices']
st_codes = g['st_codes']
z_panels, ic_series = g['z_panels'], g['ic_series']
SLIP, STAMP, TRANSFER, COMM, COMM_MIN = (g['SLIP'], g['STAMP'],
                                         g['TRANSFER'], g['COMM'], g['COMM_MIN'])
CASH = g['CASH']
NORM = dict(combo=['droe', 'roa'], train=500, n_top=50, freq=1)   # 基准配置
log('管线就绪')


def make_score_fn(combo, train, weighting='icir'):
    ics = pd.DataFrame({k: ic_series[k] for k in combo}).dropna()
    zp = {n: z_panels[n] for n in combo}
    dmap = {n: zp[n].index.get_level_values('date').unique().sort_values()
            for n in combo}

    def score(date):
        hist = ics[ics.index < date].tail(train)
        if len(hist) < 120:
            return None
        if weighting == 'equal':
            w = np.ones(len(combo)) / len(combo)
        else:
            w = fcw.weights_from_train(hist, weighting)
        parts = []
        for i, name in enumerate(combo):
            avail = dmap[name]
            pos = avail.searchsorted(date, side='right') - 1
            if pos < 0:
                return None
            zdate = avail[pos]
            stale = int(prices.index.searchsorted(date)
                        - prices.index.searchsorted(zdate))
            if stale > 60:
                return None
            parts.append(zp[name].xs(zdate, level='date') * w[i])
        s = sum(parts).dropna()
        day_prices = prices.loc[date] if date in prices.index else None
        if day_prices is None:
            return None
        valid = [c for c in s.index if c not in st_codes
                 and c in day_prices.index and day_prices[c] <= 30
                 and not np.isnan(day_prices[c])]
        s = s[valid]
        return s if len(s) >= 50 else None
    return score


def simulate(combo, train, n_top, freq=1, weighting='icir'):
    """向量真实执行(双引擎已验证), 返回月度nav/年收益/换手/持仓数。"""
    score_fn = make_score_fn(combo, train, weighting)
    cash = CASH
    shares = {}
    nav_m, dates_m, holds, turns = [], [], [], []
    prev = None
    step = 0
    for rd_ in reb_dates:
        step += 1
        if (step - 1) % freq != 0:      # 季度=每3个月
            nxt = rd_ + pd.offsets.MonthBegin(1)
            days = prices.index[(prices.index >= rd_) & (prices.index < nxt)]
            if len(days):
                row = prices.loc[days[-1]]
                px0 = prices.loc[rd_]
                mkt = sum(q * (row.get(c, np.nan) if row.get(c, np.nan) == row.get(c, np.nan)
                               else px0.get(c, np.nan)) for c, q in shares.items())
                nav_m.append(cash + mkt); dates_m.append(days[-1])
            continue
        s = score_fn(rd_)
        if s is None:
            continue
        holdings = s.nlargest(n_top).index.tolist()
        new_set = set(holdings)
        px = prices.loc[rd_]
        for c in list(shares):
            if c in new_set:
                continue
            p = px.get(c)
            if p is None or p != p or shares[c] <= 0:
                continue
            v = shares.pop(c) * p
            proceeds = v * (1 - SLIP)
            fee = proceeds * (STAMP + TRANSFER) + max(proceeds * COMM, COMM_MIN)
            cash += proceeds - fee
        mkt_now = sum(sh * px.get(c, np.nan) for c, sh in shares.items()
                      if not pd.isna(px.get(c)))
        budget = (cash + mkt_now) * 0.98 / len(new_set)
        for c in sorted(new_set - set(shares)):
            p = px.get(c)
            if p is None or p != p or p <= 0:
                continue
            n_lot = int(budget / (p * (1 + COMM + TRANSFER + SLIP)) / 100)
            if n_lot <= 0:
                continue
            q = n_lot * 100
            v = q * p
            fee = v * TRANSFER + max(v * COMM, COMM_MIN)
            cost = v * (1 + SLIP) + fee
            if cost > cash:
                q *= cash / cost
                v, cost = q * p, cash
            cash -= cost
            shares[c] = shares.get(c, 0) + q
        nxt = rd_ + pd.offsets.MonthBegin(1)
        days = prices.index[(prices.index >= rd_) & (prices.index < nxt)]
        if len(days):
            row = prices.loc[days[-1]]
            mkt = 0.0
            for c, q in shares.items():
                p = row.get(c, np.nan)
                if p is None or p != p:
                    p = px.get(c, np.nan)
                if p == p:
                    mkt += q * p
            nav_m.append(cash + mkt); dates_m.append(days[-1])
            holds.append(len(shares))
            if prev is not None:
                turns.append(1 - len(new_set & prev) / max(len(new_set), 1))
            prev = new_set
    nav = pd.Series(nav_m, index=pd.DatetimeIndex(dates_m)) / CASH
    return nav, dict(hold=np.mean(holds) if holds else 0,
                     turn=np.mean(turns) if turns else 0)


def stats(nav):
    n = len(nav) / 12
    r = nav.pct_change().dropna()
    return dict(cagr=nav.iloc[-1] ** (1 / n) - 1,
                sharpe=r.mean() / (r.std() + 1e-12) * np.sqrt(12),
                mdd=(nav / nav.cummax() - 1).min(),
                win=(r > 0).mean())


# ── 沪深300基准 ──
con = sqlite3.connect(r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro', uri=True)
hs300 = pd.read_sql_query(
    'SELECT date, close FROM index_daily_meta WHERE code=\'000300\' AND date BETWEEN 20180101 AND 20260731 ORDER BY date',
    con).set_index(pd.to_datetime(
        pd.read_sql_query('SELECT date FROM index_daily_meta WHERE code=\'000300\' AND date BETWEEN 20180101 AND 20260731 ORDER BY date', con)['date'].astype(str), format='%Y%m%d'))
con.close()
hs300_m = hs300['close'].resample('ME').last()

# ═══════════ [A] 分年度 ═══════════
log('[A] 基准配置模拟...')
nav, info = simulate(**NORM)
st = stats(nav)
rep('\n' + '=' * 78)
rep('[A] WF-icir Top50 真实执行 分年度 vs 沪深300 (基准配置 droe+roa/icir500/Top50/月度)')
rep('=' * 78)
rep('  整体: CAGR %.2f%% Sharpe %.2f MDD %.2f%% 月胜率 %.0f%% 持仓%.0f 月换手%.0f%%'
    % (st['cagr'] * 100, st['sharpe'], st['mdd'] * 100, st['win'] * 100,
       info['hold'], info['turn'] * 100))
rep('  %-6s %10s %10s %10s %10s' % ('年份', '策略', '沪深300', '超额', '年内MDD'))
for y, gr in nav.groupby(nav.index.year):
    ynav = gr / gr.iloc[0]
    b = hs300_m[hs300_m.index.year == y]
    br = (b.iloc[-1] / b.iloc[0] - 1) if len(b) > 1 else 0.0
    ymdd = (ynav / ynav.cummax() - 1).min()
    rep('  %-6d %+9.1f%% %+9.1f%% %+9.1f%% %+9.1f%%'
        % (y, (ynav.iloc[-1] - 1) * 100, br * 100,
           (ynav.iloc[-1] - 1 - br) * 100, ymdd * 100))

# ═══════════ [B] 参数敏感性 ═══════════
log('[B] 参数邻域敏感性...')
rep('\n' + '=' * 78)
rep('[B] 参数敏感性: CAGR%%(Sharpe) — 行=TopN, 列=train窗; 另附调仓频率与月/季')
rep('=' * 78)
for freq, fname in ((1, '月度'), (3, '季度')):
    rep('  ── %s调仓 ──' % fname)
    rep('  %-8s %12s %12s %12s' % ('', 'train250', 'train500', 'train750'))
    for n_top in (30, 50, 80):
        row = ['  Top%-5d' % n_top]
        for train in (250, 750 if False else 500, 750):
            nv, _ = simulate(['droe', 'roa'], train, n_top, freq)
            s = stats(nv)
            row.append('%6.1f(%.2f)' % (s['cagr'] * 100, s['sharpe']))
        rep(' '.join(row))

# ═══════════ [C] 因子消融 ═══════════
log('[C] 因子消融...')
rep('\n' + '=' * 78)
rep('[C] 因子/权重消融 (Top50, train500, 月度)')
rep('=' * 78)
for label, kw in (
        ('droe单因子', dict(combo=['droe'], train=500, n_top=50, freq=1)),
        ('roa单因子', dict(combo=['roa'], train=500, n_top=50, freq=1)),
        ('droe+roa等权', dict(combo=['droe', 'roa'], train=500, n_top=50, freq=1, weighting='equal')),
        ('droe+roa icir(基准)', dict(combo=['droe', 'roa'], train=500, n_top=50, freq=1)),
        ('droe+roa+bp三因子', None)):
    if kw is None:
        continue
    nv, _ = simulate(**kw)
    s = stats(nv)
    rep('  %-22s CAGR %7.2f%%  Sharpe %6.2f  MDD %8.2f%%  月胜率%.0f%%'
        % (label, s['cagr'] * 100, s['sharpe'], s['mdd'] * 100, s['win'] * 100))

# ═══════════ [D] 当前名单+bp+ep重合 ═══════════
log('[D] 当前Top50名单与bp+ep重合度...')
score_fn = make_score_fn(['droe', 'roa'], 500)
last = reb_dates[-1]
s = score_fn(last)
top50 = s.nlargest(50).index.tolist()
con = sqlite3.connect(r'file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro', uri=True)
names = dict(con.execute('SELECT code, name FROM stock_info').fetchall())
con.close()
rep('\n' + '=' * 78)
rep('[D] 最新信号日 %s 的Top50(名称经stock_info核实):' % pd.Timestamp(last).date())
rep('=' * 78)
line, lines = [], []
for i, c in enumerate(top50, 1):
    line.append('%s %s' % (c, names.get(c, '?')))
    if len(line) == 5:
        lines.append('  ' + ' | '.join(line)); line = []
if line:
    lines.append('  ' + ' | '.join(line))
rep('\n'.join(lines))
unk = [c for c in top50 if names.get(c) in (None, '?')]
rep('  未核实名称: %d只 %s' % (len(unk), unk if unk else '(无)'))

# bp+ep当前Top20(生产口径)重合度
sys.path.insert(0, r'D:\zcodesj')
from bt_bp_ep_realistic_backtest import month_end_dates as bp_me, build_signals as bp_sig
me_dates = bp_me()
ind_map = {}
con2 = sqlite3.connect(r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro', uri=True)
for c2, sec in con2.execute('SELECT code, sector_name FROM stock_sector_sw1'):
    ind_map[c2[-6:]] = sec
con2.close()
bpsig, _, _ = bp_sig(me_dates, ind_map)
bp_cur = bpsig[max(bpsig)]
inter = set(top50) & set(bp_cur)
rep('  与bp+ep最新Top20重合: %d只 %s' % (len(inter), sorted(inter)))

# 报告落盘
out = r'D:\zcodesj\reports\WFICIR_TOP50_EVALUATION_20260815.md'
with open(out, 'w', encoding='utf-8') as f:
    f.write('# WF-icir Top50 选型前全面评估 (2026-08-15)\n\n```\n'
            + '\n'.join(REPORT) + '\n```\n')
rep('\n报告已落盘: %s | 总耗时%.0f秒' % (out, time.time() - t0))
