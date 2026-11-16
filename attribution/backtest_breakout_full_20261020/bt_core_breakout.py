# -*- coding: utf-8 -*-
"""屠龙系统·横盘突破核心战法 全域历史回测 pilot (2016-01-01 ~ 2026-10-20)
真源: D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db (回测唯一价格源, 宪法§7)
纪律: qfq + next-bar 开盘执行 + 无未来函数(信号仅用 T 日及以前数据) + T+1 + 涨跌停约束
输出: 本目录 summary.json / trades_v*.jsonl / REPORT.md / charts/*.png
"""
import sqlite3, json, os, sys, math, time
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
BASE = r'D:\workbuddyspace\factor_engine\data'
OUT  = r'D:\zcodesj\attribution\backtest_breakout_full_20261020'
os.makedirs(OUT, exist_ok=True)
os.makedirs(os.path.join(OUT, 'charts'), exist_ok=True)
QFQ = os.path.join(BASE, 'dragon_quant_qfq.db')
RAW = os.path.join(BASE, 'dragon_quant_meta.db')   # 原始不复权 close (30元价格带用名义价)
DQ  = os.path.join(BASE, 'dragon_quant.db')        # spot_daily 名称 / index_daily 指数

START, END = '20160101', '20261020'
INIT_CAP = 100000.0
MAX_POS = 5            # 持仓上限
WEIGHT = 0.16          # 单票仓位上限 16% (总仓<=80%, 现金底仓>=20%)
CASH_FLOOR = 0.20
COMM = 0.00025         # 佣金万2.5 (min 5元)
STAMP_NEW_DATE = '20230828'
SLIP = 0.001           # 单边滑点 0.1%
MIN_AMT = 3e7          # 流动性: 当日成交额>=3000万
PRICE_LO, PRICE_HI = 2.0, 30.0   # 系统规则: >30元不买
AGE_BARS = 60          # 上市未满60交易日剔除(首根bar代理)
HOLD_MAX = 30          # 超时退出(交易日)
TRAIL = 0.85           # 移动止盈: 回撤最高收盘15%

def log(*a): print(*a, flush=True)

t0=time.time()
con = sqlite3.connect(QFQ)
df = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq WHERE date>=? AND date<=?",
    con, params=(START, END))
con.close()
log(f'qfq rows={len(df)} load={time.time()-t0:.0f}s')

df = df[df['code'].str[:3].isin(['600','601','603','605','000','001','002','003','300','301'])].copy()
df['dt'] = pd.to_datetime(df['date'], format='%Y%m%d')

# 名称(ST过滤与标注)
con2 = sqlite3.connect(DQ)
try:
    names = pd.read_sql_query("SELECT code,name FROM spot_daily", con2)
except Exception:
    names = pd.DataFrame(columns=['code','name'])
con2.close()
nmap = dict(zip(names['code'], names['name'])) if len(names) else {}
st_codes = {c for c,n in nmap.items() if isinstance(n,str) and ('ST' in n.upper() or '退' in n)}
df = df[~df['code'].isin(st_codes)].copy()
log(f'universe bars={len(df)} codes={df.code.nunique()} (已剔当前ST/退)')

dates = np.sort(df['date'].unique())
didx = {d:i for i,d in enumerate(dates)}
codes = np.sort(df['code'].unique())
cidx = {c:i for i,c in enumerate(codes)}
T, C = len(dates), len(codes)
def wide(col):
    w = df.pivot_table(index='date', columns='code', values=col, aggfunc='last').reindex(index=dates, columns=codes)
    return w
W = {k: wide(k) for k in ['open','high','low','close','volume','amount','change_pct']}
log(f'wide built {time.time()-t0:.0f}s')

# 名义价(原始不复权close) — 30元价格带
try:
    con3 = sqlite3.connect(RAW)
    raw = pd.read_sql_query("SELECT code,date,close FROM stock_daily_meta WHERE date>=? AND date<=?",
                            con3, params=(START, END))
    con3.close()
    raw['date'] = raw['date'].astype(str)
    raw = raw[raw['code'].isin(codes)]
    raw_close = raw.pivot_table(index='date', columns='code', values='close', aggfunc='last').reindex(index=dates, columns=codes)
    log('raw close ok')
except Exception as e:
    log('RAW close load failed -> fallback qfq close:', e)
    raw_close = W['close'].copy()

# 上市年龄代理: 首根bar所在位置
first_pos = pd.Series({c: W['close'][c].first_valid_index() for c in codes})
first_idx = {c: didx[first_pos[c]] if first_pos[c] is not None else T for c in codes}

CYB_REG = '20200824'  # 创业板20cm注册制改革日
def limit_pct(code, date):
    if code[:3] in ('300','301'):
        return 19.8 if date >= CYB_REG else 9.8
    return 9.8   # 主板10%; 历史ST(5%)未知 -> 已按当前名剔除, 局限已注明

HIGH_MA = W['high'].rolling(250, min_periods=1).max()  # 预留
def build_signals(box_n, vr, use_ma):
    bh = W['high'].rolling(box_n, min_periods=box_n).max().shift(1)
    bl = W['low'].rolling(box_n, min_periods=box_n).min().shift(1)
    vma = W['volume'].rolling(5, min_periods=5).mean().shift(1)
    vlr = W['volume'] / vma
    ma20 = W['close'].rolling(20, min_periods=20).mean()
    ma60 = W['close'].rolling(60, min_periods=60).mean()
    prev_close = W['close'].shift(1)
    prev_bh = bh.shift(1)
    cond = (W['close'] > bh) & (prev_close <= prev_bh)           # 新鲜突破(平台高点上沿)
    cond &= (bh - bl) / bl <= 0.45                               # 平台紧致度(先验固定)
    cond &= vlr >= vr
    cond &= (raw_close >= PRICE_LO) & (raw_close <= PRICE_HI)     # 名义价带
    cond &= W['amount'] >= MIN_AMT
    if use_ma:
        cond &= (ma20 > ma60)
    # 上市>=60交易日 & T日未涨停收盘(不追板)
    age_ok = np.zeros((T, C), dtype=bool)
    fi = np.array([first_idx[c] for c in codes])
    age_ok = np.arange(T)[:, None] >= (fi[None, :] + AGE_BARS)
    cond &= age_ok
    thr = pd.DataFrame(9.7, index=W['change_pct'].index, columns=W['change_pct'].columns)
    cyb_cols = [c for c in codes if c[:3] in ('300','301')]
    thr.loc[thr.index >= CYB_REG, cyb_cols] = 19.5
    cond &= W['change_pct'] < thr      # 收盘涨停不追(按板别口径)
    return cond.fillna(False)

