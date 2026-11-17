# -*- coding: utf-8 -*-
"""
E-SELECT-4 预注册实验 runner（画像选股 trial#3：分行业启动前画像）
=================================================================
唯一真源 = data/PREREG_PORTRAIT_SELECT_E4_V1_20261118.json（判据冻结，本脚本不得改动判据）。
与 E-SELECT-3 的差异（预注册 new_premises）：
  1) 分行业挖掘：每合格申万L1行业独立挖 Top5 规则（27候选/行业，无板块热度特征）；
  2) 行业内分位成员判定（行业当日宇宙>=10只才判分位，<10只->不满足，NaN->不满足）；
  3) 行业合格闸：IS 窗 up_pre 有效样本>=100 且 dn_pre 有效样本>=100；
  4) 合并池评价：判据在合格行业合并池（跨行业 score>=4 合并）上评。
其余口径承继 trial#2（日度截面/IS 2020-07~2024-12/OOS 至库尾前20行/NW lag=20 t>=2.5/组合呈现）。
产出（仅三个文件）：analysis/portrait_select_e4_20261118.py + data/portrait_select_e4_result.json
  + reports/PORTRAIT_SELECT_E4_20261118.md。三跑逐字节一致（输出无时间戳）。

三跑方式：
  第1遍：python analysis/portrait_select_e4_20261118.py                      -> 打印 RESULT_MD5 <m1>
  第2遍：python analysis/portrait_select_e4_20261118.py --md5-list <m1>      -> 报告记录 [m1,m2]
  第3遍：python analysis/portrait_select_e4_20261118.py --md5-list <m1>,<m2> -> 报告记录 [m1,m2,m3] + 一致性判定

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
PREREG_PATH = BASE + '/data/PREREG_PORTRAIT_SELECT_E4_V1_20261118.json'
RESULT_PATH = BASE + '/data/portrait_select_e4_result.json'
REPORT_PATH = BASE + '/reports/PORTRAIT_SELECT_E4_20261118.md'

DB_D = BASE + '/data/universe_portrait_fullseries.db'
DB_SWING = BASE + '/data/swing_labels.db'

# ---------------- 预注册冻结口径（与 PREREG json 逐条对应，不新增不改动） ----------------
SENTINEL = -9999.0          # SQL IFNULL 哨兵，等价 NaN；NaN 值特征 -> 不满足（不满足 != bottom，声明）
IS_LO, IS_HI = 20200701, 20241231       # 挖掘窗（IS，日度）
OOS_LO = 20250101                        # OOS 起点；终点 = 库尾前 20 行
LABEL_FWD = 20                           # 标签窗口 = 启动后 20 个市场交易日
NW_LAG = 20                              # 重叠 20 行标签的 Newey-West 滞后（承继 trial#2）
NW_T_MIN = 2.5                           # 主判据 t 阈值（承继 trial#2 重复检验补偿值）
N_DAYS_MIN = 200                         # 主判据有效日数下限
SCORE_GE = 4                             # 判据与组合的 score 门槛
COST = 0.0015                            # 组合双边合计 0.15%
MAX_PRICE = 30.0                         # 收盘 <= 30 元（qfq 近似名义价，声明）
MIN_AMT20 = 0.5                          # amt20 >= 0.5 亿元
MIN_LOCAL_ROWS = 60                      # 次新过滤：局部行号 >= 60
MIN_IND_UNI = 10                         # 行业当日宇宙内 >=10 只才判行业内分位
IND_GATE_MIN = 100                       # 行业合格闸：up_pre/dn_pre 有效样本 >= 100
TOPK = 5                                 # 每行业规则特征数上限
RATE_MIN = 0.30                          # 准入：rate_up >= 0.30
WF_LABELS = ('上涨趋势', '下跌趋势', '宽幅震荡', '窄幅震荡')
NO_IND = '__no_ind__'                    # stock_dim 无归属（静态快照缺口，如实计数声明）

CONT_FEATS = ['ret20', 'ret60', 'pos250', 'pos60', 'bias20', 'ma20_slope5',
              'vr5', 'amt_ratio', 'vola20', 'dd250']
ENV_FEATS = ['mean_ret20', 'pct_above20', 'n_nh250']

# 候选方向清单（每行业 27 个，跑前冻结；无板块热度特征）
CAND_DIRECTIONS = []
for _f in CONT_FEATS:
    CAND_DIRECTIONS.append(('cont', _f, 'top'))
    CAND_DIRECTIONS.append(('cont', _f, 'bottom'))
CAND_DIRECTIONS.append(('align3', 'ma_align_eq3', 'eq'))
for _f in ENV_FEATS:
    CAND_DIRECTIONS.append(('env', _f, 'top'))
    CAND_DIRECTIONS.append(('env', _f, 'bottom'))

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
    stock_n = (end_idx - start_idx).astype(np.int64)
    bounds_start = dict(zip(uniq_codes.tolist(), start_idx.tolist()))
    bounds_end = dict(zip(uniq_codes.tolist(), end_idx.tolist()))

    # 日期分组（升序）
    order = np.argsort(date_col, kind='stable')
    sdates = date_col[order]
    uniq_dates, dstart = np.unique(sdates, return_index=True)
    dend = np.append(dstart[1:], len(order))

    # 2) market_daily：mdates / hs300 / 环境特征（环境特征为候选，需加载）
    md = cur.execute('SELECT date, idx_hs300_c, mean_ret20, pct_above20, n_nh250 '
                     'FROM market_daily ORDER BY date').fetchall()
    con.close()
    mdates = [r[0] for r in md]
    hs300 = np.array([r[1] for r in md], dtype=np.float64)
    env_map = {r[0]: {'mean_ret20': r[2], 'pct_above20': r[3], 'n_nh250': r[4]} for r in md}

    # 3) stock_dim：行业归属（静态快照非 PIT，2026-10-18 建，声明）
    con2 = sqlite3.connect(DB_D)
    industry = dict(con2.execute('SELECT code, industry_name FROM stock_dim').fetchall())
    con2.close()

    # 4) swing 库：up 段（标签用）+ IS 段（挖掘用）+ wf 标签（分层呈现用）
    con4 = sqlite3.connect(DB_SWING)
    all_segs = con4.execute('SELECT code, seg_type, start_date FROM swing_segments '
                            'ORDER BY code, start_date').fetchall()
    wf_label = dict(con4.execute('SELECT date, label FROM mkt_state_wf').fetchall())
    up_recs = con4.execute("SELECT code, start_date, ret FROM swing_segments "
                           "WHERE seg_type='up' ORDER BY code, start_date").fetchall()
    con4.close()
    up_by_code = {}
    for code_s, start_d, ret in up_recs:
        up_by_code.setdefault(int(code_s), []).append((start_d, float(ret)))
    for k in up_by_code:
        up_by_code[k].sort()

    return {
        'code_col': code_col, 'date_col': date_col, 'vals': vals,
        'bounds_start': bounds_start, 'bounds_end': bounds_end,
        'uniq_codes': uniq_codes.astype(np.int64),
        'uniq_starts': start_idx.astype(np.int64), 'stock_n': stock_n,
        'order': order, 'uniq_dates': uniq_dates, 'dstart': dstart, 'dend': dend,
        'mdates': mdates, 'hs300': hs300, 'env_map': env_map,
        'industry': industry, 'all_segs': all_segs,
        'up_by_code': up_by_code, 'wf_label': wf_label,
    }


# ---------------- 日级上下文（宇宙截面 + 行业内分位） ----------------

def build_ctx(D, S):
    """测试日/快照日 D 的上下文。
    宇宙过滤口径（冻结，同 E2/3）：有行 & susp==0 & st==0 & close>0 & 局部行号>=60
    & close<=30 & amt20>=0.5 & 前缀60/00/30。
    行业内分位：行业当日宇宙 >=MIN_IND_UNI 只时按行业内截面分位（每列剔除哨兵），
    否则该行业分位类特征判不满足（q 为空 dict）。
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
    uni_codes = codes[mask]
    # 按行业分组（uni 内局部索引）
    groups = {}
    for j in range(len(uni_rows)):
        ind = S['industry'].get(int(uni_codes[j]))
        if ind is None:
            continue
        g = groups.get(ind)
        if g is None:
            groups[ind] = [j]
        else:
            g.append(j)
    uv = S['vals'][uni_rows]
    ind_ctx = {}
    for ind, jlist in groups.items():
        n = len(jlist)
        sub = uv[jlist]
        q = {}
        if n >= MIN_IND_UNI:
            for f in CONT_FEATS:
                colv = sub[:, COL[f]]
                colv = colv[colv > SENTINEL + 1.0]      # NaN 不参与分位（声明）
                if len(colv):
                    q[f] = (float(np.quantile(colv, 1.0 / 3.0)),
                            float(np.quantile(colv, 2.0 / 3.0)))
                else:
                    q[f] = None                          # 该特征行业内全 NaN -> 不满足
        ind_ctx[ind] = {'n': n, 'q': q,
                        'idx': np.array(jlist, dtype=np.int64)}
    return {'date': D, 'uni_rows': uni_rows, 'ind': ind_ctx, 'env': S['env_map'].get(D)}


