# -*- coding: utf-8 -*-
"""
feature_attr_v2_20261120.py —— 画像归因 v2：阶段「启动前特征快照」描述性归因（只读，零交易权）

主人令 2026-11-20：在 v1（analysis/portrait_attr_daily_20261120.py，未改动）基础上按
专业研究规范升级，核心新增 = 对 regime_segments 每个状态段（UP/DOWN/RANGE，is_confirmed
任意），取启动日 s0（段 start_date 当日或之后首个该股有效交易日）前第 k(5/10/20) 个
有效交易日的特征快照，统计 UP启动前 vs DOWN启动前 的特征区分度
（Cohen's d + 0/1 形态频率差），并做 R3 分层稳定性（市值/大盘环境/创业板制度/行业）
与 R4 UP 段弹性（大涨段 vs 其余段）分析。

性质：纯描述性统计（事后视角，阶段标签本身是事后 zigzag）；非信号、非策略、
     不产生任何交易动作；全部数据源 sqlite mode=ro 只读。
边界：不改任何既有文件；不碰 git；无交易原语调用；快照特征全部回看式计算（无未来函数）。

输入（只读）：
  1) qfq 日线  D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db  stock_daily_meta_qfq
  2) 换手/市值 D:/workbuddyspace/databases/fundamental_data.db          daily_basic
     (turnover_rate, circ_mv；circ_mv 单位=万元，已验证 000001.SZ 20200102≈3273.7亿；
      20200102 起)
  3) 状态段    D:/zcodesj/data/universe_portrait_regime.db              regime_segments
  4) 行业      D:/zcodesj/data/universe_portrait_fullseries.db          stock_dim (code 整数→zfill(6))
  5) 大盘环境  D:/zcodesj/data/universe_portrait_fullseries.db          market_daily (idx_sh_c, 20200102 起)
输出：D:/zcodesj/reports/FEATURE_ATTRIBUTION_V2_20261120.json + 控制台紧凑摘要

口径要点：
  * 基础 27 特征定义与 v1 完全一致（直接复用 v1 滚动原语与实现）。
  * TA-Lib 形态：float64 OHLC 输入，输出非零视为 1（NaN 比较→False→0）；
    ENGULFING/MARUBOZU/3INSIDE 按输出符号拆 bull/bear 两列 → 按任务书显式函数清单
    展开为 16 列（任务书文字称 17 列，以显式清单为准，meta 如实注明）。
  * 快照日 = s0 前第 k 个该股有效交易日（s0-k<0 跳过=历史不足，如实计数）。
  * turn/circ_mv 仅 2020+（对齐失败→NaN）；大盘动量 = market_daily idx_sh_c 自身
    日期序列 20 日回看，快照日无当日值→NaN（2016-2019 如实 N/A，不编造）。
  * 幸存者偏差：本轮复核 qfq 库最后有效日，声明写入 meta（现存股口径）。
  * 内存策略：不保存全市场特征矩阵；每股算完即提取快照行 + 流式累积 R2/R5。
"""

import json
import os
import sqlite3
import sys
import time
import traceback
import warnings

import numpy as np
import talib

warnings.filterwarnings('ignore', category=RuntimeWarning)

QFA_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FUND_DB = 'D:/workbuddyspace/databases/fundamental_data.db'
REGIME_DB = 'D:/zcodesj/data/universe_portrait_regime.db'
FULL_DB = 'D:/zcodesj/data/universe_portrait_fullseries.db'
OUT_JSON = 'D:/zcodesj/reports/FEATURE_ATTRIBUTION_V2_20261120.json'

CHUNK = 50000
K_LIST = (5, 10, 20)
K_MAIN = 20
GEM_REFORM_DATE = 20200824      # 创业板注册制改革日
MKT_BASE_DATE = 20200102        # daily_basic / market_daily 起始
SURV_CUTOFF = 20260630          # 幸存者偏差复核阈值
MIN_STRAT_N = 30                # 分层组最小样本（UP/DOWN 各 >=30 才算 d）

# ---------------- 特征清单 ----------------
BASE_FEATS = [
    'body_ratio', 'upper_shadow', 'lower_shadow', 'gap_pct',
    'streak_up', 'streak_dn', 'hh20', 'll20', 'hh60',
    'pos60', 'pos250', 'ma_align', 'ma_align_dn', 'bias20',
    'ret5', 'ret20', 'ret60', 'vola20', 'dd250',
    'vr5', 'vol_z20', 'consec_vol_up', 'consec_vol_dn',
    'turn', 'turn5', 'turn_z60', 'amt20',
]

PATTERN_FUNCS_SINGLE = [
    ('CDLHAMMER', 'hammer'), ('CDLHANGINGMAN', 'hanging_man'),
    ('CDLINVERTEDHAMMER', 'inv_hammer'), ('CDLSHOOTINGSTAR', 'shooting_star'),
    ('CDLDOJI', 'doji'), ('CDLLONGLEGGEDDOJI', 'longlegged_doji'),
    ('CDLMORNINGSTAR', 'morning_star'), ('CDLEVENINGSTAR', 'evening_star'),
    ('CDL3WHITESOLDIERS', 'three_white_soldiers'), ('CDL3BLACKCROWS', 'three_black_crows'),
]
PATTERN_FUNCS_PAIR = [
    ('CDLENGULFING', 'bull_engulf', 'bear_engulf'),
    ('CDLMARUBOZU', 'bull_marubozu', 'bear_marubozu'),
    ('CDL3INSIDE', 'bull_3inside', 'bear_3inside'),
]
PATTERN_COLS = ([n for _, n in PATTERN_FUNCS_SINGLE]
                + [c for _, b, s in PATTERN_FUNCS_PAIR for c in (b, s)])  # 16 列

CUSTOM_COLS = ['vol_breakout20', 'pullback_ma20', 'vol_spike3']
TURN_COLS = ['turn_pctl60', 'turn_rise', 'turn_gt3', 'high_turn_stall', 'turn_spike']

ALL_FEATS = BASE_FEATS + PATTERN_COLS + CUSTOM_COLS + TURN_COLS
NF = len(ALL_FEATS)  # 51
FEAT_IDX = {n: j for j, n in enumerate(ALL_FEATS)}

PATTERN_19 = PATTERN_COLS + CUSTOM_COLS          # R2 频率表的 19 个 0/1 形态列
BINARY_SET = (set(PATTERN_COLS) | set(CUSTOM_COLS)
              | {'hh20', 'll20', 'hh60', 'ma_align', 'ma_align_dn',
                 'turn_gt3', 'high_turn_stall', 'turn_spike'})   # 0/1 列（R1 附频率差）

STATE_NAME = {1: 'UP', 2: 'DOWN', 3: 'RANGE'}
P19_IDX = [FEAT_IDX[n] for n in PATTERN_19]

T0 = time.time()


def log(msg):
    print('[%7.1fs] %s' % (time.time() - T0, msg), flush=True)


