# -*- coding: utf-8 -*-
"""
mdd_overlay_audit_20260815 — bp+ep"MDD减仓overlay"Mylnikov式前视偏差体检 (2026-08-15)
=====================================================================================
背景:
  bp+ep月度Top20组合机器(向量口径十年+21.35%)中, MDD减仓overlay向量版贡献为正,
  但真实交易版(bt_bp_ep_full_machine.py D1-B/D2-C)=-2.5pp负贡献, 分歧未解释。
  Mylnikov(JPM 2021)警告: MDD是全路径统计量, 用历史MDD定仓位天然含后视成分。

原版口径定位(代码审计结论):
  向量版老框架 = bt_bp_ep_realistic_backtest.py::vector_sim (A3轨):
    - 每日用"全历史运行峰值"(running peak, 递推, 无字面未来函数)算当前DD;
    - DD<-20% -> 免费把持仓权重砍半(零成本); DD修复到>-6% -> 免费加倍回来;
    - 月末调仓时若仍处保护态, 新仓重置为 0.5/TopN (保护态跨月延续);
    - 阈值-20%/-6%为全样本内标定。
  => 字面上的"全样本MDD回看"未在代码中出现; 原版的"软前视"来自三处:
     (a)减仓/恢复零成本(免费缩放); (b)日频DD反应嵌在月频策略里;
     (c)阈值与样本路径同源(事后标定)。
  本实验用四轨+一轨补充把它们逐一量化。

四轨对照(同信号/同价格/同成本0.1%单边/同next-bar执行):
  轨0 base   : 无overlay基线(月频Top20等权, 敞口100%)
  轨1 orig   : 原版MDD减仓(复刻vector_sim机制: 日频running-peak DD, 免费缩放,
               保护态跨月延续; 减仓动作不收费=忠实复刻原版的"免费"特性)
  轨2 monthly: 月频限制版(仅调仓日用"截至当日的滚动3年窗口"当前DD定仓位档:
               滚动3年峰值下DD<-20%->次月敞口50%, 修复>-6%->恢复100%;
               档位变化随调仓真实成交并计0.1%单边成本)
  轨3 lag1m  : Mylnikov严格版(轨2基础上, 档位决策滞后1个月执行——本月生效档位
               由上月调仓日信息决定, 杜绝当月任何信息)
  [补充] 轨1c: 轨1机制+减仓/恢复也收0.1%单边成本(拆"免费缩放"的水分)

数据:
  价格  dragon_quant_qfq.stock_daily_meta_qfq (前复权, 唯一干净价格源)
  因子  factor_pit.db.stock_factors_fundamental_pit (PIT, pit_avail_date对齐,
        法定披露截止日规则; eps=ep*ref_close, bps=ref_close/bp 重建到信号日收盘)
  行业  dragon_quant_meta.stock_sector_sw1 (当前快照, 非PIT, 与生产同缺陷)
  窗口  信号2015-12月末~2026-06月末, 收益2016-01~2026-07(月末信号->次一交易日收盘换仓)

运行: python D:/zcodesj/mdd_overlay_audit_20260815.py
"""
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ_DB = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
PIT_DB = r'file:D:\zcodesj\factor_pit.db?mode=ro'
META_DB = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro'

LOAD_START, LOAD_END = '20151201', '20260731'
SIG_YM_FIRST, SIG_YM_LAST = 201512, 202606     # 信号月末(执行至2026-07)
TOPN, PRICE_MAX, MAX_PER_IND = 20, 30.0, 4
COST_SIDE = 0.001                               # 单边成本0.1%
MDD_TRIGGER, MDD_RECOVER = -0.20, -0.06
TIER_PROT = 0.5
ROLL_DAYS = 756                                 # 滚动3年(交易日)


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


# ============================================================ 数据加载
def load_pit_factors():
    con = sqlite3.connect(PIT_DB, uri=True)
    df = pd.read_sql_query(
        'SELECT code, report_date, pit_avail_date, ep, bp, ref_close '
        'FROM stock_factors_fundamental_pit', con)
    con.close()
    df = df[(df['ep'].notna()) & (df['bp'].notna()) & (df['ref_close'] > 0)]
    df = df[(df['ep'] > 0) & (df['bp'] > 0)]
    return df.sort_values('pit_avail_date').reset_index(drop=True)


