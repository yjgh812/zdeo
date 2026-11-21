# -*- coding: utf-8 -*-
"""
portrait_attr_daily_20261120.py —— 日线特征 × 涨跌状态 描述性归因统计（只读分析，零交易权）

主人令 2026-11-20：拆解日线形态/换手率/成交量，归因日线上涨下跌，
看哪种形态特征历史上对应涨/跌。

性质：纯描述性统计（S1 状态条件分布 / S2 分位桶→fwd20 / S4 下跌段尾征兆），
     非信号、非策略、不产生任何交易动作；全部数据源 sqlite mode=ro 只读。
边界：不改任何既有文件；不碰 git；无交易原语调用。

输入（只读）：
  1) qfq 日线  D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db  stock_daily_meta_qfq
  2) 换手率    D:/workbuddyspace/databases/fundamental_data.db          daily_basic
  3) 状态段    D:/zcodesj/data/universe_portrait_regime.db              regime_segments
输出：D:/zcodesj/reports/FEATURE_ATTRIBUTION_DAILY_20261120.json + 控制台摘要

口径要点：
  * 有效行 = close>0 AND volume>0；按 code,date 有序（PK 索引扫描，免临时排序）。
  * 状态归属：每股 searchsorted 区间法（li=searchsorted(ss,d,'right')-1; d<=se[li]），
    UP=1 / DOWN=2 / RANGE=3（int8），0=无状态。
  * 换手率：daily_basic turnover_rate（%），按 6 位代码前缀+日期每股 searchsorted 对齐，
    同日无值→NaN；turn5/turn_z60 滑窗含 NaN→NaN（daily_basic 仅 2020-01-02 起）。
  * 滚动实现：ma=cumsum 法；roll_max/min=sliding_window_view；roll_std=ma(a,w)与ma(a²,w)
    组合 sqrt(max(m2-m1²,0))；前 w-1 行 NaN；hh20/ll20/hh60/ma_align 的 NaN 比较一律 False→0。
  * streak_up/dn、consec_vol_up/dn 用小 python 循环（全市场约 12M 行，一次循环同时算两组）。
  * S1 全量直算（无抽样），n<200 跳过；S2 无 NW t（v1 均值+胜率）；S4 全量 Cohen's d。
"""

import json
import os
import sqlite3
import sys
import time
import traceback
import warnings

import numpy as np

warnings.filterwarnings('ignore', category=RuntimeWarning)

QFA_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FUND_DB = 'D:/workbuddyspace/databases/fundamental_data.db'
REGIME_DB = 'D:/zcodesj/data/universe_portrait_regime.db'
OUT_JSON = 'D:/zcodesj/reports/FEATURE_ATTRIBUTION_DAILY_20261120.json'

FEATS = [
    'body_ratio', 'upper_shadow', 'lower_shadow', 'gap_pct',
    'streak_up', 'streak_dn', 'hh20', 'll20', 'hh60',
    'pos60', 'pos250', 'ma_align', 'ma_align_dn', 'bias20',
    'ret5', 'ret20', 'ret60', 'vola20', 'dd250',
    'vr5', 'vol_z20', 'consec_vol_up', 'consec_vol_dn',
    'turn', 'turn5', 'turn_z60', 'amt20',
]
NF = len(FEATS)  # 27
STATE_NAME = {1: 'UP', 2: 'DOWN', 3: 'RANGE'}
S1_BASE_DATE = 20160101
CHUNK = 50000

T0 = time.time()


def log(msg):
    print('[%7.1fs] %s' % (time.time() - T0, msg), flush=True)


def ro_connect(path):
    return sqlite3.connect('file:' + path + '?mode=ro', uri=True)


# ---------------- 滚动原语（滑动窗实现） ----------------

def ma_cumsum(a, w):
    """ma 用 cumsum 法；前 w-1 行 NaN。要求 a 无 NaN（turn 系列用 roll_mean_nan）。"""
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    cs = np.concatenate(([0.0], np.cumsum(np.asarray(a, np.float64))))
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def roll_maxmin(a, w, fn):
    """roll_max/min 用 sliding_window_view；前 w-1 行 NaN。"""
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    out[w - 1:] = fn(np.lib.stride_tricks.sliding_window_view(np.asarray(a, np.float64), w), axis=1)
    return out