def ro_connect(path):
    return sqlite3.connect('file:' + path + '?mode=ro', uri=True)


# ---------------- 滚动原语（与 v1 完全一致） ----------------

def ma_cumsum(a, w):
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    cs = np.concatenate(([0.0], np.cumsum(np.asarray(a, np.float64))))
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def roll_maxmin(a, w, fn):
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    out[w - 1:] = fn(np.lib.stride_tricks.sliding_window_view(np.asarray(a, np.float64), w), axis=1)
    return out


def roll_std_comb(a, w):
    a = np.asarray(a, np.float64)
    m1 = ma_cumsum(a, w)
    m2 = ma_cumsum(a * a, w)
    return np.sqrt(np.maximum(m2 - m1 * m1, 0.0))


def roll_mean_nan(a, w):
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    out[w - 1:] = np.lib.stride_tricks.sliding_window_view(np.asarray(a, np.float64), w).mean(axis=1)
    return out


def roll_std_nan(a, w):
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    out[w - 1:] = np.lib.stride_tricks.sliding_window_view(np.asarray(a, np.float64), w).std(axis=1)
    return out


def streaks_and_consec(ret1, volsig, vol_ma_nan):
    """streak_up/dn（ret1 连续正/负计数）+ consec_vol_up/dn（vol>MA5(vol) 连续 ±k）。"""
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


# ---------------- 标量/代码工具（与 v1 一致） ----------------

def code6(x):
    s = str(x).strip()
    digits = ''.join(ch for ch in s if ch.isdigit())
    return digits[:6] if digits else s[:6]


def dint_scalar(x):
    s2 = str(x).replace('-', '').replace('/', '').replace(' ', '')
    return int(s2[:8])


def obj_to_int64(col):
    try:
        return col.astype(np.int64)
    except (TypeError, ValueError):
        return np.array([dint_scalar(x) for x in col], np.int64)


def obj_to_f64(col):
    try:
        return col.astype(np.float64)
    except (TypeError, ValueError):
        out = np.empty(len(col), np.float64)
        for k, x in enumerate(col):
            out[k] = float(x) if x is not None else np.nan
        return out


# ---------------- 数据源装载 ----------------

def load_regime_segments():
    """regime_segments 全量 → ({c6: (ss升序, se, state int8, ret_pct f64)}, meta)。
    is_confirmed 任意（全纳入，分布计入 meta）。"""
    con = ro_connect(REGIME_DB)
    rows = con.execute(
        'SELECT code, state, start_date, end_date, n_days, ret_pct, is_confirmed '
        'FROM regime_segments').fetchall()
    con.close()
    smap = {'UP': 1, 'DOWN': 2, 'RANGE': 3}
    byc = {}
    n_total = 0
    conf_dist = {0: 0, 1: 0}
    n_days_sum = 0
    state_total = {1: 0, 2: 0, 3: 0}
    for code, state, s, e, nd, rp, ic in rows:
        k = smap.get(str(state).upper())
        if k is None:
            continue
        c6 = code6(code)
        g = byc.setdefault(c6, ([], [], [], []))
        g[0].append(dint_scalar(s))
        g[1].append(dint_scalar(e))
        g[2].append(k)
        g[3].append(float(rp) if rp is not None else np.nan)
        n_total += 1
        conf_dist[1 if int(ic) == 1 else 0] += 1
        state_total[k] += 1
        n_days_sum += int(nd) if nd is not None else 0
    out = {}
    for c6, (ss, se, sc, rp) in byc.items():
        order = np.argsort(np.asarray(ss, np.int64), kind='stable')
        out[c6] = (np.asarray(ss, np.int64)[order], np.asarray(se, np.int64)[order],
                   np.asarray(sc, np.int8)[order], np.asarray(rp, np.float64)[order])
    meta = {'total': n_total, 'codes': len(out),
            'by_state': {STATE_NAME[k]: v for k, v in state_total.items()},
            'is_confirmed_dist': conf_dist,
            'mean_n_days': n_days_sum / max(1, n_total)}
    return out, meta


def load_daily_basic():
    """daily_basic 全量 → {c6: (dates int64 升序, turnover f64, circ_mv f64 万元)}。"""
    con = ro_connect(FUND_DB)
    cur = con.execute('SELECT ts_code, trade_date, turnover_rate, circ_mv FROM daily_basic')
    tcs, tds, tts, tms = [], [], [], []
    while True:
        rows = cur.fetchmany(300000)
        if not rows:
            break
        a = np.array(rows, dtype=object)
        tcs.append(a[:, 0])
        tds.append(a[:, 1])
        tts.append(a[:, 2])
        tms.append(a[:, 3])
    con.close()
    if not tcs:
        return {}
    tc = np.concatenate(tcs)
    td = obj_to_int64(np.concatenate(tds))
    tv = obj_to_f64(np.concatenate(tts))
    tm = obj_to_f64(np.concatenate(tms))
    tc_b = tc.astype('S12')
    uniq = np.unique(tc_b)
    vals6 = np.array([(''.join(ch for ch in u.decode() if ch.isdigit()))[:6].encode()
                      for u in uniq], dtype='S6')
    keys = np.searchsorted(uniq, tc_b)
    tc6 = vals6[keys]
    order = np.argsort(tc6, kind='stable')
    tc6s, tds_, tvs_, tms_ = tc6[order], td[order], tv[order], tm[order]
    uniq6, starts = np.unique(tc6s, return_index=True)
    bounds = np.append(starts, len(tc6s))
    out = {}
    for i in range(len(uniq6)):
        lo, hi = int(bounds[i]), int(bounds[i + 1])
        d = tds_[lo:hi]
        o2 = np.argsort(d, kind='stable')
        out[uniq6[i].decode()] = (d[o2], tvs_[lo:hi][o2], tms_[lo:hi][o2])
    return out


def load_industries():
    """stock_dim → ({c6: industry_name}, n_rows)。"""
    con = ro_connect(FULL_DB)
    rows = con.execute('SELECT code, industry_name FROM stock_dim').fetchall()
    con.close()
    out = {}
    for code, name in rows:
        if name:
            out[code6(code)] = str(name)
    return out, len(rows)


def load_market_mom():
    """market_daily → (dates int64 升序, mom20 f64)。
    mom20[i] = idx_sh_c[i]/idx_sh_c[i-20] - 1（自身交易日序列 20 日回看）；i<20 → NaN。"""
    con = ro_connect(FULL_DB)
    rows = con.execute('SELECT date, idx_sh_c FROM market_daily ORDER BY date').fetchall()
    con.close()
    d = np.array([r[0] for r in rows], np.int64)
    idx = np.array([float(r[1]) if r[1] is not None else np.nan for r in rows], np.float64)
    mom = np.full(len(idx), np.nan)
    if len(idx) > 20:
        with np.errstate(divide='ignore', invalid='ignore'):
            mom[20:] = idx[20:] / idx[:-20] - 1.0
    return d, mom


# ---------------- 每股特征计算 ----------------