def load_close_pivot(codes):
    con = sqlite3.connect(QFQ_DB, uri=True)
    df = pd.read_sql_query(
        'SELECT code, date, close FROM stock_daily_meta_qfq '
        "WHERE date BETWEEN ? AND ? AND close > 0", con,
        params=[LOAD_START, LOAD_END])
    con.close()
    df = df[df['code'].isin(set(codes))]
    piv = df.pivot(index='date', columns='code', values='close')
    piv.index = piv.index.astype(int)
    piv = piv.sort_index()
    # 停牌ffill(qfq缺行=停牌, 按最后价估值; 上市前仍为NaN)
    piv = piv.ffill()
    return piv


def load_industry():
    con = sqlite3.connect(META_DB, uri=True)
    df = pd.read_sql_query('SELECT code, sector_name FROM stock_sector_sw1', con)
    con.close()
    df['code'] = df['code'].str[-6:]
    return dict(zip(df['code'], df['sector_name']))


# ============================================================ 信号构建(PIT)
def build_signals(piv, factors, ind_map):
    """月末信号日T(收盘): PIT as-of(pit_avail_date<=T)取每股最新报告期,
    ep/bp重建到T日qfq收盘, <=30元过滤, 等权pct-rank合成, 行业<=4, Top20。"""
    signals = {}
    tmp = pd.DataFrame({'d': piv.index})
    tmp['ym'] = tmp['d'].astype(str).str[:6].astype(int)
    me_dates = [int(d) for d in tmp.groupby('ym')['d'].max().tolist()
                if SIG_YM_FIRST <= int(str(d)[:6]) <= SIG_YM_LAST]

    pit_dates = factors['pit_avail_date'].values
    for T in me_dates:
        k = int(np.searchsorted(pit_dates, T, side='right'))
        sub = factors.iloc[:k]
        idx = sub.groupby('code')['report_date'].idxmax()
        latest = sub.loc[idx]
        close_T = piv.loc[T]
        j = latest.set_index('code').join(close_T.rename('close'), how='inner')
        j = j[(j['close'] > 0) & (j['close'] <= PRICE_MAX)]
        eps = j['ep'] * j['ref_close']          # 每股盈利
        bps = j['ref_close'] / j['bp']          # 每股净资产
        j = j.assign(ep_T=eps / j['close'], bp_T=j['close'] / bps)
        j = j[(j['ep_T'] > 0) & (j['bp_T'] > 0)]
        if len(j) < TOPN * 3:
            continue
        combo = (j['ep_T'].rank(pct=True) + j['bp_T'].rank(pct=True)) / 2
        j = j.assign(combo=combo).sort_values('combo', ascending=False)
        picks, used = [], {}
        for code in j.index:
            if len(picks) >= TOPN:
                break
            ind = ind_map.get(code, 'UNKNOWN')
            if used.get(ind, 0) >= MAX_PER_IND:
                continue
            used[ind] = used.get(ind, 0) + 1
            picks.append(code)
        signals[int(T)] = picks
    return signals