def roll_std_comb(a, w):
    """roll_std 用 ma(a,w) 与 ma(a²,w) 组合：sqrt(max(m2-m1²,0))；前 w-1 行 NaN。"""
    a = np.asarray(a, np.float64)
    m1 = ma_cumsum(a, w)
    m2 = ma_cumsum(a * a, w)
    return np.sqrt(np.maximum(m2 - m1 * m1, 0.0))


def roll_mean_nan(a, w):
    """含 NaN 数组的滑动均值：窗口内含 NaN→NaN（cumsum 会被 NaN 污染，故用滑窗）。"""
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    out[w - 1:] = np.lib.stride_tricks.sliding_window_view(np.asarray(a, np.float64), w).mean(axis=1)
    return out


def roll_std_nan(a, w):
    """含 NaN 数组的滑动 std：窗口内含 NaN→NaN。"""
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    out[w - 1:] = np.lib.stride_tricks.sliding_window_view(np.asarray(a, np.float64), w).std(axis=1)
    return out


def streaks_and_consec(ret1, volsig, vol_ma_nan):
    """一次 python 循环同时算：
    streak_up/dn = ret1>0 / ret1<0 的连续计数（==0 归零）；
    consec_vol_up/dn = volsig 连续 True→+k / False→-k 序列的正负两列。
    vol_ma_nan=True 的行（MA5 尚未成形）无信号，consec 记 0。"""
    n = len(ret1)
    up = np.zeros(n)
    dn = np.zeros(n)
    cvu = np.zeros(n)
    cvd = np.zeros(n)
    cu = cd = pv = 0
    for i in range(n):
        r = ret1[i]
        if r > 0:
            cu += 1
            cd = 0
        elif r < 0:
            cd += 1
            cu = 0
        else:
            cu = 0
            cd = 0
        if vol_ma_nan[i]:
            pv = 0
        elif volsig[i]:
            pv = pv + 1 if pv > 0 else 1
        else:
            pv = pv - 1 if pv < 0 else -1
        up[i] = cu
        dn[i] = cd
        cvu[i] = pv if pv > 0 else 0.0
        cvd[i] = pv if pv < 0 else 0.0
    return up, dn, cvu, cvd


# ---------------- 标量/代码工具 ----------------

def code6(x):
    s = str(x).strip()
    digits = ''.join(ch for ch in s if ch.isdigit())
    return digits[:6] if digits else s[:6]


def dint_scalar(x):
    s2 = str(x).replace('-', '').replace('/', '').replace(' ', '')
    return int(s2[:8])


def obj_to_int64(col):
    """object 列（int 或 'YYYYMMDD' str）→ int64；str 走 astype 快路径。"""
    try:
        return col.astype(np.int64)
    except (TypeError, ValueError):
        return np.array([dint_scalar(x) for x in col], np.int64)


def obj_to_f64(col):
    """object 数值列 → float64（None→NaN），快路径 astype。"""
    try:
        return col.astype(np.float64)
    except (TypeError, ValueError):
        out = np.empty(len(col), np.float64)
        for k, x in enumerate(col):
            out[k] = float(x) if x is not None else np.nan
        return out


# ---------------- 数据源装载 ----------------

def load_regime_segments():
    """返回 (seg_by_code, records)。records=[(c6, state_code, si, ei, n_days)]，
    seg_by_code[c6]=(ss 按 start 排序, se, state_codes)。"""
    con = ro_connect(REGIME_DB)
    rows = con.execute(
        'SELECT code, state, start_date, end_date, n_days FROM regime_segments').fetchall()
    con.close()
    smap = {'UP': 1, 'DOWN': 2, 'RANGE': 3}
    records = []
    for code, state, s, e, nd in rows:
        k = smap.get(str(state).upper())
        if k is None:
            continue
        records.append((code6(code), k, dint_scalar(s), dint_scalar(e),
                        int(nd) if nd is not None else 0))
    byc = {}
    for c6, k, si, ei, nd in records:
        g = byc.setdefault(c6, ([], [], []))
        g[0].append(si)
        g[1].append(ei)
        g[2].append(k)
    out = {}
    for c6, (ss, se, sc) in byc.items():
        order = np.argsort(np.asarray(ss, np.int64), kind='stable')
        out[c6] = (np.asarray(ss, np.int64)[order], np.asarray(se, np.int64)[order],
                   np.asarray(sc, np.int8)[order])
    return out, records