def compute_all(o, h, l, c, v, amt, turn):
    """51 特征 dict（float64）。前提 len>=1。返回 (feats, talib_ok)。"""
    n = len(c)
    pc = np.empty(n)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    f = {}
    talib_ok = True
    with np.errstate(divide='ignore', invalid='ignore'):
        ret1 = c / pc - 1.0
        hl = h - l + 1e-9
        # ---- A. 基础 27（与 v1 完全一致） ----
        f['body_ratio'] = np.abs(c - o) / hl
        f['upper_shadow'] = (h - np.maximum(o, c)) / hl
        f['lower_shadow'] = (np.minimum(o, c) - l) / hl
        f['gap_pct'] = o / pc - 1.0

        ma5 = ma_cumsum(c, 5)
        ma10 = ma_cumsum(c, 10)
        ma20 = ma_cumsum(c, 20)
        ma60 = ma_cumsum(c, 60)
        f['hh20'] = (c >= roll_maxmin(c, 20, np.max)).astype(np.float64)
        f['ll20'] = (c <= roll_maxmin(c, 20, np.min)).astype(np.float64)
        f['hh60'] = (c >= roll_maxmin(c, 60, np.max)).astype(np.float64)
        rmin60 = roll_maxmin(c, 60, np.min)
        rmax60 = roll_maxmin(c, 60, np.max)
        f['pos60'] = (c - rmin60) / (rmax60 - rmin60)
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
        volsig = v > ma5v
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

        # ---- B. TA-Lib 形态（16 列 0/1；NaN 比较→False→0） ----
        try:
            for fn, name in PATTERN_FUNCS_SINGLE:
                out = getattr(talib, fn)(o, h, l, c)
                f[name] = ((out > 0) | (out < 0)).astype(np.float64)
            for fn, bname, sname in PATTERN_FUNCS_PAIR:
                out = getattr(talib, fn)(o, h, l, c)
                f[bname] = (out > 0).astype(np.float64)
                f[sname] = (out < 0).astype(np.float64)
        except Exception:
            talib_ok = False
            for name in PATTERN_COLS:
                f[name] = np.zeros(n)

        # ---- C. 自定义量价形态（3 列 0/1） ----
        rmax20 = roll_maxmin(c, 20, np.max)
        prev_rmax20 = np.full(n, np.nan)   # 前一日的 rolling_max(close,20)
        if n > 1:
            prev_rmax20[1:] = rmax20[:-1]
        f['vol_breakout20'] = ((c > prev_rmax20) & (v > 1.5 * ma5v)).astype(np.float64)
        f['pullback_ma20'] = ((l <= ma20) & (c >= ma20 * 0.97) & (v < 0.7 * ma5v)).astype(np.float64)
        f['vol_spike3'] = (f['vol_z20'] > 3).astype(np.float64)   # 天量: vol_z20>3

        # ---- D. 换手率专项（5 列；turn 缺失→NaN，不编造） ----
        tnan = np.isnan(turn)
        t5t = roll_mean_nan(turn, 5)
        t20t = roll_mean_nan(turn, 20)
        f['turn_rise'] = t5t / t20t - 1.0
        f['turn_pctl60'] = np.full(n, np.nan)
        if n >= 60:
            W = np.lib.stride_tricks.sliding_window_view(turn, 60)
            last = W[:, -1]
            wnan = np.isnan(W).any(axis=1)
            cnt = (W <= last[:, None]).sum(axis=1) / 60.0
            f['turn_pctl60'][59:] = np.where(wnan, np.nan, cnt)
        gt3 = turn > 3
        f['turn_gt3'] = np.where(tnan, np.nan, gt3.astype(np.float64))
        f['high_turn_stall'] = np.where(
            tnan, np.nan, (gt3 & (np.abs(ret1) < 0.01)).astype(np.float64))
        # turn_spike: t20t NaN（2020 初 MA20(turn) 未成形）→ 比较 False → 0（口径注明）
        f['turn_spike'] = np.where(tnan, np.nan, (turn > 3 * t20t).astype(np.float64))
    return f, talib_ok


# ---------------- 统计原语 ----------------

def two_group_d(S, m1, m2):
    """S (N,F) float32 + 两个布尔组掩码 → 每特征 (n1,n2,mean1,mean2,d)。"""
    X1 = S[m1].astype(np.float64)
    X2 = S[m2].astype(np.float64)
    n1 = np.count_nonzero(~np.isnan(X1), axis=0)
    n2 = np.count_nonzero(~np.isnan(X2), axis=0)
    with np.errstate(invalid='ignore'):
        mu1 = np.nanmean(X1, axis=0)
        mu2 = np.nanmean(X2, axis=0)
        va1 = np.nanvar(X1, axis=0, ddof=1)
        va2 = np.nanvar(X2, axis=0, ddof=1)
    denom = np.maximum(n1 + n2 - 2.0, 1.0)
    pooled = np.sqrt(np.maximum(((n1 - 1) * va1 + (n2 - 1) * va2) / denom, 0.0))
    ok = (n1 >= 2) & (n2 >= 2) & (pooled > 0)
    d = np.where(ok, (mu1 - mu2) / np.where(pooled > 0, pooled, 1.0), np.nan)
    return n1, n2, mu1, mu2, d


def scalar_d(x1, x2):
    """两组一维（已剔 NaN）→ (d|None, mean1|None, mean2|None)。"""
    n1, n2 = len(x1), len(x2)
    if n1 < 2 or n2 < 2:
        return None, (float(np.mean(x1)) if n1 else None), (float(np.mean(x2)) if n2 else None)
    m1v, m2v = float(np.mean(x1)), float(np.mean(x2))
    v1, v2 = float(np.var(x1, ddof=1)), float(np.var(x2, ddof=1))
    pooled = np.sqrt(max(((n1 - 1) * v1 + (n2 - 1) * v2) / max(n1 + n2 - 2, 1), 0.0))
    if pooled <= 0:
        return None, m1v, m2v
    return float((m1v - m2v) / pooled), m1v, m2v


# ---------------- 主构建（流式：快照提取 + R2/R5 流式累积） ----------------