# ============================================================ 四轨引擎
def run_engine(close_piv, signals, mode, trigger=None, recover=None):
    """mode: base | orig | orig_cost | monthly | lag1m
    统一规则: 信号日T收盘出信号 -> 次一交易日收盘换仓(next-bar);
    换仓成本=买入权重+卖出权重合计 x 0.1%(单边);
    orig模式减仓/恢复动作免费(复刻原版); orig_cost对减仓动作同样收费;
    monthly/lag1m档位变化计入换仓成本。
    trigger/recover可覆盖默认-20%/-6%(用于参数敏感性)。"""
    trigger = MDD_TRIGGER if trigger is None else trigger
    recover = MDD_RECOVER if recover is None else recover
    dates = close_piv.index.tolist()
    date_pos = {d: i for i, d in enumerate(dates)}
    try:
        ret_piv = close_piv.pct_change(fill_method=None)
    except TypeError:
        ret_piv = close_piv.pct_change()

    nav = 1.0
    peak = 1.0                 # 全历史峰值(orig用)
    w = {}                     # 当前权重(含敞口, sum(w)=当前敞口)
    navs = [1.0]               # navs[i] = 第i个交易日收盘净值
    protected = False          # orig: 日频状态机
    m_prot = False             # monthly: 调仓日状态机
    prev_decided_tier = 1.0    # lag1m: 上次调仓日决策的档位
    episodes = []              # orig: (触发日, 退出日)
    tier_months = []           # (信号日, 该月实际生效档位)

    def roll_dd(idx):
        """截至dates[idx]收盘的滚动3年窗口当前DD(对窗口内峰值);
        历史不足3年用全部可得历史(无任何未来信息)。
        注: navs[k+1] = 第k个交易日收盘净值, 故窗口右端=navs[idx+2]。"""
        hi = idx + 2
        lo = max(0, hi - ROLL_DAYS)
        seg = navs[lo:hi]
        if not seg:
            return 0.0
        pk = max(seg)
        return seg[-1] / pk - 1.0 if pk > 0 else 0.0

    for i, d in enumerate(dates):
        row = ret_piv.iloc[i]
        # ---- 当日收益(旧持仓, close-to-close, 权重漂移后重归一到原敞口)
        if w:
            pr = 0.0
            nw = {}
            for c, wi in w.items():
                r = row.get(c)
                if r is None or r != r or np.isinf(r):
                    r = 0.0
                pr += wi * r
                nw[c] = wi * (1.0 + r)
            tot = sum(nw.values())
            if tot > 0:
                base = sum(w.values())
                w = {c: v / tot * base for c, v in nw.items()}
            nav *= (1.0 + pr)

        # ---- 日频overlay(仅orig/orig_cost): 全历史running peak, 免费缩放
        if mode in ('orig', 'orig_cost') and w:
            peak = max(peak, nav)
            dd = nav / peak - 1.0 if peak > 0 else 0.0
            if not protected and dd < trigger:
                protected = True
                w = {c: v * TIER_PROT for c, v in w.items()}
                episodes.append([d, None])
            elif protected and dd > recover:
                protected = False
                if episodes and episodes[-1][1] is None:
                    episodes[-1][1] = d
                w = {c: v / TIER_PROT for c, v in w.items()}
        elif w:
            peak = max(peak, nav)

        # ---- next-bar换仓(昨日为信号日, 今日收盘成交并计成本)
        if i > 0 and dates[i - 1] in signals:
            s = dates[i - 1]
            picks = [c for c in signals[s] if c in close_piv.columns]
            if mode == 'base':
                new_tier = 1.0
            elif mode in ('orig', 'orig_cost'):
                new_tier = TIER_PROT if protected else 1.0
            elif mode == 'monthly':
                dd3 = roll_dd(date_pos[s])
                if not m_prot and dd3 < trigger:
                    m_prot = True
                elif m_prot and dd3 > recover:
                    m_prot = False
                new_tier = TIER_PROT if m_prot else 1.0
            else:  # lag1m
                dd3 = roll_dd(date_pos[s])
                if not m_prot and dd3 < trigger:
                    m_prot = True
                elif m_prot and dd3 > recover:
                    m_prot = False
                decided_now = TIER_PROT if m_prot else 1.0
                new_tier = prev_decided_tier      # 本月用上月决策
                prev_decided_tier = decided_now   # 存本月决策给下月
            tier_months.append((s, new_tier))

            tgt = {c: new_tier / TOPN for c in picks}
            allc = set(w) | set(tgt)
            sells = sum(max(w.get(c, 0.0) - tgt.get(c, 0.0), 0.0) for c in allc)
            buys = sum(max(tgt.get(c, 0.0) - w.get(c, 0.0), 0.0) for c in allc)
            if mode == 'orig':
                # 原版: 档位缩放免费, 仅成员进出收费
                outs = [c for c in w if c not in tgt]
                ins = [c for c in tgt if c not in w]
                sells = sum(w[c] for c in outs)
                buys = sum(tgt[c] for c in ins)
            cost = (sells + buys) * COST_SIDE
            nav *= (1.0 - cost)
            w = dict(tgt)
        navs.append(nav)

    # ---- 指标(逐日净值; 剔除建仓前的平坦前缀, 收益窗=2016-01-04~2026-07-31)
    full_dates = [dates[0]] + dates          # navs[0]=起始基点
    nav_arr = np.array(navs)
    chg = np.where(nav_arr != nav_arr[0])[0]
    k0 = 0
    if len(chg):
        k0 = max(int(chg[0]) - 1, 0)
        nav_arr = nav_arr[k0:]
    nav_dates = full_dates[k0:]
    rets = nav_arr[1:] / nav_arr[:-1] - 1.0
    n = len(rets)
    final = float(nav_arr[-1])
    ann = final ** (252.0 / max(n, 1)) - 1.0
    sharpe = float(rets.mean() / (rets.std() + 1e-12) * np.sqrt(252))
    mdd = float((nav_arr / np.maximum.accumulate(nav_arr) - 1.0).min())
    prot_months = sum(1 for _, t in tier_months if t < 1.0)
    return dict(mode=mode, final=final, ann=ann, sharpe=sharpe, mdd=mdd,
                n_days=n, episodes=episodes, tier_months=tier_months,
                prot_months=prot_months, nav_arr=nav_arr,
                nav_dates=nav_dates)