def load_turnover():
    """daily_basic 全量 → {c6: (dates int64 升序, turnover_rate float64)}。
    键=ts_code 前 6 位数字（6 位代码跨市场唯一）。"""
    con = ro_connect(FUND_DB)
    cur = con.execute('SELECT ts_code, trade_date, turnover_rate FROM daily_basic')
    tcs, tds, tvs = [], [], []
    while True:
        rows = cur.fetchmany(300000)
        if not rows:
            break
        a = np.array(rows, dtype=object)
        tcs.append(a[:, 0])
        tds.append(a[:, 1])
        tvs.append(a[:, 2])
    con.close()
    if not tcs:
        return {}
    tc = np.concatenate(tcs)
    td = obj_to_int64(np.concatenate(tds))
    tv = obj_to_f64(np.concatenate(tvs))
    tc_b = tc.astype('S12')
    uniq = np.unique(tc_b)
    map6 = {}
    for u in uniq:
        s = u.decode()
        p = s[:6]
        if not p.isdigit():
            p = ''.join(ch for ch in s if ch.isdigit())[:6]
        map6[u] = p.encode()
    keys = np.searchsorted(uniq, tc_b)
    vals6 = np.array([map6[u] for u in uniq], dtype='S6')
    tc6 = vals6[keys]
    order = np.argsort(tc6, kind='stable')
    tc6s = tc6[order]
    tds_ = td[order]
    tvs_ = tv[order]
    uniq6, starts = np.unique(tc6s, return_index=True)
    bounds = np.append(starts, len(tc6s))
    out = {}
    for i in range(len(uniq6)):
        lo, hi = int(bounds[i]), int(bounds[i + 1])
        d = tds_[lo:hi]
        vv = tvs_[lo:hi]
        o2 = np.argsort(d, kind='stable')
        out[uniq6[i].decode()] = (d[o2], vv[o2])
    return out


# ---------------- 每股特征计算 ----------------

def compute_features(o, h, l, c, v, amt, turn):
    """27 特征（float64 dict）。前提：len>=1，close>0, volume>0。"""
    n = len(c)
    pc = np.empty(n)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    f = {}
    with np.errstate(divide='ignore', invalid='ignore'):
        ret1 = c / pc - 1.0
        hl = h - l + 1e-9
        f['body_ratio'] = np.abs(c - o) / hl
        f['upper_shadow'] = (h - np.maximum(o, c)) / hl
        f['lower_shadow'] = (np.minimum(o, c) - l) / hl
        f['gap_pct'] = o / pc - 1.0  # 首行 prev_close=c[0] → 0

        ma5 = ma_cumsum(c, 5)
        ma10 = ma_cumsum(c, 10)
        ma20 = ma_cumsum(c, 20)
        ma60 = ma_cumsum(c, 60)
        f['hh20'] = (c >= roll_maxmin(c, 20, np.max)).astype(np.float64)  # NaN 比较→False→0
        f['ll20'] = (c <= roll_maxmin(c, 20, np.min)).astype(np.float64)
        f['hh60'] = (c >= roll_maxmin(c, 60, np.max)).astype(np.float64)
        rmin60 = roll_maxmin(c, 60, np.min)
        rmax60 = roll_maxmin(c, 60, np.max)
        f['pos60'] = (c - rmin60) / (rmax60 - rmin60)  # 平台退化 0/0→NaN
        rmin250 = roll_maxmin(c, 250, np.min)
        rmax250 = roll_maxmin(c, 250, np.max)
        f['pos250'] = (c - rmin250) / (rmax250 - rmin250)
        f['ma_align'] = ((ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)).astype(np.float64)
        f['ma_align_dn'] = ((ma5 < ma10) & (ma10 < ma20) & (ma20 < ma60)).astype(np.float64)
        f['bias20'] = c / ma20 - 1.0

        def shift_fill(a, k):
            s = np.empty(n)
            s[:min(k, n)] = a[0]
            if k < n:
                s[k:] = a[:-k]
            return s

        f['ret5'] = c / shift_fill(c, 5) - 1.0
        f['ret20'] = c / shift_fill(c, 20) - 1.0
        f['ret60'] = c / shift_fill(c, 60) - 1.0
        f['vola20'] = roll_std_comb(ret1, 20)
        f['dd250'] = c / rmax250 - 1.0

        ma5v = ma_cumsum(v, 5)
        ma20v = ma_cumsum(v, 20)
        f['vr5'] = v / ma5v
        f['vol_z20'] = (v - ma20v) / roll_std_comb(v, 20)
        volsig = v > ma5v  # ma5 NaN 比较→False
        up, dn, cvu, cvd = streaks_and_consec(ret1, volsig, np.isnan(ma5v))
        f['streak_up'] = up
        f['streak_dn'] = dn
        f['consec_vol_up'] = cvu
        f['consec_vol_dn'] = cvd

        f['turn'] = turn
        f['turn5'] = roll_mean_nan(turn, 5)
        t60m = roll_mean_nan(turn, 60)
        t60s = roll_std_nan(turn, 60)
        f['turn_z60'] = (turn - t60m) / t60s
        f['amt20'] = ma_cumsum(amt, 20) / 1e6
    return f


