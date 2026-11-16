# -*- coding: utf-8 -*-
# ⚠️ 2026-08-22 bps/sps 修复警示: 本脚本 bps 消费基于旧口径(close/bps, 且当时 factor_pit.bps=ref_close/bp 失真~50倍)。
# factor_pit.db 已修复(bps=bp×ref_close), 如需重跑本脚本请将 close/bps 改为 bps/close(仅历史审计, 非生产链路)。
"""
bpep_engine_audit_rerun — bp+ep 生产策略绩效专项审计·独立重跑 (2026-08-15)
=====================================================================
目标：把 multifactor backtester 的 22.89% 逐项拆解（复权/22%跳过/MDD overlay/
行业约束/基本面前瞻/日历幽灵日），并用 bt_ashare_realistic 真实约束引擎重跑
全域与≤30元域两轨。只读审计，不修改生产代码。

价格：收益一律用 qfq 库 stock_daily_meta_qfq（前复权）；
      复刻轨 T0 用 stock_daily_hsj（原始价，与生产引擎一致，仅作复刻验证）。
因子：stock_factors_fundamental（ep=EPS, bp=P/B, 生产口径）。
      表内既有 PIT 约定 = report_date(季末) ≤ date 的 as-of join（含前瞻，见审计）。
      strict-PIT 轨 = 法定披露截止日才可用（Q1→4/30, H1→8/31, Q3→10/31, 年报→次年4/30）。

运行: python D:/zcodesj/bpep_engine_audit_rerun_20260815.py
"""
import json
import sqlite3
import sys
import time

sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd

FACTOR_DB = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro'
QFQ_DB = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
START, END = 20180101, 20260810
TOPN = 20
CAND_POOL = TOPN * 3          # 生产 head(60)
MAX_PER_IND = 4               # 单行业 20% / 单股 5%
COST_RATE = 0.0035            # 生产: turnover × (0.25%+0.1%)
GHOST_DATES = {20181231, 20260627}   # 非交易日幽灵日(2行/4588行)

RESULTS = {}


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


# ============================================================ 日历与月末信号日
def get_calendar():
    con = sqlite3.connect(FACTOR_DB, uri=True)
    dates = [r[0] for r in con.execute(
        'SELECT DISTINCT date FROM stock_daily_hsj WHERE date BETWEEN ? AND ? '
        'ORDER BY date', (START, END))]
    con.close()
    return dates


def m_first_dates(calendar):
    df = pd.DataFrame({'d': calendar})
    df['ym'] = df['d'].astype(str).str[:6]
    return [int(x) for x in df.groupby('ym')['d'].first().tolist()]


def deadline(pe):
    y, md = pe // 10000, pe % 10000
    if md == 331:
        return y * 10000 + 430
    if md == 630:
        return y * 10000 + 831
    if md == 930:
        return y * 10000 + 1031
    return (y + 1) * 10000 + 430      # 1231 年报→次年4/30


# ============================================================ 数据加载
def load_factor_snap(dates):
    con = sqlite3.connect(FACTOR_DB, uri=True)
    ph = ','.join('?' * len(dates))
    df = pd.read_sql_query(
        'SELECT date, code, close, ep, bp, report_date FROM stock_factors_fundamental '
        'WHERE date IN (%s)' % ph, con, params=list(dates))
    con.close()
    return df


def load_industry():
    con = sqlite3.connect(FACTOR_DB, uri=True)
    df = pd.read_sql_query('SELECT code, industry FROM stock_info', con)
    con.close()
    return dict(zip(df['code'], df['industry']))


def load_hsj_close_at(dates):
    con = sqlite3.connect(FACTOR_DB, uri=True)
    ph = ','.join('?' * len(dates))
    df = pd.read_sql_query(
        'SELECT code, date, close FROM stock_daily_hsj WHERE date IN (%s)' % ph, con,
        params=list(dates))
    con.close()
    return df