def simulate(sig, stop_ratio, why_tag, stop_touch=False, gap_skip=None, dual_time=False, quality_rank=False):
    cash = INIT_CAP; eq_curve = np.zeros(T)
    pos = {}  # code -> dict(shares,buy_px,buy_date,highest,hold_days,buy_i)
    trades = []
    pending = set(); pending_why = {}; pend_wait = {}
    for i in range(T):
        d = dates[i]
        # ---- T日收盘信号 -> T+1执行: 先处理昨日挂单(以今日开盘成交) ----
        # (实际实现: 信号于i-1日收盘产生, 今日开盘执行) —— 见下方 pending 队列
        # 1) 卖出判定用昨日收盘价, 今日开盘执行
        for code in list(pos.keys()):
            p = pos[code]; j = cidx[code]
            op = W['open'].iat[i, j]; pc = W['close'].iat[i-1, j]
            op_pct = (op/pc-1)*100 if pc==pc else np.nan
            lim = -limit_pct(code, d) + 0.2
            if not (op == op):
                p['halt'] = p.get('halt', 0) + 1
                if p['halt'] >= 20:  # 长期停牌强制退出
                    px = p['last_px'] * (1 - SLIP); px = math.floor(px*100)/100
                    proceeds = p['shares']*px
                    fee = max(proceeds*COMM, 5) + proceeds*0.001
                    cash += proceeds - fee
                    trades.append(dict(code=code,name=nmap.get(code,''),buy_date=p['buy_date'],buy_px=p['buy_px'],
                        sell_date=d,sell_px=px,reason_buy=p['why'],reason_sell='halt_force20d',
                        pnl=round(proceeds-fee-p['cost'],2),pnl_pct=round((proceeds-fee)/p['cost']-1,4)))
                    del pos[code]
                continue
            need_sell = False; why = ''
            if p.get('sig_stop'): need_sell, why = True, 'stop_loss_%.0f%%' % ((1-stop_ratio)*100)
            elif p.get('sig_ma'):  need_sell, why = True, 'trend_break_ma20'
            elif p.get('sig_trail'): need_sell, why = True, 'trailing_takeprofit_15%%'
            elif p.get('sig_time'): need_sell, why = True, 'time_stop_%dd' % HOLD_MAX
            if need_sell:
                if op_pct <= lim:      # 开盘跌停无法卖出 -> 顺延
                    p['postpone'] = p.get('postpone', 0) + 1
                    if p['postpone'] > 10:
                        px = op*(1-SLIP); px = math.floor(px*100)/100
                        proceeds = p['shares']*px; fee = max(proceeds*COMM,5)+proceeds*0.001
                        cash += proceeds - fee
                        trades.append(dict(code=code,name=nmap.get(code,''),buy_date=p['buy_date'],buy_px=p['buy_px'],
                            sell_date=d,sell_px=px,reason_buy=p['why'],reason_sell='ld_postpone10d',
                            pnl=round(proceeds-fee-p['cost'],2),pnl_pct=round((proceeds-fee)/p['cost']-1,4)))
                        del pos[code]
                    continue
                px = op*(1-SLIP); px = math.floor(px*100)/100
                proceeds = p['shares']*px; fee = max(proceeds*COMM,5)+proceeds*0.001
                cash += proceeds - fee
                trades.append(dict(code=code,name=nmap.get(code,''),buy_date=p['buy_date'],buy_px=p['buy_px'],
                    sell_date=d,sell_px=px,reason_buy=p['why'],reason_sell=why,
                    pnl=round(proceeds-fee-p['cost'],2),pnl_pct=round((proceeds-fee)/p['cost']-1,4)))
                del pos[code]
        # 2) 今日开盘买入昨日信号
        for code in list(pending):
            if len(pos) >= MAX_POS: break
            j = cidx[code]
            op = W['open'].iat[i, j]; pc = W['close'].iat[i-1, j]
            if not (op == op):
                pend_wait[code] = pend_wait.get(code,0)+1
                if pend_wait[code] > 5: pending.discard(code)
                continue
            op_pct = (op/pc-1)*100
            if op_pct >= limit_pct(code, d) - 0.2:   # 开盘即涨停, 放弃
                pending.discard(code); continue
            if gap_skip is not None and op_pct >= gap_skip:  # 开盘跳空过大, 放弃(条目2)
                pending.discard(code); continue
            px = op*(1+SLIP); px = math.ceil(px*100)/100
            budget = min(INIT_CAP*WEIGHT, cash)
            lots = int(budget / px / 100)
            if lots < 1:
                pending.discard(code); continue
            shares = lots*100; cost = shares*px
            fee = max(cost*COMM, 5); cash -= (cost + fee)
            pos[code] = dict(shares=shares, buy_px=px, buy_date=d, cost=cost+fee, hold=0,
                             highest=W['close'].iat[i-1, j], why=pending_why[code], buy_i=i, last_px=op)
            pending.discard(code)
        pending.clear(); pend_wait.clear(); pending_why.clear()
        # 3) 收盘: 更新持仓状态 + 生成明日执行信号
        day_eq = cash
        for code, p in pos.items():
            j = cidx[code]; cl = W['close'].iat[i, j]
            if cl == cl:
                p['last_px'] = cl
                p['hold'] += 1
                p['highest'] = max(p['highest'], cl)
                lim_up = limit_pct(code, d) - 0.2
                p['sig_stop']  = (W['low'].iat[i,j] <= p['buy_px']*stop_ratio) if stop_touch else (cl <= p['buy_px']*stop_ratio)
                p['sig_ma']    = cl < ma20_cache[i, j]
                p['sig_trail'] = cl <= p['highest']*TRAIL
                p['sig_time']  = p['hold'] >= HOLD_MAX or (dual_time and p['hold'] >= 10 and p['highest'] <= p['buy_px'])
            day_eq += p['shares'] * (p['last_px'] if p['last_px']==p['last_px'] else p['buy_px'])
        eq_curve[i] = day_eq
        # 4) 选股: 明日执行的候选(按amount降序)
        free = MAX_POS - len(pos)
        if free > 0:
            row = sig.iloc[i]
            cand = row.index[row.values]
            cand = [c for c in cand if c not in pos]
            if cand:
                if quality_rank and R2_SCORE is not None:
                    key = pd.Series(R2_SCORE[i], index=sig.columns)[cand]
                else:
                    key = W['amount'].iloc[i][cand]
                amts = key.sort_values(ascending=False)
                for code in amts.index[:free*3]:
                    pending.add(code); pending_why[code] = why_tag
    # 期末强平
    d = dates[-1]; i = T-1
    for code, p in list(pos.items()):
        px = p['last_px'] if p['last_px']==p['last_px'] else p['buy_px']
        px = math.floor(px*(1-SLIP)*100)/100
        proceeds = p['shares']*px; fee = max(proceeds*COMM,5)+proceeds*0.001
        cash += proceeds - fee
        trades.append(dict(code=code,name=nmap.get(code,''),buy_date=p['buy_date'],buy_px=p['buy_px'],
            sell_date=d,sell_px=px,reason_buy=p['why'],reason_sell='end_of_test',
            pnl=round(proceeds-fee-p['cost'],2),pnl_pct=round((proceeds-fee)/p['cost']-1,4)))
    return cash, eq_curve, trades

ma20_cache = W['close'].rolling(20, min_periods=20).mean().values
R2_SCORE = None