# ---------------- 主构建（flat 矩阵） ----------------

def build():
    log('加载状态段 regime_segments ...')
    seg_by_code, seg_records = load_regime_segments()
    log('状态段 %d 条, 覆盖 %d 只' % (len(seg_records), len(seg_by_code)))
    log('加载换手率 daily_basic ...')
    turn_by_code = load_turnover()
    log('换手率覆盖 %d 只' % len(turn_by_code))

    con = ro_connect(QFA_DB)
    total = con.execute(
        'SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE close>0 AND volume>0').fetchone()[0]
    log('qfq 有效行总数 = %d' % total)

    F = np.empty((total, NF), np.float32)
    dates_a = np.empty(total, np.int32)
    st_a = np.zeros(total, np.uint8)
    fwd_a = np.full(total, np.nan, np.float32)
    cid_a = np.zeros(total, np.uint16)
    code_bounds = {}
    nstocks = [0]
    pos = [0]
    bad_ohlc = [0]

    def flush(code_raw, rows):
        m = len(rows)
        a = np.array(rows, dtype=object)
        c6 = code6(code_raw)
        o = obj_to_f64(a[:, 2])
        h = obj_to_f64(a[:, 3])
        l = obj_to_f64(a[:, 4])
        c = obj_to_f64(a[:, 5])
        v = obj_to_f64(a[:, 6])
        amt = obj_to_f64(a[:, 7])
        dint = obj_to_int64(a[:, 1])

        bad_ohlc[0] += int(np.count_nonzero((h < l) | (h <= 0) | (l <= 0)))

        # 状态归属（每股 searchsorted 区间法）
        st = np.zeros(m, np.uint8)
        segs = seg_by_code.get(c6)
        if segs is not None:
            ss, se, sc = segs
            li = np.searchsorted(ss, dint, 'right') - 1
            ok = li >= 0
            ok2 = ok.copy()
            ok2[ok] = dint[ok] <= se[li[ok]]
            st[ok2] = sc[li[ok2]]

        # 换手率对齐（同日无值→NaN）
        turn = np.full(m, np.nan)
        td = turn_by_code.get(c6)
        if td is not None:
            td_d, td_v = td
            idx = np.searchsorted(td_d, dint)
            ok = idx < len(td_d)
            ok2 = ok.copy()
            ok2[ok] = td_d[idx[ok]] == dint[ok]
            turn[ok2] = td_v[idx[ok2]]

        feats = compute_features(o, h, l, c, v, amt, turn)
        Fm = np.empty((m, NF), np.float32)
        for j, name in enumerate(FEATS):
            Fm[:, j] = feats[name]

        fwd = np.full(m, np.nan)
        if m > 20:
            fwd[:-20] = c[20:] / c[:-20] - 1.0

        p0 = pos[0]
        F[p0:p0 + m] = Fm
        dates_a[p0:p0 + m] = dint.astype(np.int32)
        st_a[p0:p0 + m] = st
        fwd_a[p0:p0 + m] = fwd.astype(np.float32)
        cid_a[p0:p0 + m] = len(code_bounds)
        code_bounds[c6] = (p0, p0 + m)
        pos[0] = p0 + m
        nstocks[0] += 1
        if nstocks[0] % 1000 == 0:
            log('构建进度 %d 只 / %d 行 (%.0f%%)' % (nstocks[0], pos[0], 100.0 * pos[0] / max(1, total)))

    log('流式读取 qfq 日线（ORDER BY code,date 走 PK 索引）...')
    cur = con.execute(
        'SELECT code,date,open,high,low,close,volume,amount '
        'FROM stock_daily_meta_qfq WHERE close>0 AND volume>0 ORDER BY code,date')
    buf = []
    cur_code = None
    while True:
        chunk = cur.fetchmany(CHUNK)
        if not chunk:
            break
        for row in chunk:
            cd = row[0]
            if cd != cur_code:
                if buf:
                    flush(cur_code, buf)
                buf = []
                cur_code = cd
            buf.append(row)
    if buf:
        flush(cur_code, buf)
    con.close()

    n = pos[0]
    log('构建完成: 行=%d 只=%d (预分配 %d, OHLC 异常行=%d)'
        % (n, nstocks[0], total, bad_ohlc[0]))
    if n < total:
        F = F[:n]
        dates_a = dates_a[:n]
        st_a = st_a[:n]
        fwd_a = fwd_a[:n]
        cid_a = cid_a[:n]
    return F, dates_a, st_a, fwd_a, cid_a, code_bounds, seg_records


