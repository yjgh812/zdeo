#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""universe_startup_backtest_20260924.py — U系列: 全宇宙低位启动扫描器历史回放臂（第二轮迭代）

主人指令: 结合屠龙系统选股+全宇宙选股, 提高收益率/胜率, 优化迭代(2026-09-24)。
信号真源 = universe_startup_scan.py 漏斗(赢家画像v3)。T日收盘判定→T+1开盘执行, 无未来函数。

执行语义出处 core/backtest_system/strategy.py:
- L9-12/L53-85 信号T日收盘生成→T+1开盘成交(pending); L88-95 时间止损=自然日(.days)对比max_holding_bars;
  L104-108 止损=收盘浮亏≤-8%→次开卖出(收盘判定非盘中触及)。
成本 core/backtest_system/config.py: commission 0.00025双边min5 + stamp 0.0005卖 + slippage 0.002双边。

口径决定(如实标注):
- 域=60/00/30开头(剔科创/北交所), 现名剔ST/退(历史ST态不可得=近似); qfq价2-30; 历史≥400根且T侧≥250根
- 缩量cap固定0.95(历史LOW_VOL态无日度序列); --check23 用当日真实cap=1.56与实弹扫描JSON对账
- 买不进近似: T+1开盘较T收盘≥+7% 放弃(次日若仍在目标再试); 停牌日开盘无数据=放弃重排
- 目标持仓法: 每日TopN, 掉名单卖(不trim超配), 空位按分数序补; 卖因优先=时间止损>止损>掉名单
- 样本末持仓按最后收盘清算(end_of_sample)