def load_daily_close(table, codes):
    """table: 'stock_daily_hsj'(raw) / 'stock_daily_meta_qfq'(qfq)"""
    db = FACTOR_DB if table == 'stock_daily_hsj' else QFQ_DB
    con = sqlite3.connect(db, uri=True)
    frames = []
    codes = list(codes)
    B = 400
    for i in range(0, len(codes), B):
        chunk = codes[i:i + B]
        ph = ','.join('?' * len(chunk))
        frames.append(pd.read_sql_query(
            'SELECT code, date, close FROM %s WHERE code IN (%s) AND date BETWEEN ? AND ?'
            % (table, ph), con, params=chunk + [START, END]))
        if i % 1200 == 0:
            log('  %s close %d/%d...' % (table, i, len(codes)))
    con.close()
    df = pd.concat(frames, ignore_index=True)
    piv = df.pivot_table(index='date', columns='code', values='close')
    piv.index = piv.index.astype(int)
    return piv


def load_qfq_ohlcv(codes):
    con = sqlite3.connect(QFQ_DB, uri=True)
    frames = []
    codes = list(codes)
    B = 400
    for i in range(0, len(codes), B):
        chunk = codes[i:i + B]
        ph = ','.join('?' * len(chunk))
        frames.append(pd.read_sql_query(
            'SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq '
            'WHERE code IN (%s) AND date BETWEEN ? AND ?' % ph, con,
            params=chunk + [START, END]))
    con.close()
    df = pd.concat(frames, ignore_index=True)
    return df


# ============================================================ 信号构建
def pick_top(scores_df, ind_map, price_avail):
    """生产 _rebalance 口径: rank→可用价过滤→head(60)→行业≤4→Top20"""
    g = scores_df[(scores_df['ep'] > 0) & (scores_df['bp'] > 0)].copy()
    if g.empty:
        return []
    g['combo'] = (g['ep'].rank(pct=True) + g['bp'].rank(pct=True)) / 2
    g = g.sort_values('combo', ascending=False)
    g = g[g['code'].isin(price_avail)]
    picks, used = [], {}
    for code in g['code'].head(CAND_POOL):
        if len(picks) >= TOPN:
            break
        ind = ind_map.get(code, 'UNKNOWN')
        if used.get(ind, 0) >= MAX_PER_IND:
            continue
        used[ind] = used.get(ind, 0) + 1
        picks.append(code)
    return picks


def build_signals(reb_dates, snap, ind_map, close_at_reb, domain, strict_pit,
                  period_first_rows, calendar_after):
    """domain: 'all' / 'le30'。strict_pit=False 用表内 as-of 值(生产口径)。"""
    snap_idx = {d: g for d, g in snap.groupby('date')}
    avail_idx = {d: set(g['code']) for d, g in close_at_reb.groupby('date')}
    sig = {}
    for d in reb_dates:
        g = snap_idx.get(d)
        if g is None or g.empty:
            continue
        g = g.copy()
        if strict_pit:
            # 当日合法报告期 P*: 法定披露截止 ≤ d
            pe_ok = [pe for pe in sorted(period_first_rows) if deadline(pe) <= d]
            if not pe_ok:
                continue
            pe = max(pe_ok)
            pf = period_first_rows.get(pe)
            if pf is None:
                continue
            # ep_P 常数; BPS_P = close(PE+1)/bp(PE+1); bp_strict = close_raw(d)/BPS_P
            g = g.merge(pf[['code', 'ep', 'bps']], on='code', how='left',
                        suffixes=('_old', ''))
            g = g.dropna(subset=['ep', 'bps'])
            g['bp'] = g['close'] / g['bps']
        if domain == 'le30':
            g = g[g['close'] <= 30.0]
        sig[d] = pick_top(g, ind_map, avail_idx.get(d, set()))
    return sig


def build_period_first(pe_first_map, snap_extra):
    """每个季报期 PE -> 期后首个因子日的 {code: ep, bps=close/bp}"""
    out = {}
    idx = {d: g for d, g in snap_extra.groupby('date')}
    for pe, d0 in pe_first_map.items():
        g = idx.get(d0)
        if g is None:
            continue
        g = g[(g['bp'] > 0) & (g['close'] > 0)].copy()
        g['bps'] = g['close'] / g['bp']
        out[pe] = g[['code', 'ep', 'bps']].drop_duplicates('code')
    return out


