# -*- coding: utf-8 -*-
"""
bt_bp_ep_realistic_backtest — bp+ep月度调仓 真实约束层复测 (完整机器复刻版)
=====================================================================
生产官方口径(multifactor/config.py + backtester.py + 方案C):
  ep=EPS(ROE×BPS), bp=P/B(close/BPS), 均dir+1(质量成长)
  等权pct-rank合成 → 候选按分排序, 单股5%, 单行业≤20%(即每行业最多4只),
  Top20, point-in-time close≤30元, 窗口20180101~20260814
  老框架还含: MDD>20%时免费减仓至50%(回撤修复至-6%恢复), 成本=换手×0.35%

分解对照(定位+21.35%的来源):
  [A1] 向量: 纯因子Top20(无行业约束,无MDD保护)
  [A2] 向量: +行业上限(每行业≤4只)
  [A3] 向量: +MDD免费减仓保护(老框架完整口径,应≈+21.35%)
  [B]  bt理想化: 月末信号→次月开盘, 零成本无约束
  [C]  bt真实约束: T+1/封板/停牌/整手/真实费用/滑点0.1%/量上限25%/当日有效
运行: python D:/zcodesj/bt_bp_ep_realistic_backtest.py

⚠️ 宇宙口径注记(2026-10-23 审计发现B, 证据=reports/AUDIT_EXTERNAL_TDX_PIPELINE_20261023.md §六/§七):
   选股未剔 ST/科创/北交(底表 stock_factors_fundamental 入库层零过滤)。材料度实测
   (analysis/st_universe_materiality_20261023.py, 2018-2026 月度Top20 共2040笔):
   科创/北交混入 0 笔、当前快照ST 仅 4 笔(0.20%) → 数字级影响可忽略, 属文档级口径瑕疵;
   解读收益数字时按「含科创/北交/ST历史」口径理解。历史ST时点过滤的修复须 ST 历史源
   (见审计报告§七, 当前被数据源权限/额度门控), 源就位前不改选股逻辑。
"""
import sqlite3
import sys
import time

sys.path.insert(0, r'D:\zcodesj')

import numpy as np
import pandas as pd
import backtrader as bt

from bt_ashare_realistic import (
    build_cerebro, add_ashare_data, align_dataframes,
)

META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
FACTOR_DB = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
START, END = 20180101, 20260814
TOPN = 20
PRICE_MAX = 30.0
CASH = 2_000_000.0
COST_VECTOR = 0.004          # 向量基线单次成本(近似老框架换手×0.35%月均)
MAX_PER_IND = 4              # 单行业20% / 单股5%
MDD_TRIGGER, MDD_RECOVER = -0.20, -0.06


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def month_end_dates():
    con = sqlite3.connect(FACTOR_DB, uri=True)
    dates = [r[0] for r in con.execute(
        'SELECT DISTINCT date FROM stock_factors_fundamental '
        'WHERE date BETWEEN ? AND ? ORDER BY date', (START, END))]
    con.close()
    df = pd.DataFrame({'d': dates})
    df['ym'] = df['d'].astype(str).str[:6]
    return [int(x) for x in df.groupby('ym')['d'].max().tolist()]


def load_industry():
    con = sqlite3.connect('file:%s?mode=ro' % META_DB, uri=True)
    df = pd.read_sql_query(
        'SELECT code, sector_name FROM stock_sector_sw1', con)
    con.close()
    df['code'] = df['code'].str[-6:]
    return dict(zip(df['code'], df['sector_name']))


def build_signals(me_dates, ind_map):
    con = sqlite3.connect(FACTOR_DB, uri=True)
    ph = ','.join('?' * len(me_dates))
    snap = pd.read_sql_query(
        'SELECT date, code, close, ep, bp FROM stock_factors_fundamental '
        'WHERE date IN (%s)' % ph, con, params=me_dates)
    con.close()

    signals = {}          # date -> 带行业约束的Top20(生产口径)
    signals_raw = {}      # date -> 纯因子Top20
    for d in me_dates:
        g = snap[(snap['date'] == d) & (snap['close'] > 0)
                 & (snap['close'] <= PRICE_MAX)].dropna(subset=['ep', 'bp'])
        if len(g) < TOPN * 3:
            continue
        g = g.assign(combo=(g['ep'].rank(pct=True) + g['bp'].rank(pct=True)) / 2)
        g = g.sort_values('combo', ascending=False)
        signals_raw[int(d)] = g['code'].head(TOPN).tolist()
        picks, used = [], {}
        for code in g['code']:
            if len(picks) >= TOPN:
                break
            ind = ind_map.get(code, 'UNKNOWN')
            if used.get(ind, 0) >= MAX_PER_IND:
                continue
            used[ind] = used.get(ind, 0) + 1
            picks.append(code)
        signals[int(d)] = picks
    return signals, signals_raw, snap