守门: --verify N 随机N命中用逐字复刻路径重算断言一致(矩阵vs真源); --check23 与
reports/universe_startup_scan_20260923.json 实弹名单对账; 现金守恒断言(±0.5%)。
用法: python universe_startup_backtest_20260924.py [--from 20160101] [--verify 20] [--check23]
"""
import os, sys, json, time, random, argparse
from datetime import date
import numpy as np
import pandas as pd
import sqlite3

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
RAW_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
OUT = os.path.join(ROOT, "reports", "universe_arm_20260924")
os.makedirs(OUT, exist_ok=True)
INIT, END_DATE = 100_000.0, 20260923
COMMISSION, MIN_COMM, STAMP, SLIP, STOP = 0.00025, 5.0, 0.0005, 0.002, 0.08
ARMS = {"U5-45": (5, 45), "U10-45": (10, 45), "U10-0": (10, 0), "U10-22": (10, 22),
        "U5-H60": (5, -60), "U10-H60": (10, -60),   # 负值=到期持有模式(交易日bars), 无每日名单重排
        "UALL-H60-NS": (0, -60, True, False),       # 主人令「名单不应限制」: 全信号无TopN, 到期60日, 无止损(豁免口径)
        "UALL-H60-SL": (0, -60, True, True)}        # 全信号无TopN, 到期60日, 含-8%止损(对照)
FIXED_NOTIONAL = 1000.0   # 全信号臂单票固定名义(元); 现金不足跳过(先到先得, 分数序优先如实标注)


def log(*a):
    print(*a, flush=True)


def to_d(i):
    return date(i // 10000, i // 100 % 100, i % 100)


# ================================================================ 漏斗(矩阵路径)
def funnel_stock(c, v, cap=0.95):
    """时间正序单票序列 → 命中布尔+打分。逻辑逐条对照 universe_startup_scan.py。"""
    n = len(c)
    if n < 400:
        return None, None
    cs = np.concatenate([[0.0], np.cumsum(c)])
    vs = np.concatenate([[0.0], np.cumsum(v)])
    def sma(arr, w, pad):
        d = (arr[w:] - arr[:-w]) / w
        return np.concatenate([np.full(pad, np.nan), d])
    ma20 = sma(cs, 20, 19); ma60 = sma(cs, 60, 59)
    vm20 = sma(vs, 20, 19); vm250 = sma(vs, 250, 249)
    W250 = np.lib.stride_tricks.sliding_window_view(c, 250)
    lo250 = np.concatenate([np.full(249, np.nan), W250.min(1)])
    hi250 = np.concatenate([np.full(249, np.nan), W250.max(1)])
    rng = hi250 - lo250
    pos250 = 100.0 * (c - lo250) / np.where(rng > 0, rng, np.nan)
    lo_day = np.concatenate([np.full(249, -1), np.arange(249, n) - W250[:, ::-1].argmin(1)])  # 全局index: T - rev_argmin
    W750 = np.lib.stride_tricks.sliding_window_view(c, 750) if n >= 750 else None
    if W750 is not None:
        hi3 = np.concatenate([np.full(749, np.nan), W750.max(1)])
        lo3 = np.concatenate([np.full(749, np.nan), W750.min(1)])
        fa, fb = np.maximum.accumulate(c), np.minimum.accumulate(c)   # 正向=截至T历史(scanner yr3语义), 反向累积=未来函数
        hi3 = np.where(np.isnan(hi3), fa, hi3); lo3 = np.where(np.isnan(lo3), fb, lo3)
    else:
        hi3 = np.maximum.accumulate(c); lo3 = np.minimum.accumulate(c)
    pos3 = 100.0 * (c - lo3) / np.where(hi3 - lo3 > 0, hi3 - lo3, np.nan)
    off_lo3 = (c / lo3 - 1) * 100
    VW = np.lib.stride_tricks.sliding_window_view(v, 250)
    vrank = np.concatenate([np.full(249, np.nan), 100.0 * (VW <= VW[:, -1:]).mean(1)])
    pct = np.concatenate([[0.0], c[1:] / np.where(c[:-1] > 0, c[:-1], np.nan) - 1.0])
    ev = (v >= 2 * vm20) & (pct >= 0.03) & (vm20 > 0)
    E = np.where(ev)[0]
    if len(E) == 0:
        return np.zeros(n, bool), np.zeros(n)
    # 事件属性
    pre_dd_ok = np.zeros(len(E), bool)      # 事件级: 启动前60(≥40根时)跌≥25% 或 不足40根豁免
    pre20v = np.ones(len(E))                # 事件级分子(豁免事件=1)
    gate = np.zeros(len(E), bool)           # 该事件是否触发缩量/深跌闸(e>=40)
    for j, e in enumerate(E):
        w = c[max(0, e - 60):e]
        if len(w) >= 40:
            gate[j] = True
            pre_dd_ok[j] = (w.min() / w.max() - 1) * 100 <= -25
            pre20v[j] = v[max(0, e - 20):e].mean() if e > 0 else 1.0
        else:
            pre_dd_ok[j] = True
    idx = np.searchsorted(E, np.clip(lo_day, 0, None), side="left")
    valid = idx < len(E)
    eT = np.where(valid, E[np.clip(idx, 0, len(E) - 1)], 0)
    e_gate = gate[idx.clip(0, len(E) - 1)]
    e_dd = pre_dd_ok[idx.clip(0, len(E) - 1)]
    e_p20 = pre20v[idx.clip(0, len(E) - 1)]
    with np.errstate(invalid="ignore", divide="ignore"):
        shrink = np.where(vm250 > 0, e_p20 / vm250, 9)
        off_start = (c / np.where(eT >= 0, c[eT], np.nan) - 1) * 100
    days = np.arange(n) - eT
    bias = (c / ma20 - 1) * 100
    dchg = np.concatenate([[0.0], np.sign(np.diff(c))])
    csu = np.cumsum(np.concatenate([[0.0], np.where(dchg > 0, v, 0)]))
    csd = np.cumsum(np.concatenate([[0.0], np.where(dchg < 0, v, 0.0)]))
    csun = np.cumsum(np.concatenate([[0.0], (dchg > 0).astype(float)]))
    csdn = np.cumsum(np.concatenate([[0.0], (dchg < 0).astype(float)]))
    s20 = lambda A: np.concatenate([np.full(19, np.nan), A[20:] - A[:-20]])
    with np.errstate(invalid="ignore", divide="ignore"):
        vp = (s20(csu) / s20(csun)) / (s20(csd) / s20(csdn))
    cond = (
        (np.arange(n) >= 399)   # 扫描器要求截至T已有≥400根(非全历史≥400)
        & (c >= 2) & (c <= 30)
        & np.isfinite(ma20) & np.isfinite(ma60) & (ma20 > ma60)
        & (pos250 < 50)
        & valid & (days >= 0) & (days <= 40)   # scanner代码=start_i>40才剔, days=0不剔(与文本2-40不同, 以代码为准)
        & (off_start >= -3) & (off_start <= 17)
        & (~e_gate | e_dd)
        & (~e_gate | (shrink <= cap))
        & (pos3 < 55) & (off_lo3 < 80)
        & (vrank < 80) & (bias <= 3)
    )
    score = (pos250 + np.nan_to_num(pos3, nan=99)) / 2 - np.minimum(off_start, 10) * 0.5 + np.where(vp >= 1.3, 2.0, 0.0)
    return cond, score


# ================================================================ 逐字复刻路径(守门)
def verify_single(code, dts, cap=0.95):
    """直读qfq倒序780根, 复刻 scanner 判定 code@<=dts 是否命中。返回 (bool, score)。"""
    db = sqlite3.connect(QFQ_DB, uri=True)
    r = db.execute("SELECT close,high,low,volume FROM stock_daily_meta_qfq WHERE code=? AND date<=? "
                   "ORDER BY date DESC LIMIT 780", (str(code), int(dts))).fetchall()
    db.close()
    if len(r) < 400:
        return False, None
    closes = [float(x[0]) for x in r]; vols = [float(x[3] or 0) for x in r]
    if any(pd.isna(v) for v in closes[:300]):
        return False, None
    c0 = closes[0]
    if not c0 or c0 > 30 or c0 < 2:
        return False, None
    ma20 = sum(closes[:20]) / 20; ma60 = sum(closes[:60]) / 60
    if ma20 <= ma60:
        return False, None
    win = closes[:250]; hi, lo = max(win), min(win)
    pos250 = 100 * (c0 - lo) / (hi - lo) if hi > lo else 50
    if pos250 >= 50:
        return False, None
    lo_i = win.index(lo); start_i = None
    for i in range(lo_i, -1, -1):
        w20 = vols[i:i + 20]
        if len(w20) < 20:
            break
        if i + 1 < len(closes) and closes[i + 1] and vols[i] >= 2 * (sum(w20) / 20) and (closes[i] / closes[i + 1] - 1) >= 0.03:
            start_i = i; break
    if start_i is None:
        return False, None
    pre60 = closes[start_i + 1:start_i + 61]
    if len(pre60) >= 40:
        if (min(pre60) / max(pre60) - 1) * 100 > -25:
            return False, None
        v250 = sum(vols[:250]) / 250
        pre20 = vols[start_i + 1:start_i + 21]
        pre20v = sum(pre20) / len(pre20) if pre20 else v250
        if (pre20v / v250 if v250 else 1) > cap:
            return False, None
    off_start = (c0 / closes[start_i] - 1) * 100 if closes[start_i] else 999
    if not (-3 <= off_start <= 17):
        return False, None
    if start_i > 40:
        return False, None
    yr3 = closes[:750]
    hi3, lo3 = max(yr3), min(yr3)
    pos3 = 100 * (c0 - lo3) / (hi3 - lo3) if hi3 > lo3 else 50
    if pos3 >= 55 or (c0 / lo3 - 1) * 100 > 80:
        return False, None
    off = (c0 / ma20 - 1) * 100
    vs = sorted(vols[:250]); vrank = 100 * sum(1 for x in vs if x <= vols[0]) / len(vs)
    chg1 = (closes[0] / closes[1] - 1) * 100 if closes[1] else 0
    if pos250 >= 80 and vols[0] >= 1.5 * (sum(vols[:20]) / 20) and chg1 <= 1:
        return False, None
    if vrank >= 80 or off > 3:
        return False, None
    up_v = dn_v = 0.0; up_n = dn_n = 0
    for i in range(20):
        ch = closes[i] - closes[i + 1]
        if ch > 0: up_v += vols[i]; up_n += 1
        elif ch < 0: dn_v += vols[i]; dn_n += 1
    vp = (up_v / up_n) / (dn_v / dn_n) if up_n and dn_n and dn_v else 1.0
    return True, round((pos250 + pos3) / 2 - min(off_start, 10) * 0.5 + (2.0 if vp >= 1.3 else 0), 4)


# ================================================================ 数据与模拟
def load():
    raw = sqlite3.connect(RAW_DB, uri=True)
    info = raw.execute("SELECT code FROM stock_info WHERE instrument_type='stock'").fetchall()
    raw.close()
    codes = []
    for (code,) in info:
        code = str(code)
        if code[:2] in ('60', '00', '30'):
            nm = (get_name(code) or '').upper()
            if 'ST' in nm or '退' in nm:
                continue
            codes.append(code)
    cache = os.path.join(OUT, "_pivot_cache.npz")
    meta = os.path.join(OUT, "_pivot_cache_meta.json")
    if os.path.exists(cache) and os.path.exists(meta):
        m = json.load(open(meta, encoding='utf-8'))
        if m['codes'] == len(codes) and m['end'] == END_DATE:
            z = np.load(cache)
            log(f"[LOAD] cache hit {z['CM'].shape}")
            return z['CM'], z['VM'], z['OM'], z['dates'], m['cidx'], m['codes_list']
    db = sqlite3.connect(QFQ_DB, uri=True)
    t0 = time.time()
    parts = []
    for ch in range(0, len(codes), 900):
        cl = ",".join(f"'{c}'" for c in codes[ch:ch + 900])
        parts.append(pd.read_sql(f"SELECT code, date, open, close, volume FROM stock_daily_meta_qfq "
                                f"WHERE code IN ({cl}) AND date>=20150101 AND date<={END_DATE}", db))
    db.close()
    df = pd.concat(parts, ignore_index=True)
    log(f"[LOAD] rows={len(df):,} codes={df.code.nunique()} in {time.time()-t0:.0f}s")
    piv = lambda col: df.pivot_table(index='date', columns='code', values=col, aggfunc='first')
    CM = piv('close'); VM = piv('volume'); OM = piv('open')
    dates = np.sort(CM.index.unique().to_numpy(np.int64))
    codes_list = list(CM.columns)
    cidx = {c: j for j, c in enumerate(codes_list)}
    np.savez_compressed(cache, CM=CM.to_numpy(np.float64), VM=VM.to_numpy(np.float64),
                       OM=OM.to_numpy(np.float64), dates=dates)
    json.dump({'codes': len(codes_list), 'end': END_DATE, 'cidx': cidx, 'codes_list': codes_list},
              open(meta, 'w', encoding='utf-8'))
    return CM.to_numpy(np.float64), VM.to_numpy(np.float64), OM.to_numpy(np.float64), dates, cidx, codes_list


def build_hits(CM, VM, dates, cidx, cap=0.95, dfrom=20160101, log_every=800):
    hd, hs, hc = [], [], []
    t0 = time.time()
    d0 = int(np.searchsorted(dates, dfrom))
    for code, j in cidx.items():
        col = CM[:, j]; mask = np.isfinite(col)
        if mask.sum() < 400:
            continue
        c = col[mask]; v = VM[mask, j]; dd = dates[mask]
        cond, score = funnel_stock(c, v, cap)
        if cond is None or not cond.any():
            continue
        sel = cond & (dd >= dfrom)
        if sel.any():
            hd.append(dd[sel]); hs.append(score[sel]); hc.extend([code] * int(sel.sum()))
        if len(hd) % log_every == 1 and len(hd) // log_every:
            pass
    H = pd.DataFrame({'date': np.concatenate(hd), 'code': hc, 'score': np.concatenate(hs)}).sort_values(['date', 'score'])
    log(f"[FUNNEL] hits={len(H):,} in {time.time()-t0:.0f}s")
    return H


def run_arm(name, N, MD, H_by_date, CM, OM, dates, cidx, codes, all_mode=False, use_stop=True, gap_skip=True):
    cash = INIT
    pos = {}
    pend_s, pend_b = [], []
    trades = []; eq = []
    fees_total = 0.0
    col = {c: cidx[c] for c in cidx}
    for i, di in enumerate(dates):
        if i == 0 or di < 20160101:
            continue
        rowO = OM[i]
        # 开盘执行队列
        for code, why in pend_s:
            p = pos.get(code)
            if p is None:
                continue
            px = rowO[col[code]]
            if not np.isfinite(px) or px <= 0:
                continue
            px_e = px * (1 - SLIP)
            gross = p['shares'] * px_e
            fee = max(COMMISSION * gross, MIN_COMM) + STAMP * gross
            cash += gross - fee; fees_total += fee
            ct = gross - fee
            pnl = ct - p['buy_cost']
            trades.append(dict(code=code, name=get_name(code), buy_date=p['buy_date'], buy_px=p['cost'],
                               sell_date=int(di), sell_px=px, reason=why, pnl=pnl,
                               pnl_pct=pnl / p['buy_cost'] * 100, hold_days=(to_d(int(di)) - to_d(p['buy_date'])).days,
                               fees=p['buy_cost'] - p['shares'] * p['cost'] + fee))
            del pos[code]
        pend_s = []
        held_now = set(pos) | {c for c, _ in pend_s}
        for code, want in pend_b:
            if code in pos:
                continue
            px = rowO[col[code]]
            if not np.isfinite(px) or px <= 0:
                continue
            pc = CM[i - 1][col[code]]
            if gap_skip and np.isfinite(pc) and pc > 0 and px / pc - 1 >= 0.07:
                continue
            px_e = px * (1 + SLIP)
            shares = int(min(want, cash) / px_e / 100) * 100
            if shares <= 0:
                continue
            val = shares * px_e
            fee = max(COMMISSION * val, MIN_COMM)
            if val + fee > cash:
                shares -= 100; val = shares * px_e; fee = max(COMMISSION * val, MIN_COMM)
                if shares <= 0:
                    continue
            cash -= val + fee; fees_total += fee
            pos[code] = dict(shares=shares, cost=px, buy_cost=val + fee, buy_date=int(di), last=px, entry_i=i)
        pend_b = []
        # 收盘盯市
        mv = 0.0
        rowC = CM[i]
        for code, p in pos.items():
            cp = rowC[col[code]]
            if np.isfinite(cp):
                p['last'] = cp
            mv += p['shares'] * p['last']
        equity = cash + mv
        eq.append((int(di), equity))
        if i == len(dates) - 1:
            break
        # 收盘决策
        tgt = H_by_date.get(int(di), [])
        if N > 0:
            tgt = tgt[:N]
        sold_q = {c for c, _ in pend_s}
        if MD < 0:
            # 到期持有模式: 只止损/到bars期卖出, 空位由当日名单分数序补入, 无掉名单卖
            hold_bars = -MD
            for code, p in list(pos.items()):
                if code in sold_q:
                    continue
                cp = p['last']
                why = None
                if i - p['entry_i'] >= hold_bars:
                    why = 'max_holding'
                elif use_stop and cp / p['cost'] - 1 <= -STOP:
                    why = 'stop_loss'
                if why:
                    pend_s.append((code, why))
            free = [c for c in tgt if c not in pos and c not in {x for x, _ in pend_s}]
            if all_mode:
                # 主人令「名单不应限制」: 全信号可买, 固定名义, 现金不足自动跳过(分数序优先)
                for code in free:
                    pend_b.append((code, FIXED_NOTIONAL))
            else:
                k = max(N - len(pos) + len(pend_s), 0)
                for code in free[:k]:
                    pend_b.append((code, equity / N))
        else:
            for code, p in list(pos.items()):
                if code in sold_q:
                    continue
                cp = p['last']
                why = None
                if MD > 0 and (to_d(int(di)) - to_d(p['buy_date'])).days >= MD:
                    why = 'max_holding'
                elif use_stop and cp / p['cost'] - 1 <= -STOP:
                    why = 'stop_loss'
                elif code not in tgt:
                    why = 'rebalance'
                if why:
                    pend_s.append((code, why))
            free = [c for c in tgt if c not in pos and c not in {x for x, _ in pend_s}]
            if all_mode:
                for code in free:
                    pend_b.append((code, FIXED_NOTIONAL))
            else:
                k = max(N - len(pos) + len(pend_s), 0)
                for code in free[:k]:
                    pend_b.append((code, equity / N))
    # 样本末清算
    if pos:
        for code, p in pos.items():
            gross = p['shares'] * p['last'] * (1 - SLIP)
            fee = max(COMMISSION * gross, MIN_COMM) + STAMP * gross
            pnl = gross - fee - p['buy_cost']
            trades.append(dict(code=code, name=get_name(code), buy_date=p['buy_date'], buy_px=p['cost'],
                               sell_date=int(dates[-1]), sell_px=p['last'], reason='end_of_sample', pnl=pnl,
                               pnl_pct=pnl / p['buy_cost'] * 100,
                               hold_days=(to_d(int(dates[-1])) - to_d(p['buy_date'])).days, fees=fee))
            fees_total += fee
        cash_final = eq[-1][1] if eq else INIT
    else:
        cash_final = cash
    return eq, trades, fees_total


def summarize(eq, trades, fees):
    e = np.array([x for _, x in eq]); d = np.array([x for x, _ in eq])
    peak = np.maximum.accumulate(e); mdd = float((e / peak - 1).min())
    yrs = max((to_d(int(d[-1])) - to_d(int(d[0]))).days / 365.25, 0.5)
    tot = e[-1] / INIT - 1
    cagr = (e[-1] / INIT) ** (1 / yrs) - 1
    w = sum(1 for t in trades if t['pnl'] > 0)
    yr = {}; last = INIT
    dd = pd.Series(e, index=d); gl = dd.groupby(dd.index // 10000).last()
    for y, val in gl.items():
        yr[int(y)] = float(val / last - 1) * 100; last = val
    return dict(final=float(e[-1]), total_pct=tot * 100, cagr_pct=cagr * 100, mdd_pct=mdd * 100,
                n_trades=len(trades), win_pct=100 * w / max(len(trades), 1), fees=float(fees),
                avg_hold_days=float(np.mean([t['hold_days'] for t in trades])) if trades else 0, yearly=yr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='dfrom', default='20160101')
    ap.add_argument('--verify', type=int, default=20)
    ap.add_argument('--check23', action='store_true')
    ap.add_argument('--noarms', action='store_true')
    a = ap.parse_args()
    CM, VM, OM, dates, cidx, codes = load()
    gates = {'report': [], 'all_pass': True}

    if a.check23:
        # 实弹JSON=2026-09-23 13:34生成(彼时qfq最新完整bar=20260922) → 对账基准日=20260922
        log("[CHECK23] 20260922 cap=1.56 两路径对账(实弹生成时刻口径)")
        cut = int(np.searchsorted(dates, 20260922, side='right'))  # 含20260922的行数
        matrix_hits = {}
        for code, j in cidx.items():
            col = CM[:cut, j]; mask = np.isfinite(col)
            if mask.sum() < 400:
                continue
            cond, score = funnel_stock(col[mask], VM[:cut, j][mask], 1.56)
            if cond is not None and len(cond) and cond[-1]:
                matrix_hits[code] = float(score[-1])
        verify_hits = {}
        for code in codes:
            ok, sc = verify_single(code, 20260922, 1.56)
            if ok:
                verify_hits[code] = sc
        ms, vs = set(matrix_hits), set(verify_hits)
        agree = (ms == vs)
        real = json.load(open(os.path.join(ROOT, "reports", "universe_startup_scan_20260923.json"), encoding='utf-8'))
        rs = {str(h['code']) for h in real['hits']}
        inter = len(vs & rs) / max(len(vs | rs), 1)
        log(f"[CHECK23] matrix={len(ms)} verify={len(vs)} equal={agree} | 实弹JSON={len(rs)} Jaccard={inter:.2f}")
        gates['report'].append(dict(check='check23', matrix_eq_verify=agree, vs_real_jaccard=inter))
        if not (agree and inter >= 0.8):
            gates['all_pass'] = False

    # 全周期命中
    H = build_hits(CM, VM, dates, cidx, cap=0.95, dfrom=int(a.dfrom))
    H_by_date = {d: g['code'].tolist() for d, g in H.groupby('date')}
    if a.verify:
        samp = H.sample(min(a.verify, len(H)), random_state=42) if len(H) else H
        okn = bad = 0
        for _, r in samp.iterrows():
            ok, sc = verify_single(r['code'], int(r['date']), 0.95)
            if ok and abs((sc or 0) - r['score']) < 1e-3:
                okn += 1
            else:
                bad += 1; log(f"[VERIFY-FAIL] {r['code']}@{r['date']} matrix={r['score']:.4f} verify_ok={ok} score={sc}")
        log(f"[VERIFY] {okn}/{okn+bad} 一致")
        gates['report'].append(dict(check='verify', ok=okn, bad=bad))
        if bad:
            gates['all_pass'] = False

    if a.noarms:
        H.to_csv(os.path.join(OUT, "hits_full.csv.gz"), index=False, compression='gzip')
        json.dump(gates, open(os.path.join(OUT, "gates.json", ), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        log("[DONE-noarms] hits saved")
        return

    dfrom_idx = 0
    results = {}
    for name, spec in ARMS.items():
        N, MD = spec[0], spec[1]
        all_mode = bool(spec[2]) if len(spec) > 2 else False
        use_stop = bool(spec[3]) if len(spec) > 3 else True
        t0 = time.time()
        eq, trades, fees = run_arm(name, N, MD, H_by_date, CM, OM, dates, cidx, codes, all_mode=all_mode, use_stop=use_stop)
        s = summarize(eq, trades, fees)
        results[name] = s
        log(f"[ARM] {name}: total={s['total_pct']:+.1f}% cagr={s['cagr_pct']:+.1f}% mdd={s['mdd_pct']:.1f}% "
            f"n={s['n_trades']} win={s['win_pct']:.1f}% avg_hold={s['avg_hold_days']:.0f}d ({time.time()-t0:.0f}s)")
        pd.DataFrame(eq, columns=['date', 'equity']).to_csv(os.path.join(OUT, f"equity_{name}.csv"), index=False)
        pd.DataFrame(trades).to_csv(os.path.join(OUT, f"trades_{name}.csv"), index=False)
        json.dump(s, open(os.path.join(OUT, f"summary_{name}.json"), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        # 现金守恒(末笔含end_of_sample清算后恒等式应精确)
        drift = abs(eq[-1][1] - (INIT + sum(t['pnl'] for t in trades))) / INIT
        # 注: end_of_sample 未加回cash(记在pnl) 恒等式按净值口径
        okc = drift < 0.005
        gates['report'].append(dict(check='cash_conservation', arm=name, drift_pct=drift * 100, ok=bool(okc)))
        if not okc:
            gates['all_pass'] = False
    json.dump({'gates': gates, 'arms': results}, open(os.path.join(OUT, "results.json"), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    log(f"[GATES] all_pass={gates['all_pass']}")
    log("[SUMMARY] " + " | ".join(f"{k}: {v['total_pct']:+.1f}%/{v['win_pct']:.1f}%" for k, v in results.items()))


if __name__ == '__main__':
    main()