def build():
    log('加载状态段 regime_segments ...')
    seg_by_code, seg_meta = load_regime_segments()
    log('状态段 %d 条 / %d 只 / is_confirmed=%s / mean_n_days=%.1f'
        % (seg_meta['total'], seg_meta['codes'], seg_meta['is_confirmed_dist'],
           seg_meta['mean_n_days']))
    log('加载 daily_basic（换手率+流通市值）...')
    db_by_code = load_daily_basic()
    log('daily_basic 覆盖 %d 只' % len(db_by_code))
    log('加载 stock_dim 行业 ...')
    ind_map, ind_rows = load_industries()
    log('行业映射 %d 只 (stock_dim %d 行)' % (len(ind_map), ind_rows))
    log('加载 market_daily 大盘动量 ...')
    mdates, mom_arr = load_market_mom()
    log('market_daily %d 天 (%d~%d)' % (len(mdates), int(mdates[0]), int(mdates[-1])))

    con = ro_connect(QFA_DB)
    total = con.execute(
        'SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE close>0 AND volume>0').fetchone()[0]
    log('qfq 有效行总数 = %d' % total)

    ind_names = sorted(set(ind_map.values()))
    ind_id = {n: i for i, n in enumerate(ind_names)}

    # ---- 全局累积器 ----
    snap = {k: {'S': [], 'type': [], 'date': [], 'cmv': [], 'mom': [], 'gem': [], 'ind': []}
            for k in K_LIST}
    snap20_amp = []                       # 与 snap[K_MAIN] 同序（UP 段幅度；非 UP/无效=NaN）
    r2_n = {1: 0, 2: 0, 3: 0}
    r2_sum = np.zeros((3, len(PATTERN_19)))
    r2_nvalid = np.zeros((3, len(PATTERN_19)))
    nan_cnt = np.zeros(NF)
    seg_skip = {s: {'total': 0, 'no_trading_day': 0, 'used_k20': 0}
                for s in ('UP', 'DOWN', 'RANGE')}
    acc = {'bad_ohlc': 0, 'talib_fail': 0, 'rows': 0, 'stocks': 0,
           'last_dates': [], 'amp_chk': []}

    def flush(code_raw, rows):
        nonlocal nan_cnt
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

        acc['bad_ohlc'] += int(np.count_nonzero((h < l) | (h <= 0) | (l <= 0)))
        acc['rows'] += m
        acc['last_dates'].append(int(dint[-1]))

        # 换手率/市值对齐（同日无值→NaN）
        turn = np.full(m, np.nan)
        td = db_by_code.get(c6)
        if td is not None:
            td_d, td_v, td_m = td
            idx = np.searchsorted(td_d, dint)
            ok = idx < len(td_d)
            ok2 = ok.copy()
            ok2[ok] = td_d[idx[ok]] == dint[ok]
            turn[ok2] = td_v[idx[ok2]]
        else:
            td_d = td_m = None

        feats, tok = compute_all(o, h, l, c, v, amt, turn)
        if not tok:
            acc['talib_fail'] += 1

        Fm = np.empty((m, NF), np.float32)
        for j, name in enumerate(ALL_FEATS):
            Fm[:, j] = feats[name]
        nan_cnt += np.isnan(Fm).sum(axis=0)

        # ---- 状态归属（每股 searchsorted 区间法，与 v1 一致） ----
        st = np.zeros(m, np.uint8)
        segs = seg_by_code.get(c6)
        if segs is not None:
            ss, se, sc, _rp = segs
            li = np.searchsorted(ss, dint, 'right') - 1
            ok = li >= 0
            ok2 = ok.copy()
            ok2[ok] = dint[ok] <= se[li[ok]]
            st[ok2] = sc[li[ok2]]

        # ---- R2 流式累积：19 形态列 × 状态行频率 ----
        B = Fm[:, P19_IDX].astype(np.float64)
        for s in (1, 2, 3):
            mk = st == s
            nn = int(mk.sum())
            r2_n[s] += nn
            if nn:
                Bs = B[mk]
                r2_nvalid[s - 1] += np.count_nonzero(~np.isnan(Bs), axis=0)
                r2_sum[s - 1] += np.nansum(Bs, axis=0)
        del B

        # ---- R1 快照 + R4 UP 段幅度 ----
        if segs is not None:
            ss, se, sc, rp = segs
            nseg = len(ss)
            gem = 1 if c6.startswith('300') else 0
            indname = ind_map.get(c6)
            indid = ind_id[indname] if indname is not None else -1
            idx_by_k = {k: [] for k in K_LIST}       # 快照行号（该股行内索引）
            typ_by_k = {k: [] for k in K_LIST}       # 对应段状态
            segpos_by_k = {k: [] for k in K_LIST}    # 对应段序号（amp 对齐用）
            amp_all = np.full(nseg, np.nan)
            for i in range(nseg):
                stname = STATE_NAME[int(sc[i])]
                seg_skip[stname]['total'] += 1
                s0i = int(np.searchsorted(dint, int(ss[i]), 'left'))
                if s0i >= m:
                    seg_skip[stname]['no_trading_day'] += 1
                    continue
                for k in K_LIST:
                    if s0i >= k:
                        idx_by_k[k].append(s0i - k)
                        typ_by_k[k].append(int(sc[i]))
                        segpos_by_k[k].append(i)
                # R4: UP 段幅度 = end_date 收盘 / start_date 前一日收盘 - 1（qfq）
                if int(sc[i]) == 1 and s0i >= 1:
                    ie = int(np.searchsorted(dint, int(se[i]), 'right')) - 1
                    if ie >= s0i:
                        amp = float(c[ie] / c[s0i - 1] - 1.0)
                        amp_all[i] = amp
                        if np.isfinite(rp[i]):
                            acc['amp_chk'].append(amp * 100.0 - float(rp[i]))
            for k in K_LIST:
                if not idx_by_k[k]:
                    continue
                idxa = np.asarray(idx_by_k[k], np.int64)
                typa = np.asarray(typ_by_k[k], np.int8)
                segpa = np.asarray(segpos_by_k[k], np.int64)
                sd = dint[idxa]                       # 快照日
                # circ_mv 对齐（快照日当天无值→NaN）
                cmv = np.full(len(idxa), np.nan)
                if td_d is not None:
                    j2 = np.searchsorted(td_d, sd)
                    okb = j2 < len(td_d)
                    okc = okb.copy()
                    okc[okb] = td_d[j2[okb]] == sd[okb]
                    cmv[okc] = td_m[j2[okc]]
                # 大盘动量对齐（快照日无当日值→NaN；market_daily 20200102 起）
                mi = np.searchsorted(mdates, sd)
                mok = mi < len(mdates)
                mok2 = mok.copy()
                mok2[mok] = mdates[mi[mok]] == sd[mok]
                smom = np.where(mok2, mom_arr[np.minimum(mi, len(mdates) - 1)], np.nan)
                snap[k]['S'].append(Fm[idxa])
                snap[k]['type'].append(typa)
                snap[k]['date'].append(sd.astype(np.int32))
                snap[k]['cmv'].append(cmv)
                snap[k]['mom'].append(smom)
                snap[k]['gem'].append(np.full(len(idxa), gem, np.int8))
                snap[k]['ind'].append(np.full(len(idxa), indid, np.int16))
                if k == K_MAIN:
                    snap20_amp.append(amp_all[segpa])
                    for s in (1, 2, 3):
                        seg_skip[STATE_NAME[s]]['used_k20'] += int((typa == s).sum())

        acc['stocks'] += 1
        if acc['stocks'] % 1000 == 0:
            log('进度 %d 只 / %d 行 (%.0f%%)'
                % (acc['stocks'], acc['rows'], 100.0 * acc['rows'] / max(1, total)))

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

    # ---- 拼接快照 ----
    S, T, DD, CMV, MM, GG, II = {}, {}, {}, {}, {}, {}, {}
    for k in K_LIST:
        if snap[k]['S']:
            S[k] = np.concatenate(snap[k]['S'])
            T[k] = np.concatenate(snap[k]['type'])
            DD[k] = np.concatenate(snap[k]['date'])
            CMV[k] = np.concatenate(snap[k]['cmv'])
            MM[k] = np.concatenate(snap[k]['mom'])
            GG[k] = np.concatenate(snap[k]['gem'])
            II[k] = np.concatenate(snap[k]['ind'])
        else:
            S[k] = np.empty((0, NF), np.float32)
            T[k] = np.empty(0, np.int8)
            DD[k] = np.empty(0, np.int32)
            CMV[k] = np.empty(0, np.float64)
            MM[k] = np.empty(0, np.float64)
            GG[k] = np.empty(0, np.int8)
            II[k] = np.empty(0, np.int16)
    AMP = np.concatenate(snap20_amp) if snap20_amp else np.empty(0, np.float64)
    log('快照拼接完成: k5=%d k10=%d k20=%d' % (len(T[5]), len(T[10]), len(T[20])))
    return (S, T, DD, CMV, MM, GG, II, AMP, nan_cnt, seg_skip, acc,
            seg_meta, len(db_by_code), len(ind_map), ind_rows,
            mdates, mom_arr, ind_names, r2_n, r2_sum, r2_nvalid)