# ============================================================ 向量引擎(复刻multifactor)
def vector_run(calendar, price_rows, signals, use_22skip, use_mdd, use_cost,
               drop_ghost=False):
    """price_rows: dict[date -> Series(close, index=code)]"""
    dates = [d for d in calendar if not (drop_ghost and d in GHOST_DATES)]
    cur = {}
    nav, peak, prot = 1.0, 1.0, False
    prev = None
    rets, navs = [], []
    turns, costs = [], []
    reb = set(signals)
    empty = pd.Series(dtype=np.float64)
    for d in dates:
        pt = price_rows.get(d, empty)
        pr = 0.0
        if cur and prev is not None:
            valid = {}
            for c, w in cur.items():
                if c in pt.index and c in prev.index:
                    p0, p1 = prev[c], pt[c]
                    if p0 and p0 > 0 and p1 and p1 > 0:
                        r = p1 / p0 - 1
                        if use_22skip and abs(r) > 0.22:
                            continue
                        pr += w * r
                        valid[c] = w
            if valid:
                drift, tot = {}, 0.0
                for c, w in valid.items():
                    drift[c] = w * (pt[c] / prev[c])
                    tot += drift[c]
                if tot > 0:
                    orig = sum(cur.values())
                    cur = {c: w / tot * orig for c, w in drift.items()}
        nav *= (1 + pr)
        peak = max(peak, nav)
        dd = nav / peak - 1
        rets.append(pr)
        navs.append(nav)
        if use_mdd:
            if dd < -0.20 and not prot:
                prot = True
                t = sum(cur.values())
                if t > 0:
                    cur = {k: v * 0.5 / t for k, v in cur.items()}
            elif prot and dd > -0.06:
                prot = False
        if d in reb:
            new = signals[d]
            nh = {c: min(1.0 / TOPN, 0.05) for c in new}
            old, ns = set(cur), set(nh)
            bw = sum(nh[c] for c in ns - old)
            sw = sum(cur[c] for c in old - ns)
            turn = (bw + sw) / 2
            cost = turn * COST_RATE
            if use_cost:
                nav *= (1 - cost)
                peak = max(peak, nav)
            cur = nh
            turns.append(turn)
            costs.append(cost)
        prev = pt
    r = pd.Series(rets, index=dates)
    n = pd.Series(navs, index=dates)
    out = metrics(r, n)
    out['avg_month_turnover'] = round(float(np.mean(turns)), 4) if turns else 0
    out['total_cost'] = round(float(sum(costs)), 4)
    out['n_months'] = len(turns)
    out['final_nav'] = round(float(navs[-1]), 4) if navs else 1.0
    yr = (1 + r).groupby(r.index.astype(str).str[:4]).prod() - 1
    out['yearly'] = {k: round(float(v), 4) for k, v in yr.items()}
    return out


def metrics(rets, nav):
    ann_vol = rets.std() * np.sqrt(252)
    return {
        'ann': round(float((1 + rets).prod() ** (252 / len(rets)) - 1), 4),
        'sharpe': round(float(rets.mean() * 252 / ann_vol), 3) if ann_vol > 0 else 0,
        'mdd': round(float((nav / nav.cummax() - 1).min()), 4),
        'n_days': len(rets),
    }


