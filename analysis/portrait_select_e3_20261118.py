# -*- coding: utf-8 -*-
"""
E-SELECT-3 预注册实验 runner（画像选股 trial#2：规则承继 + 日度截面功效修复）
==========================================================================
唯一真源 = data/PREREG_PORTRAIT_SELECT_E3_V1_20261118.json（判据冻结，本脚本不得改动判据）。
与 E-SELECT-2 的差异（预注册 new_premises）：
  1) 规则承继不重挖：pos250/pos60/vola20/ret60/ret20 全 bottom 硬编码（成员=值<=当日宇宙截面1/3分位，NaN->不满足）；
  2) 日度截面：测试日=全部市场交易日（IS=2020-07-01~2024-12-31，OOS=2025-01-01~库尾前20行）；
  3) 环境不过滤：wf 标签仅分层呈现（描述性），不剔除任何日期；
  4) 主判据 G1 = OOS 日度 score>=4 组超额序列均值>0 且 NW(lag=20) t>=2.5 且有效日数>=200。
产出（仅三个文件）：analysis/portrait_select_e3_20261118.py + data/portrait_select_e3_result.json
  + reports/PORTRAIT_SELECT_E3_20261118.md。三跑逐字节一致（输出无时间戳）。

双跑/三跑方式：
  第1遍：python analysis/portrait_select_e3_20261118.py                     -> 打印 RESULT_MD5 <m1>
  第2遍：python analysis/portrait_select_e3_20261118.py --md5-list <m1>     -> 报告记录 [m1,m2]
  第3遍：python analysis/portrait_select_e3_20261118.py --md5-list <m1>,<m2> -> 报告记录 [m1,m2,m3] 并给出一致性判定

边界声明：本实验属研究域，零交易权，PASS 也不自动接线。
"""
import bisect
import hashlib
import json
import math
import sys

import numpy as np
import sqlite3

BASE = 'D:/zcodesj'
PREREG_PATH = BASE + '/data/PREREG_PORTRAIT_SELECT_E3_V1_20261118.json'
RESULT_PATH = BASE + '/data/portrait_select_e3_result.json'
REPORT_PATH = BASE + '/reports/PORTRAIT_SELECT_E3_20261118.md'

DB_D = BASE + '/data/universe_portrait_fullseries.db'
DB_SWING = BASE + '/data/swing_labels.db'

# ---------------- 预注册冻结口径（与 PREREG json 逐条对应，不新增不改动） ----------------
SENTINEL = -9999.0          # SQL IFNULL 哨兵，等价 NaN；NaN 值特征 -> 不满足（不满足 != bottom，声明）
IS_LO, IS_HI = 20200701, 20241231       # 挖掘承继窗（IS，日度）
OOS_LO = 20250101                        # OOS 起点；终点 = 库尾前 20 行（最后测试日）
LABEL_FWD = 20                           # 标签窗口 = 启动后 20 个市场交易日
NW_LAG = 20                              # 重叠 20 行标签的 Newey-West 滞后
NW_T_MIN = 2.5                           # 主判据 t 阈值（重复检验补偿）
N_DAYS_MIN = 200                         # 主判据有效日数下限
SCORE_GE = 4                             # 判据与组合的 score 门槛
COST = 0.0015                            # 组合双边合计 0.15%
MAX_PRICE = 30.0                         # 收盘 <= 30 元（qfq 近似名义价，声明）
MIN_AMT20 = 0.5                          # amt20 >= 0.5 亿元
MIN_LOCAL_ROWS = 60                      # 次新过滤：局部行号 >= 60
WF_LABELS = ('上涨趋势', '下跌趋势', '宽幅震荡', '窄幅震荡')

# 规则跑前冻结（预注册 rule_frozen 一字对应：承继自 E-SELECT-2 的 IS 挖掘产物，全 bottom）
RULE_FEATS = ['pos250', 'pos60', 'vola20', 'ret60', 'ret20']