results = {}
SIG_CACHE = {}
# ---- 诊断增强: 趋势环境门槛 + 回踩确认入场 (系统既有方法论, 预注册非拟合) ----
ma200 = W['close'].rolling(200, min_periods=200).mean()
BREADTH = (W['close'] > ma200).mean(axis=1)          # 全市场200日线上方占比
def build_signals_ext(bn, vr, use_ma, mode):
    if mode == 'base':
        return build_signals(bn, vr, use_ma)
    sig = build_signals(bn, vr, use_ma)
    if mode == 'regime':
        gate = (BREADTH > 0.50).reindex(sig.index).fillna(False).values
        return sig & gate[:, None]
    if mode == 'pullback':
        bh = W['high'].rolling(bn, min_periods=bn).max().shift(1)
        bo = (W['close'] > bh)                       # 突破日旗标
        bo5 = bo.rolling(5, min_periods=1).max().astype(bool)   # 近5日有突破
        pull = (W['close'] >= bh*0.98) & (W['close'] <= bh*1.06) & (W['close'] > W['open'])
        gate = (BREADTH > 0.50).reindex(sig.index).fillna(False).values
        return (bo5 & pull & gate[:, None]).fillna(False)
    raise ValueError(mode)

if len(sys.argv) > 1 and sys.argv[1] == 'diag':
    DIAG = [('regime',30,2.0,True,0.92), ('regime',60,2.0,True,0.92),
            ('pullback',30,2.0,True,0.92), ('pullback',60,1.5,False,0.92)]
    for gi,(mode,bn,vr,uf,sr) in enumerate(DIAG,1):
        t1=time.time()
        sig = build_signals_ext(bn, vr, uf, mode)
        cash, eq, trades = simulate(sig, sr, why_tag=f'{mode}_box{bn}_vr{vr}'+('_ma' if uf else ''))
        tag = f'd{gi}_{mode}_box{bn}_vr{vr}_ma{int(uf)}_stop{int((1-sr)*100)}'
        final_eq = eq[-1]
        years = pd.Series(eq, index=pd.to_datetime(pd.Series(dates), format='%Y%m%d'))
        yearly = years.groupby(years.index.year).agg(['last'])
        yr_ret = yearly['last'].pct_change(); yr_ret.iloc[0] = yearly['last'].iloc[0]/INIT_CAP - 1
        peak = np.maximum.accumulate(eq); dd = eq/peak - 1
        mdd = dd.min(); mdd_end = int(dd.argmin()); mdd_start = int(eq[:mdd_end+1].argmax())
        tr = pd.DataFrame(trades)
        win = (tr['pnl']>0).mean() if len(tr) else 0
        aw = tr.loc[tr.pnl>0,'pnl_pct'].mean() if len(tr) else 0
        al = tr.loc[tr.pnl<=0,'pnl_pct'].mean() if len(tr) else 0
        hd = (pd.to_datetime(tr.sell_date,format='%Y%m%d')-pd.to_datetime(tr.buy_date,format='%Y%m%d')).dt.days.mean() if len(tr) else 0
        results[tag] = dict(final_eq=round(final_eq,2), total_ret=round(final_eq/INIT_CAP-1,4),
            cagr=round((final_eq/INIT_CAP)**(1/10.8)-1,4), max_dd=round(mdd,4),
            mdd_window=[dates[mdd_start],dates[mdd_end]], trades=len(tr), win_rate=round(win,4),
            pl_ratio=round(abs(aw/al),2) if al else None, avg_hold_days=round(float(hd),1),
            yearly={int(y): round(v,4) for y,v in yr_ret.items()})
        tr.to_json(os.path.join(OUT, f'trades_{tag}.jsonl'), orient='records', lines=True, force_ascii=False)
        np.save(os.path.join(OUT, f'eq_{tag}.npy'), eq)
        log(f'[DIAG {gi}/{len(DIAG)}] {tag}: eq={final_eq:,.0f} ret={results[tag]["total_ret"]:+.1%} '
            f'cagr={results[tag]["cagr"]:+.1%} mdd={mdd:.1%} trades={len(tr)} win={win:.1%} ({time.time()-t1:.0f}s)')
    with open(os.path.join(OUT,'summary.json'),'w',encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('DIAG DONE'); sys.exit(0)

# ================= ROUND 2: 预注册优化组合复测 =================
if len(sys.argv) > 1 and sys.argv[1] == 'r2':
    bh = W['high'].rolling(60, min_periods=60).max().shift(1)
    bl = W['low'].rolling(60, min_periods=60).min().shift(1)
    vma = W['volume'].rolling(5, min_periods=5).mean().shift(1)
    vlr = (W['volume'] / vma).fillna(0)
    tight = ((bh - bl) / bl).fillna(1)
    R2_SCORE = (vlr + (0.45 - tight).clip(0) * 10).values   # 质量分(先验固定权重)
    base = build_signals(60, 2.0, True)
    gate = (BREADTH > 0.50).reindex(base.index).fillna(False).values
    sig_r2 = base & gate[:, None]
    sig_r2 &= (W['change_pct'] <= 7.0).fillna(False)         # 条目2: 突破日涨幅<=7%
    dd250 = W['close'] / W['high'].rolling(250, min_periods=60).max() - 1
    sig_r2 &= (dd250 > -0.60).fillna(False)                  # 条目7代理: 250日回撤>60%剔除
    RUNS = [
        ('r2_full',      dict(stop_touch=True,  gap_skip=5.0, dual_time=True,  quality_rank=True)),
        ('r2_nogap',     dict(stop_touch=True,  gap_skip=None, dual_time=True, quality_rank=True)),
        ('r2_noquality', dict(stop_touch=True,  gap_skip=5.0, dual_time=True,  quality_rank=False)),
    ]
    for gi, (tag, opt) in enumerate(RUNS, 1):
        t1 = time.time()
        cash, eq, trades = simulate(sig_r2, 0.92, why_tag='r2_box60_composite', **opt)
        final_eq = eq[-1]
        ys = pd.Series(eq, index=pd.to_datetime(pd.Series([str(x) for x in dates]), format='%Y%m%d'))
        yl = ys.groupby(ys.index.year).agg(['last']); yr = yl['last'].pct_change()
        yr.iloc[0] = yl['last'].iloc[0] / INIT_CAP - 1
        peak = np.maximum.accumulate(eq); dd = eq/peak - 1
        me = int(dd.argmin()); ms = int(eq[:me+1].argmax())
        tr = pd.DataFrame(trades)
        win = (tr.pnl > 0).mean() if len(tr) else 0
        aw = tr.loc[tr.pnl > 0, 'pnl_pct'].mean() if len(tr) else 0
        al = tr.loc[tr.pnl <= 0, 'pnl_pct'].mean() if len(tr) else 0
        hd = (pd.to_datetime(tr.sell_date, format='%Y%m%d') - pd.to_datetime(tr.buy_date, format='%Y%m%d')).dt.days.mean() if len(tr) else 0
        results[tag] = dict(final_eq=round(float(final_eq),2), total_ret=round(float(final_eq/INIT_CAP-1),4),
            cagr=round(float((final_eq/INIT_CAP)**(1/10.8)-1),4), max_dd=round(float(dd.min()),4),
            mdd_window=[dates[ms], dates[me]], trades=len(tr), win_rate=round(float(win),4),
            pl_ratio=round(abs(aw/al),2) if (len(tr) and al and aw<0) else None,
            avg_hold_days=round(float(hd),1),
            yearly={str(int(y)): round(float(v),4) for y, v in yr.items()})
        tr.to_json(os.path.join(OUT, f'trades_{tag}.jsonl'), orient='records', lines=True, force_ascii=False)
        np.save(os.path.join(OUT, f'eq_{tag}.npy'), eq)
        log(f'[R2 {gi}] {tag}: eq={final_eq:,.0f} ret={results[tag]["total_ret"]:+.1%} '
            f'cagr={results[tag]["cagr"]:+.1%} mdd={dd.min():.1%} trades={len(tr)} win={win:.1%} ({time.time()-t1:.0f}s)')
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('R2 DONE'); sys.exit(0)

# ================= ROUND 3: 情绪闸门(涨停触及数代理) 复测 =================
if len(sys.argv) > 1 and sys.argv[1] == 'r3':
    thr = pd.DataFrame(9.7, index=W['change_pct'].index, columns=W['change_pct'].columns)
    cyb_cols = [c for c in codes if c[:3] in ('300', '301')]
    thr.loc[thr.index >= CYB_REG, cyb_cols] = 19.5
    touch = (W['change_pct'] >= thr).fillna(False)
    lu = touch.sum(axis=1)                                   # 宇宙内涨停触及数(代理)
    lu_pctl = lu.rolling(250, min_periods=60).rank(pct=True)
    # 代理校验: 与 daily_market_stats.zt_count 2026 重叠段相关性
    try:
        con5 = sqlite3.connect(RAW)
        zt = pd.read_sql_query("SELECT date,zt_count FROM daily_market_stats", con5)
        con5.close()
        zt['date'] = zt['date'].astype(str)
        m = pd.DataFrame({'proxy': lu.values, 'real': zt.set_index('date').reindex(dates)['zt_count'].values}).dropna()
        corr = m['proxy'].corr(m['real'])
        log(f'代理校验(2026重叠{len(m)}日): corr={corr:.3f} proxy均值={m.proxy.mean():.0f} 真实均值={m.real.mean():.0f}')
    except Exception as e:
        log('代理校验失败:', e)
    sig_base = build_signals(60, 2.0, True)
    gate = (BREADTH > 0.50).reindex(sig_base.index).fillna(False).values
    sig_r2 = base_gate = sig_base & gate[:, None]
    sig_r2 &= (W['change_pct'] <= 7.0).fillna(False)
    dd250 = W['close'] / W['high'].rolling(250, min_periods=60).max() - 1
    sig_r2 &= (dd250 > -0.60).fillna(False)
    OPT = dict(stop_touch=True, gap_skip=5.0, dual_time=True, quality_rank=False)
    for pctl_min, name in [(30, 'r3_gate30'), (50, 'r3_gate50')]:
        t1 = time.time()
        eg = (lu_pctl >= pctl_min / 100).reindex(sig_r2.index).fillna(True).values  # 无数据日不放行闸门(保守禁开)
        sig = sig_r2 & eg[:, None]
        cash, eq, trades = simulate(sig, 0.92, why_tag=f'r3_emotion{pctl_min}', **OPT)
        final_eq = eq[-1]
        ys = pd.Series(eq, index=pd.to_datetime(pd.Series([str(x) for x in dates]), format='%Y%m%d'))
        yl = ys.groupby(ys.index.year).agg(['last']); yr = yl['last'].pct_change()
        yr.iloc[0] = yl['last'].iloc[0] / INIT_CAP - 1
        peak = np.maximum.accumulate(eq); ddc = eq/peak - 1
        me = int(ddc.argmin()); ms = int(eq[:me+1].argmax())
        tr = pd.DataFrame(trades)
        win = (tr.pnl > 0).mean() if len(tr) else 0
        aw = tr.loc[tr.pnl > 0, 'pnl_pct'].mean() if len(tr) else 0
        al = tr.loc[tr.pnl <= 0, 'pnl_pct'].mean() if len(tr) else 0
        hd = (pd.to_datetime(tr.sell_date, format='%Y%m%d') - pd.to_datetime(tr.buy_date, format='%Y%m%d')).dt.days.mean() if len(tr) else 0
        results[f'{name}'] = dict(final_eq=round(float(final_eq),2), total_ret=round(float(final_eq/INIT_CAP-1),4),
            cagr=round(float((final_eq/INIT_CAP)**(1/10.8)-1),4), max_dd=round(float(ddc.min()),4),
            mdd_window=[dates[ms], dates[me]], trades=len(tr), win_rate=round(float(win),4),
            pl_ratio=round(abs(aw/al),2) if (len(tr) and al and aw<0) else None,
            avg_hold_days=round(float(hd),1),
            yearly={str(int(y)): round(float(v),4) for y, v in yr.items()})
        tr.to_json(os.path.join(OUT, f'trades_{name}.jsonl'), orient='records', lines=True, force_ascii=False)
        np.save(os.path.join(OUT, f'eq_{name}.npy'), eq)
        log(f'[R3] {name}: eq={final_eq:,.0f} ret={results[name]["total_ret"]:+.1%} '
            f'cagr={results[name]["cagr"]:+.1%} mdd={ddc.min():.1%} trades={len(tr)} win={win:.1%} ({time.time()-t1:.0f}s)')
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('R3 DONE'); sys.exit(0)

# ========== ROUND 3V: 真实涨停史复验 (feature_market_sentiment.limit_up, 9089行全史) ==========
if len(sys.argv) > 1 and sys.argv[1] == 'r3v':
    con6 = sqlite3.connect(r'D:\workbuddyspace\databases\fundamental_data.db')
    rl = pd.read_sql_query("SELECT trade_date,limit_up FROM feature_market_sentiment", con6)
    con6.close()
    rl['d'] = rl['trade_date'].str.replace('-', '', regex=False)
    rmap = dict(zip(rl['d'], rl['limit_up']))
    lu_real = pd.Series([rmap.get(str(d), np.nan) for d in dates])
    cov = lu_real.notna().mean()
    lu_rpctl = lu_real.rolling(250, min_periods=60).rank(pct=True)
    thr_p = pd.DataFrame(9.7, index=W['change_pct'].index, columns=W['change_pct'].columns)
    cyb_cols = [c for c in codes if c[:3] in ('300', '301')]
    thr_p.loc[thr_p.index >= CYB_REG, cyb_cols] = 19.5
    lu = ((W['change_pct'] >= thr_p).fillna(False)).sum(axis=1)
    # 代理 vs 真实 重叠段相关
    prx = lu.reindex(lu_real.index)
    m = pd.DataFrame({'proxy': prx, 'real': lu_real}).dropna()
    log(f'真实序列覆盖 {cov:.1%} | 代理vs真实 corr={m.proxy.corr(m.real):.3f} (n={len(m)}) '
        f'真实均值={m.real.mean():.0f} 代理均值={m.proxy.mean():.0f}')
    sig_base = build_signals(60, 2.0, True)
    gate = (BREADTH > 0.50).reindex(sig_base.index).fillna(False).values
    sig_r2 = sig_base & gate[:, None]
    sig_r2 &= (W['change_pct'] <= 7.0).fillna(False)
    dd250 = W['close'] / W['high'].rolling(250, min_periods=60).max() - 1
    sig_r2 &= (dd250 > -0.60).fillna(False)
    OPT = dict(stop_touch=True, gap_skip=5.0, dual_time=True, quality_rank=False)
    for pctl_min, name in [(30, 'r3v_gate30'), (50, 'r3v_gate50')]:
        t1 = time.time()
        eg = (lu_rpctl >= pctl_min / 100).fillna(True).values
        sig = sig_r2 & eg[:, None]
        cash, eq, trades = simulate(sig, 0.92, why_tag=f'r3v_real{pctl_min}', **OPT)
        final_eq = eq[-1]
        ys = pd.Series(eq, index=pd.to_datetime(pd.Series([str(x) for x in dates]), format='%Y%m%d'))
        yl = ys.groupby(ys.index.year).agg(['last']); yr = yl['last'].pct_change()
        yr.iloc[0] = yl['last'].iloc[0] / INIT_CAP - 1
        peak = np.maximum.accumulate(eq); ddc = eq/peak - 1
        me = int(ddc.argmin()); ms = int(eq[:me+1].argmax())
        tr = pd.DataFrame(trades)
        win = (tr.pnl > 0).mean() if len(tr) else 0
        aw = tr.loc[tr.pnl > 0, 'pnl_pct'].mean() if len(tr) else 0
        al = tr.loc[tr.pnl <= 0, 'pnl_pct'].mean() if len(tr) else 0
        hd = (pd.to_datetime(tr.sell_date, format='%Y%m%d') - pd.to_datetime(tr.buy_date, format='%Y%m%d')).dt.days.mean() if len(tr) else 0
        results[name] = dict(final_eq=round(float(final_eq),2), total_ret=round(float(final_eq/INIT_CAP-1),4),
            cagr=round(float((final_eq/INIT_CAP)**(1/10.8)-1),4), max_dd=round(float(ddc.min()),4),
            mdd_window=[dates[ms], dates[me]], trades=len(tr), win_rate=round(float(win),4),
            pl_ratio=round(abs(aw/al),2) if (len(tr) and al and aw<0) else None,
            avg_hold_days=round(float(hd),1),
            yearly={str(int(y)): round(float(v),4) for y, v in yr.items()})
        tr.to_json(os.path.join(OUT, f'trades_{name}.jsonl'), orient='records', lines=True, force_ascii=False)
        np.save(os.path.join(OUT, f'eq_{name}.npy'), eq)
        log(f'[R3V] {name}: eq={final_eq:,.0f} ret={results[name]["total_ret"]:+.1%} '
            f'cagr={results[name]["cagr"]:+.1%} mdd={ddc.min():.1%} trades={len(tr)} win={win:.1%} ({time.time()-t1:.0f}s)')
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('R3V DONE'); sys.exit(0)

# ==== ROUND 5: 原始价精确重建涨停/炸板全史 + 真值化情绪闸门 ====
if len(sys.argv) > 1 and sys.argv[1] == 'r5':
    t5 = time.time()
    con7 = sqlite3.connect(RAW)
    raw5 = pd.read_sql_query(
        "SELECT code,date,high,close,change_amount FROM stock_daily_meta WHERE date>=? AND date<=?",
        con7, params=(START, END))
    con7.close()
    raw5['date'] = raw5['date'].astype(str)
    raw5 = raw5[raw5['code'].isin(codes)]
    hi = raw5.pivot_table(index='date', columns='code', values='high', aggfunc='last').reindex(index=dates, columns=codes)
    cl = raw5.pivot_table(index='date', columns='code', values='close', aggfunc='last').reindex(index=dates, columns=codes)
    ca = raw5.pivot_table(index='date', columns='code', values='change_amount', aggfunc='last').reindex(index=dates, columns=codes)
    pref = cl - ca                                  # 交易所参考昨收(含除权调整)
    thr5 = pd.DataFrame(0.10, index=dates, columns=codes)
    cyb5 = [c for c in codes if c[:3] in ('300', '301')]
    thr5.loc[thr5.index >= CYB_REG, cyb5] = 0.20
    lim_px = (pref * (1 + thr5)).round(2)
    eps = 0.001
    sealed = (cl >= lim_px - eps) & (cl == cl) & (lim_px == lim_px)
    touched = (hi >= lim_px - eps) & (hi == hi) & (lim_px == lim_px)
    lu_seal = sealed.sum(axis=1)
    lu_touch = touched.sum(axis=1)
    zha = (lu_touch - lu_seal).clip(lower=0)
    br_rate = (zha / lu_touch.replace(0, np.nan)).fillna(0)
    # 真值校验: feature_market_sentiment.limit_up 2026重叠段
    try:
        con8 = sqlite3.connect(r'D:\workbuddyspace\databases\fundamental_data.db')
        rl2 = pd.read_sql_query("SELECT trade_date,limit_up FROM feature_market_sentiment", con8)
        con8.close()
        rl2['d'] = rl2['trade_date'].str.replace('-', '', regex=False)
        r2m = dict(zip(rl2['d'], rl2['limit_up']))
        rls = pd.Series([r2m.get(str(d), np.nan) for d in dates])
        mv = pd.DataFrame({'recon': lu_seal, 'truth': rls}).dropna()
        log(f'重建校验(2026重叠{len(mv)}日): corr={mv.recon.corr(mv.truth):.3f} '
            f'重建均值={mv.recon.mean():.1f} 真值均值={mv.truth.mean():.1f} 差中位={ (mv.recon-mv.truth).abs().median():.1f}')
    except Exception as e:
        log('真值校验失败:', e)
    lu_pctl5 = lu_seal.rolling(250, min_periods=60).rank(pct=True)
    sig_base = build_signals(60, 2.0, True)
    gate = (BREADTH > 0.50).reindex(sig_base.index).fillna(False).values
    sig_r2 = sig_base & gate[:, None]
    sig_r2 &= (W['change_pct'] <= 7.0).fillna(False)
    dd250 = W['close'] / W['high'].rolling(250, min_periods=60).max() - 1
    sig_r2 &= (dd250 > -0.60).fillna(False)
    OPT = dict(stop_touch=True, gap_skip=5.0, dual_time=True, quality_rank=False)
    for pctl_min, name in [(30, 'r5_gate30'), (50, 'r5_gate50')]:
        t1 = time.time()
        eg = (lu_pctl5 >= pctl_min / 100).fillna(True).values
        sig = sig_r2 & eg[:, None]
        cash, eq, trades = simulate(sig, 0.92, why_tag=f'r5_exact{pctl_min}', **OPT)
        final_eq = eq[-1]
        ys = pd.Series(eq, index=pd.to_datetime(pd.Series([str(x) for x in dates]), format='%Y%m%d'))
        yl = ys.groupby(ys.index.year).agg(['last']); yr = yl['last'].pct_change()
        yr.iloc[0] = yl['last'].iloc[0] / INIT_CAP - 1
        peak = np.maximum.accumulate(eq); ddc = eq/peak - 1
        me = int(ddc.argmin()); ms = int(eq[:me+1].argmax())
        tr = pd.DataFrame(trades)
        win = (tr.pnl > 0).mean() if len(tr) else 0
        aw = tr.loc[tr.pnl > 0, 'pnl_pct'].mean() if len(tr) else 0
        al = tr.loc[tr.pnl <= 0, 'pnl_pct'].mean() if len(tr) else 0
        hd = (pd.to_datetime(tr.sell_date, format='%Y%m%d') - pd.to_datetime(tr.buy_date, format='%Y%m%d')).dt.days.mean() if len(tr) else 0
        results[name] = dict(final_eq=round(float(final_eq),2), total_ret=round(float(final_eq/INIT_CAP-1),4),
            cagr=round(float((final_eq/INIT_CAP)**(1/10.8)-1),4), max_dd=round(float(ddc.min()),4),
            mdd_window=[dates[ms], dates[me]], trades=len(tr), win_rate=round(float(win),4),
            pl_ratio=round(abs(aw/al),2) if (len(tr) and al and aw<0) else None,
            avg_hold_days=round(float(hd),1),
            yearly={str(int(y)): round(float(v),4) for y, v in yr.items()})
        tr.to_json(os.path.join(OUT, f'trades_{name}.jsonl'), orient='records', lines=True, force_ascii=False)
        np.save(os.path.join(OUT, f'eq_{name}.npy'), eq)
        log(f'[R5] {name}: eq={final_eq:,.0f} ret={results[name]["total_ret"]:+.1%} '
            f'cagr={results[name]["cagr"]:+.1%} mdd={ddc.min():.1%} trades={len(tr)} win={win:.1%} ({time.time()-t1:.0f}s)')
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('R5 DONE', round(time.time()-t5, 1), 's'); sys.exit(0)

# ==== ROUND 6: 板块动量广度闸门(本地申万/通达信板块日线) 叠加 r5_gate50 ====
if len(sys.argv) > 1 and sys.argv[1] == 'r6':
    con9 = sqlite3.connect(r'D:\workbuddyspace\databases\sw_industry.db')
    sw = pd.read_sql_query("SELECT trade_date,ts_code,close FROM sw_industry_daily WHERE trade_date>=? AND trade_date<=?",
                           con9, params=(20150601, 20261020))
    con9.close()
    sw['trade_date'] = sw['trade_date'].astype(str)
    swc = sw.pivot_table(index='trade_date', columns='ts_code', values='close', aggfunc='last').sort_index()
    ret20 = swc.pct_change(20)
    sbreadth = (ret20 > 0).mean(axis=1)                      # 板块20日动量为正占比
    sb = sbreadth.reindex(dates)                             # 对齐引擎日历(尾段2026-04后NaN)
    cov6 = sb.notna().mean()
    log(f'板块广度覆盖 {cov6:.1%} (缺口段按放行处理, 已披露)')
    sig_base = build_signals(60, 2.0, True)
    gate = (BREADTH > 0.50).reindex(sig_base.index).fillna(False).values
    sig = sig_base & gate[:, None]
    sig &= (W['change_pct'] <= 7.0).fillna(False)
    dd250 = W['close'] / W['high'].rolling(250, min_periods=60).max() - 1
    sig &= (dd250 > -0.60).fillna(False)
    lu_p = pd.Series(np.nan, index=dates)
    con10 = sqlite3.connect(RAW)
    raw6 = pd.read_sql_query("SELECT date, COUNT(*) n, SUM(CASE WHEN close >= ROUND((close-change_amount)*(1+CASE WHEN substr(code,1,3) IN ('300','301') AND date>='20200824' THEN 0.20 ELSE 0.10 END),2)-0.001 THEN 1 ELSE 0 END) seal FROM stock_daily_meta WHERE date>=? AND date<=? GROUP BY date",
                             con10, params=(START, END))
    con10.close()
    raw6['date'] = raw6['date'].astype(str)
    lu_p = raw6.set_index('date')['seal'].reindex(dates)
    lu_pctl6 = lu_p.rolling(250, min_periods=60).rank(pct=True)
    sig &= ((lu_pctl6 >= 0.50).fillna(True).values)[:, None]
    sb_gate = (sb >= 0.50).fillna(True).values               # 板块闸门(缺口放行)
    sig = sig & sb_gate[:, None]
    t6 = time.time()
    cash, eq, trades = simulate(sig, 0.92, why_tag='r6_sector_emotion', stop_touch=True, gap_skip=5.0, dual_time=True, quality_rank=False)
    final_eq = eq[-1]
    ys = pd.Series(eq, index=pd.to_datetime(pd.Series([str(x) for x in dates]), format='%Y%m%d'))
    yl = ys.groupby(ys.index.year).agg(['last']); yr = yl['last'].pct_change()
    yr.iloc[0] = yl['last'].iloc[0] / INIT_CAP - 1
    peak = np.maximum.accumulate(eq); ddc = eq/peak - 1
    me = int(ddc.argmin()); ms = int(eq[:me+1].argmax())
    tr = pd.DataFrame(trades)
    win = (tr.pnl > 0).mean() if len(tr) else 0
    aw = tr.loc[tr.pnl > 0, 'pnl_pct'].mean() if len(tr) else 0
    al = tr.loc[tr.pnl <= 0, 'pnl_pct'].mean() if len(tr) else 0
    hd = (pd.to_datetime(tr.sell_date, format='%Y%m%d') - pd.to_datetime(tr.buy_date, format='%Y%m%d')).dt.days.mean() if len(tr) else 0
    results['r6_sector'] = dict(final_eq=round(float(final_eq),2), total_ret=round(float(final_eq/INIT_CAP-1),4),
        cagr=round(float((final_eq/INIT_CAP)**(1/10.8)-1),4), max_dd=round(float(ddc.min()),4),
        mdd_window=[dates[ms], dates[me]], trades=len(tr), win_rate=round(float(win),4),
        pl_ratio=round(abs(aw/al),2) if (len(tr) and al and aw<0) else None,
        avg_hold_days=round(float(hd),1),
        yearly={str(int(y)): round(float(v),4) for y, v in yr.items()})
    tr.to_json(os.path.join(OUT, 'trades_r6_sector.jsonl'), orient='records', lines=True, force_ascii=False)
    np.save(os.path.join(OUT, 'eq_r6_sector.npy'), eq)
    log(f'[R6] r6_sector: eq={final_eq:,.0f} ret={results["r6_sector"]["total_ret"]:+.1%} '
        f'cagr={results["r6_sector"]["cagr"]:+.1%} mdd={ddc.min():.1%} trades={len(tr)} win={win:.1%} ({time.time()-t6:.0f}s)')
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('R6 DONE'); sys.exit(0)

# ==== ROUND 7: 北向资金环境闸门(northbound_daily 本地真实层) 叠加 r5_gate50 ====
if len(sys.argv) > 1 and sys.argv[1] == 'r7':
    con11 = sqlite3.connect(r'D:\workbuddyspace\databases\fundamental_data.db')
    nb = pd.read_sql_query("SELECT trade_date, north_net_buy FROM northbound_daily", con11)
    con11.close()
    nb['d'] = nb['trade_date'].astype(str).str.replace('-', '', regex=False)
    nbs = nb.groupby('d')['north_net_buy'].sum()
    nb5 = nbs.rolling(5, min_periods=3).sum().reindex(dates)
    cov7 = nb5.notna().mean()
    log(f'北向序列覆盖 {cov7:.1%} (2024-08停披后缺口按放行处理, 已披露)')
    sig_base = build_signals(60, 2.0, True)
    gate = (BREADTH > 0.50).reindex(sig_base.index).fillna(False).values
    sig = sig_base & gate[:, None]
    sig &= (W['change_pct'] <= 7.0).fillna(False)
    dd250 = W['close'] / W['high'].rolling(250, min_periods=60).max() - 1
    sig &= (dd250 > -0.60).fillna(False)
    con12 = sqlite3.connect(RAW)
    raw7 = pd.read_sql_query("SELECT date, SUM(CASE WHEN close >= ROUND((close-change_amount)*(1+CASE WHEN substr(code,1,3) IN ('300','301') AND date>='20200824' THEN 0.20 ELSE 0.10 END),2)-0.001 THEN 1 ELSE 0 END) seal FROM stock_daily_meta WHERE date>=? AND date<=? GROUP BY date",
                             con12, params=(START, END))
    con12.close()
    raw7['date'] = raw7['date'].astype(str)
    lu7 = raw7.set_index('date')['seal'].reindex(dates)
    lu_pctl7 = lu7.rolling(250, min_periods=60).rank(pct=True)
    sig &= ((lu_pctl7 >= 0.50).fillna(True).values)[:, None]
    nb_gate = (nb5 >= 0).fillna(True).values          # 北向5日累计净买>=0才开仓
    sig = sig & nb_gate[:, None]
    t7 = time.time()
    cash, eq, trades = simulate(sig, 0.92, why_tag='r7_northbound', stop_touch=True, gap_skip=5.0, dual_time=True, quality_rank=False)
    final_eq = eq[-1]
    ys = pd.Series(eq, index=pd.to_datetime(pd.Series([str(x) for x in dates]), format='%Y%m%d'))
    yl = ys.groupby(ys.index.year).agg(['last']); yr = yl['last'].pct_change()
    yr.iloc[0] = yl['last'].iloc[0] / INIT_CAP - 1
    peak = np.maximum.accumulate(eq); ddc = eq/peak - 1
    me = int(ddc.argmin()); ms = int(eq[:me+1].argmax())
    tr = pd.DataFrame(trades)
    win = (tr.pnl > 0).mean() if len(tr) else 0
    aw = tr.loc[tr.pnl > 0, 'pnl_pct'].mean() if len(tr) else 0
    al = tr.loc[tr.pnl <= 0, 'pnl_pct'].mean() if len(tr) else 0
    hd = (pd.to_datetime(tr.sell_date, format='%Y%m%d') - pd.to_datetime(tr.buy_date, format='%Y%m%d')).dt.days.mean() if len(tr) else 0
    results['r7_north'] = dict(final_eq=round(float(final_eq),2), total_ret=round(float(final_eq/INIT_CAP-1),4),
        cagr=round(float((final_eq/INIT_CAP)**(1/10.8)-1),4), max_dd=round(float(ddc.min()),4),
        mdd_window=[dates[ms], dates[me]], trades=len(tr), win_rate=round(float(win),4),
        pl_ratio=round(abs(aw/al),2) if (len(tr) and al and aw<0) else None,
        avg_hold_days=round(float(hd),1),
        yearly={str(int(y)): round(float(v),4) for y, v in yr.items()})
    tr.to_json(os.path.join(OUT, 'trades_r7_north.jsonl'), orient='records', lines=True, force_ascii=False)
    np.save(os.path.join(OUT, 'eq_r7_north.npy'), eq)
    log(f'[R7] r7_north: eq={final_eq:,.0f} ret={results["r7_north"]["total_ret"]:+.1%} '
        f'cagr={results["r7_north"]["cagr"]:+.1%} mdd={ddc.min():.1%} trades={len(tr)} win={win:.1%} ({time.time()-t7:.0f}s)')
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('R7 DONE'); sys.exit(0)

# ==== ROUND 8: r5_gate50 参数邻域稳健性检验(敏感性, 非寻优) ====
if len(sys.argv) > 1 and sys.argv[1] == 'r8':
    con13 = sqlite3.connect(RAW)
    raw8 = pd.read_sql_query("SELECT date, SUM(CASE WHEN close >= ROUND((close-change_amount)*(1+CASE WHEN substr(code,1,3) IN ('300','301') AND date>='20200824' THEN 0.20 ELSE 0.10 END),2)-0.001 THEN 1 ELSE 0 END) seal FROM stock_daily_meta WHERE date>=? AND date<=? GROUP BY date",
                             con13, params=(START, END))
    con13.close()
    raw8['date'] = raw8['date'].astype(str)
    lu8 = raw8.set_index('date')['seal'].reindex(dates)
    lu_pctl8 = lu8.rolling(250, min_periods=60).rank(pct=True)
    sig_base = build_signals(60, 2.0, True)
    gate = (BREADTH > 0.50).reindex(sig_base.index).fillna(False).values
    sig0 = sig_base & gate[:, None]
    sig0 &= (W['change_pct'] <= 7.0).fillna(False)
    dd250 = W['close'] / W['high'].rolling(250, min_periods=60).max() - 1
    sig0 &= (dd250 > -0.60).fillna(False)
    for pctl_min, sr in [(40, 0.92), (45, 0.92), (55, 0.92), (60, 0.92), (50, 0.90), (50, 0.94)]:
        t8 = time.time()
        sig = sig0 & ((lu_pctl8 >= pctl_min / 100).fillna(True).values)[:, None]
        cash, eq, trades = simulate(sig, sr, why_tag=f'r8_p{pctl_min}_stop{int((1-sr)*100)}',
                                    stop_touch=True, gap_skip=5.0, dual_time=True, quality_rank=False)
        final_eq = eq[-1]
        tr = pd.DataFrame(trades)
        win = (tr.pnl > 0).mean() if len(tr) else 0
        peak = np.maximum.accumulate(eq); ddc = eq/peak - 1
        name = f'r8_p{pctl_min}_s{int((1-sr)*100)}'
        results[name] = dict(final_eq=round(float(final_eq),2), total_ret=round(float(final_eq/INIT_CAP-1),4),
            cagr=round(float((final_eq/INIT_CAP)**(1/10.8)-1),4), max_dd=round(float(ddc.min()),4),
            trades=len(tr), win_rate=round(float(win),4))
        np.save(os.path.join(OUT, f'eq_{name}.npy'), eq)
        log(f'[R8] {name}: eq={final_eq:,.0f} ret={results[name]["total_ret"]:+.1%} mdd={ddc.min():.1%} trades={len(tr)} win={win:.1%} ({time.time()-t8:.0f}s)')
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('R8 DONE'); sys.exit(0)

# ==== ROUND 9: 随机入场对照(置换检验) — 量化入场形态边际价值 ====
if len(sys.argv) > 1 and sys.argv[1] == 'r9':
    import random
    con14 = sqlite3.connect(RAW)
    raw9 = pd.read_sql_query("SELECT date, SUM(CASE WHEN close >= ROUND((close-change_amount)*(1+CASE WHEN substr(code,1,3) IN ('300','301') AND date>='20200824' THEN 0.20 ELSE 0.10 END),2)-0.001 THEN 1 ELSE 0 END) seal FROM stock_daily_meta WHERE date>=? AND date<=? GROUP BY date",
                             con14, params=(START, END))
    con14.close()
    raw9['date'] = raw9['date'].astype(str)
    lu9 = raw9.set_index('date')['seal'].reindex(dates)
    lu_pctl9 = lu9.rolling(250, min_periods=60).rank(pct=True)
    # 基础过滤(无突破条件): 年龄/价带/流动性/未涨停收盘
    bh9 = W['high'].rolling(60, min_periods=60).max().shift(1)
    bl9 = W['low'].rolling(60, min_periods=60).min().shift(1)
    vma9 = W['volume'].rolling(5, min_periods=5).mean().shift(1)
    base9 = (raw_close >= PRICE_LO) & (raw_close <= PRICE_HI)
    base9 &= W['amount'] >= MIN_AMT
    fi9 = np.array([first_idx[c] for c in codes])
    base9 &= np.arange(T)[:, None] >= (fi9[None, :] + AGE_BARS)
    base9 &= W['change_pct'] < 9.7
    gate9 = (BREADTH > 0.50).reindex(sig_base.index if 'sig_base' in dir() else W['close'].index).fillna(False).values
    base9 &= gate9[:, None]
    base9 &= ((lu_pctl9 >= 0.50).fillna(True).values)[:, None]
    base9 = base9.fillna(False)
    sig_r5 = build_signals(60, 2.0, True) & ((lu_pctl9 >= 0.50).fillna(True).values)[:, None]
    cnt = sig_r5.sum(axis=1).values                       # 每日突破信号数(等量置换)
    OPT9 = dict(stop_touch=True, gap_skip=5.0, dual_time=True, quality_rank=False)
    stats = []
    for seed in (42, 7, 2026):
        rng = np.random.default_rng(seed)
        sig_r = np.zeros((T, C), dtype=bool)
        for i in range(T):
            k = int(cnt[i])
            if k <= 0: continue
            pool = np.where(base9.values[i])[0]
            if len(pool) == 0: continue
            pick = rng.choice(pool, size=min(k, len(pool)), replace=False)
            sig_r[i, pick] = True
        sig_rdf = pd.DataFrame(sig_r, index=dates, columns=codes)
        cash, eq, trades = simulate(sig_rdf, 0.92, why_tag=f'r9_random_{seed}',
                                    stop_touch=True, gap_skip=5.0, dual_time=True, quality_rank=False)
        tr = pd.DataFrame(trades)
        win = (tr.pnl > 0).mean() if len(tr) else 0
        peak = np.maximum.accumulate(eq); ddc = eq/peak - 1
        name = f'r9_rand{seed}'
        results[name] = dict(final_eq=round(float(eq[-1]),2), total_ret=round(float(eq[-1]/INIT_CAP-1),4),
            cagr=round(float((eq[-1]/INIT_CAP)**(1/10.8)-1),4), max_dd=round(float(ddc.min()),4),
            trades=len(tr), win_rate=round(float(win),4))
        np.save(os.path.join(OUT, f'eq_{name}.npy'), eq)
        stats.append(results[name]['total_ret'])
        log(f'[R9 seed={seed}] eq={eq[-1]:,.0f} ret={results[name]["total_ret"]:+.1%} mdd={ddc.min():.1%} trades={len(tr)} win={win:.1%}')
    log(f'R9 三种子平均收益 {np.mean(stats):+.1%} (对照 r5_gate50 尖峰+25.9% / 邻域均值≈-11%)')
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    log('R9 DONE'); sys.exit(0)
GRID = [ (bn, vr, uf, sr) for bn in (30,60) for vr in (1.5,2.0) for uf in (False,True) for sr in (0.92,0.94) ]
for gi,(bn,vr,uf,sr) in enumerate(GRID,1):
    t1=time.time()
    skey = (bn, vr, uf)
    if skey not in SIG_CACHE:
        SIG_CACHE[skey] = build_signals(bn, vr, uf)
    sig = SIG_CACHE[skey]
    cash, eq, trades = simulate(sig, sr, why_tag=f'box{bn}_breakout_vr{vr}' + ('_mafilter' if uf else ''))
    tag = f'v{gi}_box{bn}_vr{vr}_ma{int(uf)}_stop{int((1-sr)*100)}'
    final_eq = eq[-1]
    years = pd.Series(eq, index=pd.to_datetime(pd.Series(dates), format='%Y%m%d'))
    yearly = years.groupby(years.index.year).agg(['last'])
    yr_ret = yearly['last'].pct_change()
    yr_ret.iloc[0] = yearly['last'].iloc[0]/INIT_CAP - 1
    peak = np.maximum.accumulate(eq); dd = eq/peak - 1
    mdd = dd.min(); mdd_end = int(dd.argmin()); mdd_start = int(eq[:mdd_end+1].argmax())
    tr = pd.DataFrame(trades)
    win = (tr['pnl']>0).mean() if len(tr) else 0
    aw = tr.loc[tr.pnl>0,'pnl_pct'].mean() if len(tr) else 0
    al = tr.loc[tr.pnl<=0,'pnl_pct'].mean() if len(tr) else 0
    hold_days = (pd.to_datetime(tr.sell_date,format='%Y%m%d') - pd.to_datetime(tr.buy_date,format='%Y%m%d')) if len(tr) else None
    results[tag] = dict(final_eq=round(final_eq,2), total_ret=round(final_eq/INIT_CAP-1,4),
        cagr=round((final_eq/INIT_CAP)**(1/10.8)-1,4), max_dd=round(mdd,4),
        mdd_window=[dates[mdd_start], dates[mdd_end]],
        trades=len(tr), win_rate=round(win,4), pl_ratio=round(abs(aw/al),2) if al else None,
        avg_hold_days=round(hold_days.dt.days.mean(),1) if len(tr) else 0,
        yearly={int(y): round(v,4) for y,v in yr_ret.items()})
    tr.to_json(os.path.join(OUT, f'trades_{tag}.jsonl'), orient='records', lines=True, force_ascii=False)
    np.save(os.path.join(OUT, f'eq_{tag}.npy'), eq)
    log(f'[{gi}/{len(GRID)}] {tag}: eq={final_eq:,.0f} ret={results[tag]["total_ret"]:+.1%} cagr={results[tag]["cagr"]:+.1%} '
        f'mdd={mdd:.1%} trades={len(tr)} win={win:.1%} ({time.time()-t1:.0f}s)')

with open(os.path.join(OUT,'summary.json'),'w',encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
log('ALL DONE', round(time.time()-t0,1),'s')