# ============================================================ backtrader 真实约束
def run_realistic(signals, ohlcv, calendar, fees=True):
    import backtrader as bt
    from bt_ashare_realistic import (build_cerebro, add_ashare_data,
                                     align_dataframes)

    cal = [d for d in calendar if d not in GHOST_DATES]
    cal_ts = pd.to_datetime([str(d) for d in cal], format='%Y%m%d')
    idx = pd.DatetimeIndex(cal_ts)

    codes = sorted({c for lst in signals.values() for c in lst})
    dfs = {}
    for code, g in ohlcv[ohlcv['code'].isin(set(codes))].groupby('code'):
        g2 = g.drop_duplicates('date')
        g2 = g2.set_index(pd.to_datetime(g2['date'].astype(str), format='%Y%m%d'))
        dfs[code] = g2[['open', 'high', 'low', 'close', 'volume']].astype(float)
    dfs = align_dataframes(dfs)
    out_dfs = {}
    for code, df in dfs.items():
        df = df.reindex(idx)                     # 剔幽灵日(20181231/20260627)
        df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].ffill()
        df['volume'] = df['volume'].fillna(0.0)
        out_dfs[code] = df
    dfs = out_dfs

    class BpEpMonthly(bt.Strategy):
        params = (('topn', TOPN), ('signals', None))

        def __init__(self):
            self.reb = set(self.p.signals)
            self.values = []
            self.n_buys = 0
            self.n_months = 0

        def _d(self):
            dt = self.datas[0].datetime
            return int('%d%02d%02d' % (dt.date(0).year, dt.date(0).month,
                                       dt.date(0).day))

        def next(self):
            self.values.append(self.broker.getvalue())
            d0 = self._d()
            if d0 not in self.reb:
                return
            self.n_months += 1
            targets = set(self.p.signals[d0])
            # 卖出: 按实际持仓(勿用getposition——会给未上市feed建零仓位,
            # 0×NaN收盘污染broker估值)
            for d in self.datas:
                pos = self.broker.positions.get(d)
                if pos is not None and pos.size > 0 and d._name not in targets:
                    self.sell(data=d, size=pos.size)
            # 买入: 等权预算
            v = self.broker.getvalue()
            if not np.isfinite(v):
                return
            budget = v * 0.95 / self.p.topn
            held = {d._name for d, p in self.broker.positions.items()
                    if p and p.size > 0}
            for code in sorted(targets - held):
                d = self.env.datasbyname.get(code) if hasattr(self.env, 'datasbyname') else None
                if d is None:
                    continue
                c = d.close[0]
                if c is None or c != c or c <= 0:
                    continue
                size = int(budget / (c * 1.002) / 100) * 100
                if size > 0:
                    o = self.buy(data=d, size=size)
                    if o:
                        self.n_buys += 1

    common = dict(runonce=False, stdstats=False)
    if fees:
        cerebro = build_cerebro(cash=2_000_000.0, **common)
    else:
        cerebro = build_cerebro(
            cash=2_000_000.0, commission=0, min_commission=0, stamp_duty=0,
            transfer_fee=0, slippage_perc=0, vol_cap=0, t1=False,
            enforce_limit=False, enforce_round_lot=False, forbid_short=False,
            day_only=False, **common)
    for code, df in dfs.items():
        add_ashare_data(cerebro, df, code)
    cerebro.addstrategy(BpEpMonthly, signals=signals)
    st = cerebro.run()[0]
    v = np.array(st.values)
    rets = v[1:] / v[:-1] - 1
    cum = np.cumprod(1 + rets)
    out = {
        'ann': round(float((v[-1] / v[0]) ** (252 / len(rets)) - 1), 4),
        'sharpe': round(float(rets.mean() / (rets.std() + 1e-12) * np.sqrt(252)), 3),
        'mdd': round(float((cum / np.maximum.accumulate(cum) - 1).min()), 4),
        'n_days': len(rets),
        'final_value': round(float(v[-1]), 0),
        'avg_month_turnover': round(st.n_buys / max(st.n_months, 1) / TOPN, 4),
        'n_months': st.n_months,
    }
    yr = pd.Series(rets, index=cal_ts[1:cal_ts.size][:len(rets)])
    y = (1 + yr).groupby(yr.index.year).prod() - 1
    out['yearly'] = {int(k): round(float(v), 4) for k, v in y.items()}
    ev = dict(st.broker.event_stats)
    out['events'] = dict(ev)
    return out