def summarize(label, r, base):
    print('  %-12s 年化%+8.2f%%  Sharpe%7.2f  MDD%+8.2f%%  终值%7.3f  '
          '| Δ年化%+7.2fpp  ΔSharpe %+5.2f  ΔMDD %+7.2fpp'
          % (label, r['ann'] * 100, r['sharpe'], r['mdd'] * 100, r['final'],
             (r['ann'] - base['ann']) * 100, r['sharpe'] - base['sharpe'],
             (r['mdd'] - base['mdd']) * 100))


# ============================================================ 第二阶段: 复现"向量版正贡献"
def load_raw_pivot(codes, start, end):
    con = sqlite3.connect(META_DB, uri=True)
    ph = ','.join('?' * len(codes))
    df = pd.read_sql_query(
        'SELECT code, date, close FROM stock_daily_meta '
        'WHERE code IN (%s) AND date BETWEEN ? AND ?' % ph,
        con, params=list(codes) + [start, end])
    con.close()
    piv = df.pivot(index='date', columns='code', values='close')
    piv.index = pd.to_datetime(piv.index.astype(str), format='%Y%m%d')
    return piv.sort_index().ffill()


def stage2_reproduce(pit_signals):
    """复现8/15早间"向量版MDD贡献为正"的原始口径, 并逐项换干净件定位翻号来源。
    2a: 脏口径原样 = 期末as-of因子(dragon_quant.db) + 原始价(stock_daily_meta)
        + 同收盘换仓(vector_sim, 22%跳过, 成本=换手x0.35%)
    2b: 同vector_sim引擎, 换PIT信号+qfq价
    2c: 同vector_sim引擎, 脏信号+qfq价(隔离复权)
    2d: 同vector_sim引擎, PIT信号+原始价(隔离因子前视)"""
    from bt_bp_ep_realistic_backtest import (
        month_end_dates, load_industry, build_signals, vector_sim,
        START, END,
    )
    me = month_end_dates()
    ind = load_industry()
    sig_dirty, _, _ = build_signals(me, ind)
    codes = sorted({c for lst in sig_dirty.values() for c in lst})
    raw_piv = load_raw_pivot(codes, START, END)

    # PIT信号对齐到同日历(取与脏轨同月的qfq月末)
    qfq_con = sqlite3.connect(QFQ_DB, uri=True)
    dts = [r[0] for r in qfq_con.execute(
        'SELECT DISTINCT date FROM stock_daily_meta_qfq '
        'WHERE date BETWEEN ? AND ? ORDER BY date', (LOAD_START, LOAD_END))]
    qfq_con.close()
    tmp = pd.DataFrame({'d': dts})
    tmp['ym'] = tmp['d'].astype(str).str[:6].astype(int)
    qfq_me = {int(ym): int(d) for ym, d in tmp.groupby('ym')['d'].max().items()}
    sig_pit_same_cal = {}
    for T in sorted(sig_dirty):
        ym = int(str(T)[:6])
        if ym in qfq_me and qfq_me[ym] in pit_signals:
            sig_pit_same_cal[qfq_me[ym]] = pit_signals[qfq_me[ym]]
    # 2b/2d用与四轨表同窗(2016-2026), 价格矩阵取 脏∪PIT 持仓并集(避免丢票)
    pit_codes = sorted({c for lst in pit_signals.values() for c in lst})
    union_codes = sorted(set(codes) | set(pit_codes))

    con = sqlite3.connect(QFQ_DB, uri=True)
    ph = ','.join('?' * len(union_codes))
    qfq_piv_w = pd.read_sql_query(
        'SELECT code, date, close FROM stock_daily_meta_qfq '
        'WHERE code IN (%s) AND date BETWEEN ? AND ?' % ph,
        con, params=list(union_codes) + [LOAD_START, LOAD_END])
    con.close()
    qfq_piv_w = qfq_piv_w.pivot(index='date', columns='code', values='close')
    qfq_piv_w.index = pd.to_datetime(
        qfq_piv_w.index.astype(str), format='%Y%m%d')
    qfq_piv_w = qfq_piv_w.sort_index().ffill()
    # 脏轨窗口(2018-2026)的qfq矩阵: 同codes切片
    qfq_piv_d = qfq_piv_w[
        (qfq_piv_w.index >= pd.Timestamp(str(START)))
        & (qfq_piv_w.index <= pd.Timestamp(str(END)))]
    raw_piv_pit = load_raw_pivot(union_codes, LOAD_START, LOAD_END)

    print('\n===== 第二阶段: 原版向量引擎(vector_sim同收盘口径)复现与翻号定位 =====')
    rows = []
    for tag, sigs, px in [
            ('2a 脏因子+原始价 2018-26(原样复现)', sig_dirty, raw_piv),
            ('2c 脏因子+qfq价   2018-26', sig_dirty, qfq_piv_d),
            ('2b PIT因子+qfq价  2016-26(同四轨窗)', pit_signals, qfq_piv_w),
            ('2d PIT因子+原始价 2016-26(同四轨窗)', pit_signals, raw_piv_pit)]:
        off = vector_sim(sigs, px, with_mdd=False)
        on = vector_sim(sigs, px, with_mdd=True)
        rows.append((tag, off, on))
        print('  %s: 无overlay年化%+7.2f%%  +overlay年化%+7.2f%%  '
              '-> overlay贡献 %+6.2f pp/年 (MDD %+5.2f pp -> %+5.2f pp)'
              % (tag, off['ann'] * 100, on['ann'] * 100,
                 (on['ann'] - off['ann']) * 100,
                 off['mdd'] * 100, on['mdd'] * 100))
    return rows


