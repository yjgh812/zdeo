# -*- coding: utf-8 -*-
"""
bt_wficir_realistic_recheck — WF-icir(droe+roa)组合 真实成色复测
================================================================
宣称基准(8/15报告): 十年(2016-01~2026-07) CAGR 33.2% / Sharpe 1.37 / MDD -34%
方法: 具体问题具体分析,四层递进,每层只改一个维度:
  L1 原口径复刻   Top10%≈400只, 日频等权再平衡(免费), 换手成本0.1%单边
                  → 验证能否复现33.2%
  L2 真实费用+月度漂移  月内持有不动(权重漂移), 调仓只换差集,
                  真实费用(佣金万2.5最低5元+印花0.05%卖+过户万0.1+滑点0.1%)
                  → 量化"日频免费再平衡"理想化+真实费用
  L3 +整手+资金约束  200万资金/400只≈5000元目标仓位: 100股整手取整,
                  最低佣金5元重税, 低价股严重欠配
                  → 结构性执行损耗
  L4 Top50合规版  主人铁律持仓≤50只: n_top=50, 真实费用+整手
                  → 可执行版本的真实成色
注: 本复测为向量精确模拟(费用/整手/漂移逐股计算); T+1/封板/停牌拦截
未建模——bp+ep实测该类拦截对月度策略影响≈0.5pp内,此处如实声明。
运行: python D:/zcodesj/bt_wficir_realistic_recheck.py
"""
import sys
import time
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, r'D:\zcodesj')
sys.path.insert(0, r'D:\zcodesj')   # fcw imports afd via parent dir

import numpy as np
import pandas as pd

import factor_combo_walkforward as fcw
import alphalens_factor_diagnosis as afd

START, END = '20160104', '20260731'
COMBO = ['droe', 'roa']
TRAIN = 500
CASH = 2_000_000.0
# 真实费率(与 bt_ashare_realistic 默认一致)
COMM, COMM_MIN = 0.00025, 5.0
STAMP, TRANSFER, SLIP = 0.0005, 0.00001, 0.001
N_TOP_COMPLIANT = 50

t0 = time.time()


def log(m):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), m), flush=True)


# ------------------------------------------------ 1. 数据与因子
log('加载价格与因子(复刻原脚本口径)...')
prices = fcw.load_price_pivot(START, END)
st_codes = fcw.load_st_codes()
log('价格 %d日 × %d股 | ST %d只' % (prices.shape[0], prices.shape[1], len(st_codes)))

afd.START_DATE = '%d%s' % (int(START[:4]) - 1, START[4:])
afd.END_DATE = END
date_slice = prices.index
z_panels, ic_series = {}, {}
fwd = (prices.shift(-20) / prices - 1).stack()
fwd.index.names = ['date', 'asset']
for name in COMBO:
    desc, loader = afd.FACTOR_REGISTRY[name]
    s = loader(date_slice)
    z = fcw.cs_rank_z(s).dropna()
    z_panels[name] = z
    ic_series[name] = fcw.compute_ic_series(z, fwd)
    log('%s: %d样本 IC=%+.4f' % (name, len(s), ic_series[name].mean()))
del fwd
combo_ics = pd.DataFrame({k: ic_series[k] for k in COMBO}).dropna()
all_z_dates = {n: z_panels[n].index.get_level_values('date').unique().sort_values()
               for n in COMBO}


def rd_score(date, n_top_mode='pct', n_fixed=N_TOP_COMPLIANT):
    """WF-icir打分(严格无未来): 权重=截至date的IC历史tail(500) icir。"""
    hist = combo_ics[combo_ics.index < date].tail(TRAIN)
    if len(hist) < 120:
        return None, None
    w = fcw.weights_from_train(hist, 'icir')
    parts, max_stale = [], 0
    for i, name in enumerate(COMBO):
        avail = all_z_dates[name]
        pos = avail.searchsorted(date, side='right') - 1
        if pos < 0:
            return None, None
        zdate = avail[pos]
        stale = int(prices.index.searchsorted(date) - prices.index.searchsorted(zdate))
        if stale > 60:
            return None, None
        max_stale = max(max_stale, stale)
        parts.append(z_panels[name].xs(zdate, level='date') * w[i])
    score = sum(parts).dropna()
    day_prices = prices.loc[date] if date in prices.index else None
    if day_prices is None:
        return None, None
    valid = [c for c in score.index if c not in st_codes
             and c in day_prices.index and day_prices[c] <= 30
             and not np.isnan(day_prices[c])]
    score = score[valid]
    if len(score) < 50:
        return None, None
    if n_top_mode == 'pct':
        n_top = max(5, int(len(score) * 0.10))
    else:
        n_top = min(n_fixed, len(score))
    return score.nlargest(n_top).index.tolist(), max_stale