# ============================================================ 主流程
def main():
    t0 = time.time()
    calendar = get_calendar()
    reb_dates = m_first_dates(calendar)
    log('日历%d天, M-FIRST调仓日%d个' % (len(calendar), len(reb_dates)))

    ind_map = load_industry()
    snap = load_factor_snap(reb_dates)
    log('因子快照: %d行' % len(snap))

    close_at_reb = load_hsj_close_at(reb_dates)
    log('调仓日原始close: %d行' % len(close_at_reb))

    # strict-PIT 所需: 各季报期(含周末季末/2017年报告期)后首个有因子行的日期
    pes_all = [y * 10000 + md for y in range(2017, 2027)
               for md in (331, 630, 930, 1231)]
    pes_all = [p for p in pes_all if p <= 20260630]
    con = sqlite3.connect(FACTOR_DB, uri=True)
    pe_first_map = {}
    for pe in pes_all:
        r = con.execute('SELECT MIN(date) FROM stock_factors_fundamental WHERE date > ?',
                        [pe]).fetchone()
        if r and r[0] and r[0] <= END:
            pe_first_map[pe] = int(r[0])
    con.close()
    extra_dates = sorted(set(pe_first_map.values()))
    snap_extra = load_factor_snap(extra_dates)
    period_first = build_period_first(pe_first_map, snap_extra)
    log('strict-PIT 报告期数: %d' % len(period_first))

    # ---- 信号族
    sig_tab_all = build_signals(reb_dates, snap, ind_map, close_at_reb, 'all', False, None, None)
    sig_tab_le30 = build_signals(reb_dates, snap, ind_map, close_at_reb, 'le30', False, None, None)
    sig_str_all = build_signals(reb_dates, snap, ind_map, close_at_reb, 'all', True, period_first, None)
    sig_str_le30 = build_signals(reb_dates, snap, ind_map, close_at_reb, 'le30', True,
                                 period_first, None)

    # 无行业约束版本: 用 snap 重建
    def build_nocap(domain, strict):
        out = {}
        snap_idx = {d: g for d, g in snap.groupby('date')}
        avail_idx = {d: set(g['code']) for d, g in close_at_reb.groupby('date')}
        for d in reb_dates:
            g = snap_idx.get(d)
            if g is None or g.empty:
                continue
            g = g.copy()
            if strict:
                pe_ok = [pe for pe in sorted(period_first) if deadline(pe) <= d]
                if not pe_ok:
                    continue
                pf = period_first[max(pe_ok)]
                g = g.merge(pf[['code', 'ep', 'bps']], on='code', how='left',
                            suffixes=('_old', ''))
                g = g.dropna(subset=['ep', 'bps'])
                g['bp'] = g['close'] / g['bps']
            if domain == 'le30':
                g = g[g['close'] <= 30.0]
            gg = g[(g['ep'] > 0) & (g['bp'] > 0)]
            if gg.empty:
                continue
            gg = gg.assign(combo=(gg['ep'].rank(pct=True) + gg['bp'].rank(pct=True)) / 2)
            gg = gg.sort_values('combo', ascending=False)
            gg = gg[gg['code'].isin(avail_idx.get(d, set()))]
            out[d] = gg['code'].head(TOPN).tolist()
        return out

    sig_tab_all_nocap = build_nocap('all', False)

    n_sig = {k: len(v) for k, v in [('tab_all', sig_tab_all), ('tab_le30', sig_tab_le30),
                                    ('str_all', sig_str_all), ('str_le30', sig_str_le30),
                                    ('tab_all_nocap', sig_tab_all_nocap)]}
    log('信号月数: %s' % n_sig)

    # ---- 与生产 22.89% 运行的持仓重合度验证
    try:
        orig = pd.read_csv(r'D:\zcodesj\multifactor\reports\weekly_backtest'
                           r'\backtest_20260812_182725\holdings.csv')
        orig['date'] = orig['date'].astype(int)
        ovl = []
        for d, codes in sig_tab_all.items():
            oc = set(orig[orig['date'] == d]['code'])
            if oc:
                ovl.append(len(set(codes) & oc) / len(oc | set(codes)) * 20 / 20)
                ovl[-1] = len(set(codes) & oc)
        log('复刻信号 vs 原始22.89%运行: 月均重合 %.1f/20 (共%d个月)'
            % (float(np.mean(ovl)), len(ovl)))
    except Exception as e:
        log('原始holdings对比失败: %s' % e)

    # ---- 价格矩阵
    union = sorted({c for s in (sig_tab_all, sig_tab_le30, sig_str_all, sig_str_le30,
                                sig_tab_all_nocap)
                    for lst in s.values() for c in lst})
    log('入选union: %d只' % len(union))
    piv_raw = load_daily_close('stock_daily_hsj', union)
    piv_qfq = load_daily_close('stock_daily_meta_qfq', union)
    raw_rows = {d: piv_raw.loc[d].dropna() for d in piv_raw.index if d in set(calendar)}
    qfq_rows = {d: piv_qfq.loc[d].dropna() for d in piv_qfq.index if d in set(calendar)}
    log('价格矩阵: raw %s | qfq %s' % (piv_raw.shape, piv_qfq.shape))

    # ---- 向量分解轨 (全域)
    tracks = [
        ('T0_复刻_raw+22skip+MDD+成本+幽灵日',
         dict(sig=sig_tab_all, rows=raw_rows, s22=True, mdd=True, cost=True, ghost=False)),
        ('T1_仅换qfq价', dict(sig=sig_tab_all, rows=qfq_rows, s22=True, mdd=True,
                              cost=True, ghost=False)),
        ('T2_剔幽灵日', dict(sig=sig_tab_all, rows=qfq_rows, s22=True, mdd=True,
                             cost=True, ghost=True)),
        ('T3_去22%跳过', dict(sig=sig_tab_all, rows=qfq_rows, s22=False, mdd=True,
                              cost=True, ghost=True)),
        ('T4_去MDD_overlay', dict(sig=sig_tab_all, rows=qfq_rows, s22=False, mdd=False,
                                  cost=True, ghost=True)),
        ('T4b_T4零成本(扣费前)', dict(sig=sig_tab_all, rows=qfq_rows, s22=False,
                                      mdd=False, cost=False, ghost=True)),
        ('T5_去行业约束', dict(sig=sig_tab_all_nocap, rows=qfq_rows, s22=False,
                               mdd=False, cost=True, ghost=True)),
        ('T6_strictPIT(生产结构)', dict(sig=sig_str_all, rows=qfq_rows, s22=False,
                                        mdd=False, cost=True, ghost=True)),
        ('V_le30_生产PIT', dict(sig=sig_tab_le30, rows=qfq_rows, s22=False, mdd=False,
                                cost=True, ghost=True)),
        ('V_le30_strictPIT', dict(sig=sig_str_le30, rows=qfq_rows, s22=False, mdd=False,
                                  cost=True, ghost=True)),
    ]
    print('\n===== 向量分解轨 (2018-01~2026-08, M-FIRST, Top20, 成本=换手x0.35%) =====')
    for name, kw in tracks:
        r = vector_run(calendar, kw['rows'], kw['sig'], kw['s22'], kw['mdd'],
                       kw['cost'], kw['ghost'])
        RESULTS[name] = r
        print('%-28s 年化%8.2f%%  Sharpe%6.2f  MDD%8.2f%%  月换手%6.1f%%  总成本%6.2f%%  终值%.3f'
              % (name, r['ann'] * 100, r['sharpe'], r['mdd'] * 100,
                 r['avg_month_turnover'] * 100, r['total_cost'] * 100, r['final_nav']))

    # ---- 真实约束引擎
    log('加载 qfq OHLCV (union %d只)...' % len(union))
    ohlcv = load_qfq_ohlcv(union)
    log('OHLCV: %d行' % len(ohlcv))

    bt_tracks = [
        ('BT_全域_生产PIT_扣费', sig_tab_all, True),
        ('BT_全域_生产PIT_零费', sig_tab_all, False),
        ('BT_全域_strictPIT_扣费', sig_str_all, True),
        ('BT_le30_生产PIT_扣费', sig_tab_le30, True),
        ('BT_le30_生产PIT_零费', sig_tab_le30, False),
        ('BT_le30_strictPIT_扣费', sig_str_le30, True),
    ]
    print('\n===== 真实约束引擎 (T+1开盘成交/封板/停牌/整手/真实费用/量上限25%/滑点0.1%) =====')
    for name, sig, fees in bt_tracks:
        log('%s ...' % name)
        r = run_realistic(sig, ohlcv, calendar, fees=fees)
        RESULTS[name] = r
        print('%-28s 年化%8.2f%%  Sharpe%6.2f  MDD%8.2f%%  月换手%6.1f%%  终值%.0f'
              % (name, r['ann'] * 100, r['sharpe'], r['mdd'] * 100,
                 r['avg_month_turnover'] * 100, r['final_value']))
        print('   事件: %s' % r['events'])

    with open(r'D:\zcodesj\reports\_bpep_audit_rerun_results_20260815.json', 'w',
              encoding='utf-8') as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=1, default=str)
    log('结果已存 reports/_bpep_audit_rerun_results_20260815.json | 总耗时 %.0f秒'
        % (time.time() - t0))


def pick_top_nocap(g):
    gg = g[(g['ep'] > 0) & (g['bp'] > 0)]
    gg = gg.assign(combo=(gg['ep'].rank(pct=True) + gg['bp'].rank(pct=True)) / 2)
    return gg.sort_values('combo', ascending=False)['code'].head(TOPN).tolist()


if __name__ == '__main__':
    main()