# stock_daily 读取列序 -> vals 数组列索引（0..14）
SELECT_COLS = ('code, date, close, susp, st, amt20, '
               'IFNULL(ret20,%d) AS ret20, IFNULL(ret60,%d) AS ret60, '
               'IFNULL(pos250,%d) AS pos250, IFNULL(pos60,%d) AS pos60, '
               'IFNULL(bias20,%d) AS bias20, IFNULL(ma_align,-1) AS ma_align, '
               'IFNULL(ma20_slope5,%d) AS ma20_slope5, IFNULL(vr5,%d) AS vr5, '
               'IFNULL(amt_ratio,%d) AS amt_ratio, IFNULL(vola20,%d) AS vola20, '
               'IFNULL(dd250,%d) AS dd250' % ((SENTINEL,) * 10))
COL = {'close': 0, 'susp': 1, 'st': 2, 'amt20': 3, 'ret20': 4, 'ret60': 5, 'pos250': 6,
       'pos60': 7, 'bias20': 8, 'ma_align': 9, 'ma20_slope5': 10, 'vr5': 11,
       'amt_ratio': 12, 'vola20': 13, 'dd250': 14}
RULE_COLS = [COL[f] for f in RULE_FEATS]


def verify_prereg():
    """预注册指纹校验：pop('fingerprint') 后剩余 dict 的 canonical json md5 前 16 位。"""
    with open(PREREG_PATH, encoding='utf-8') as f:
        d = json.load(f)
    fp = d.pop('fingerprint')
    s = json.dumps(d, ensure_ascii=False, sort_keys=True).encode('utf-8')
    h = hashlib.md5(s).hexdigest()[:16]
    if h != fp:
        print('STRUCTURED_ERROR: prereg fingerprint mismatch expected=%s got=%s' % (fp, h))
        sys.exit(2)
    return fp