# ---------------------------------------- 向量基线(日频,复刻老框架机制)
def vector_sim(signals, close_df, with_mdd):
    """close_df: 对齐后的日频close矩阵; 月末重置等权, 期间漂移; with_mdd=免费减仓."""
    dates = close_df.index
    me_set = set(pd.Timestamp(str(d)) for d in signals)
    me_list = sorted(me_set)
    rets, nav = [], 1.0
    peak = 1.0
    prot = False
    w = {}
    prev = None
    for dt in dates:
        row = close_df.loc[dt]
        if w and prev is not None:
            pr = 0.0
            nw = {}
            for c, wi in w.items():
                p0, p1 = prev.get(c), row.get(c)
                if p0 and p1 and p0 > 0 and p1 > 0 and p1 / p0 - 1 < 0.22:
                    r = p1 / p0 - 1
                    pr += wi * r
                    nw[c] = wi * (1 + r)
            tot = sum(nw.values())
            if tot > 0:
                base = sum(w.values())
                w = {c: v / tot * base for c, v in nw.items()}
            nav *= (1 + pr)
            peak = max(peak, nav)
            rets.append(nav)
            dd = nav / peak - 1
            if with_mdd:
                if not prot and dd < MDD_TRIGGER:
                    prot = True
                    w = {c: v * 0.5 for c, v in w.items()}
                elif prot and dd > MDD_RECOVER:
                    prot = False
                    w = {c: v * 2.0 for c, v in w.items()}
        prev = row
        if dt in me_set:
            d_int = int(dt.strftime('%Y%m%d'))
            new = signals[d_int]
            old = set(w)
            turn = (len(set(new) - old) + len(old - set(new))) / max(TOPN, 1) / 2
            nav *= (1 - min(turn, 1.0) * 0.0035)
            peak = max(peak, nav)
            scale = 0.5 if (with_mdd and prot) else 1.0
            w = {c: scale / TOPN for c in new if c in close_df.columns}
    r = np.diff(np.log(np.maximum(np.array(rets), 1e-12))) if len(rets) > 1 else np.array([])
    rs = np.array(rets)
    rs = rs[rs > 0]
    daily = np.diff(rs) / rs[:-1] if len(rs) > 1 else np.array([0.0])
    n = len(daily)
    return dict(final=nav, ann=nav ** (252 / max(n, 1)) - 1,
                sharpe=daily.mean() / (daily.std() + 1e-12) * np.sqrt(252),
                mdd=(rs / np.maximum.accumulate(rs) - 1).min() if len(rs) else 0,
                n=n)


# ------------------------------------------------ backtrader策略
class BpEpMonthly(bt.Strategy):
    params = (('topn', TOPN), ('signals', None))

    def __init__(self):
        self.me_set = set(self.p.signals)
        self.values = []
        self.held = set()

    def _d(self):
        dt = self.datas[0].datetime
        return int('%d%02d%02d' % (dt.date(0).year, dt.date(0).month, dt.date(0).day))

    def next(self):
        self.values.append(self.broker.getvalue())
        d0 = self._d()
        if d0 not in self.me_set:
            return
        targets = set(self.p.signals[d0])
        for code in list(self.held):
            if code in targets:
                continue
            d = self.getdatabyname(code)
            pos = self.getposition(d)
            if pos.size > 0 and d.close[0] == d.close[0]:
                self.sell(data=d, size=pos.size)
            self.held.discard(code)
        budget = self.broker.getvalue() * 0.95 / self.p.topn
        for code in sorted(targets - self.held):
            d = self.getdatabyname(code)
            c = d.close[0]
            if c != c or c <= 0:
                continue
            size = int(budget / (c * 1.002) / 100) * 100
            if size > 0:
                self.buy(data=d, size=size)
                self.held.add(code)