# ---------------- 三张统计表 ----------------

def stat_s1(F, dates_a, st_a):
    """S1 状态条件分布：全量直算，dates>=20160101 & 特征非NaN，按 st=1/2/3 分组，
    n<200 跳过。median/p25/p75 用 np.percentile。"""
    base = dates_a >= S1_BASE_DATE
    st_masks = {k: base & (st_a == k) for k in (1, 2, 3)}
    S1 = {}
    for j, name in enumerate(FEATS):
        col = np.ascontiguousarray(F[:, j])
        fv = ~np.isnan(col)
        cell = {}
        for k in (1, 2, 3):
            m = st_masks[k] & fv
            nn = int(m.sum())
            if nn < 200:
                cell[STATE_NAME[k]] = {'n': nn, 'skipped': True}
                continue
            vals = col[m].astype(np.float64)
            p25, med, p75 = np.percentile(vals, [25, 50, 75])
            cell[STATE_NAME[k]] = {'n': nn, 'median': float(med),
                                   'p25': float(p25), 'p75': float(p75)}
        S1[name] = cell
        del col
    return S1


def stat_s2(F, fwd_a):
    """S2 特征全样本 20/40/60/80 分位桶 → fwd20 均值(%)与胜率（fwd 非NaN 才计入；无 NW t）。"""
    fwd = fwd_a.astype(np.float64)
    fvalid = ~np.isnan(fwd)
    S2 = {}
    for j, name in enumerate(FEATS):
        col = np.ascontiguousarray(F[:, j])
        v = ~np.isnan(col)
        edges = np.nanpercentile(col, [20, 40, 60, 80])
        both = v & fvalid
        b = np.full(len(col), -1, np.int32)
        b[both] = np.digitize(col[both], edges)  # 仅有效行参与分桶（NaN 不落桶）
        rows = []
        for k in range(5):
            mk = b == k
            nn = int(mk.sum())
            if nn == 0:
                rows.append({'bucket': k, 'n': 0, 'fwd20_mean_pct': None, 'share_pos': None})
                continue
            fk = fwd[mk].astype(np.float64)
            rows.append({'bucket': k, 'n': nn,
                         'fwd20_mean_pct': float(fk.mean() * 100.0),
                         'share_pos': float(np.mean(fk > 0))})
        S2[name] = {'edges': [float(e) for e in edges], 'buckets': rows}
        del col
    return S2