def member(ctx, g, ind, direction, S):
    """挖掘用单方向成员判定（行业内口径）。ind=段样本所属行业（由分组提供）。"""
    kind = direction[0]
    if kind == 'align3':
        return float(S['vals'][g, COL['ma_align']]) == 3.0
    ic = ctx['ind'].get(ind)
    if ic is None or ic['n'] < MIN_IND_UNI:
        return False
    if kind == 'cont':
        f, side = direction[1], direction[2]
        val = float(S['vals'][g, COL[f]])
        if val <= SENTINEL + 1.0:
            return False
        qq = ic['q'].get(f)
        if qq is None:
            return False
        return (val >= qq[1]) if side == 'top' else (val <= qq[0])
    if kind == 'env':
        f = direction[1]
        env = ctx['env']
        # 行业内口径数学等价：行业>=10只时行业内 env 全同值 -> 分位=该值 -> 恒满足
        return env is not None and env.get(f) is not None
    raise ValueError('unknown direction %r' % (direction,))


# ---------------- Newey-West（预注册公式一字对应，承继 trial#2/3） ----------------

def nw_t(x, lag=NW_LAG):
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
    last_test = mdates[n_md - 1 - LABEL_FWD]   # 库尾前 20 行：最后测试日
    ctx_cache = {}

    def get_ctx(D):
        c = ctx_cache.get(D)
        if c is None:
            c = build_ctx(D, S)
            ctx_cache[D] = c
        return c

    # ============ 分行业挖掘（IS 窗内启动段） ============
    industry = S['industry']
    ind_stat = {}          # ind -> seg_type -> {'total','used','dropped_snapshot','dropped_window'}
    seg_samples = {}       # ind -> seg_type -> [(g, code, D), ...]
    for code_s, seg_type, start_date in S['all_segs']:
        if not (IS_LO <= start_date <= IS_HI):
            continue
        code = int(code_s)
        ind = industry.get(code, NO_IND)
        st_ = ind_stat.setdefault(ind, {}).setdefault(
            seg_type, {'total': 0, 'used': 0, 'dropped_snapshot': 0, 'dropped_window': 0})
        st_['total'] += 1
        pos = bisect.bisect_left(mdates, start_date)
        di = pos - 10
        if di < 0 or pos >= n_md:
            st_['dropped_window'] += 1
            continue
        D = mdates[di]
        b = S['bounds_start'].get(code)
        if b is None:
            st_['dropped_snapshot'] += 1
            continue
        s, e = b, S['bounds_end'][code]
        i = int(np.searchsorted(S['date_col'][s:e], D))
        if i >= e - s or int(S['date_col'][s + i]) != D:
            st_['dropped_snapshot'] += 1
            continue
        g = s + i
        vv = S['vals'][g]
        if vv[COL['susp']] != 0 or vv[COL['st']] != 0 or vv[COL['close']] <= 0:
            st_['dropped_snapshot'] += 1
            continue
        st_['used'] += 1
        seg_samples.setdefault(ind, {}).setdefault(seg_type, []).append((g, code, D))

    # 行业合格闸 + 每合格行业 27 候选挖掘 -> Top5 规则
    all_inds = sorted(k for k in ind_stat if k != NO_IND)
    qualified_inds, disqualified_inds = [], []
    rules, ind_rule_stats = {}, {}
    for ind in all_inds:
        stat = ind_stat[ind]
        up_used = stat['up']['used']
        dn_used = stat['dn']['used']
        if up_used >= IND_GATE_MIN and dn_used >= IND_GATE_MIN:
            qualified_inds.append(ind)
        else:
            disqualified_inds.append(ind)
    for ind in qualified_inds:
        samples = seg_samples[ind]
        nu = len(samples['up'])
        nd = len(samples['dn'])
        no = len(samples['osc'])
        cand_stats = []
        for d in CAND_DIRECTIONS:
            cu = cd = co = 0
            for (g, code, D) in samples['up']:
                if member(get_ctx(D), g, ind, d, S):
                    cu += 1
            for (g, code, D) in samples['dn']:
                if member(get_ctx(D), g, ind, d, S):
                    cd += 1
            for (g, code, D) in samples['osc']:
                if member(get_ctx(D), g, ind, d, S):
                    co += 1
            ru = cu / nu if nu else 0.0
            rd = cd / nd if nd else 0.0
            ro = co / no if no else 0.0
            cand_stats.append({'direction': d, 'rate_up': ru, 'rate_dn': rd,
                               'rate_osc': ro, 'disc': ru - max(rd, ro),
                               'admit': ru >= RATE_MIN})
        cand_stats.sort(key=lambda x: (-x['disc'], -x['rate_up'],
                                       str(x['direction'][1]) + '|' + str(x['direction'][2])))
        mined = [c for c in cand_stats if c['admit']][:TOPK]
        rules[ind] = [c['direction'] for c in mined]
        ind_rule_stats[ind] = [{'feat': c['direction'][1], 'dir': c['direction'][2],
                                'rate_up': c['rate_up'], 'rate_dn': c['rate_dn'],
                                'rate_osc': c['rate_osc'], 'disc': c['disc']} for c in mined]

    # ============ 日度截面验证（合格行业合并池） ============
    test_dates = [d for d in mdates
                  if (IS_LO <= d <= IS_HI) or (OOS_LO <= d <= last_test)]
    up_starts = {k: [r[0] for r in v] for k, v in S['up_by_code'].items()}  # start_date 列表（bisect 加速）
    ind_oos = {ind: [] for ind in qualified_inds}            # OOS score>=4 超额（分行业描述）
    dates_out = []
    day_recs = []
    for T in test_dates:
        ti = bisect.bisect_left(mdates, T)
        ctx = get_ctx(T)
        rec = {'date': T, 'n_universe': 0, 'n_pool': 0, 'wf_label': S['wf_label'].get(T),
               'bucket_mean': [None] * 6, 'hit_rate': None, 'n_hits': 0,
               'seg_ret_mean_of_hits': None}
        dates_out.append(rec)
        if ctx is None or len(ctx['uni_rows']) == 0:
            day_recs.append({'date': T, 'skipped': 'no_ctx_or_empty', 'buckets': None,
                             'ge4_mean': None, 'wf_label': rec['wf_label']})
            continue
        uni = ctx['uni_rows']
        n_uni = len(uni)
        rec['n_universe'] = n_uni
        # 全宇宙超额（矢量化，同 E3 口径）
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
        uv = S['vals'][uni]

        # 每合格行业独立打分（行业内分位口径）；不合格行业 -> -1（不参与）
        scores = np.full(n_uni, -1, dtype=np.int64)
        for ind in qualified_inds:
            ic = ctx['ind'].get(ind)
            if ic is None:
                continue
            rows_local = ic['idx']
            sub = uv[rows_local]
            score_arr = np.zeros(len(rows_local), dtype=np.int64)
            for d in rules[ind]:
                kind = d[0]
                if kind == 'align3':
                    score_arr += (sub[:, COL['ma_align']] == 3.0)
                elif kind == 'cont':
                    if ic['n'] < MIN_IND_UNI:
                        continue                    # 行业当日<10只 -> 分位类不满足
                    qq = ic['q'].get(d[1])
                    if qq is None:
                        continue                    # 行业内全 NaN -> 不满足
                    col = sub[:, COL[d[1]]].astype(np.float64)
                    val_ok = col > SENTINEL + 1.0
                    comp = (col >= qq[1]) if d[2] == 'top' else (col <= qq[0])
                    score_arr += (val_ok & comp)
                elif kind == 'env':
                    if ic['n'] < MIN_IND_UNI:
                        continue
                    env = ctx['env']
                    if env is None or env.get(d[1]) is None:
                        continue
                    score_arr += 1                  # 行业内口径数学等价（恒满足）
            scores[rows_local] = score_arr

        valid = scores >= 0                          # 合格行业合并池
        buckets = {k: [] for k in range(6)}
        for k in range(6):
            m = valid & (scores == k)
            if m.any():
                rec['bucket_mean'][k] = float(np.mean(excess[m]))
                buckets[k] = excess[m].tolist()
        ge4_mask = valid & (scores >= SCORE_GE)
        n_pool = int(ge4_mask.sum())
        rec['n_pool'] = n_pool
        ge4_mean = float(np.mean(excess[ge4_mask])) if n_pool else None
        # 标签：score>=4 合并池逐股（up 段 start_date in (T, mdates[ti+20]]）
        wend = mdates[ti20]
        n_hits = 0
        seg_rets = []
        pool_idx = np.nonzero(ge4_mask)[0].tolist()
        pool_codes = codes_u[ge4_mask].tolist()
        for j, code in zip(pool_idx, pool_codes):
            code = int(code)
            starts = up_starts.get(code)
            if starts:
                k = bisect.bisect_right(starts, T)
                if k < len(starts) and S['up_by_code'][code][k][0] <= wend:
                    n_hits += 1
                    seg_rets.append(S['up_by_code'][code][k][1])
        rec['n_hits'] = n_hits
        if n_pool:
            rec['hit_rate'] = n_hits / n_pool
        if seg_rets:
            rec['seg_ret_mean_of_hits'] = float(np.mean(seg_rets))
        # 分行业 OOS score>=4 超额（描述用）
        if T >= OOS_LO and n_pool:
            for ind in qualified_inds:
                ic = ctx['ind'].get(ind)
                if ic is None:
                    continue
                m_ind = ge4_mask[ic['idx']]
                if m_ind.any():
                    exs = excess[ic['idx'][m_ind]]
                    ind_oos[ind].append((T, exs.tolist()))
        day_recs.append({'date': T, 'skipped': None, 'buckets': buckets,
                         'ge4_mean': ge4_mean, 'wf_label': rec['wf_label']})

    # ============ 汇总与判据 ============

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

    # 环境分层呈现（描述性，合并池口径）
    env_layers = {}
    wf_missing_days = [r['date'] for r in day_recs if r['skipped'] is None and r['wf_label'] is None]
    for lab in WF_LABELS:
        vals_l = [r['ge4_mean'] for r in day_recs
                  if r['skipped'] is None and r['ge4_mean'] is not None and r['wf_label'] == lab]
        env_layers[lab] = {'n_days_ge4': len(vals_l),
                           'score_ge4_mean': float(np.mean(vals_l)) if vals_l else None}

    # ============ 组合呈现（非判据）：每月最后交易日入口，合并池 Top20 ============
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
        codes_u = S['code_col'][uni]
        # 合并池（重算 score，与验证循环同口径）
        scores = np.full(len(uni), -1, dtype=np.int64)
        for ind in qualified_inds:
            ic = ctx['ind'].get(ind)
            if ic is None:
                continue
            rows_local = ic['idx']
            sub = S['vals'][uni][rows_local]
            score_arr = np.zeros(len(rows_local), dtype=np.int64)
            for d in rules[ind]:
                kind = d[0]
                if kind == 'align3':
                    score_arr += (sub[:, COL['ma_align']] == 3.0)
                elif kind == 'cont':
                    if ic['n'] < MIN_IND_UNI:
                        continue
                    qq = ic['q'].get(d[1])
                    if qq is None:
                        continue
                    col = sub[:, COL[d[1]]].astype(np.float64)
                    val_ok = col > SENTINEL + 1.0
                    comp = (col >= qq[1]) if d[2] == 'top' else (col <= qq[0])
                    score_arr += (val_ok & comp)
                elif kind == 'env':
                    if ic['n'] < MIN_IND_UNI:
                        continue
                    env = ctx['env']
                    if env is None or env.get(d[1]) is None:
                        continue
                    score_arr += 1
            scores[rows_local] = score_arr
        ge4_mask = scores >= SCORE_GE
        if not ge4_mask.any():
            continue
        g_rows = uni[ge4_mask]
        g_codes = codes_u[ge4_mask]
        amt = S['vals'][g_rows, COL['amt_ratio']].astype(np.float64)
        amt_eff = np.where(amt > SENTINEL + 1.0, amt, -1e18)
        order_idx = sorted(range(len(g_rows)), key=lambda j: (-amt_eff[j], int(g_codes[j])))
        top = order_idx[:20]
        i1 = min(ti + 1, n_md - 1)
        i21 = min(ti + 21, n_md - 1)
        bench = float(hs300[i21]) / float(hs300[i1]) - 1.0
        rets = []
        for j in top:
            g = int(g_rows[j])
            code = int(g_codes[j])
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

    # ============ 分行业描述（合格规则 + OOS score>=4 表现） ============
    industries_out = {}
    for ind in all_inds:
        stat = ind_stat[ind]
        o = {'qualified': ind in rules, 'samples': {
            t: {'total': stat[t]['total'], 'used': stat[t]['used'],
                'dropped_snapshot': stat[t]['dropped_snapshot'],
                'dropped_window': stat[t]['dropped_window']} for t in ('up', 'dn', 'osc')}}
        if ind in rules:
            o['rule'] = ind_rule_stats[ind]
            exs = []
            pos_n = neg_n = 0
            for _d, el in ind_oos.get(ind, []):
                exs.extend(el)
            for x in exs:
                if x > 0:
                    pos_n += 1
                elif x < 0:
                    neg_n += 1
            o['oos_ge4'] = {'n_obs': len(exs),
                            'mean_excess': float(np.mean(exs)) if exs else None,
                            'pos_obs': pos_n, 'neg_obs': neg_n}
        else:
            o['rule'] = None
            o['oos_ge4'] = None
        industries_out[ind] = o
    no_ind_stat = ind_stat.get(NO_IND)

    n_empty_days = sum(1 for r in dates_out if r['n_universe'] == 0)

    result = {
        'prereg_fp': PREREG_FP,
        'rule_count_per_industry': TOPK,
        'n_industries_total': len(all_inds),
        'n_qualified': len(qualified_inds),
        'qualified_inds': qualified_inds,
        'n_disqualified': len(disqualified_inds),
        'disqualified_inds': disqualified_inds,
        'no_industry_segments': (no_ind_stat if no_ind_stat else None),
        'industries': industries_out,
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
    oos = res['oos_summary']
    isum = res['is_summary']
    p = res['portfolio']['all']
    all_same = len(set(md5_list)) == 1
    fp_line = '一致' if all_same else '不一致（FAIL-safe：本次结果不作为定论）'
    L = []
    L.append('# 预注册实验 E-SELECT-4 结果报告（画像选股 trial#3：分行业启动前画像）')
    L.append('')
    L.append('- 实验名：PREREG_PORTRAIT_SELECT_E4_V1（指纹 %s，已逐字节校验通过）' % res['prereg_fp'])
    L.append('- 三跑 md5（result json 文件字节）：' + ' / '.join(md5_list) + ' —— %s' % fp_line)
    L.append('- 结论：**%s**（G3=%s, G1=%s, G2=%s）' % (
        res['verdict']['overall'], res['verdict']['G3'], res['verdict']['G1'], res['verdict']['G2']))
    L.append('')
    L.append('## 白话结论（发生了什么 / 对买卖意味着什么 / 要不要做事）')
    L.append('')
    L.append('1. **发生了什么**：把画像挖掘从全市场改为按申万一级行业分别进行——31 个行业里 %d 个通过合格闸'
             '（IS 窗上涨段有效样本与下跌段有效样本都至少 100 个），每个行业独立挖出自己的 5 条特征规则；'
             '评分也从"全市场前/后 1/3"改成"行业内前/后 1/3"，判据在 %d 个合格行业的合并池上按日度截面评价'
             '（IS %d 天 + OOS %d 天）。' % (res['n_qualified'], res['n_qualified'],
                                            res['n_dates_is'], res['n_dates_oos']))
    if res['verdict']['overall'] == 'PASS':
        L.append('2. **对买卖意味着什么**：主判据（样本外 OOS 合并池日度）通过预注册阈值——为真效应证据；'
                 '但本实验零交易权，PASS 不自动接线，生产化仍须 wfa_gate 新指纹 + 主人裁决。')
    else:
        L.append('2. **对买卖意味着什么**：主判据（样本外 OOS 合并池日度）未通过——分行业机制下效应仍达不到'
                 '预注册要求，该画像选股路线按预注册彻底归档（trial_budget=3 耗尽），画像定位回归描述+检索。')
    L.append('3. **要不要做事**：不需要。这是预注册研究实验，无论 PASS/FAIL 都不触发任何交易动作。')
    L.append('')
    L.append('## 合格行业闸（IS 窗 up_pre/dn_pre 有效样本 >=100/100）')
    L.append('')
    L.append('- 合格 %d 个：%s' % (res['n_qualified'], '、'.join(res['qualified_inds'])))
    L.append('- 剔除 %d 个：%s' % (res['n_disqualified'], '、'.join(res['disqualified_inds'])))
    if res['no_industry_segments']:
        ni = res['no_industry_segments']
        L.append('- 无行业归属段（stock_dim 静态快照未覆盖股，不参与任何行业挖掘/合并池）：up %d、dn %d、osc %d'
                 % (ni['up']['used'], ni['dn']['used'], ni['osc']['used']))
    L.append('')
    L.append('## 分行业规则摘要（每行业独立 Top5，方向 top=行业内前1/3、bottom=行业内后1/3）')
    L.append('')
    L.append('| 行业 | 规则（特征+方向） | OOS score>=4 观测数 | OOS score>=4 超额均值 | 正/负观测 |')
    L.append('|------|--------------------|---------------------|------------------------|-----------|')
    for ind in res['qualified_inds']:
        o = res['industries'][ind]
        rule_s = ', '.join('%s(%s)' % (r['feat'], r['dir']) for r in o['rule'])
        og = o['oos_ge4']
        L.append('| %s | %s | %d | %.4f | %d/%d |' % (
            ind, rule_s, og['n_obs'], og['mean_excess'] or 0, og['pos_obs'], og['neg_obs']))
    L.append('')
    L.append('## 判据核对（预注册冻结，一字未改；合并池口径）')
    L.append('')
    L.append('| 判据 | 内容 | 结果 |')
    L.append('|------|------|------|')
    L.append('| G3 (IS 前置) | IS 合并池 score>=4 组超额均值>0 | %s（均值=%.4f，非空日数=%d） |'
             % (isum['G3'], isum['score_ge4_mean_excess'] or 0, isum['n_days_ge4']))
    L.append('| G1 (OOS 主判据) | OOS 合并池 score>=4 序列均值>0 且 NW(lag=20) t>=2.5 且有效日数>=200 | %s（均值=%.4f，t=%.4f，有效日数=%d） |'
             % (oos['G1'], oos['score_ge4_mean_excess'] or 0, oos['nw_t_lag20'] or 0, oos['n_days_ge4']))
    L.append('| G2 (稳健) | OOS 桶 0..5 相邻差为正>=3/5 且 2025/2026 两自然年 score>=4 超额均>0 | %s（正差=%d/5；2025=%.4f，2026=%.4f） |'
             % (oos['G2'], oos['adjacent_positive'], oos['year2025_mean_excess'] or 0, oos['year2026_mean_excess'] or 0))
    bm = oos['bucket_means']
    L.append('')
    L.append('OOS 六桶跨日期均值（score 0..5，合格行业合并池）：%s' % ', '.join(
        ('%.4f' % bm[k]) if bm[k] is not None else '空' for k in range(6)))
    L.append('')
    L.append('## 环境分层呈现（描述性，不过滤；wf=PIT walk-forward 标签）')
    L.append('')
    L.append('| 市场状态 | score>=4 非空日数 | 合并池 score>=4 日度超额均值 |')
    L.append('|----------|-------------------|-------------------------------|')
    for lab in ('上涨趋势', '下跌趋势', '宽幅震荡', '窄幅震荡'):
        e = res['env_layers'][lab]
        L.append('| %s | %d | %.4f |' % (lab, e['n_days_ge4'], e['score_ge4_mean'] or 0))
    L.append('')
    L.append('## 关键数字')
    L.append('')
    L.append('- 测试日：%d 个市场交易日（IS %d + OOS %d），最后测试日 %d；空宇宙日 %d；wf 缺失日 %d'
             % (res['n_dates'], res['n_dates_is'], res['n_dates_oos'], res['last_test_date'],
                res['n_empty_universe_days'], res['n_wf_missing_days']))
    L.append('- 组合呈现（非判据，每月最后交易日入口 %d 个月，跨行业合并 score>=4 池 Top20，T+1 收盘买持 20 行，双边 0.15%%）：'
             '整体月均收益 %.4f、月均超额 %.4f、月胜率 %.2f%%'
             % (res['n_port_months'], p['mean_ret'] or 0, p['mean_excess'] or 0, (p['win_rate'] or 0) * 100))
    L.append('  - 入口跳过月：%s' % (','.join(str(d) for d in res['port_skip_months']) or '无'))
    for y, d in sorted(res['portfolio']['by_year'].items()):
        if d['n_months']:
            L.append('  - %s 年：%d 个月，月均收益 %.4f，月均超额 %.4f，胜率 %.2f%%'
                     % (y, d['n_months'], d['mean_ret'], d['mean_excess'], d['win_rate'] * 100))
    L.append('')
    L.append('## 边界声明（预注册口径，如实记录）')
    L.append('')
    L.append('- 本实验属**研究域，零交易权**；PASS 也不自动接线（生产接线仍须 wfa_gate 新指纹 + 主人裁决）。')
    L.append('- 行业归属 = stock_dim.industry_name（**静态快照非 PIT**，2026-10-18 建），存在行业变更历史偏差，预注册已声明。')
    L.append('- stock_dim 静态快照未覆盖部分 D 层股票：无行业归属段（up %d/dn %d/osc %d 有效）不参与任何行业挖掘与合并池，如实计数。'
             % (res['no_industry_segments']['up']['used'],
                res['no_industry_segments']['dn']['used'],
                res['no_industry_segments']['osc']['used']) if res['no_industry_segments']
             else '- 无行业归属段：0。')
    L.append('- 环境特征成员按行业内口径数学等价实现（行业>=10只时行业内全同值 -> 分位=该值 -> 恒满足），disc=0 不进 Top5。')
    L.append('- 特征值 NaN/哨兵一律"不满足"（不满足 != bottom）；行业内分位计算剔除 NaN（NaN 不参与分位）。')
    L.append('- 行业当日宇宙<10 只时该行业分位类特征判不满足（ma_align==3 仍按值判断，与行业分位无关）。')
    L.append('- 收盘<=30 元过滤使用 qfq 复权价近似名义价（口径近似，预注册已声明）。')
    L.append('- 幸存者偏差：D 层含 391 只窗口内退市/长停股，全库覆盖（5010 codes）。')
    L.append('- PORTRAIT 选股族 trial_budget=3 已耗；任一 FAIL -> 彻底归档，不再立项任何画像选股变体。')
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
        print('verdict: %s | G3=%s G1=%s G2=%s | qualified=%d' % (
            res['verdict']['overall'], res['verdict']['G3'], res['verdict']['G1'],
            res['verdict']['G2'], res['n_qualified']))
    else:
        write_report(res, all_md5)
        print('REPORT_WRITTEN %s' % REPORT_PATH)
        print('md5 runs=%s match=%s' % (','.join(all_md5), len(set(all_md5)) == 1))


if __name__ == '__main__':
    main()