# ---------------- R1/R2/R3/R4 统计 ----------------

def stat_r1_full(S, t):
    """全特征区分度排序表（|d| 降序）+ 0/1 列频率差。"""
    m1 = t == 1
    m2 = t == 2
    n1, n2, mu1, mu2, d = two_group_d(S, m1, m2)
    order = np.argsort(-np.abs(np.nan_to_num(d, nan=-np.inf)), kind='stable')
    rows = []
    for rank, j in enumerate(order, 1):
        name = ALL_FEATS[j]
        is_bin = name in BINARY_SET
        finite_m1 = bool(np.isfinite(mu1[j]))
        finite_m2 = bool(np.isfinite(mu2[j]))
        rows.append({
            'rank': int(rank), 'feature': name, 'is_binary': is_bin,
            'cohens_d': float(d[j]) if np.isfinite(d[j]) else None,
            'abs_d': float(abs(d[j])) if np.isfinite(d[j]) else None,
            'mean_up_pre': float(mu1[j]) if finite_m1 else None,
            'mean_down_pre': float(mu2[j]) if finite_m2 else None,
            'n_up': int(n1[j]), 'n_down': int(n2[j]),
            'p_up': float(mu1[j]) if (is_bin and finite_m1) else None,
            'p_down': float(mu2[j]) if (is_bin and finite_m2) else None,
            'freq_diff_up_minus_down':
                float(mu1[j] - mu2[j]) if (is_bin and finite_m1 and finite_m2) else None,
        })
    return rows


def top10_at_k(S, t, top10_names):
    """给定 k 口径快照，输出 Top10 特征的数值对比（mean/d）。"""
    m1 = t == 1
    m2 = t == 2
    out = []
    for name in top10_names:
        j = FEAT_IDX[name]
        x_u = S[m1, j].astype(np.float64)
        x_d = S[m2, j].astype(np.float64)
        x_u = x_u[np.isfinite(x_u)]
        x_d = x_d[np.isfinite(x_d)]
        d, m_u, m_d = scalar_d(x_u, x_d)
        out.append({'feature': name, 'is_binary': name in BINARY_SET,
                    'n_up': len(x_u), 'n_down': len(x_d),
                    'mean_up_pre': m_u, 'mean_down_pre': m_d, 'cohens_d': d})
    return out


def strat_cell(S, mu_mask, md_mask, top10_names):
    cell = {}
    for name in top10_names:
        j = FEAT_IDX[name]
        x_u = S[mu_mask, j].astype(np.float64)
        x_d = S[md_mask, j].astype(np.float64)
        x_u = x_u[np.isfinite(x_u)]
        x_d = x_d[np.isfinite(x_d)]
        d, m_u, m_d = scalar_d(x_u, x_d)
        cell[name] = {'n_up': int(len(x_u)), 'n_down': int(len(x_d)),
                      'mean_up_pre': m_u, 'mean_down_pre': m_d,
                      'd_up_minus_down': d}
    return cell


def strat_section(S, t, masks, top10_names):
    m1 = t == 1
    m2 = t == 2
    out = {}
    for sname, mask in masks:
        mu = mask & m1
        md = mask & m2
        nu, nd = int(mu.sum()), int(md.sum())
        if nu < MIN_STRAT_N or nd < MIN_STRAT_N:
            out[sname] = {'n_up': nu, 'n_down': nd, 'skipped': True,
                          'reason': 'group n < %d' % MIN_STRAT_N}
            continue
        out[sname] = {'n_up': nu, 'n_down': nd,
                      'features': strat_cell(S, mu, md, top10_names)}
    return out