def stat_s4(F, dates_a, code_bounds, seg_records):
    """S4 下跌段尾征兆：state='DOWN' 且 n_days>=50，段内覆盖日（剔全 NaN 行）>=40 才计入；
    mid=段内前 30 覆盖日，tail=最后 10 覆盖日；全量流式累积
    Cohen's d = (mean_tail - mean_mid) / pooled_std，
    pooled_std = sqrt(((n1-1)s1²+(n2-1)s2²)/max(1,n1+n2-2))，n=特征非 NaN 行数。"""
    n1 = np.zeros(NF)
    s1 = np.zeros(NF)
    q1 = np.zeros(NF)  # tail（最后10覆盖日）
    n2 = np.zeros(NF)
    s2 = np.zeros(NF)
    q2 = np.zeros(NF)  # mid（前30覆盖日）
    segs_down = {}
    n_considered = 0
    for c6, k, si, ei, nd in seg_records:
        if k == 2 and nd >= 50:
            n_considered += 1
            segs_down.setdefault(c6, []).append((si, ei))
    used = 0
    for c6, segs in segs_down.items():
        b = code_bounds.get(c6)
        if b is None:
            continue
        lo, hi = b
        d = dates_a[lo:hi]
        sub_all = F[lo:hi]
        for si, ei in segs:
            i0 = int(np.searchsorted(d, si))
            i1 = int(np.searchsorted(d, ei, 'right'))
            if i1 <= i0:
                continue
            sub = sub_all[i0:i1]
            rowvalid = ~np.isnan(sub).all(axis=1)
            idx = np.nonzero(rowvalid)[0]
            if len(idx) < 40:
                continue
            used += 1
            for grp, (na, sa, qa) in ((idx[:30], (n2, s2, q2)),
                                      (idx[-10:], (n1, s1, q1))):
                X = sub[grp].astype(np.float64)
                Vm = ~np.isnan(X)
                na += Vm.sum(axis=0)
                sa += np.where(Vm, X, 0.0).sum(axis=0)
                qa += np.where(Vm, X * X, 0.0).sum(axis=0)
    mean1 = np.where(n1 > 0, s1 / np.maximum(n1, 1), np.nan)
    mean2 = np.where(n2 > 0, s2 / np.maximum(n2, 1), np.nan)
    var1 = np.maximum((q1 - n1 * mean1 ** 2) / np.maximum(n1 - 1, 1), 0.0)
    var2 = np.maximum((q2 - n2 * mean2 ** 2) / np.maximum(n2 - 1, 1), 0.0)
    pooled = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / np.maximum(1.0, n1 + n2 - 2.0))
    okd = (n1 > 0) & (n2 > 0) & (pooled > 0)
    d = np.where(okd, (mean1 - mean2) / np.where(pooled > 0, pooled, 1.0), np.nan)
    order = np.argsort(-np.abs(np.nan_to_num(d, nan=-np.inf)))
    feats_out = []
    top_cnt = 0
    for j in order:
        finite = bool(np.isfinite(d[j]))
        top = bool(finite and top_cnt < 8)
        if top:
            top_cnt += 1
        feats_out.append({
            'feature': FEATS[j],
            'cohens_d': float(d[j]) if finite else None,
            'abs_d': float(abs(d[j])) if finite else None,
            'mean_tail': float(mean1[j]) if n1[j] > 0 else None,
            'mean_mid': float(mean2[j]) if n2[j] > 0 else None,
            'n_tail': int(n1[j]),
            'n_mid': int(n2[j]),
            'top8': top,
        })
    return {'segments_considered': n_considered, 'segments_used': used,
            'features': feats_out}


# ---------------- 输出 ----------------