def clean_nan(o):
    """递归把 NaN -> None（合法 JSON），浮点 round(6)。"""
    if isinstance(o, float):
        if math.isnan(o):
            return None
        return round(o, 6)
    if isinstance(o, dict):
        return {k: clean_nan(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_nan(v) for v in o]
    return o


# ---------------- 数据加载 ----------------

def load_all():
    con = sqlite3.connect(DB_D)
    cur = con.cursor()

    # 1) D 层 stock_daily：全局列式数组（code,date int32；其余 float32）
    code_chunks, date_chunks, val_chunks = [], [], []
    c = cur.execute('SELECT %s FROM stock_daily ORDER BY code, date' % SELECT_COLS)
    while True:
        batch = c.fetchmany(200000)
        if not batch:
            break
        a = np.array(batch, dtype=np.float64)
        code_chunks.append(a[:, 0:1].astype(np.int32))
        date_chunks.append(a[:, 1:2].astype(np.int32))
        val_chunks.append(a[:, 2:].astype(np.float32))
    code_col = np.concatenate(code_chunks)[:, 0]
    date_col = np.concatenate(date_chunks)[:, 0]
    vals = np.concatenate(val_chunks)          # (N,15) 列序见 COL
    del code_chunks, date_chunks, val_chunks

    # 每股边界（code 升序）
    uniq_codes, start_idx = np.unique(code_col, return_index=True)
    end_idx = np.append(start_idx[1:], len(code_col))
    stock_n = (end_idx - start_idx).astype(np.int64)     # 每股行数
    bounds_start = dict(zip(uniq_codes.tolist(), start_idx.tolist()))
    bounds_end = dict(zip(uniq_codes.tolist(), end_idx.tolist()))

    # 日期分组（升序）：date -> 行块（按 code,date 稳定排序后的全局行号）
    order = np.argsort(date_col, kind='stable')
    sdates = date_col[order]
    uniq_dates, dstart = np.unique(sdates, return_index=True)
    dend = np.append(dstart[1:], len(order))

    # 2) market_daily：mdates / hs300（本件无板块/环境特征，不加载 sector_momentum）
    md = cur.execute('SELECT date, idx_hs300_c FROM market_daily ORDER BY date').fetchall()
    con.close()
    mdates = [r[0] for r in md]
    hs300 = np.array([r[1] for r in md], dtype=np.float64)

    # 3) swing 库：up 段（标签用）+ wf 标签（分层呈现用）
    con4 = sqlite3.connect(DB_SWING)
    up_segs = con4.execute("SELECT code, start_date, ret FROM swing_segments "
                           "WHERE seg_type='up' ORDER BY code, start_date").fetchall()
    wf_label = dict(con4.execute('SELECT date, label FROM mkt_state_wf').fetchall())
    con4.close()
    up_by_code = {}
    for code_s, start_d, ret in up_segs:
        up_by_code.setdefault(int(code_s), []).append((start_d, float(ret)))

    return {
        'code_col': code_col, 'date_col': date_col, 'vals': vals,
        'bounds_start': bounds_start, 'bounds_end': bounds_end,
        'uniq_codes': uniq_codes.astype(np.int64),
        'uniq_starts': start_idx.astype(np.int64), 'stock_n': stock_n,
        'order': order, 'uniq_dates': uniq_dates, 'dstart': dstart, 'dend': dend,
        'mdates': mdates, 'hs300': hs300,
        'up_by_code': up_by_code, 'wf_label': wf_label,
    }


# ---------------- 日级上下文（宇宙截面 + 5 特征 bottom 阈值） ----------------

def build_ctx(D, S):
    """测试日 D 的上下文。宇宙过滤口径（冻结，同 E-SELECT-2）：
    有行 & susp==0 & st==0 & close>0 & 局部行号>=60 & close<=30 & amt20>=0.5 & 前缀60/00/30。
    bottom 阈值 = 宇宙截面有效值（剔除哨兵）的 np.quantile 线性插值 1/3 分位。
    返回 None 表示 D 无数据块。"""
    di = int(np.searchsorted(S['uniq_dates'], D))
    if di >= len(S['uniq_dates']) or int(S['uniq_dates'][di]) != D:
        return None
    lo, hi = int(S['dstart'][di]), int(S['dend'][di])
    rows = S['order'][lo:hi]
    codes = S['code_col'][rows]
    v = S['vals'][rows]
    close = v[:, COL['close']]
    susp = v[:, COL['susp']]
    st = v[:, COL['st']]
    amt20 = v[:, COL['amt20']]
    cpos = np.searchsorted(S['uniq_codes'], codes)
    local = np.arange(lo, hi) - S['uniq_starts'][cpos]
    divk = (codes // 10000)
    prefix_ok = ((divk == 60) | (divk == 30) | (codes < 10000))
    mask = (susp == 0) & (st == 0) & (close > 0) & (local >= MIN_LOCAL_ROWS) \
        & (close <= MAX_PRICE) & (amt20 >= MIN_AMT20) & prefix_ok
    uni_rows = rows[mask]
    ctx = {'date': D, 'uni_rows': uni_rows, 'q33': {}}
    if len(uni_rows) == 0:
        for f in RULE_FEATS:
            ctx['q33'][f] = None
    else:
        uv = S['vals'][uni_rows]
        for f in RULE_FEATS:
            colv = uv[:, COL[f]]
            colv = colv[colv > SENTINEL + 1.0]        # NaN 不参与分位（声明）
            ctx['q33'][f] = float(np.quantile(colv, 1.0 / 3.0)) if len(colv) else None
    return ctx


def rule_scores(ctx, S):
    """矢量化 score(0..5)：5 特征 bottom 成员（值<=当日截面1/3分位；NaN/哨兵->不满足）。"""
    uni = ctx['uni_rows']
    if len(uni) == 0:
        return np.array([], dtype=np.int64)
    feats = S['vals'][uni][:, RULE_COLS].astype(np.float64)
    q33 = np.array([ctx['q33'][f] if ctx['q33'][f] is not None else np.nan
                    for f in RULE_FEATS], dtype=np.float64)
    memb = (feats > SENTINEL + 1.0) & (feats <= q33)   # q33 为 NaN 时该列全 False（空宇宙防御）
    return memb.sum(axis=1)


# ---------------- Newey-West（预注册公式一字对应） ----------------

def nw_t(x, lag=NW_LAG):
    """t=mean/sqrt((g0+SUM_{l=1..lag} 2(1-l/(lag+1))*gl)/n)，gl=(u[l:]@u[:-l])/n，u=x-mean。"""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    mean = float(x.mean())
    u = x - mean
    g = [float(u @ u) / n if l == 0 else float(u[l:] @ u[:-l]) / n
         for l in range(0, lag + 1)]
    var = g[0] + sum(2.0 * (1.0 - l / (lag + 1.0)) * g[l] for l in range(1, lag + 1))
    return mean / math.sqrt(var / n) if var > 0 else 0.0


# ---------------- 主实验 ----------------

def run_experiment():
    verify_prereg()
    S = load_all()
    mdates = S['mdates']
    hs300 = S['hs300']
    n_md = len(mdates)
    last_test = mdates[n_md - 1 - LABEL_FWD]   # 库尾前 20 行：最后测试日（标签行序 20 行完整）

    # 测试日 = 全部市场交易日：IS 范围 + OOS 范围（至 last_test）
    test_dates = [d for d in mdates
                  if (IS_LO <= d <= IS_HI) or (OOS_LO <= d <= last_test)]
    ctx_cache = {}

    def get_ctx(D):
        c = ctx_cache.get(D)
        if c is None:
            c = build_ctx(D, S)
            ctx_cache[D] = c
        return c

    # up 段 start_date 列表（每股，已按 start_date 排序）供 bisect
    up_starts = {k: [u[0] for u in v] for k, v in S['up_by_code'].items()}

    # ---- 日度截面验证 ----
    dates_out = []
    day_recs = []          # 每个非空宇宙日：{date, buckets(6 lists), ge4_mean, wf_label}
    for T in test_dates:
        ti = bisect.bisect_left(mdates, T)
        ctx = get_ctx(T)
        rec = {'date': T, 'n_samples': 0, 'wf_label': S['wf_label'].get(T),
               'bucket_mean': [None] * 6, 'hit_rate': None, 'n_hits': 0,
               'seg_ret_mean_of_hits': None}
        dates_out.append(rec)
        if ctx is None or len(ctx['uni_rows']) == 0:
            day_recs.append({'date': T, 'skipped': 'no_ctx_or_empty', 'buckets': None,
                             'ge4_mean': None, 'wf_label': rec['wf_label']})
            continue
        uni = ctx['uni_rows']
        n_samples = len(uni)
        rec['n_samples'] = n_samples
        scores = rule_scores(ctx, S)

        codes_u = S['code_col'][uni]
        cpos = np.searchsorted(S['uniq_codes'], codes_u)
        s0 = S['uniq_starts'][cpos]
        nloc = S['stock_n'][cpos]
        p = uni - s0
        i20 = np.minimum(p + LABEL_FWD, nloc - 1)
        c0 = S['vals'][uni, COL['close']].astype(np.float64)
        stk_ret = S['vals'][s0 + i20, COL['close']].astype(np.float64) / c0 - 1.0
        ti20 = min(ti + LABEL_FWD, n_md - 1)
        hs_ret = float(hs300[ti20]) / float(hs300[ti]) - 1.0
        excess = stk_ret - hs_ret

        # 标签：存在 up 段 start_date in (T, mdates[ti+20]]（swing 库日历口径）
        wend = mdates[ti20]
        hit_mask = np.zeros(n_samples, dtype=bool)
        seg_rets = []
        uni_list = uni.tolist()
        codes_list = codes_u.tolist()
        for j in range(n_samples):
            code = codes_list[j]
            starts = up_starts.get(code)
            if starts:
                k = bisect.bisect_right(starts, T)
                if k < len(starts):
                    sd, ret = S['up_by_code'][code][k]
                    if sd <= wend:
                        hit_mask[j] = True
                        seg_rets.append(ret)
        n_hits = int(hit_mask.sum())
        rec['n_hits'] = n_hits
        if n_samples:
            rec['hit_rate'] = n_hits / n_samples
        if seg_rets:
            rec['seg_ret_mean_of_hits'] = float(np.mean(seg_rets))
        buckets = {k: [] for k in range(6)}
        for k in range(6):
            m = (scores == k)
            if m.any():
                rec['bucket_mean'][k] = float(np.mean(excess[m]))
                buckets[k] = excess[m].tolist()
        ge4_mask = scores >= SCORE_GE
        ge4_mean = float(np.mean(excess[ge4_mask])) if ge4_mask.any() else None
        day_recs.append({'date': T, 'skipped': None, 'buckets': buckets,
                         'ge4_mean': ge4_mean, 'wf_label': rec['wf_label'],
                         'ge4_excess': excess[ge4_mask].tolist() if ge4_mask.any() else []})

    # ---- 汇总与判据 ----
    def ge4_series(lo, hi):
        return [(r['date'], r['ge4_mean']) for r in day_recs
                if r['skipped'] is None and r['ge4_mean'] is not None and lo <= r['date'] <= hi]

    def bucket_means(lo, hi):
        acc = {k: [] for k in range(6)}
        for r in day_recs:
            if r['skipped'] is not None or not (lo <= r['date'] <= hi):
                continue
            for k in range(6):
                if r['buckets'][k]:
                    acc[k].append(float(np.mean(r['buckets'][k])))
        return {k: (float(np.mean(v)) if v else None) for k, v in acc.items()}

    is_series = ge4_series(IS_LO, IS_HI)
    oos_series = ge4_series(OOS_LO, last_test)
    is_mean = float(np.mean([v for _d, v in is_series])) if is_series else None
    g3 = 'PASS' if (is_mean is not None and is_mean > 0) else 'FAIL'

    oos_vals = [v for _d, v in oos_series]
    oos_mean = float(np.mean(oos_vals)) if oos_vals else None
    oos_t = nw_t(oos_vals) if len(oos_vals) >= 2 else None
    n_oos = len(oos_series)
    g1_ok = (oos_mean is not None and oos_mean > 0
             and oos_t is not None and oos_t >= NW_T_MIN and n_oos >= N_DAYS_MIN)
    g1 = 'PASS' if g1_ok else 'FAIL'

    oos_buckets = bucket_means(OOS_LO, last_test)
    diffs = []
    for k in range(5):
        a, b = oos_buckets[k], oos_buckets[k + 1]
        diffs.append(None if (a is None or b is None) else b - a)
    pos_diffs = sum(1 for x in diffs if x is not None and x > 0)
    y2025 = ge4_series(20250101, 20251231)
    y2026 = ge4_series(20260101, last_test)
    m25 = float(np.mean([v for _d, v in y2025])) if y2025 else None
    m26 = float(np.mean([v for _d, v in y2026])) if y2026 else None
    g2_ok = pos_diffs >= 3 and m25 is not None and m25 > 0 and m26 is not None and m26 > 0
    g2 = 'PASS' if g2_ok else 'FAIL'
    verdict = 'PASS' if (g3 == 'PASS' and g1 == 'PASS' and g2 == 'PASS') else 'FAIL'

    # ---- 环境分层呈现（描述性，不过滤）：四类市场状态下 score>=4 日度超额均值 ----
    env_layers = {}
    wf_missing_days = [r['date'] for r in day_recs if r['skipped'] is None and r['wf_label'] is None]
    for lab in WF_LABELS:
        vals_l = [r['ge4_mean'] for r in day_recs
                  if r['skipped'] is None and r['ge4_mean'] is not None and r['wf_label'] == lab]
        env_layers[lab] = {'n_days_ge4': len(vals_l),
                           'score_ge4_mean': float(np.mean(vals_l)) if vals_l else None}

    # ---- 组合呈现（非判据）：每月最后交易日 <= last_test 为入口，score>=4 池 Top20 ----
    month_last = {}
    for d in mdates:
        if d >= IS_LO:
            m = d // 100
            if d > month_last.get(m, 0):
                month_last[m] = d
    port_dates = sorted(v for v in month_last.values() if v <= last_test)
    port_skip_months = sorted(v for v in month_last.values() if v > last_test)

    port_months = []
    for T in port_dates:
        ti = bisect.bisect_left(mdates, T)
        ctx = get_ctx(T)
        if ctx is None or len(ctx['uni_rows']) == 0:
            continue
        uni = ctx['uni_rows']
        scores = rule_scores(ctx, S)
        ge4_mask = scores >= SCORE_GE
        if not ge4_mask.any():
            continue
        g_rows = uni[ge4_mask]
        amt = S['vals'][g_rows, COL['amt_ratio']].astype(np.float64)
        amt_eff = np.where(amt > SENTINEL + 1.0, amt, -1e18)
        codes_g = S['code_col'][g_rows]
        order_idx = sorted(range(len(g_rows)), key=lambda j: (-amt_eff[j], int(codes_g[j])))
        top = order_idx[:20]
        i1 = min(ti + 1, n_md - 1)
        i21 = min(ti + 21, n_md - 1)
        bench = float(hs300[i21]) / float(hs300[i1]) - 1.0
        rets = []
        for j in top:
            g = int(g_rows[j])
            code = int(codes_g[j])
            s0 = S['bounds_start'][code]
            nloc = S['bounds_end'][code] - s0
            i = g - s0
            i_in = min(i + 1, nloc - 1)
            i_out = min(i + 21, nloc - 1)
            entry = float(S['vals'][s0 + i_in, COL['close']])
            exit_ = float(S['vals'][s0 + i_out, COL['close']])
            rets.append(exit_ / entry - 1.0 - COST)
        excesses = [r - bench for r in rets]
        port_months.append({'date': T, 'n_picks': len(rets),
                            'mean_ret': float(np.mean(rets)),
                            'mean_excess': float(np.mean(excesses)),
                            'win_rate': float(np.mean([r > 0 for r in rets]))})

    def port_sum(lo=None, hi=None):
        ms = [p for p in port_months
              if (lo is None or lo <= p['date']) and (hi is None or p['date'] <= hi)]
        if not ms:
            return {'n_months': 0, 'mean_ret': None, 'mean_excess': None, 'win_rate': None}
        rets = [p['mean_ret'] for p in ms]
        exs = [p['mean_excess'] for p in ms]
        return {'n_months': len(ms), 'mean_ret': float(np.mean(rets)),
                'mean_excess': float(np.mean(exs)),
                'win_rate': float(np.mean([p['win_rate'] for p in ms]))}

    portfolio = {'by_year': {
        str(y): port_sum(y * 10000 + 101, y * 10000 + 1231)
        for y in (2020, 2021, 2022, 2023, 2024, 2025, 2026)
    }, 'all': port_sum(), 'months': port_months}

    n_empty_days = sum(1 for r in dates_out if r['n_samples'] == 0)

    result = {
        'prereg_fp': PREREG_FP,
        'rule_frozen': [{'feat': f, 'dir': 'bottom', 'def': '值<=当日宇宙截面1/3分位; NaN->不满足'}
                        for f in RULE_FEATS],
        'last_test_date': last_test,
        'test_dates': test_dates,
        'n_dates': len(test_dates),
        'n_dates_is': len([d for d in test_dates if d <= IS_HI]),
        'n_dates_oos': len([d for d in test_dates if d >= OOS_LO]),
        'n_empty_universe_days': n_empty_days,
        'n_wf_missing_days': len(wf_missing_days),
        'dates': dates_out,
        'is_summary': {
            'n_days_ge4': len(is_series),
            'score_ge4_mean_excess': is_mean,
            'G3': g3,
            'daily_series': [{'date': d, 'excess': v} for d, v in is_series],
        },
        'oos_summary': {
            'n_days_ge4': n_oos,
            'score_ge4_mean_excess': oos_mean,
            'nw_t_lag20': oos_t,
            'G1': g1,
            'G2': g2,
            'bucket_means': oos_buckets,
            'adjacent_diffs': diffs,
            'adjacent_positive': pos_diffs,
            'year2025_mean_excess': m25,
            'year2026_mean_excess': m26,
            'daily_series': [{'date': d, 'excess': v} for d, v in oos_series],
        },
        'env_layers': env_layers,
        'portfolio': portfolio,
        'n_port_months': len(port_months),
        'port_skip_months': port_skip_months,
        'verdict': {'G3': g3, 'G1': g1, 'G2': g2, 'overall': verdict},
    }
    return result


# ---------------- 报告 ----------------

def write_report(res, md5_list):
    rule = res['rule_frozen']
    oos = res['oos_summary']
    isum = res['is_summary']
    p = res['portfolio']['all']
    all_same = len(set(md5_list)) == 1
    fp_line = '一致' if all_same else '不一致（FAIL-safe：本次结果不作为定论）'
    L = []
    L.append('# 预注册实验 E-SELECT-3 结果报告（画像选股 trial#2：规则承继 + 日度截面功效修复）')
    L.append('')
    L.append('- 实验名：PREREG_PORTRAIT_SELECT_E3_V1（指纹 %s，已逐字节校验通过）' % res['prereg_fp'])
    L.append('- 三跑 md5（result json 文件字节）：' + ' / '.join(md5_list) + ' —— %s' % fp_line)
    L.append('- 结论：**%s**（G3=%s, G1=%s, G2=%s）' % (
        res['verdict']['overall'], res['verdict']['G3'], res['verdict']['G1'], res['verdict']['G2']))
    L.append('')
    L.append('## 白话结论（发生了什么 / 对买卖意味着什么 / 要不要做事）')
    L.append('')
    L.append('1. **发生了什么**：把 E-SELECT-2 挖出的"低位低波"五条规则（%s）原样承继，'
             '不再重挖；评价协议从"每月最后一个交易日"改成"每个市场交易日"（日度截面），'
             '下跌趋势不再整月剔除（只分层呈现），样本外拉到 2026-10-19（库尾前 20 行，保证 20 行标签完整）。'
             '共 %d 个测试日（IS %d + OOS %d），检验功效比 E-SELECT-2 大幅提高。'
             % ('、'.join('%s bottom' % r['feat'] for r in rule),
                res['n_dates'], res['n_dates_is'], res['n_dates_oos']))
    if res['verdict']['overall'] == 'PASS':
        L.append('2. **对买卖意味着什么**：主判据（样本外 OOS 日度）在更高功效下通过预注册阈值——'
                 '为真效应证据；但本实验零交易权，PASS 不自动接线，生产化仍须 wfa_gate 新指纹 + 主人裁决。')
    else:
        L.append('2. **对买卖意味着什么**：主判据（样本外 OOS 日度）未通过——更高功效下效应仍达不到预注册要求，'
                 '该画像规则不值得接入实盘/模拟盘。')
    L.append('3. **要不要做事**：不需要。这是预注册研究实验，无论 PASS/FAIL 都不触发任何交易动作；'
             'PORTRAIT 选股族 trial_budget=2 已耗，再翻案须新指纹 + 全新机制前提（非协议修补）。')
    L.append('')
    L.append('## 承继规则（score = 满足特征数，0~5，硬编码不重挖）')
    L.append('')
    L.append('| # | 特征 | 方向 | 成员定义 |')
    L.append('|---|------|------|----------|')
    for i, r in enumerate(rule, 1):
        L.append('| %d | %s | %s | %s |' % (i, r['feat'], r['dir'], r['def']))
    L.append('')
    L.append('分位阈值按测试日宇宙截面实时计算（PIT，无冻结常数）；NaN/哨兵一律判"不满足"（不满足 != bottom）。')
    L.append('')
    L.append('## 判据核对（预注册冻结，一字未改）')
    L.append('')
    L.append('| 判据 | 内容 | 结果 |')
    L.append('|------|------|------|')
    L.append('| G3 (IS 前置) | IS 日度 score>=4 组超额均值>0 | %s（均值=%.4f，非空日数=%d） |'
             % (isum['G3'], isum['score_ge4_mean_excess'] or 0, isum['n_days_ge4']))
    L.append('| G1 (OOS 主判据) | OOS 日度 score>=4 序列均值>0 且 NW(lag=20) t>=2.5 且有效日数>=200 | %s（均值=%.4f，t=%.4f，有效日数=%d） |'
             % (oos['G1'], oos['score_ge4_mean_excess'] or 0, oos['nw_t_lag20'] or 0, oos['n_days_ge4']))
    bm = oos['bucket_means']
    L.append('| G2 (稳健) | OOS 桶 0..5 相邻差为正>=3/5 且 2025/2026 两自然年 score>=4 超额均>0 | %s（正差=%d/5；2025=%.4f，2026=%.4f） |'
             % (oos['G2'], oos['adjacent_positive'], oos['year2025_mean_excess'] or 0, oos['year2026_mean_excess'] or 0))
    L.append('')
    L.append('OOS 六桶跨日期均值（score 0..5）：%s' % ', '.join(
        ('%.4f' % bm[k]) if bm[k] is not None else '空' for k in range(6)))
    L.append('')
    L.append('## 环境分层呈现（描述性，不过滤；wf=PIT walk-forward 标签）')
    L.append('')
    L.append('| 市场状态 | score>=4 非空日数 | score>=4 组日度超额均值 |')
    L.append('|----------|-------------------|-------------------------|')
    for lab in ('上涨趋势', '下跌趋势', '宽幅震荡', '窄幅震荡'):
        e = res['env_layers'][lab]
        L.append('| %s | %d | %.4f |' % (lab, e['n_days_ge4'], e['score_ge4_mean'] or 0))
    L.append('')
    L.append('## 关键数字')
    L.append('')
    L.append('- 测试日：%d 个市场交易日（IS %d + OOS %d），最后测试日 %d（库尾前 20 行，标签 20 行完整）；'
             '空宇宙日 %d 个；wf 标签缺失日 %d 个'
             % (res['n_dates'], res['n_dates_is'], res['n_dates_oos'], res['last_test_date'],
                res['n_empty_universe_days'], res['n_wf_missing_days']))
    L.append('- 组合呈现（非判据，每月最后交易日入口 %d 个月，score>=4 池 Top20，T+1 收盘买持 20 行，双边 0.15%%）：'
             '整体月均收益 %.4f、月均超额 %.4f、月胜率 %.2f%%'
             % (res['n_port_months'], p['mean_ret'] or 0, p['mean_excess'] or 0, (p['win_rate'] or 0) * 100))
    L.append('  - 入口跳过月（最后交易日 > 最后测试日，标签/持有窗口不完整）：%s'
             % (','.join(str(d) for d in res['port_skip_months']) or '无'))
    for y, d in sorted(res['portfolio']['by_year'].items()):
        if d['n_months']:
            L.append('  - %s 年：%d 个月，月均收益 %.4f，月均超额 %.4f，胜率 %.2f%%'
                     % (y, d['n_months'], d['mean_ret'], d['mean_excess'], d['win_rate'] * 100))
    L.append('')
    L.append('## 边界声明（预注册口径，如实记录）')
    L.append('')
    L.append('- 本实验属**研究域，零交易权**；PASS 也不自动接线（生产接线仍须 wfa_gate 新指纹 + 主人裁决）。')
    L.append('- 同一 OOS 窗（2025 起）已被 E-SELECT-2 评价过一次，存在多重比较；主判据阈值已按预注册升至 t>=2.5 作补偿。')
    L.append('- 规则对 IS 数据的拟合程度与 E-SELECT-2 完全相同（承继不重挖），本件只更换评价协议。')
    L.append('- 分层呈现为描述性统计（该层内 score>=4 非空日的日度超额序列均值），不参与判据。')
    L.append('- 特征值 NaN/哨兵一律"不满足"（不满足 != bottom）；分位阈值计算剔除 NaN（NaN 不参与分位）。')
    L.append('- 收盘<=30 元过滤使用 qfq 复权价近似名义价（口径近似，预注册已声明）。')
    L.append('- 最后测试日按预注册公式 mdates[pos(20261116)-20] 精确计算为 %d（预注册正文"约20261016"为估计值，以公式为准）。'
             % res['last_test_date'])
    L.append('- 幸存者偏差：D 层含 391 只窗口内退市/长停股，全库覆盖（5010 codes）。')
    L.append('- PORTRAIT 选股族 trial_budget=2 已耗；再翻案须新指纹 + 全新机制前提（非协议修补）。')
    L.append('')
    with open(REPORT_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L) + '\n')


PREREG_FP = ''


def main():
    global PREREG_FP
    args = sys.argv[1:]
    md5_list = []
    if '--md5-list' in args:
        md5_list = [x for x in args[args.index('--md5-list') + 1].split(',') if x]
    PREREG_FP = verify_prereg()
    res = run_experiment()
    payload = json.dumps(clean_nan(res), ensure_ascii=False, sort_keys=True, indent=2)
    with open(RESULT_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write(payload + '\n')
    # md5 以落盘文件字节为准（外部可对文件复核）
    with open(RESULT_PATH, 'rb') as f:
        m = hashlib.md5(f.read()).hexdigest()
    all_md5 = md5_list + [m]
    if not md5_list:
        print('RESULT_MD5 %s' % m)
        print('verdict: %s | G3=%s G1=%s G2=%s' % (
            res['verdict']['overall'], res['verdict']['G3'], res['verdict']['G1'], res['verdict']['G2']))
    else:
        write_report(res, all_md5)
        print('REPORT_WRITTEN %s' % REPORT_PATH)
        print('md5 runs=%s match=%s' % (','.join(all_md5), len(set(all_md5)) == 1))


if __name__ == '__main__':
    main()