def main():
    t0 = time.time()
    log('加载PIT因子表...')
    factors = load_pit_factors()
    log('PIT行=%d 码=%d 披露窗%s~%s'
        % (len(factors), factors['code'].nunique(),
           factors['pit_avail_date'].min(), factors['pit_avail_date'].max()))

    ind_map = load_industry()
    log('行业映射%d只' % len(ind_map))

    log('加载qfq收盘价矩阵(前复权)...')
    piv = load_close_pivot(set(factors['code'].unique()))
    log('价格矩阵: %s | 日历%s~%s'
        % (piv.shape, piv.index[0], piv.index[-1]))

    log('构建PIT月末信号...')
    signals = build_signals(piv, factors, ind_map)
    log('有效信号月=%d | 首末=%s~%s | 持仓union=%d只 | 平均每期%.1f只'
        % (len(signals), min(signals), max(signals),
           len({c for lst in signals.values() for c in lst}),
           np.mean([len(v) for v in signals.values()])))

    print('\n===== 四轨对照 (bp+ep月度Top20, <=30元, 行业<=4, next-bar, '
          '单边成本0.1%%, 2016-01~2026-07) =====')
    res = {}
    for mode in ('base', 'orig', 'monthly', 'lag1m', 'orig_cost'):
        res[mode] = run_engine(piv, signals, mode)
    base = res['base']
    for mode, label in [('base', '轨0-无overlay'),
                        ('orig', '轨1-原版MDD'),
                        ('monthly', '轨2-月频限制'),
                        ('lag1m', '轨3-滞后1月'),
                        ('orig_cost', '轨1c-原版+收费')]:
        summarize(label, res[mode], base)

    # ---- 机制诊断
    print('\n----- 保护期诊断 -----')
    for mode in ('orig', 'monthly', 'lag1m'):
        r = res[mode]
        print('  [%-6s] 处于50%%档的调仓月数: %d/%d'
              % (mode, r['prot_months'], len(r['tier_months'])))
        if mode == 'orig':
            for ent, ex in r['episodes']:
                print('    日频触发 %d -> 退出 %s'
                      % (ent, ex if ex else '期末未退出'))
        else:
            prot = [s for s, t in r['tier_months'] if t < 1.0]
            if prot:
                print('    50%%档信号月: %s' % prot)

    print('\n===== 结论速览 =====')
    print('  原版口径贡献(轨1-轨0)   : %+6.2f pp/年 (MDD %+6.2f pp, Sharpe %+5.2f)'
          % ((res['orig']['ann'] - base['ann']) * 100,
             (res['orig']['mdd'] - base['mdd']) * 100,
             res['orig']['sharpe'] - base['sharpe']))
    print('  原版+真实收费(轨1c-轨0) : %+6.2f pp/年'
          % ((res['orig_cost']['ann'] - base['ann']) * 100))
    print('  月频限制版(轨2-轨0)     : %+6.2f pp/年 (MDD %+6.2f pp, Sharpe %+5.2f)'
          % ((res['monthly']['ann'] - base['ann']) * 100,
             (res['monthly']['mdd'] - base['mdd']) * 100,
             res['monthly']['sharpe'] - base['sharpe']))
    print('  Mylnikov严格版(轨3-轨0) : %+6.2f pp/年 (MDD %+6.2f pp, Sharpe %+5.2f)'
          % ((res['lag1m']['ann'] - base['ann']) * 100,
             (res['lag1m']['mdd'] - base['mdd']) * 100,
             res['lag1m']['sharpe'] - base['sharpe']))
    print('  免费缩放水分(轨1-轨1c)  : %+6.2f pp/年'
          % ((res['orig']['ann'] - res['orig_cost']['ann']) * 100))

    # ---- 第二阶段: 复现"向量版正贡献"并翻号定位
    try:
        stage2_reproduce(signals)
    except Exception as e:
        print('\n[第二阶段跳过] %r' % e)

    # ---- 第三阶段: 参数敏感性(检验-20%/-6%阈值是否全样本内标定的唯一问题)
    print('\n===== 第三阶段: 轨2(月频限制版)参数敏感性 =====')
    print('  %-16s %8s %8s %10s %10s %8s'
          % ('触发/恢复阈值', '年化', 'MDD', 'Δ年化', 'ΔMDD', '50%档月数'))
    for trg, rec in [(-0.10, -0.03), (-0.15, -0.05), (-0.20, -0.06),
                     (-0.25, -0.08), (-0.30, -0.10), (-0.40, -0.12)]:
        r = run_engine(piv, signals, 'monthly', trigger=trg, recover=rec)
        print('  %-16s %+7.2f%% %+7.2f%% %+9.2fpp %+9.2fpp %6d/%d'
              % ('%.0f%%/%.0f%%' % (trg * 100, rec * 100),
                 r['ann'] * 100, r['mdd'] * 100,
                 (r['ann'] - base['ann']) * 100,
                 (r['mdd'] - base['mdd']) * 100,
                 r['prot_months'], len(r['tier_months'])))

    # ---- 分年收益对照
    print('\n----- 分年收益(轨0 vs 轨3) -----')
    years = sorted({int(str(d)[:4]) for d in res['base']['nav_dates'][1:]})
    print('  年份   ' + ''.join('%9s' % y for y in years))
    for mode, label in [('base', '轨0'), ('lag1m', '轨3')]:
        r = res[mode]
        cells = []
        for y in years:
            idx = [i for i, d in enumerate(r['nav_dates'])
                   if i >= 1 and int(str(d)[:4]) == y]
            if idx:
                seg = r['nav_arr'][max(idx[0] - 1, 0):idx[-1] + 1]
                cells.append('%+8.1f%%' % ((seg[-1] / seg[0] - 1) * 100))
            else:
                cells.append('%9s' % '-')
        print('  %-6s%s' % (label, ''.join(cells)))
    log('总耗时 %.0f 秒' % (time.time() - t0))


if __name__ == '__main__':
    main()