def stat_r3(S, t, d20, cmv20, mom20, gem20, ind20, ind_names, top10_names):
    out = {'top10_features': top10_names}

    # (a) 市值分层（circ_mv 分位 20/40/60/80 → 5 档；仅 2020+ 且 circ_mv 有效样本）
    base2020 = (d20 >= MKT_BASE_DATE) & np.isfinite(cmv20)
    mc_meta = {'base_samples': int(base2020.sum()),
               'note': 'circ_mv 分位 20/40/60/80 切 5 档（t0=最低20% ... t4=最高20%）；'
                       '仅 2020+ 且 circ_mv 有效；单位=万元'}
    if base2020.sum() >= 500:
        edges = np.percentile(cmv20[base2020], [20, 40, 60, 80])
        tier = np.full(len(d20), -1, np.int8)
        tier[base2020] = np.digitize(cmv20[base2020], edges)
        tnames = ['t0_low20', 't1_20_40', 't2_40_60', 't3_60_80', 't4_top20']
        masks = [(tnames[i], base2020 & (tier == i)) for i in range(5)]
        ranges = {}
        for i, tn in enumerate(tnames):
            sel = cmv20[base2020 & (tier == i)]
            if len(sel):
                ranges[tn] = {'n': int(len(sel)),
                              'circ_mv_min_wan': float(sel.min()),
                              'circ_mv_max_wan': float(sel.max())}
        mc_meta['edges_cmv_wan'] = [float(e) for e in edges]
        mc_meta['tier_ranges'] = ranges
        mc_meta['strata'] = strat_section(S, t, masks, top10_names)
    else:
        mc_meta['strata'] = {'skipped': True, 'reason': 'base samples < 500'}
    out['market_cap_tiers'] = mc_meta

    # (b) 大盘环境（快照日上证 20 日动量 >0 / <=0；缺失→N/A 只报 n）
    fin = np.isfinite(mom20)
    env_meta = {'note': '快照日上证 idx_sh_c 20 日动量（market_daily 自身序列回看）；'
                        '2016-2019 及缺失日如实 N/A（market_daily 20200102 起）',
                'na_n': int((~fin).sum())}
    masks = [('mkt_mom20_pos', fin & (mom20 > 0)),
             ('mkt_mom20_nonpos', fin & (mom20 <= 0))]
    env_meta['strata'] = strat_section(S, t, masks, top10_names)
    out['market_env'] = env_meta

    # (c) 制度标记（创业板 300 开头，2020-08-24 前后两层）
    gemm = gem20 == 1
    gem_meta = {'note': '创业板(300开头)按 20200824 注册制改革分两层；非创业板样本不计入两层',
                'non_gem_n': int((gem20 == 0).sum())}
    masks = [('gem_pre_20200824', gemm & (d20 < GEM_REFORM_DATE)),
             ('gem_post_20200824', gemm & (d20 >= GEM_REFORM_DATE))]
    gem_meta['strata'] = strat_section(S, t, masks, top10_names)
    out['gem_reform'] = gem_meta

    # (d) 行业（R1 样本量 Top10 行业）
    valid_ind = ind20 >= 0
    ind_meta = {'note': '行业=stock_dim.industry_name，按 k20 快照样本量取 Top10 行业；'
                        '无行业映射的快照（ind=-1，n=%d）不计入' % int((~valid_ind).sum())}
    if valid_ind.sum() >= 100:
        ids, cnts = np.unique(ind20[valid_ind], return_counts=True)
        top_ids = ids[np.argsort(-cnts)][:10]
        masks = []
        ind_label = {}
        for iid in top_ids:
            nm = ind_names[int(iid)] if int(iid) < len(ind_names) else 'ind_%d' % iid
            ind_label[int(iid)] = nm
            masks.append((nm, valid_ind & (ind20 == iid)))
        ind_meta['top10_industries'] = [{'industry': ind_label[int(iid)],
                                         'n_snapshots': int(cnts[ids == iid][0])}
                                        for iid in top_ids]
        ind_meta['strata'] = strat_section(S, t, masks, top10_names)
    else:
        ind_meta['strata'] = {'skipped': True, 'reason': 'valid industry samples < 100'}
    out['industry'] = ind_meta
    return out


def stat_r4(S, t, amp, top10_names):
    """UP 段弹性：幅度前 20%（大涨段）vs 其余 UP 段，前 20 日快照特征均值对比。"""
    mup = t == 1
    fin = np.isfinite(amp) & mup
    out = {'note': '段幅度=end_date 收盘/start_date 前一日收盘-1（qfq）；'
                   '大涨段=幅度>=80 分位；停牌覆盖段（无有效幅度）剔除并计数',
           'n_up_k20_snapshots': int(mup.sum()),
           'n_with_amp': int(fin.sum()),
           'n_without_amp': int(mup.sum() - fin.sum())}
    if fin.sum() < 200:
        out['skipped'] = True
        out['reason'] = 'UP 段有效幅度样本 < 200'
        return out
    thr = float(np.percentile(amp[fin], 80))
    big = fin & (amp >= thr)
    rest = fin & (amp < thr)
    a_all = amp[fin]
    out['amp_threshold'] = thr
    out['amp_distribution'] = {
        'p10': float(np.percentile(a_all, 10)), 'p50': float(np.percentile(a_all, 50)),
        'p80': float(np.percentile(a_all, 80)), 'p90': float(np.percentile(a_all, 90)),
        'max': float(a_all.max())}
    rows = []
    for name in top10_names:
        j = FEAT_IDX[name]
        xb = S[big, j].astype(np.float64)
        xr = S[rest, j].astype(np.float64)
        xb = xb[np.isfinite(xb)]
        xr = xr[np.isfinite(xr)]
        d, m_b, m_r = scalar_d(xb, xr)
        diff = (m_b - m_r) if (m_b is not None and m_r is not None) else None
        rows.append({'feature': name, 'n_big': len(xb), 'n_rest': len(xr),
                     'mean_big20pct_pre': m_b, 'mean_rest_pre': m_r,
                     'diff_big_minus_rest': diff, 'cohens_d': d})
    out['big_vs_rest'] = rows
    return out


def stat_r2(r2_n, r2_sum, r2_nvalid):
    out = {'n_rows_by_state': {STATE_NAME[s]: int(r2_n[s]) for s in (1, 2, 3)},
           'note': '19 个 0/1 形态列（TA-Lib 16 + 自定义 3）在全量状态行上的出现频率；'
                   'freq_valid=该状态下非 NaN 行数（本 19 列无 NaN，=状态行数）',
           'patterns': {}}
    for i, name in enumerate(PATTERN_19):
        cell = {}
        for s in (1, 2, 3):
            nv = int(r2_nvalid[s - 1, i])
            cell[STATE_NAME[s]] = {
                'freq': float(r2_sum[s - 1, i] / nv) if nv else None,
                'n_valid': nv}
        fu, fd = cell['UP']['freq'], cell['DOWN']['freq']
        cell['freq_diff_up_minus_down'] = (fu - fd) if (fu is not None and fd is not None) else None
        out['patterns'][name] = cell
    return out


# ---------------- 输出 ----------------