reb_dates = fcw.month_starts(prices.index,
                             from_date=prices.index[min(TRAIN, len(prices.index) - 1)])
daily_ret = prices.pct_change().clip(-0.21, 0.21)

# 清洁价格矩阵v2: 滚动中位数锚定(价偏离21日中位数±3倍=脏行)∪单日|涨跌|>21%。
# v1缺陷实证: 连续多日脏价段中间行pct≈0不触发单日掩码,清洁序列自己产生
# 0.51→12154跳变(000150清洁月收益+228万%),脏红利可被月初调仓锁定
_med = prices.rolling(21, min_periods=5).median()
_ratio = prices / _med
_bad = (_ratio > 3) | (_ratio < 1.0 / 3) | (prices.pct_change().abs() > 0.21)
clean_prices = prices.mask(_bad).ffill()
log('价格清洁v2: 剔除错误行%d个,涉及%d只(滚动中位数锚定)'
    % (int(_bad.sum().sum()), int((_bad.sum() > 0).sum())))
prices = clean_prices
log('调仓期数=%d' % len(reb_dates))


def perf(navs, label, base=1.0):
    nav = pd.Series(navs) / base
    n = len(nav) / 252
    cagr = nav.iloc[-1] ** (1 / n) - 1 if n > 0 else 0
    r = nav.pct_change().dropna()
    sh = r.mean() / (r.std() + 1e-12) * np.sqrt(252)
    mdd = (nav / nav.cummax() - 1).min()
    print('  %-16s CAGR %7.2f%%  Sharpe %6.2f  MDD %8.2f%%  (总倍数×%.2f)'
          % (label, cagr * 100, sh, mdd * 100, nav.iloc[-1]))
    return dict(cagr=cagr, sharpe=sh, mdd=mdd, final=nav.iloc[-1])


# ------------------------------------------------ L1 原口径复刻
log('L1 原口径复刻(日频等权,0.1%单边)...')

def _l1_score_fn(date):
    h, stale = rd_score(date, 'pct')
    if h is None:
        return None
    return pd.Series(1.0, index=h), stale

l1 = fcw.simulate_portfolio(_l1_score_fn, prices, reb_dates, cost=0.001)
print('\n[L1] 原口径复刻(宣称CAGR 33.2%):')
perf(l1.nav, 'L1-原口径')