def run_bt(signals, dfs, realistic):
    """realistic=True 全约束; False=同机器零参数(约束全关/费用全零)。"""
    common = dict(runonce=False, stdstats=False)
    if realistic:
        cerebro = build_cerebro(cash=CASH, **common)
    else:
        cerebro = build_cerebro(
            cash=CASH, commission=0, min_commission=0, stamp_duty=0,
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
    return dict(final=v[-1],
                ann=(v[-1] / v[0]) ** (252 / len(rets)) - 1 if len(rets) else 0,
                sharpe=rets.mean() / (rets.std() + 1e-12) * np.sqrt(252),
                mdd=(cum / np.maximum.accumulate(cum) - 1).min(),
                n=len(rets), broker=st.broker, held=len(st.held))


def fmt(label, r, base=CASH):
    print('  %-12s 终值%12.0f  年化%8.2f%%  Sharpe%7.2f  MDD%8.2f%%'
          % (label, r['final'] * (base if r['final'] < 10 else 1),
             r['ann'] * 100, r['sharpe'], r['mdd'] * 100))


if __name__ == '__main__':
    t0 = time.time()
    me_dates = month_end_dates()
    ind_map = load_industry()
    log('月末%d个 | 行业映射%d只' % (len(me_dates), len(ind_map)))
    signals, signals_raw, snap = build_signals(me_dates, ind_map)
    log('有效信号月=%d (官方口径: ep=EPS,bp=P/B,≤%.0f元,Top%d,每行业≤%d只)'
        % (len(signals), PRICE_MAX, TOPN, MAX_PER_IND))

    codes = sorted({c for lst in signals_raw.values() for c in lst}
                   | {c for lst in signals.values() for c in lst})
    log('加载入选union=%d只 日线...' % len(codes))
    con = sqlite3.connect(META_DB)
    ph = ','.join('?' * len(codes))
    ohlcv = pd.read_sql_query(
        'SELECT code, date, open, high, low, close, volume FROM stock_daily_meta '
        'WHERE code IN (%s) AND date BETWEEN ? AND ? ORDER BY code, date'
        % ph, con, params=codes + [START, END])
    con.close()
    dfs = {}
    for code, g in ohlcv.groupby('code'):
        idx = pd.to_datetime(g['date'].astype(str), format='%Y%m%d')
        dfs[code] = g.drop(columns=['code', 'date']).set_index(idx).astype(float)
    dfs = align_dataframes(dfs)
    # 停牌=前收盘ffill+volume 0(按最后价估值,贴近实盘;无量bar由约束层拦截交易)
    for df in dfs.values():
        df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].ffill()
        df['volume'] = df['volume'].fillna(0.0)
    close_df = pd.DataFrame({c: df['close'] for c, df in dfs.items()})
    log('对齐完成: %d只 × %dbar' % (len(dfs), len(close_df)))

    a1 = vector_sim(signals_raw, close_df, with_mdd=False)
    a2 = vector_sim(signals, close_df, with_mdd=False)
    a3 = vector_sim(signals, close_df, with_mdd=True)
    print('\n[A] 向量基线分解(月末close计价,换手成本0.35%):')
    fmt('A1-纯因子', {**a1, 'final': a1['final'] * CASH})
    fmt('A2-+行业≤4', {**a2, 'final': a2['final'] * CASH})
    fmt('A3-+MDD免费减仓', {**a3, 'final': a3['final'] * CASH})

    log('[B] bt理想化(零成本,次月开盘)...')
    nb = run_bt(signals, dfs, realistic=False)
    fmt('B-理想化', nb, base=1)

    log('[C] bt真实约束(全约束层)...')
    rb = run_bt(signals, dfs, realistic=True)
    fmt('C-真实约束', rb, base=1)
    rb['broker'].print_event_summary()

    print('\n' + '=' * 80)
    print('bp+ep月度Top%d(≤%.0f元) %d-%d | 老框架全机器口径=+21.35%%'
          % (TOPN, PRICE_MAX, START, END))
    print('=' * 80)
    fmt('A1-纯因子', {**a1, 'final': a1['final'] * CASH})
    fmt('A2-+行业≤4', {**a2, 'final': a2['final'] * CASH})
    fmt('A3-+MDD免费减仓', {**a3, 'final': a3['final'] * CASH})
    fmt('B-理想化', nb, base=1)
    fmt('C-真实约束', rb, base=1)
    print('  期末残留持仓: %d只 | 耗时 %.0f秒' % (rb['held'], time.time() - t0))
