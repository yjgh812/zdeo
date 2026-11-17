# -*- coding: utf-8 -*-
"""
E-SELECT-2 预注册实验 runner（画像选股：启动前画像区分度挖掘 + 组合验证）
=========================================================================
唯一真源 = data/PREREG_PORTRAIT_SELECT_E2_V1_20261118.json（判据冻结，本脚本不得改动判据）。
产出（仅三个文件，无其它写入）：
  1) analysis/portrait_select_e2_20261118.py（本脚本）
  2) data/portrait_select_e2_result.json（无时间戳字段，双跑逐字节一致）
  3) reports/PORTRAIT_SELECT_E2_20261118.md（白话报告，含双跑 md5）

双跑方式：
  第1遍：python analysis/portrait_select_e2_20261118.py            -> 打印 RESULT_MD5 <m1>
  第2遍：python analysis/portrait_select_e2_20261118.py --first-run-md5 <m1>
         -> 重算并覆写 result json，生成报告（记录两个 md5；不一致则 FAIL-safe）

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
PREREG_PATH = BASE + '/data/PREREG_PORTRAIT_SELECT_E2_V1_20261118.json'
RESULT_PATH = BASE + '/data/portrait_select_e2_result.json'
REPORT_PATH = BASE + '/reports/PORTRAIT_SELECT_E2_20261118.md'

DB_D = BASE + '/data/universe_portrait_fullseries.db'
DB_SWING = BASE + '/data/swing_labels.db'
DB_SECTOR = BASE + '/data/sector_momentum.db'

# ---------------- 预注册冻结口径（与 PREREG json 逐条对应，不新增不改动） ----------------
SENTINEL = -9999.0          # SQL IFNULL 哨兵，等价 NaN；NaN 值特征 -> 不满足（不满足 != bottom，声明）
IS_LO, IS_HI = 20200701, 20241231       # 挖掘窗（IS）
OOS_LO, OOS_HI = 20250101, 20260831     # 主判据窗（OOS）
TEST_LO, TEST_HI = 20200701, 20260831   # 测试窗
BOARD_LAST = 20260828       # 板块热度数据尾；月最后交易日 > 此值则该月跳过并计数
SNAP_LAG = 10               # 快照日 = 启动日前 10 个市场交易日
LABEL_FWD = 20              # 标签窗口 = 启动后 20 个市场交易日
TOPK = 5                    # 规则特征数上限
RATE_MIN = 0.30             # 准入：rate_up >= 0.30
NW_LAG = 5
SCORE_GE = 4                # 判据与组合的 score 门槛
COST = 0.0015               # 组合双边合计 0.15%
MAX_PRICE = 30.0            # 收盘 <= 30 元（qfq 近似名义价，声明）
MIN_AMT20 = 0.5             # amt20 >= 0.5 亿元
MIN_LOCAL_ROWS = 60         # 次新过滤：局部行号 >= 60

CONT_FEATS = ['ret20', 'ret60', 'pos250', 'pos60', 'bias20', 'ma20_slope5',
              'vr5', 'amt_ratio', 'vola20', 'dd250']
ENV_FEATS = ['mean_ret20', 'pct_above20', 'n_nh250']

# 候选方向清单（跑前冻结：10 连续 x2 + ma_align==3 + 3 环境 x2 + 板块 x2 = 29 个方向）
CAND_DIRECTIONS = []
for _f in CONT_FEATS:
    CAND_DIRECTIONS.append(('cont', _f, 'top'))
    CAND_DIRECTIONS.append(('cont', _f, 'bottom'))
CAND_DIRECTIONS.append(('align3', 'ma_align_eq3', 'eq'))
for _f in ENV_FEATS:
    CAND_DIRECTIONS.append(('env', _f, 'top'))
    CAND_DIRECTIONS.append(('env', _f, 'bottom'))
CAND_DIRECTIONS.append(('sector', 'sector_rs20_rank', 'top'))
CAND_DIRECTIONS.append(('sector', 'sector_rs20_rank', 'bottom'))

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
C_CODE, C_DATE = 0, 1   # 全局 int32 数组中的列
PFX_OK_SET = (60, 30)   # 前缀 60/30；前缀 00 即 code < 10000


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


def r6(x):
    """浮点统一保留 6 位小数（确定性输出）；None/非浮点原样。"""
    if isinstance(x, float):
        return round(x, 6)
    return x


def clean_nan(o):
    """递归把 NaN -> None（合法 JSON），浮点 round(6)。"""
    if isinstance(o, float):
        if math.isnan(o):
            return None
        return r6(o)
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
    bounds_start = dict(zip(uniq_codes.tolist(), start_idx.tolist()))
    bounds_end = dict(zip(uniq_codes.tolist(), end_idx.tolist()))

    # 日期分组（升序）：date -> 行块（按 code,date 稳定排序后的全局行号）
    order = np.argsort(date_col, kind='stable')
    sdates = date_col[order]
    uniq_dates, dstart = np.unique(sdates, return_index=True)
    dend = np.append(dstart[1:], len(order))

    # 2) market_daily：mdates / hs300 / 环境特征
    md = cur.execute('SELECT date, idx_hs300_c, mean_ret20, pct_above20, n_nh250 '
                     'FROM market_daily ORDER BY date').fetchall()
    con.close()
    mdates = [r[0] for r in md]
    hs300 = np.array([r[1] for r in md], dtype=np.float64)
    env_map = {r[0]: {'mean_ret20': r[2], 'pct_above20': r[3], 'n_nh250': r[4]} for r in md}

    # 3) stock_dim：行业名
    con2 = sqlite3.connect(DB_D)
    industry = dict(con2.execute('SELECT code, industry_name FROM stock_dim').fetchall())
    con2.close()

    # 4) sector_momentum：date(TEXT) -> {sector: rs_20}
    con3 = sqlite3.connect(DB_SECTOR)
    sector_map = {}
    for d, sec, rs in con3.execute('SELECT date, sector, rs_20 FROM sector_mom'):
        sector_map.setdefault(int(d), {})[sec] = rs
    con3.close()

    # 5) swing 库：段 + wf 标签
    con4 = sqlite3.connect(DB_SWING)
    segs = con4.execute('SELECT code, seg_type, start_date, ret FROM swing_segments '
                        'ORDER BY code, start_date').fetchall()
    wf_label = dict(con4.execute('SELECT date, label FROM mkt_state_wf').fetchall())
    con4.close()

    return {
        'code_col': code_col, 'date_col': date_col, 'vals': vals,
        'bounds_start': bounds_start, 'bounds_end': bounds_end,
        'uniq_codes': uniq_codes.astype(np.int64),
        'uniq_starts': start_idx.astype(np.int64),
        'order': order, 'uniq_dates': uniq_dates, 'dstart': dstart, 'dend': dend,
        'mdates': mdates, 'hs300': hs300, 'env_map': env_map,
        'industry': industry, 'sector_map': sector_map,
        'segs': segs, 'wf_label': wf_label,
    }


# ---------------- 日级上下文（宇宙截面 + 分位阈值 + 环境 + 板块分位） ----------------

def build_ctx(D, S):
    """快照日/测试日 D 的上下文。
    S = load_all() 结果。宇宙过滤口径（冻结）：有行 & susp==0 & st==0 & close>0
        & 局部行号>=60 & close<=30 & amt20>=0.5 & 前缀60/00/30。
    分位阈值 = 宇宙截面有效值（剔除哨兵）的 np.quantile 线性插值 1/3 与 2/3。
    环境特征为日期级（宇宙内全同值），其截面 quantile 数学上恒等于该值，直接赋值（等价口径）。
    """
    di = int(np.searchsorted(S['uniq_dates'], D))
    if di >= len(S['uniq_dates']) or int(S['uniq_dates'][di]) != D:
        return None
    lo, hi = int(S['dstart'][di]), int(S['dend'][di])
    rows = S['order'][lo:hi]                       # 该日全部行的全局行号
    codes = S['code_col'][rows]
    v = S['vals'][rows]                            # (m,15) 视图
    close = v[:, COL['close']]
    susp = v[:, COL['susp']]
    st = v[:, COL['st']]
    amt20 = v[:, COL['amt20']]
    # 局部行号（股内行索引）
    cu = S['uniq_codes']
    cpos = np.searchsorted(cu, codes)
    starts_arr = S['uniq_starts'][cpos]
    local = np.arange(lo, hi) - starts_arr
    # 前缀 60/00/30
    divk = (codes // 10000)
    prefix_ok = ((divk == 60) | (divk == 30) | (codes < 10000))
    mask = (susp == 0) & (st == 0) & (close > 0) & (local >= MIN_LOCAL_ROWS) \
        & (close <= MAX_PRICE) & (amt20 >= MIN_AMT20) & prefix_ok
    uni_rows = rows[mask]
    ctx = {'date': D, 'uni_rows': uni_rows, 'q33': {}, 'q67': {},
           'env_q33': {}, 'env_q67': {}, 'sector_pct': {}}
    if len(uni_rows) == 0:
        ctx['uni_rows'] = np.array([], dtype=np.int64)
        for f in CONT_FEATS:
            ctx['q33'][f] = None
            ctx['q67'][f] = None
    else:
        uv = S['vals'][uni_rows]
        for f in CONT_FEATS:
            col = uv[:, COL[f]]
            colv = col[col > SENTINEL + 1.0]        # 剔除哨兵（NaN 不参与分位）
            if len(colv) == 0:
                ctx['q33'][f] = None
                ctx['q67'][f] = None
            else:
                ctx['q33'][f] = float(np.quantile(colv, 1.0 / 3.0))
                ctx['q67'][f] = float(np.quantile(colv, 2.0 / 3.0))
    # 环境特征（日期级，宇宙内全同值）
    env = S['env_map'].get(D)
    for f in ENV_FEATS:
        if env is None or env.get(f) is None:
            ctx['env_q33'][f] = None
            ctx['env_q67'][f] = None
        else:
            vv = float(env[f])
            ctx['env_q33'][f] = vv
            ctx['env_q67'][f] = vv
    # 板块分位：当日板块 rs_20 升序排名 pct = rank/(n-1)
    sm = S['sector_map'].get(D)
    if sm:
        items = sorted(sm.items(), key=lambda kv: kv[1])
        n = len(items)
        for rank_i, (sec, _rs) in enumerate(items):
            ctx['sector_pct'][sec] = (rank_i / (n - 1)) if n > 1 else 0.5
    return ctx


def member(ctx, g, code, direction, S):
    """单方向成员判定。g=全局行号；NaN/哨兵 -> 不满足。"""
    kind = direction[0]
    if kind == 'cont':
        f, side = direction[1], direction[2]
        val = float(S['vals'][g, COL[f]])
        if val <= SENTINEL + 1.0:
            return False
        if side == 'top':
            q = ctx['q67'].get(f)
            return q is not None and val >= q
        q = ctx['q33'].get(f)
        return q is not None and val <= q
    if kind == 'align3':
        return float(S['vals'][g, COL['ma_align']]) == 3.0
    if kind == 'env':
        f, side = direction[1], direction[2]
        env = S['env_map'].get(ctx['date'])
        if env is None or env.get(f) is None:
            return False
        val = float(env[f])
        if side == 'top':
            q = ctx['env_q67'].get(f)
            return q is not None and val >= q
        q = ctx['env_q33'].get(f)
        return q is not None and val <= q
    if kind == 'sector':
        sec = S['industry'].get(int(code))
        if sec is None:
            return False
        pct = ctx['sector_pct'].get(sec)
        if pct is None:
            return False
        if direction[2] == 'top':
            return pct >= 2.0 / 3.0
        return pct <= 1.0 / 3.0
    raise ValueError('unknown direction %r' % (direction,))


# ---------------- 主实验 ----------------

def run_experiment():
    verify_prereg()
    S = load_all()
    mdates = S['mdates']
    hs300 = S['hs300']
    n_md = len(mdates)
    ctx_cache = {}

    def get_ctx(D):
        c = ctx_cache.get(D)
        if c is None:
            c = build_ctx(D, S)
            ctx_cache[D] = c
        return c

    # ---- 挖掘（IS 窗内启动段，快照日 = mdates[pos(S)-10]）----
    segs = S['segs']
    by_type = {'up': [], 'dn': [], 'osc': []}
    for code_s, seg_type, start_date, ret in segs:
        if IS_LO <= start_date <= IS_HI:
            by_type[seg_type].append((int(code_s), start_date, float(ret)))

    mining_stat = {}
    for t in ('up', 'dn', 'osc'):
        mining_stat[t] = {'total': len(by_type[t]), 'used': 0,
                          'dropped_snapshot': 0, 'dropped_window': 0}

    hit_cnt = {d: {'up': 0, 'dn': 0, 'osc': 0} for d in CAND_DIRECTIONS}
    for t in ('up', 'dn', 'osc'):
        for code, start_d, _ret in by_type[t]:
            pos = bisect.bisect_left(mdates, start_d)
            di = pos - SNAP_LAG
            if di < 0 or pos >= n_md:
                mining_stat[t]['dropped_window'] += 1
                continue
            D = mdates[di]
            b = S['bounds_start'].get(code)
            if b is None:
                mining_stat[t]['dropped_snapshot'] += 1
                continue
            s, e = b, S['bounds_end'][code]
            i = int(np.searchsorted(S['date_col'][s:e], D))
            if i >= e - s or int(S['date_col'][s + i]) != D:
                mining_stat[t]['dropped_snapshot'] += 1
                continue
            g = s + i
            vv = S['vals'][g]
            if vv[COL['susp']] != 0 or vv[COL['st']] != 0 or vv[COL['close']] <= 0:
                mining_stat[t]['dropped_snapshot'] += 1
                continue
            ctx = get_ctx(D)
            if ctx is None:
                mining_stat[t]['dropped_snapshot'] += 1
                continue
            mining_stat[t]['used'] += 1
            for d in CAND_DIRECTIONS:
                if member(ctx, g, code, d, S):
                    hit_cnt[d][t] += 1

    # 候选方向统计：rate_up/rate_dn/rate_osc、disc、准入
    cand_stats = []
    for d in CAND_DIRECTIONS:
        nu = mining_stat['up']['used']
        nd = mining_stat['dn']['used']
        no = mining_stat['osc']['used']
        ru = hit_cnt[d]['up'] / nu if nu else 0.0
        rd = hit_cnt[d]['dn'] / nd if nd else 0.0
        ro = hit_cnt[d]['osc'] / no if no else 0.0
        disc = ru - max(rd, ro)
        cand_stats.append({'direction': d, 'rate_up': ru, 'rate_dn': rd,
                           'rate_osc': ro, 'disc': disc, 'admit': ru >= RATE_MIN})
    # Top5：按 disc 降序，tie 用 rate_up 降序、方向键字典序（确定性）
    cand_stats.sort(key=lambda x: (-x['disc'], -x['rate_up'],
                                   str(x['direction'][1]) + '|' + str(x['direction'][2])))
    mined = [c for c in cand_stats if c['admit']][:TOPK]
    rule_dirs = [c['direction'] for c in mined]
    n_rule = len(rule_dirs)

    mined_rule_out = [{'feat': c['direction'][1], 'dir': c['direction'][2],
                       'rate_up': c['rate_up'], 'rate_dn': c['rate_dn'],
                       'rate_osc': c['rate_osc'], 'disc': c['disc']} for c in mined]

    # ---- 测试日集合：2020-07~2026-08 每月最后交易日，且 <= 20260828 ----
    month_last = {}
    for d in mdates:
        if TEST_LO <= d <= TEST_HI:
            m = d // 100
            if d > month_last.get(m, 0):
                month_last[m] = d
    all_ml = sorted(month_last.values())
    board_skip = [d for d in all_ml if d > BOARD_LAST]
    test_dates = [d for d in all_ml if d <= BOARD_LAST]

    # ---- 验证（月度截面）----
    # up 段按 code 分组（全历史，标签用）
    up_by_code = {}
    for code_s, seg_type, start_date, ret in segs:
        if seg_type == 'up':
            up_by_code.setdefault(int(code_s), []).append((start_date, float(ret)))
    for k in up_by_code:
        up_by_code[k].sort()

    dates_out = []
    month_recs = []     # 每个有效测试日：{date, bucket pooled lists, ge4_mean, portfolio}
    for T in test_dates:
        ti = bisect.bisect_left(mdates, T)
        ctx = get_ctx(T)
        rec = {'date': T, 'n_samples': 0, 'n_env_skip': 0, 'bucket_mean': [None] * 6,
               'hit_rate': None, 'n_hits': 0, 'seg_ret_mean_of_hits': None}
        if ctx is not None and S['wf_label'].get(T) == '下跌趋势':
            rec['n_env_skip'] = 1
            dates_out.append(rec)
            month_recs.append({'date': T, 'skipped': 'env'})
            continue
        if ctx is None:
            dates_out.append(rec)   # 无 ctx（宇宙为空/无数据日，保守记录不伪造）
            month_recs.append({'date': T, 'skipped': 'no_ctx'})
            continue
        uni = ctx['uni_rows']
        n_samples = len(uni)
        buckets = {k: [] for k in range(6)}
        hits = []
        wend = mdates[min(ti + LABEL_FWD, n_md - 1)]
        hs_ret = float(hs300[min(ti + LABEL_FWD, n_md - 1)]) / float(hs300[ti]) - 1.0
        for g in uni:
            code = int(S['code_col'][g])
            score = 0
            for d in rule_dirs:
                if member(ctx, g, code, d, S):
                    score += 1
            s0 = S['bounds_start'][code]
            e0 = S['bounds_end'][code]
            i = int(g) - s0
            nloc = e0 - s0
            c0 = float(S['vals'][g, COL['close']])
            i20 = min(i + LABEL_FWD, nloc - 1)
            stk_ret = float(S['vals'][s0 + i20, COL['close']]) / c0 - 1.0
            excess = stk_ret - hs_ret
            buckets[score].append(excess)
            ups = up_by_code.get(code)
            if ups:
                k = bisect.bisect_right([u[0] for u in ups], T)
                if k < len(ups) and ups[k][0] <= wend:
                    hits.append(ups[k][1])
        rec['n_samples'] = n_samples
        for k in range(6):
            if buckets[k]:
                rec['bucket_mean'][k] = float(np.mean(buckets[k]))
        rec['n_hits'] = len(hits)
        if n_samples:
            rec['hit_rate'] = len(hits) / n_samples
        if hits:
            rec['seg_ret_mean_of_hits'] = float(np.mean(hits))
        dates_out.append(rec)
        # score>=4 组 = 桶4+桶5 pooled（当期均值）
        ge4 = buckets[4] + buckets[5] if n_rule >= 5 else \
            sum((buckets[k] for k in range(4, min(5, n_rule) + 1)), [])
        month_recs.append({'date': T, 'skipped': None, 'buckets': buckets,
                           'ge4_mean': float(np.mean(ge4)) if ge4 else None})

    # ---- 汇总与判据 ----
    def ge4_series(lo, hi):
        return [(m['date'], m['ge4_mean']) for m in month_recs
                if m['skipped'] is None and m['ge4_mean'] is not None
                and lo <= m['date'] <= hi]

    def bucket_means(lo, hi):
        # 跨日期桶均值（桶空日期不参与该桶）
        acc = {k: [] for k in range(6)}
        for m in month_recs:
            if m['skipped'] is not None or not (lo <= m['date'] <= hi):
                continue
            for k in range(6):
                if m['buckets'][k]:
                    acc[k].append(float(np.mean(m['buckets'][k])))
        return {k: (float(np.mean(v)) if v else None) for k, v in acc.items()}

    def nw_t(x):
        x = np.asarray(x, dtype=np.float64)
        n = len(x)
        mean = float(x.mean())
        u = x - mean
        g = [float(u @ u) / n if l == 0 else float(u[l:] @ u[:-l]) / n
             for l in range(0, NW_LAG + 1)]
        var = g[0] + sum(2.0 * (1.0 - l / 6.0) * g[l] for l in range(1, NW_LAG + 1))
        return mean / math.sqrt(var / n) if var > 0 else 0.0

    is_series = ge4_series(IS_LO, IS_HI)
    oos_series = ge4_series(OOS_LO, OOS_HI)
    is_mean = float(np.mean([v for _d, v in is_series])) if is_series else None
    g3 = 'PASS' if (is_mean is not None and is_mean > 0) else 'FAIL'

    oos_vals = [v for _d, v in oos_series]
    oos_mean = float(np.mean(oos_vals)) if oos_vals else None
    oos_t = nw_t(oos_vals) if len(oos_vals) >= 2 else None
    n_oos = len(oos_series)
    g1_ok = (oos_mean is not None and oos_mean > 0
             and oos_t is not None and oos_t >= 2.0 and n_oos >= 18)
    g1 = 'PASS' if g1_ok else 'FAIL'

    oos_buckets = bucket_means(OOS_LO, OOS_HI)
    diffs = []
    for k in range(5):
        a, b2 = oos_buckets[k], oos_buckets[k + 1]
        diffs.append(None if (a is None or b2 is None) else b2 - a)
    pos_diffs = sum(1 for x in diffs if x is not None and x > 0)
    y2025 = ge4_series(20250101, 20251231)
    y2026 = ge4_series(20260101, 20260831)
    m25 = float(np.mean([v for _d, v in y2025])) if y2025 else None
    m26 = float(np.mean([v for _d, v in y2026])) if y2026 else None
    g2_ok = pos_diffs >= 3 and m25 is not None and m25 > 0 and m26 is not None and m26 > 0
    g2 = 'PASS' if g2_ok else 'FAIL'
    verdict = 'PASS' if (g3 == 'PASS' and g1 == 'PASS' and g2 == 'PASS') else 'FAIL'

    # ---- 组合呈现（非判据）：每月 score>=4 池按 amt_ratio 降序、tie 按 code 升序 Top20 ----
    port_months = []
    for m in month_recs:
        if m['skipped'] is not None:
            continue
        T = m['date']
        ti = bisect.bisect_left(mdates, T)
        ctx = get_ctx(T)
        uni = ctx['uni_rows']
        pool = []
        for g in uni:
            code = int(S['code_col'][g])
            score = 0
            for d in rule_dirs:
                if member(ctx, g, code, d, S):
                    score += 1
            if score >= SCORE_GE and n_rule >= SCORE_GE:
                amt = float(S['vals'][g, COL['amt_ratio']])
                amt_eff = amt if amt > SENTINEL + 1.0 else -1e18
                pool.append((amt_eff, code, g))
        pool.sort(key=lambda x: (-x[0], x[1]))
        top = pool[:20]
        if not top:
            continue
        rets, excesses = [], []
        i1 = min(ti + 1, n_md - 1)
        i21 = min(ti + 21, n_md - 1)
        bench = float(hs300[i21]) / float(hs300[i1]) - 1.0
        for _amt, code, g in top:
            s0 = S['bounds_start'][code]
            nloc = S['bounds_end'][code] - s0
            i = int(g) - s0
            i_in = min(i + 1, nloc - 1)
            i_out = min(i + 21, nloc - 1)
            entry = float(S['vals'][s0 + i_in, COL['close']])
            exit_ = float(S['vals'][s0 + i_out, COL['close']])
            ret = exit_ / entry - 1.0 - COST
            rets.append(ret)
            excesses.append(ret - bench)
        port_months.append({'date': T, 'n_picks': len(top),
                            'mean_ret': float(np.mean(rets)),
                            'mean_excess': float(np.mean(excesses)),
                            'win_rate': float(np.mean([r > 0 for r in rets]))})

    def port_sum(dates_range):
        ms = [p for p in port_months if dates_range is None or dates_range[0] <= p['date'] <= dates_range[1]]
        if not ms:
            return {'n_months': 0, 'mean_ret': None, 'mean_excess': None, 'win_rate': None}
        rets = [p['mean_ret'] for p in ms]
        exs = [p['mean_excess'] for p in ms]
        return {'n_months': len(ms), 'mean_ret': float(np.mean(rets)),
                'mean_excess': float(np.mean(exs)),
                'win_rate': float(np.mean([p['win_rate'] for p in ms]))}

    portfolio = {'by_year': {
        str(y): port_sum((y * 10000 + 101, y * 10000 + 1231)) for y in (2020, 2021, 2022, 2023, 2024, 2025, 2026)
    }, 'all': port_sum(None), 'months': port_months}

    n_env_skip_total = sum(1 for r in dates_out if r['n_env_skip'] == 1)

    result = {
        'prereg_fp': PREREG_FP,
        'n_mining_samples': mining_stat,
        'mined_rule': mined_rule_out,
        'test_dates': test_dates,
        'n_dates': len(test_dates),
        'n_board_skip_months': len(board_skip),
        'board_skip_dates': board_skip,
        'n_env_skip_total': n_env_skip_total,
        'dates': dates_out,
        'is_summary': {
            'n_months_ge4': len(is_series),
            'score_ge4_mean_excess': is_mean,
            'G3': g3,
            'monthly_series': [{'date': d, 'excess': v} for d, v in is_series],
        },
        'oos_summary': {
            'n_months_ge4': n_oos,
            'score_ge4_mean_excess': oos_mean,
            'nw_t_lag5': oos_t,
            'G1': g1,
            'bucket_means': oos_buckets,
            'adjacent_diffs': diffs,
            'adjacent_positive': pos_diffs,
            'year2025_mean_excess': m25,
            'year2026_mean_excess': m26,
            'monthly_series': [{'date': d, 'excess': v} for d, v in oos_series],
        },
        'g2_summary': {'adjacent_positive': pos_diffs, 'G2': g2},
        'portfolio': portfolio,
        'verdict': {'G3': g3, 'G1': g1, 'G2': g2, 'overall': verdict},
    }
    return result


# ---------------- 报告 ----------------

def write_report(res, m1, m2):
    rule = res['mined_rule']
    oos = res['oos_summary']
    isum = res['is_summary']
    p = res['portfolio']['all']
    ok = (m1 == m2)
    fp_ok_line = '一致' if ok else '不一致（FAIL-safe：本次结果不作为定论）'
    L = []
    L.append('# 预注册实验 E-SELECT-2 结果报告（画像选股：启动前画像 + 组合验证）')
    L.append('')
    L.append('- 实验名：PREREG_PORTRAIT_SELECT_E2_V1（指纹 %s，已逐字节校验通过）' % res['prereg_fp'])
    L.append('- 双跑 md5（result json）：run1=%s / run2=%s —— %s' % (m1, m2, fp_ok_line))
    L.append('- 结论：**%s**（G3=%s, G1=%s, G2=%s）' % (
        res['verdict']['overall'], res['verdict']['G3'], res['verdict']['G1'], res['verdict']['G2']))
    L.append('')
    L.append('## 白话结论（发生了什么 / 对买卖意味着什么 / 要不要做事）')
    L.append('')
    ns = res['n_mining_samples']
    L.append('1. **发生了什么**：用 2020-07~2024-12（IS）内启动的 %d 个上涨段、%d 个下跌段、%d 个震荡段，'
             '在每段启动前 10 个交易日拍快照，从 15 个冻结特征（29 个方向）里找"上涨段快照 vs 下跌/震荡段快照"区分度最大的 5 个，'
             '组成打分规则；再用每月最后一个交易日（2020-07~2026-08）对全市场股票打分（0~5 分），'
             '看 4 分以上的股票未来 20 个交易日是否跑赢沪深300。'
             % (ns['up']['used'], ns['dn']['used'], ns['osc']['used']))
    L.append('2. **对买卖意味着什么**：%s' % (
        '主判据（样本外 OOS）未通过——这条画像规则在样本外没有达到预注册要求的显著性（月度超额均值、Newey-West t 值、样本月数三项须同时满足）。该规则不值得接入实盘/模拟盘。'
        if res['verdict']['overall'] == 'FAIL' else
        '主判据（样本外 OOS）通过——但本实验属研究域、零交易权，PASS 不自动接线；如要生产化仍需 wfa_gate 新指纹 + 主人裁决。'))
    L.append('3. **要不要做事**：不需要。这是预注册研究实验，无论 PASS/FAIL 都不触发任何交易动作；预注册试算预算已耗尽（trial_budget=1），翻案须新指纹+新前提。')
    L.append('')
    L.append('## 挖掘出的规则（score = 满足特征数，0~%d）' % len(rule))
    L.append('')
    L.append('| # | 特征 | 方向 | rate_up | rate_dn | rate_osc | 区分度 disc |')
    L.append('|---|------|------|---------|---------|----------|-------------|')
    for i, r in enumerate(rule, 1):
        L.append('| %d | %s | %s | %.4f | %.4f | %.4f | %.4f |' % (
            i, r['feat'], r['dir'], r['rate_up'], r['rate_dn'], r['rate_osc'], r['disc']))
    L.append('')
    L.append('方向含义：top = 该特征值落在当日全市场宇宙截面的前 1/3（>= 2/3 分位）；'
             'bottom = 后 1/3（<= 1/3 分位）；分位阈值按测试日宇宙截面实时计算（PIT，无冻结常数）。'
             '特征值缺失（NaN/哨兵）一律判"不满足"（不满足 ≠ bottom）。')
    L.append('')
    L.append('## 判据核对（预注册冻结，一字未改）')
    L.append('')
    L.append('| 判据 | 内容 | 结果 |')
    L.append('|------|------|------|')
    L.append('| G3 (IS 前置) | IS 窗 score>=4 组平均超额>0 | %s（均值=%.4f，非空月数=%d） |'
             % (isum['G3'], isum['score_ge4_mean_excess'] or 0, isum['n_months_ge4']))
    L.append('| G1 (OOS 主判据) | OOS score>=4 月度超额均值>0 且 NW(lag=5) t>=2.0 且 n>=18 | %s（均值=%.4f，t=%.4f，n=%d） |'
             % (oos['G1'], oos['score_ge4_mean_excess'] or 0, oos['nw_t_lag5'] or 0, oos['n_months_ge4']))
    bm = oos['bucket_means']
    L.append('| G2 (稳健) | OOS 桶 0..5 相邻差为正>=3/5 且 2025/2026 两自然年 score>=4 超额均>0 | %s（正差=%d/5；2025=%.4f，2026=%.4f） |'
             % (res['g2_summary']['G2'], oos['adjacent_positive'], oos['year2025_mean_excess'] or 0, oos['year2026_mean_excess'] or 0))
    L.append('')
    L.append('OOS 六桶跨日期均值（score 0..5）：%s' % ', '.join(
        ('%.4f' % bm[k]) if bm[k] is not None else '空' for k in range(6)))
    L.append('')
    L.append('## 关键数字')
    L.append('')
    L.append('- 挖掘样本（快照日有效/总段数/弃样）：up %d/%d/%d，dn %d/%d/%d，osc %d/%d/%d'
             % (ns['up']['used'], ns['up']['total'], ns['up']['dropped_snapshot'] + ns['up']['dropped_window'],
                ns['dn']['used'], ns['dn']['total'], ns['dn']['dropped_snapshot'] + ns['dn']['dropped_window'],
                ns['osc']['used'], ns['osc']['total'], ns['osc']['dropped_snapshot'] + ns['osc']['dropped_window']))
    L.append('- 测试日：%d 个月（2020-07~2026-08 每月最后交易日且 <= %d）；板块数据尾跳过 %d 个月（%s）；'
             '环境过滤（wf=下跌趋势）跳过 %d 个月'
             % (res['n_dates'], BOARD_LAST, res['n_board_skip_months'],
                ','.join(str(d) for d in res['board_skip_dates']), res['n_env_skip_total']))
    L.append('- 组合呈现（非判据，每月 score>=4 池 Top20，T+1 收盘买持 20 行，双边 0.15%%）：整体月均收益 %.4f、'
             '月均超额 %.4f、月胜率 %.2f%%（%d 个月）'
             % (p['mean_ret'] or 0, p['mean_excess'] or 0, (p['win_rate'] or 0) * 100, p['n_months']))
    for y, d in sorted(res['portfolio']['by_year'].items()):
        if d['n_months']:
            L.append('  - %s 年：%d 个月，月均收益 %.4f，月均超额 %.4f，胜率 %.2f%%'
                     % (y, d['n_months'], d['mean_ret'], d['mean_excess'], d['win_rate'] * 100))
    L.append('')
    L.append('## 边界声明（预注册口径，如实记录）')
    L.append('')
    L.append('- 本实验属**研究域，零交易权**；PASS 也不自动接线（生产接线仍须 wfa_gate 新指纹 + 主人裁决）。')
    L.append('- 日期级环境特征（mean_ret20/pct_above20/n_nh250）按冻结口径取"当日宇宙截面分位"，'
             '因其对全截面同值，top/bottom 判定恒成立（disc=0），按口径执行未特判。')
    L.append('- 特征值 NaN/哨兵一律"不满足"（不满足 ≠ bottom）；分位阈值计算剔除 NaN（NaN 不参与分位）。')
    L.append('- 收盘<=30 元过滤使用 qfq 复权价近似名义价（口径近似，预注册已声明）。')
    L.append('- 板块热度数据尾 %d，测试窗相应收紧（%d 月被跳过，未插补）；wf 标签为 PIT walk-forward 版，'
             '缺失日不过滤（本次核对 %d 个测试日全部有 wf 标签）。' % (BOARD_LAST, res['n_board_skip_months'], res['n_dates']))
    L.append('- 幸存者偏差：D 层含 391 只窗口内退市/长停股，全库覆盖（5010 codes）。')
    L.append('- 预注册试算预算已耗尽（trial_budget=1）；翻案须新指纹 + 新前提，禁止本文件改判据。')
    L.append('')
    with open(REPORT_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L) + '\n')


PREREG_FP = ''


def main():
    global PREREG_FP
    args = sys.argv[1:]
    first_md5 = None
    if '--first-run-md5' in args:
        first_md5 = args[args.index('--first-run-md5') + 1]
    PREREG_FP = verify_prereg()
    res = run_experiment()
    payload = json.dumps(clean_nan(res), ensure_ascii=False, sort_keys=True, indent=2)
    with open(RESULT_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write(payload + '\n')
    # md5 以落盘文件字节为准（外部可对文件复核），而非内存串
    with open(RESULT_PATH, 'rb') as f:
        m2 = hashlib.md5(f.read()).hexdigest()
    if first_md5 is None:
        print('RESULT_MD5 %s' % m2)
        print('verdict: %s | G3=%s G1=%s G2=%s' % (
            res['verdict']['overall'], res['verdict']['G3'], res['verdict']['G1'], res['verdict']['G2']))
    else:
        write_report(res, first_md5, m2)
        print('REPORT_WRITTEN %s' % REPORT_PATH)
        print('md5 run1=%s run2=%s match=%s' % (first_md5, m2, first_md5 == m2))


if __name__ == '__main__':
    main()