# ------------------------------------------------ L2/L3/L4 真实模拟器
def real_sim_v2(n_top_mode, use_lots, capital=CASH, n_fixed=N_TOP_COMPLIANT,
                light_cost=False):
    """真实执行模拟: cash/持仓市值显式分离, 月度换差集, 月内漂移。
    light_cost=True 用原口径轻成本(0.1%单边),False 用真实费用。"""
    cash = capital
    shares = {}                        # code -> 股数(可为小数=无整手)
    navs, dates_out = [], []
    stats = {'orders': 0, 'floor': 0, 'skip': 0, 'hold_n': []}
    for rd_ in reb_dates:
        nxt = rd_ + pd.offsets.MonthBegin(1)
        days = prices.index[(prices.index >= rd_) & (prices.index < nxt)]
        if len(days) == 0:
            continue
        holdings, _ = rd_score(rd_, n_top_mode, n_fixed)
        if holdings is None:
            for d in days:
                navs.append(cash + _mkt(shares, prices, rd_)); dates_out.append(d)
            continue
        new_set = set(holdings)
        px = prices.loc[rd_]
        # 卖离场股: 全平(以实际持仓为准; 停牌当月卖不掉→下月再卖)
        for c in list(shares):
            if c in new_set:
                continue
            p = px.get(c)
            if p is None or p != p or shares[c] <= 0:
                continue
            v = shares.pop(c) * p
            if light_cost:
                cash += v * (1 - 0.001)
            else:
                proceeds = v * (1 - SLIP)
                fee = proceeds * (STAMP + TRANSFER) + max(proceeds * COMM, COMM_MIN)
                cash += proceeds - fee
            stats['orders'] += 1
        # 买入场股
        mkt_now = sum(sh * px.get(c, np.nan) for c, sh in shares.items()
                      if not pd.isna(px.get(c)))
        total_eq = cash + mkt_now
        budget = total_eq * 0.98 / len(new_set)
        for c in sorted(new_set - set(shares)):
            p = px.get(c)
            if p is None or p != p or p <= 0:
                continue
            if use_lots:
                n_lot = int(budget / (p * (1 + COMM + TRANSFER + SLIP)) / 100)
                if n_lot <= 0:
                    stats['skip'] += 1
                    continue
                q = n_lot * 100
            else:
                q = budget / p
            v = q * p
            if light_cost:
                fee, slip = 0.0, 0.001
            else:
                fee, slip = v * TRANSFER + max(v * COMM, COMM_MIN), SLIP
            cost = v * (1 + slip) + fee
            if cost > cash:
                q *= cash / cost
                v, cost = q * p, cash
            cash -= cost
            shares[c] = shares.get(c, 0) + q
            stats['orders'] += 1
            if v * COMM < COMM_MIN:
                stats['floor'] += 1
        stats['hold_n'].append(len(shares))
        # 月内漂移
        for d in days:
            r = daily_ret.loc[d] if d in daily_ret.index else None
            if r is not None and shares:
                for c in shares:
                    rr = r.get(c, 0.0)
                    if pd.isna(rr):
                        rr = 0.0
                    shares[c] = shares[c]  # 股数不变
            mkt = 0.0
            if d in prices.index and shares:
                row = prices.loc[d]
                for c, q in shares.items():
                    p = row.get(c, np.nan)
                    if p is None or p != p:
                        p = px.get(c, np.nan)   # 停牌按买入价
                    if p == p:
                        mkt += q * p
            navs.append(cash + mkt)
            dates_out.append(d)
    stats['avg_hold'] = float(np.mean(stats['hold_n'])) if stats['hold_n'] else 0
    return pd.Series(navs, index=pd.DatetimeIndex(dates_out)), stats


def _mkt(shares, prices, d):
    return 0.0


print('\n[L2a] 月度漂移+原口径轻成本(0.1%单边,分离再平衡理想化):')
nav2a, s2a = real_sim_v2('pct', use_lots=False, light_cost=True)
perf(nav2a, 'L2a-漂移轻成本', base=CASH)
print('    平均持股%.0f | 订单%d笔'
      % (s2a['avg_hold'], s2a['orders']))

print('\n[L2] 真实费用+月度漂移(持股数同原口径):')
nav2, s2 = real_sim_v2('pct', use_lots=False)
perf(nav2, 'L2-真实费用漂移', base=CASH)
print('    平均持股%.0f | 订单%d笔(佣金触底%.0f%%) | 跳过%d'
      % (s2['avg_hold'], s2['orders'], s2['orders'] and s2['floor'] / s2['orders'] * 100, s2['skip']))

print('\n[L3] +整手+200万资金约束(≈5000元/只):')
nav3, s3 = real_sim_v2('pct', use_lots=True)
perf(nav3, 'L3-整手+最低佣金', base=CASH)
print('    平均持股%.0f | 订单%d笔(佣金触底%.0f%%=5元重税) | 1手买不起跳过%d只'
      % (s3['avg_hold'], s3['orders'], s3['orders'] and s3['floor'] / s3['orders'] * 100, s3['skip']))

print('\n[L4] Top50合规版(主人持仓≤50铁律)+真实执行:')
nav4, s4 = real_sim_v2('fixed', use_lots=True, n_fixed=N_TOP_COMPLIANT)
perf(nav4, 'L4-Top50真实执行', base=CASH)
print('    平均持股%.0f | 订单%d笔(佣金触底%.0f%%) | 跳过%d'
      % (s4['avg_hold'], s4['orders'], s4['orders'] and s4['floor'] / s4['orders'] * 100, s4['skip']))

print('\n' + '=' * 78)
print('WF-icir(droe+roa)真实成色对照 %s-%s (宣称CAGR 33.2%%)' % (START, END))
print('=' * 78)
perf(l1.nav, 'L1-原口径复刻')
perf(nav2a, 'L2a-漂移轻成本', base=CASH)
perf(nav2, 'L2-真实费用漂移', base=CASH)
perf(nav3, 'L3-整手+最低佣金', base=CASH)
perf(nav4, 'L4-Top50合规真实', base=CASH)
print('  耗时 %.0f秒' % (time.time() - t0))