def print_summary(S1, S2, S4, meta):
    print('\n' + '=' * 110, flush=True)
    print('S1 状态条件分布 (dates>=%d, 全量直算, n<200 跳过)  格式: n  median [p25,p75]' % S1_BASE_DATE, flush=True)
    for name in FEATS:
        cells = []
        for sname in ('UP', 'DOWN', 'RANGE'):
            cc = S1[name][sname]
            if cc.get('skipped'):
                cells.append('n=%d skip' % cc['n'])
            else:
                cells.append('n=%-8d med=%+8.4f [%+7.4f,%+7.4f]'
                             % (cc['n'], cc['median'], cc['p25'], cc['p75']))
        print('  %-14s | %s | %s | %s' % (name, cells[0], cells[1], cells[2]), flush=True)

    print('\nS2 分位桶 → fwd20（b0=最低分位→b4=最高分位; mean%% / 胜率%% / n）', flush=True)
    for name in FEATS:
        parts = []
        for bb in S2[name]['buckets']:
            if bb['n'] == 0:
                parts.append('b%d:-' % bb['bucket'])
            else:
                parts.append('b%d:%+6.2f/%5.1f%%(n=%d)'
                             % (bb['bucket'], bb['fwd20_mean_pct'], bb['share_pos'] * 100, bb['n']))
        print('  %-14s %s' % (name, '  '.join(parts)), flush=True)

    print('\nS4 下跌段尾10日 vs 前30日 Cohen d（DOWN 段 n_days>=50, 段内>=40 行; '
          'considered=%d used=%d）' % (S4['segments_considered'], S4['segments_used']), flush=True)
    print('  TOP8 |d|:', flush=True)
    for ff in S4['features']:
        if ff['top8']:
            print('    %-14s d=%+6.3f  tail=%+.4f mid=%+.4f (n_tail=%d n_mid=%d)'
                  % (ff['feature'], ff['cohens_d'], ff['mean_tail'], ff['mean_mid'],
                     ff['n_tail'], ff['n_mid']), flush=True)
    print('  全部 27 特征按 |d| 降序:', flush=True)
    for ff in S4['features']:
        ds = ('%+6.3f' % ff['cohens_d']) if ff['cohens_d'] is not None else '   NaN'
        print('    %-14s |d|=%s d=%s' % (ff['feature'],
              ('%.3f' % ff['abs_d']) if ff['abs_d'] is not None else 'NaN', ds), flush=True)
    print('=' * 110, flush=True)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    log('==== 日线特征×涨跌状态 描述性归因（只读，零交易权） ====')
    F, dates_a, st_a, fwd_a, cid_a, code_bounds, seg_records = build()

    nan_rate = {}
    for j, name in enumerate(FEATS):
        nan_rate[name] = float(np.isnan(F[:, j]).mean())
    state_counts = {STATE_NAME[k]: int((st_a == k).sum()) for k in (1, 2, 3)}
    state_counts['NONE'] = int((st_a == 0).sum())
    base_rows = int((dates_a >= S1_BASE_DATE).sum())
    fwd_valid = int(np.count_nonzero(~np.isnan(fwd_a)))

    log('总行数=%d 股票数=%d 日期 %d~%d' % (len(F), len(code_bounds), int(dates_a.min()), int(dates_a.max())))
    log('状态行数: %s' % state_counts)
    log('S1 基线行数(dates>=%d)=%d  fwd20 有效行=%d' % (S1_BASE_DATE, base_rows, fwd_valid))
    log('特征 NaN 率: %s' % {k: round(vv, 4) for k, vv in nan_rate.items()})

    log('计算 S1 状态条件分布 ...')
    S1 = stat_s1(F, dates_a, st_a)
    log('计算 S2 分位桶 → fwd20 ...')
    S2 = stat_s2(F, fwd_a)
    log('计算 S4 下跌段尾征兆 Cohen d ...')
    S4 = stat_s4(F, dates_a, code_bounds, seg_records)

    meta = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'script': 'analysis/portrait_attr_daily_20261120.py',
        'purpose': '日线特征×涨跌状态 描述性归因统计（主人令 2026-11-20）——纯描述、非信号、零交易权',
        'rows_total': int(len(F)),
        'stocks': int(len(code_bounds)),
        'date_min': int(dates_a.min()),
        'date_max': int(dates_a.max()),
        's1_base_date': S1_BASE_DATE,
        's1_base_rows': base_rows,
        'fwd20_valid_rows': fwd_valid,
        'state_rows_full': state_counts,
        'feature_nan_rate': nan_rate,
        'sources': {
            'qfq_daily': QFA_DB + ' stock_daily_meta_qfq (close>0 AND volume>0)',
            'turnover': FUND_DB + ' daily_basic (20200102起, 6位代码前缀+日期 searchsorted 对齐)',
            'regime': REGIME_DB + ' regime_segments (UP=1/DOWN=2/RANGE=3, 区间 searchsorted 归属)',
        },
        'notes': [
            'S1: 全量直算（无抽样），median/p25/p75=np.percentile，n<200 跳过',
            'S2: 桶边界=全样本 20/40/60/80 分位(忽略NaN)；fwd20=qfq close[t+20]/close[t]-1，fwd 非NaN 才计入；无 NW t（v1 均值+胜率）',
            'S4: state=DOWN 且 n_days>=50；mid=段内前30覆盖日、tail=最后10覆盖日（剔全NaN行，段内>=40行才计入）；全量 Cohen d',
            'turn 系列: daily_basic 仅 2020-01-02 起；对齐失败→NaN；turn5/turn_z60 滑窗含 NaN→NaN',
            '滚动: ma=cumsum；roll_max/min=sliding_window_view；roll_std=ma+ma²组合；前 w-1 行 NaN；hh20/ll20/hh60/ma_align NaN 比较→0（False）',
            'consec_vol_up/dn: vol>MA5(vol) 连续 True→+k/False→-k；MA5 未成形行为 0；streak_up/dn: ret1>0/<0 连续计数',
            '描述性统计，非信号、非策略，不产生任何交易动作',
        ],
    }
    out = {'meta': meta, 'S1': S1, 'S2': S2, 'S4': S4}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    log('JSON 已写出: %s' % OUT_JSON)

    print_summary(S1, S2, S4, meta)
    log('完成。')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