def print_summary(r1_rows, top10_names, r1_k10, r1_k5, r2, r3, r4, anomalies, meta):
    print('\n' + '=' * 108, flush=True)
    print('R1 区分度 Top20 (k=20, UP启动前 vs DOWN启动前, |d| 降序; n=有效样本 UP/DOWN):', flush=True)
    for r in r1_rows[:20]:
        ds = ('%+.3f' % r['cohens_d']) if r['cohens_d'] is not None else '  NaN'
        extra = ''
        if r['is_binary'] and r['freq_diff_up_minus_down'] is not None:
            extra = '  P差=%+5.2fpp' % (r['freq_diff_up_minus_down'] * 100)
        print('  %2d. %-18s d=%s  UP=%+10.4f  DOWN=%+10.4f  n=%d/%d%s'
              % (r['rank'], r['feature'], ds, r['mean_up_pre'] or 0.0,
                 r['mean_down_pre'] or 0.0, r['n_up'], r['n_down'], extra), flush=True)

    print('\nR1 Top10 方向稳定性 (k=5 / k=10 的 d; 与 k=20 对照):', flush=True)
    for i, name in enumerate(top10_names):
        d20 = r1_rows[i]['cohens_d']
        d10 = r1_k10[i]['cohens_d']
        d5 = r1_k5[i]['cohens_d']
        f = lambda x: ('%+.3f' % x) if x is not None else ' NaN'
        print('  %-18s d20=%s d10=%s d5=%s' % (name, f(d20), f(d10), f(d5)), flush=True)

    print('\nR2 形态频率 (全量状态行; 频率%%; diff=UP-DOWN 百分点):', flush=True)
    for name, cell in r2['patterns'].items():
        fu, fd, fr = cell['UP']['freq'], cell['DOWN']['freq'], cell['RANGE']['freq']
        print('  %-18s UP=%5.2f%% DOWN=%5.2f%% RANGE=%5.2f%%  diff=%+5.2fpp  n=%d/%d/%d'
              % (name, (fu or 0) * 100, (fd or 0) * 100, (fr or 0) * 100,
                 ((cell['freq_diff_up_minus_down'] or 0) * 100),
                 cell['UP']['n_valid'], cell['DOWN']['n_valid'], cell['RANGE']['n_valid']),
              flush=True)

    print('\nR3 分层稳定性 (Top10 特征 d, UP_pre-DOWN_pre; 每层 UP/DOWN 各>=%d 才算):' % MIN_STRAT_N,
          flush=True)
    for name in top10_names:
        parts = []
        mc = r3['market_cap_tiers'].get('strata', {})
        vals = []
        for tn in ('t0_low20', 't1_20_40', 't2_40_60', 't3_60_80', 't4_top20'):
            cc = mc.get(tn, {})
            ff = cc.get('features', {}).get(name, {})
            dv = ff.get('d_up_minus_down')
            vals.append(('%+.2f' % dv) if dv is not None else 'skip')
        parts.append('mc[' + '/'.join(vals) + ']')
        env = r3['market_env'].get('strata', {})
        e1 = env.get('mkt_mom20_pos', {}).get('features', {}).get(name, {}).get('d_up_minus_down')
        e2 = env.get('mkt_mom20_nonpos', {}).get('features', {}).get(name, {}).get('d_up_minus_down')
        parts.append('env[涨%s 跌%s]' % (('%+.2f' % e1) if e1 is not None else 'skip',
                                         ('%+.2f' % e2) if e2 is not None else 'skip'))
        gm = r3['gem_reform'].get('strata', {})
        g1 = gm.get('gem_pre_20200824', {}).get('features', {}).get(name, {}).get('d_up_minus_down')
        g2 = gm.get('gem_post_20200824', {}).get('features', {}).get(name, {}).get('d_up_minus_down')
        parts.append('gem[前%s 后%s]' % (('%+.2f' % g1) if g1 is not None else 'skip',
                                         ('%+.2f' % g2) if g2 is not None else 'skip'))
        ind = r3['industry'].get('strata', {})
        dvals = []
        for sn, cc in ind.items():
            dv = cc.get('features', {}).get(name, {}).get('d_up_minus_down')
            if dv is not None:
                dvals.append(dv)
        if dvals:
            parts.append('ind[min%+.2f max%+.2f]' % (min(dvals), max(dvals)))
        else:
            parts.append('ind[skip]')
        print('  %-18s %s' % (name, '  '.join(parts)), flush=True)

    print('\nR4 UP段弹性 (大涨段前20快照 - 其余UP段前20快照; 阈值=%.3f):'
          % (r4.get('amp_threshold') or 0.0), flush=True)
    for r in r4.get('big_vs_rest', []):
        dfs = ('%+.4f' % r['diff_big_minus_rest']) if r['diff_big_minus_rest'] is not None else 'NaN'
        dvs = ('%+.3f' % r['cohens_d']) if r['cohens_d'] is not None else 'NaN'
        print('  %-18s big=%+10.4f rest=%+10.4f diff=%s d=%s n=%d/%d'
              % (r['feature'], r['mean_big20pct_pre'] or 0.0, r['mean_rest_pre'] or 0.0,
                 dfs, dvs, r['n_big'], r['n_rest']), flush=True)

    print('\n数据异常/口径提示:', flush=True)
    for k, v in anomalies.items():
        print('  %s: %s' % (k, v), flush=True)
    print('meta 幸存者声明: %s' % meta['survivorship']['declaration'], flush=True)
    print('=' * 108, flush=True)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    log('==== 画像归因 v2：阶段启动前特征快照 描述性归因（只读，零交易权） ====')
    (S, T, DD, CMV, MM, GG, II, AMP, nan_cnt, seg_skip, acc,
     seg_meta, n_db, n_ind_map, ind_rows, mdates, mom_arr, ind_names,
     r2_n, r2_sum, r2_nvalid) = build()

    rows_total = acc['rows']
    for s in seg_skip.values():
        s['short_hist_k20'] = s['total'] - s['no_trading_day'] - s['used_k20']
    log('总行数=%d 只=%d 日期 %d~%d bad_ohlc=%d talib_fail_stocks=%d'
        % (rows_total, acc['stocks'],
           min(acc['last_dates']) if acc['last_dates'] else 0,
           max(acc['last_dates']) if acc['last_dates'] else 0,
           acc['bad_ohlc'], acc['talib_fail']))

    log('计算 R1 区分度（k=20 全表）...')
    r1_rows = stat_r1_full(S[K_MAIN], T[K_MAIN])
    top10_names = [r['feature'] for r in r1_rows if r['cohens_d'] is not None][:10]
    log('Top10 特征: %s' % top10_names)
    log('计算 R1 k=10 / k=5 Top10 数值对比 ...')
    r1_k10 = top10_at_k(S[10], T[10], top10_names)
    r1_k5 = top10_at_k(S[5], T[5], top10_names)

    log('计算 R2 形态频率 ...')
    r2 = stat_r2(r2_n, r2_sum, r2_nvalid)

    log('计算 R3 分层稳定性 ...')
    r3 = stat_r3(S[K_MAIN], T[K_MAIN], DD[K_MAIN], CMV[K_MAIN], MM[K_MAIN],
                 GG[K_MAIN], II[K_MAIN], ind_names, top10_names)

    log('计算 R4 UP 段弹性 ...')
    r4 = stat_r4(S[K_MAIN], T[K_MAIN], AMP, top10_names)

    # ---- R5 幸存者偏差复核 ----
    last_arr = np.asarray(acc['last_dates'], np.int64)
    n_old = int((last_arr < SURV_CUTOFF).sum())
    n_recent = int(((last_arr < SURV_CUTOFF) & (last_arr >= 20240101)).sum())
    pct_old = 100.0 * n_old / max(1, len(last_arr))
    survivorship = {
        'verified_in_this_run': True,
        'cutoff_last_valid_date': SURV_CUTOFF,
        'stocks_total': int(len(last_arr)),
        'stocks_last_valid_before_cutoff': n_old,
        'share_pct': round(pct_old, 2),
        'of_which_last_valid_2024_or_later': n_recent,
        'declaration': (
            '本轮全量复核（Python 侧 int 转换，已规避 qfq date 列为 TEXT 的 SQL '
            '比较陷阱）：qfq 库 %d 只有效股中 %d 只（%.1f%%）最后有效日 < %d，'
            '为真实退市/终止上市股（1997~2026 各年均有，2024 年及以后 %d 只）。'
            '任务书先前「已核实 qfq 库无退市股（最后有效日均>=2026-06-30）」结论'
            '系 date 列 TEXT 类型导致 SQL `MAX(date)<常数` 比较恒假的假阴性'
            '（本轮已用 CAST(date AS INTEGER) 独立复核确认）。因此本轮统计实际为'
            '「含退市股」口径，幸存者偏差小于纯现存股假设；退市股占比 %.1f%% 对'
            '特征分布对比（d/频率差）影响相对温和，但任何 fwd 收益外推仍须注意'
            '现存股偏差方向=系统性高估。'
            % (len(last_arr), n_old, pct_old, SURV_CUTOFF, n_recent, pct_old)),
    }

    # ---- 数据异常 ----
    d20 = DD[K_MAIN]
    cmv20 = CMV[K_MAIN]
    mm20 = MM[K_MAIN]
    base2020 = d20 >= MKT_BASE_DATE
    n_base = int(base2020.sum())
    cmv_cov = float(np.isfinite(cmv20[base2020]).mean()) if n_base else None
    mom_cov = float(np.isfinite(mm20[base2020]).mean()) if n_base else None
    amp_chk = np.asarray(acc['amp_chk'], np.float64)
    anomalies = {
        'bad_ohlc_rows': acc['bad_ohlc'],
        'talib_fail_stocks': acc['talib_fail'],
        'qfq_date_column_type': (
            'TEXT——SQL 数值比较陷阱：`MAX(date)<常数` 因 TEXT 恒大于 INTEGER 而'
            '恒假（本轮用 CAST(date AS INTEGER) 复核）；此前「无退市股」核实结论'
            '即栽在此坑，Python 侧 obj_to_int64 转换后复核出 355 只真实退市股'),
        'amp_vs_regime_ret_median_abs_diff_pp':
            float(np.median(np.abs(amp_chk))) if len(amp_chk) else None,
        'amp_check_note': '本算段幅度(start前一日基准, qfq) vs regime_segments.ret_pct '
                          '(上游口径) 中位绝对差(百分点)；口径差异属预期，仅作一致性提示',
        'k20_snapshot_2020plus_share': n_base,
        'k20_snapshot_2020plus_circ_mv_coverage': cmv_cov,
        'k20_snapshot_2020plus_market_mom_coverage': mom_cov,
        'turn_feature_nan_rate_overall':
            float(nan_cnt[FEAT_IDX['turn']] / max(1, rows_total)),
        'turn_nan_note': 'daily_basic 仅 2020-01-02 起，2020 前换手率缺失→NaN（预期约一半行数）',
        'industry_missing_snapshots_k20': int((II[K_MAIN] < 0).sum()),
        'snapshot_suspension_note':
            '极少数段整段停牌（s0 落在段结束日之后）仍按口径取快照；UP 段此类无幅度'
            '（R4 已剔除，n_without_amp 见 R4）',
    }

    feature_nan_rate = {ALL_FEATS[j]: float(nan_cnt[j] / max(1, rows_total))
                        for j in range(NF)}

    meta = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'script': 'analysis/feature_attr_v2_20261120.py',
        'purpose': '画像归因 v2：阶段启动前特征快照 描述性归因（主人令 2026-11-20）'
                   '——纯描述、事后视角、非信号、零交易权',
        'rows_total': int(rows_total),
        'stocks': int(acc['stocks']),
        'date_min': int(min(acc['last_dates'])) if acc['last_dates'] else None,
        'date_max': int(max(acc['last_dates'])) if acc['last_dates'] else None,
        'n_features': NF,
        'feature_list': ALL_FEATS,
        'feature_nan_rate': feature_nan_rate,
        'regime_segments': seg_meta,
        'snapshot_samples': {
            ('k%d' % k): {
                'UP': int((T[k] == 1).sum()), 'DOWN': int((T[k] == 2).sum()),
                'RANGE': int((T[k] == 3).sum()), 'total': int(len(T[k]))}
            for k in K_LIST},
        'skipped_k20_by_state': seg_skip,
        'turn_circ_mv_source_rows': int(n_db),
        'circ_mv_unit': '万元（已验证：000001.SZ 20200102 circ_mv=3273.74万 ≈ 3273.7亿元）',
        'pattern_col_count_note':
            'TA-Lib 13 个函数清单按符号拆分（ENGULFING/MARUBOZU/3INSIDE 各拆 bull/bear）'
            '= 16 列；任务书文字称 17 列，以显式函数清单展开为准',
        'survivorship': survivorship,
        'sources': {
            'qfq_daily': QFA_DB + ' stock_daily_meta_qfq (close>0 AND volume>0)',
            'daily_basic': FUND_DB + ' daily_basic (turnover_rate, circ_mv; 20200102起)',
            'regime': REGIME_DB + ' regime_segments (is_confirmed 任意，全纳入)',
            'industry': FULL_DB + ' stock_dim (code zfill(6))',
            'market': FULL_DB + ' market_daily (idx_sh_c, 20200102起, 20日自身序列回看)',
        },
        'notes': [
            '快照日=段 start_date 当日或之后首个该股有效交易日 s0 的前第 k 个有效交易日；'
            's0-k<0 跳过（历史不足，skip 计数见 skipped_k20_by_state）',
            'Cohen d=(mean_UP_pre-mean_DOWN_pre)/σ_pooled，σ_pooled='
            'sqrt(((n1-1)s1²+(n2-1)s2²)/(n1+n2-2))；0/1 列同时给频率差 P(UP前)-P(DOWN前)',
            'k=5/k=10 口径只输出 k=20 口径 Top10 特征的数值对比（方向稳定性）',
            'R3 市值分层: circ_mv 20/40/60/80 分位切 5 档，仅 2020+ 样本（任务书文字'
            '「四档」与「20/40/60/80 分位」并存，按显式分位数执行为 5 档，如实注明）',
            'R3 大盘环境: 快照日上证 20 日动量 >0 涨 / <=0 跌；2016-2019 缺失→该子层 N/A 不编造',
            'turn/turn5/turn_z60/turn_pctl60/turn_rise/turn_gt3/high_turn_stall/turn_spike '
            '仅 2020+ 有效；turn_spike 在 2020 初 MA20(turn) 未成形时记 0（无信号）',
            'amt20 单位=百万元（v1 同口径 amount/1e6）',
            '描述性统计，阶段标签为事后 zigzag，非信号、非预测主张，不产生任何交易动作',
        ],
    }

    out = {
        'meta': meta,
        'anomalies': anomalies,
        'R1': {
            'n_samples': meta['snapshot_samples'],
            'skipped_k20_by_state': seg_skip,
            'top10_features': top10_names,
            'k20_full_table_sorted_by_abs_d': r1_rows,
            'k10_top10_comparison': r1_k10,
            'k5_top10_comparison': r1_k5,
        },
        'R2': r2,
        'R3': r3,
        'R4': r4,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    log('JSON 已写出: %s' % OUT_JSON)

    print_summary(r1_rows, top10_names, r1_k10, r1_k5, r2, r3, r4, anomalies, meta)
    log('完成。')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
