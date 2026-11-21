# -*- coding: utf-8 -*-
"""
technical_profile_v3_20261120.py —— v3 技术画像形态库统计 runner（只读，零交易权）

主人令 2026-11-20：对全市场 qfq 日线建立离散形态库（29 个 0/1 形态标签 + 7 个位置/换手
环境标签），与前瞻标签引擎（MFE/MAE 路径 → short_up/short_dn/mid_up/mid_dn/trend_up/
trend_dn）做全量共现统计：单形态统计表、位置×形态、大盘环境×形态、形态∧量价共振
（Top15 形态×6 量价类=90 组合）、民间认知证伪 5 条。

性质：纯描述性统计（事后视角、历史共现）；非信号、非预测主张、不产生任何交易动作；
     全部数据源 sqlite mode=ro 只读；不碰 git；不改任何既有文件（仅新建本脚本与两份报告）。

输入（只读）：
  1) qfq 日线  D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db  stock_daily_meta_qfq
     （code=6 位裸码 TEXT；date=TEXT 'YYYYMMDD'——SQL 数值比较必须 CAST，
       本脚本日期全部在 Python 侧转 int 后比较，规避 TEXT 比较陷阱）
  2) 换手率    D:/workbuddyspace/databases/fundamental_data.db          daily_basic
     （turnover_rate；20200102 起；每股 searchsorted 等值对齐）
  3) 大盘环境  D:/zcodesj/data/universe_portrait_regime.db 无市场表 →
     用「全市场截面当日等权涨跌幅 ret_mkt」自算（当日可知、无未来函数）；
     本地无中证全指，基准对照同样用全市场等权替代（如实声明）。

输出：
  D:/zcodesj/reports/TECHNICAL_PROFILE_V3_20261120.json（全量）
  D:/zcodesj/reports/TECHNICAL_PROFILE_SUMMARY_20261120.md（五部分+Top10 双榜开头）
  控制台：Top10 双榜各一行 + 证伪 5 条 + 共振 Top5

关键口径（全部写入 JSON meta declarations）：
  * MFE_k/MAE_k 用盘中 high/low 路径（含盘中极端），窗口=T+1..T+k；窗口数据不足→NaN 剔除。
  * trend_up/dn 的回撤/反弹运行极值以 close[T] 为种子（含基准日），路径=close[T]..close[T+20]。
  * 盈亏比双口径：pl_ratio_all=形态命中行平均 MFE_10/平均|MAE_10|（全样本口径）；
    pl_ratio_cond=命中行中 mid_up 样本平均 MFE_10 / mid_dn 样本平均|MAE_10|（任务书字面口径）。
  * pos250 复用 v2 公式：(close-min(close,250))/(max(close,250)-min(close,250))，窗口含当日、需满 250 根。
  * 换手分位：当日 turn 在自身最近 250 个交易日（含当日、250 值全部有效）内的分位；仅 2020+。
  * 样本 n<100 标「参考性有限」；Top10 双榜按 P(mid_up)/P(mid_dn) 排序且 n>=500。
"""

import json
import os
import sqlite3
import sys
import time
import traceback
import warnings
from collections import Counter

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

warnings.filterwarnings('ignore', category=RuntimeWarning)

try:
    import talib
    HAVE_TALIB = True
except Exception:
    HAVE_TALIB = False

QFQ_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FUND_DB = 'D:/workbuddyspace/databases/fundamental_data.db'
REPORTS_DIR = 'D:/zcodesj/reports'
OUT_JSON = REPORTS_DIR + '/TECHNICAL_PROFILE_V3_20261120.json'
OUT_MD = REPORTS_DIR + '/TECHNICAL_PROFILE_SUMMARY_20261120.md'

EPS = 1e-9
CHUNK = 50000
MIN_N_TOP = 500      # Top10 双榜最低样本
MIN_N_RESON = 200    # 共振表展示最低联合样本
MIN_N_NOTE = 100     # 「参考性有限」阈值

T0 = time.time()


def log(msg):
    print('[%7.1fs] %s' % (time.time() - T0, msg), flush=True)


def ro_connect(path):
    return sqlite3.connect('file:' + path + '?mode=ro', uri=True)


def code6(x):
    s = str(x).strip()
    digits = ''.join(ch for ch in s if ch.isdigit())
    return digits[:6] if digits else s[:6]


def obj_to_int64(col):
    try:
        return col.astype(np.int64)
    except (TypeError, ValueError):
        return np.array([int(str(x).replace('-', '').replace('/', '')[:8]) for x in col], np.int64)


def obj_to_f64(col):
    try:
        return col.astype(np.float64)
    except (TypeError, ValueError):
        out = np.empty(len(col), np.float64)
        for k, x in enumerate(col):
            out[k] = float(x) if x is not None else np.nan
        return out


def shift_bool(a, k):
    n = len(a)
    s = np.zeros(n, bool)
    if k < n:
        s[k:] = a[:n - k]
    return s


def shift_f(a, k):
    n = len(a)
    s = np.full(n, np.nan)
    if k < n:
        s[k:] = a[:n - k]
    return s


# ---------------- 形态清单（key, 组别, 中文名=量化判定规则） ----------------

PATTERNS = [
    ('big_yang',       'single_k',  '大阳线: 非十字星且 ret1>=5%'),
    ('mid_yang',       'single_k',  '中阳线: 非十字星且 2%<=ret1<5%'),
    ('small_yang',     'single_k',  '小阳线: 非十字星且 0<ret1<2%'),
    ('doji',           'single_k',  '十字星: |c-o|/(h-l+eps)<0.1 且 |ret1|<1%（先判十字星再分大小阳阴，互斥）'),
    ('big_yin',        'single_k',  '大阴线: 非十字星且 ret1<=-5%'),
    ('mid_yin',        'single_k',  '中阴线: 非十字星且 -5%<ret1<=-2%'),
    ('small_yin',      'single_k',  '小阴线: 非十字星且 -2%<ret1<0'),
    ('hammer',         'single_k',  '锤头线: 下影>2×实体 且 收盘在当根区间上1/3'),
    ('inv_hammer',     'single_k',  '倒锤头线: 上影>2×实体 且 收盘在当根区间下1/3'),
    ('red_three',      'combo_k',   '红三兵: 连续3阳(c>o) 且每根 ret1>1%'),
    ('three_crows',    'combo_k',   '三只乌鸦: 连续3阴(c<o) 且每根 ret1<-1%'),
    ('yang_yin_yang',  'combo_k',   '两阳夹一阴: 阳-阴-阳 且 中间收盘低于两侧'),
    ('yin_yang_yin',   'combo_k',   '两阴夹一阳: 阴-阳-阴 且 中间收盘高于两侧'),
    ('box_breakout',   'structure', '箱体突破: 收盘>前60日(不含当日)最高收盘×1.03 且前一日收盘<=该上沿'),
    ('box_hold2',      'structure', '突破站稳: 前一日为有效箱体突破 且 当日收盘仍>其上沿（独立确认标签）'),
    ('gap_up_unfilled', 'structure', '向上跳空未回补: open>昨收×1.02 且 当日low>昨收'),
    ('ma20_up_cross',  'structure', '上穿MA20: 收盘上穿MA20 且 MA20>=5日前MA20'),
    ('vp_up_expand',   'volprice',  '价涨量增: ret1>0 且 vr5>1.5'),
    ('vp_up_shrink',   'volprice',  '价涨量缩: ret1>0 且 vr5<0.7'),
    ('vp_dn_expand',   'volprice',  '价跌量增: ret1<0 且 vr5>1.5'),
    ('vp_dn_shrink',   'volprice',  '价跌量缩: ret1<0 且 vr5<0.7'),
    ('vp_flat_shrink', 'volprice',  '横盘缩量: |ret1|<0.5% 且 vr5<0.7'),
    ('vp_flat_expand', 'volprice',  '横盘放量: |ret1|<0.5% 且 vr5>1.5'),
    ('macd_golden',    'indicator', 'MACD金叉: DIF上穿DEA（talib 12/26/9）'),
    ('macd_death',     'indicator', 'MACD死叉: DIF下穿DEA'),
    ('rsi_overbought', 'indicator', 'RSI超买: RSI14>70'),
    ('rsi_oversold',   'indicator', 'RSI超卖: RSI14<30'),
    ('kdj_golden',     'indicator', 'KDJ金叉: K上穿D（9,3,3 手算）'),
    ('kdj_death',      'indicator', 'KDJ死叉: K下穿D'),
]
ENV_ROWS = [
    ('pos_low',     'position', 'pos250 低位: pos250<0.3'),
    ('pos_mid',     'position', 'pos250 中位: 0.3<=pos250<=0.7'),
    ('pos_high',    'position', 'pos250 高位: pos250>0.7'),
    ('turn_low',    'turnover', '换手分位低: <0.25'),
    ('turn_mid',    'turnover', '换手分位中: 0.25~0.75'),
    ('turn_high',   'turnover', '换手分位高: 0.75~0.95'),
    ('turn_extreme', 'turnover', '换手分位异常: >0.95'),
]
ALL_ROWS = PATTERNS + ENV_ROWS
NR = len(ALL_ROWS)          # 36
N_PAT = len(PATTERNS)       # 29

GROUP_CN = {'single_k': '单K', 'combo_k': '组合K', 'structure': '结构',
            'volprice': '量价', 'indicator': '指标', 'position': '位置', 'turnover': '换手'}
VC_NAMES = ['涨+放量', '涨+缩量', '跌+放量', '跌+缩量', '横盘缩量', '横盘放量']
POS_NAMES = ['低位(<0.3)', '中位(0.3~0.7)', '高位(>0.7)']
REG_NAMES = ['上涨', '下跌', '震荡']


# ---------------- 指标计算（talib 优先，缺失时同口径 fallback） ----------------

def _ema_seed_sma(x, period):
    n = len(x)
    out = np.full(n, np.nan)
    if n < period:
        return out
    out[period - 1] = float(x[:period].mean())
    k = 2.0 / (period + 1.0)
    for i in range(period, n):
        out[i] = x[i] * k + out[i - 1] * (1.0 - k)
    return out


def macd_fallback(c):
    e12 = _ema_seed_sma(c, 12)
    e26 = _ema_seed_sma(c, 26)
    dif = e12 - e26
    dea = np.full(len(c), np.nan)
    fi = np.flatnonzero(np.isfinite(dif))
    if len(fi) >= 9:
        s0 = int(fi[0])
        seg = dif[s0:s0 + 9]
        if np.isfinite(seg).all():
            dea[s0 + 8] = float(seg.mean())
            k = 2.0 / 10.0
            for i in range(s0 + 9, len(c)):
                dea[i] = dif[i] * k + dea[i - 1] * (1.0 - k)
    return dif, dea


def rsi_fallback(c):
    n = len(c)
    rsi = np.full(n, np.nan)
    if n < 15:
        return rsi
    d = np.diff(c)
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    au = float(up[:14].mean())
    ad = float(dn[:14].mean())
    rsi[14] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    for i in range(15, n):
        au = (au * 13.0 + up[i - 1]) / 14.0
        ad = (ad * 13.0 + dn[i - 1]) / 14.0
        rsi[i] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    return rsi


def macd_arrays(c):
    if HAVE_TALIB:
        dif, dea, _hist = talib.MACD(c, fastperiod=12, slowperiod=26, signalperiod=9)
        return np.asarray(dif, np.float64), np.asarray(dea, np.float64)
    return macd_fallback(c)


def rsi_array(c):
    if HAVE_TALIB:
        return np.asarray(talib.RSI(c, timeperiod=14), np.float64)
    return rsi_fallback(c)


def kdj_arrays(h, l, c):
    """KDJ 9,3,3 手算：RSV=(C-LLV9)/(HHV9-LLV9+eps)*100；K=(2K'+RSV)/3；D=(2D'+K)/3；
    初值 K[0]=D[0]=首个 RSV。"""
    n = len(c)
    k = np.full(n, np.nan)
    d = np.full(n, np.nan)
    if n < 9:
        return k, d
    llv = sliding_window_view(l, 9).min(axis=1)
    hhv = sliding_window_view(h, 9).max(axis=1)
    rsv = (c[8:] - llv) / (hhv - llv + EPS) * 100.0
    m2 = len(rsv)
    kk = np.empty(m2)
    dd = np.empty(m2)
    kp = rsv[0]
    dp = rsv[0]
    for j in range(m2):
        kp = (2.0 * kp + rsv[j]) / 3.0
        dp = (2.0 * dp + kp) / 3.0
        kk[j] = kp
        dd[j] = dp
    k[8:] = kk
    d[8:] = dd
    return k, d


# ---------------- pass1：大盘环境自算（SQL LAG → 按日等权 ret_mkt → MA20 → regime） ----------------

def load_market_regime():
    con = ro_connect(QFQ_DB)
    cur = con.execute(
        "SELECT date, COUNT(*), AVG(rr) FROM ("
        " SELECT date, 1.0*close/LAG(close) OVER (PARTITION BY code ORDER BY date) - 1.0 AS rr"
        " FROM stock_daily_meta_qfq WHERE close>0 AND volume>0"
        ") WHERE rr IS NOT NULL GROUP BY date")
    dlist, cnts, avgs = [], [], []
    for d, nn, av in cur.fetchall():
        dlist.append(int(str(d)[:8].replace('-', '')))
        cnts.append(int(nn))
        avgs.append(float(av))
    con.close()
    order = np.argsort(np.asarray(dlist, np.int64), kind='stable')
    dates = np.asarray(dlist, np.int64)[order]
    ret_mkt = np.asarray(avgs, np.float64)[order]
    row_cnt = np.asarray(cnts, np.int64)[order]
    U = len(dates)
    ma20 = np.full(U, np.nan)
    if U >= 20:
        cs = np.concatenate(([0.0], np.cumsum(ret_mkt)))
        ma20[19:] = (cs[20:] - cs[:-20]) / 20.0
    regime = np.full(U, 2, np.int8)
    regime[(ret_mkt > 0) & (ma20 > 0)] = 0
    regime[(ret_mkt < 0) & (ma20 < 0)] = 1
    return dates, regime, row_cnt, ret_mkt, ma20


def load_turn_map():
    """daily_basic (2020+) → {code6: (dates int64 升序, turnover f64)}，每股 searchsorted 等值对齐。"""
    con = ro_connect(FUND_DB)
    cur = con.execute("SELECT ts_code, trade_date, turnover_rate FROM daily_basic WHERE trade_date>='20200101'")
    buckets = {}
    while True:
        rows = cur.fetchmany(300000)
        if not rows:
            break
        for tc, td, tv in rows:
            g = buckets.setdefault(code6(tc), ([], []))
            g[0].append(int(str(td)[:8].replace('-', '')))
            g[1].append(float(tv) if tv is not None else np.nan)
    con.close()
    out = {}
    for k, (dl, vl) in buckets.items():
        d = np.asarray(dl, np.int64)
        o2 = np.argsort(d, kind='stable')
        out[k] = (d[o2], np.asarray(vl, np.float64)[o2])
    return out


# ---------------- 累积器 ----------------

def init_acc():
    return {
        'rows': 0, 'stocks': 0, 'bad_ohlc': 0, 'date_mismatch': 0, 'no_turn_stocks': 0,
        'date_min': None, 'date_max': None,
        'cnt_n': np.zeros(NR, np.int64),
        'cnt_short_valid': np.zeros(NR, np.int64),
        'cnt_short_up': np.zeros(NR, np.int64), 'cnt_short_dn': np.zeros(NR, np.int64),
        'cnt_mid_valid': np.zeros(NR, np.int64),
        'cnt_mid_up': np.zeros(NR, np.int64), 'cnt_mid_dn': np.zeros(NR, np.int64),
        'cnt_tr_valid': np.zeros(NR, np.int64),
        'cnt_tr_up': np.zeros(NR, np.int64), 'cnt_tr_dn': np.zeros(NR, np.int64),
        'sum_mfe3': np.zeros(NR), 'sum_mae3': np.zeros(NR),
        'sum_mfe10': np.zeros(NR), 'sum_mae10': np.zeros(NR), 'sum_absmae10': np.zeros(NR),
        'sum_mfe10_mu': np.zeros(NR), 'cnt_mfe10_mu': np.zeros(NR, np.int64),
        'sum_absmae10_md': np.zeros(NR), 'cnt_absmae10_md': np.zeros(NR, np.int64),
        'pos_n': np.zeros((3, NR), np.int64), 'pos_mu': np.zeros((3, NR), np.int64),
        'pos_md': np.zeros((3, NR), np.int64),
        'reg_n': np.zeros((3, NR), np.int64), 'reg_mu': np.zeros((3, NR), np.int64),
        'reg_md': np.zeros((3, NR), np.int64),
        'vc_n': np.zeros((6, NR), np.int64), 'vc_mu': np.zeros((6, NR), np.int64),
        'vc_md': np.zeros((6, NR), np.int64),
        'un_short_valid': 0, 'un_short_up': 0, 'un_short_dn': 0,
        'un_mid_valid': 0, 'un_mid_up': 0, 'un_mid_dn': 0,
        'un_tr_valid': 0, 'un_tr_up': 0, 'un_tr_dn': 0,
        'un_mfe3': 0.0, 'un_mae3': 0.0, 'un_mfe10': 0.0, 'un_mae10': 0.0, 'un_absmae10': 0.0,
        'un_f3': 0.0, 'un_f3n': 0, 'un_f10': 0.0, 'un_f10n': 0, 'un_f20': 0.0, 'un_f20n': 0,
        'un_pos_rows': np.zeros(3, np.int64), 'un_reg_rows': np.zeros(3, np.int64),
        'un_vc_rows': np.zeros(6, np.int64), 'un_turn_rows': np.zeros(4, np.int64),
        'pos_defined_rows': 0,
        'spec_f3': np.zeros(3, np.int64),   # 放量突破(box∧vr5>1.5): n / trend有效 / trend_up命中
        'spec_f4': np.zeros(3, np.int64),   # 天量(vr5>5): n / mid有效 / mid_dn命中
        'spec_f5': np.zeros(3, np.int64),   # 地量(vr5<0.4∧换手低分位): n / mid有效 / mid_up命中
    }


# ---------------- pass2：每股流式计算 + 累积 ----------------

def make_flush(ACC, uniq_dates, regime_d, turn_map):

    def flush(code_raw, rows):
        a = np.array(rows, dtype=object)
        m = len(a)
        ACC['rows'] += m
        o = obj_to_f64(a[:, 2])
        h = obj_to_f64(a[:, 3])
        l = obj_to_f64(a[:, 4])
        c = obj_to_f64(a[:, 5])
        v = obj_to_f64(a[:, 6])
        dint = obj_to_int64(a[:, 1])
        c6 = code6(code_raw)
        d0 = int(dint.min())
        d1 = int(dint.max())
        if ACC['date_min'] is None or d0 < ACC['date_min']:
            ACC['date_min'] = d0
        if ACC['date_max'] is None or d1 > ACC['date_max']:
            ACC['date_max'] = d1

        bad = (o <= 0) | (h <= 0) | (l <= 0) | (h < l)
        if bad.any():
            ACC['bad_ohlc'] += int(bad.sum())
            keep = ~bad
            o, h, l, c, v, dint = o[keep], h[keep], l[keep], c[keep], v[keep], dint[keep]
            m = len(c)
        if m < 1:
            return
        ACC['stocks'] += 1

        # ---- 大盘环境对齐（当日可知） ----
        di = np.searchsorted(uniq_dates, dint)
        di_c = np.minimum(di, len(uniq_dates) - 1)
        exact = uniq_dates[di_c] == dint
        ACC['date_mismatch'] += int((~exact).sum())
        reg = np.where(exact, regime_d[di_c], 2)

        # ---- 换手率对齐（searchsorted 等值；无映射→NaN） ----
        turn = np.full(m, np.nan)
        tt = turn_map.get(c6)
        if tt is None:
            ACC['no_turn_stocks'] += 1
        else:
            td_d, td_v = tt
            idx = np.searchsorted(td_d, dint)
            okk = idx < len(td_d)
            ok2 = okk.copy()
            ok2[okk] = td_d[idx[okk]] == dint[okk]
            turn[ok2] = td_v[idx[ok2]]

        with np.errstate(divide='ignore', invalid='ignore'):
            # ret1（股内前收；首行 NaN）
            pc = np.empty(m)
            pc[0] = np.nan
            pc[1:] = c[:-1]
            ret1 = c / pc - 1.0
            ar1 = np.abs(ret1)

            # ---- pos250（复用 v2 公式：close 口径，窗口含当日，需满 250 根） ----
            pos250 = np.full(m, np.nan)
            pos_bucket = np.full(m, -1, np.int8)
            if m >= 250:
                swc = sliding_window_view(c, 250)
                rmin = swc.min(axis=1)
                rmax = swc.max(axis=1)
                pos250[249:] = (c[249:] - rmin) / (rmax - rmin)
                pv = pos250[249:]
                pb = np.full(m - 249, -1, np.int8)
                pb[pv < 0.3] = 0
                pb[(pv >= 0.3) & (pv <= 0.7)] = 1
                pb[pv > 0.7] = 2
                pos_bucket[249:] = pb

            # ---- 换手分位（最近 250 个交易日含当日、250 值全部有效） ----
            turn_pct = np.full(m, np.nan)
            if m >= 250:
                swt = sliding_window_view(turn, 250)
                okw = np.isfinite(swt).all(axis=1)
                if okw.any():
                    cnt = (swt[okw] <= turn[249:][okw][:, None]).mean(axis=1)
                    turn_pct[249:][okw] = cnt

            # ---- MFE/MAE 前瞻路径标签 ----
            mfe3 = np.full(m, np.nan)
            mae3 = np.full(m, np.nan)
            if m >= 4:
                rmax3 = sliding_window_view(h, 3).max(axis=1)
                rmin3 = sliding_window_view(l, 3).min(axis=1)
                mfe3[:m - 3] = rmax3[1:] / c[:m - 3] - 1.0
                mae3[:m - 3] = rmin3[1:] / c[:m - 3] - 1.0
            mfe10 = np.full(m, np.nan)
            mae10 = np.full(m, np.nan)
            if m >= 11:
                rmax10 = sliding_window_view(h, 10).max(axis=1)
                rmin10 = sliding_window_view(l, 10).min(axis=1)
                mfe10[:m - 10] = rmax10[1:] / c[:m - 10] - 1.0
                mae10[:m - 10] = rmin10[1:] / c[:m - 10] - 1.0
            f3 = np.full(m, np.nan)
            if m >= 4:
                f3[:m - 3] = c[3:] / c[:m - 3] - 1.0
            f10 = np.full(m, np.nan)
            if m >= 11:
                f10[:m - 10] = c[10:] / c[:m - 10] - 1.0
            f20 = np.full(m, np.nan)
            mdd = np.full(m, np.nan)
            mrb = np.full(m, np.nan)
            if m >= 21:
                rows21 = sliding_window_view(c, 21)   # 行 t = close[t..t+20]
                f20[:m - 20] = rows21[:, -1] / rows21[:, 0] - 1.0
                rm = np.maximum.accumulate(rows21, axis=1)
                mdd[:m - 20] = ((rm - rows21) / rm).max(axis=1)
                rmn = np.minimum.accumulate(rows21, axis=1)
                mrb[:m - 20] = ((rows21 - rmn) / rmn).max(axis=1)

            sv = np.isfinite(mfe3)
            mv = np.isfinite(mfe10)
            tv = np.isfinite(f20)
            su = mfe3 >= 0.05
            sd_ = mae3 <= -0.05
            mu = mfe10 >= 0.10
            md_ = mae10 <= -0.10
            tu = (f20 >= 0.15) & (mdd <= 0.08)
            td_ = (f20 <= -0.15) & (mrb <= 0.08)

            # ---- 单K 9 类 ----
            body = np.abs(c - o)
            rng = h - l + EPS
            upsh = h - np.maximum(o, c)
            lwsh = np.minimum(o, c) - l
            doji = (body / rng < 0.1) & (ar1 < 0.01)
            nd = ~doji
            big_yang = nd & (ret1 >= 0.05)
            mid_yang = nd & (ret1 >= 0.02) & (ret1 < 0.05)
            small_yang = nd & (ret1 > 0) & (ret1 < 0.02)
            big_yin = nd & (ret1 <= -0.05)
            mid_yin = nd & (ret1 <= -0.02) & (ret1 > -0.05)
            small_yin = nd & (ret1 < 0) & (ret1 >= -0.02)
            hammer = (lwsh > 2.0 * body) & (c >= l + (h - l) * 2.0 / 3.0)
            inv_hammer = (upsh > 2.0 * body) & (c <= l + (h - l) / 3.0)

            # ---- 组合K 4 类 ----
            yang_b = c > o
            yin_b = c < o
            y3 = yang_b & (ret1 > 0.01)
            x3 = yin_b & (ret1 < -0.01)
            red_three = y3 & shift_bool(y3, 1) & shift_bool(y3, 2)
            three_crows = x3 & shift_bool(x3, 1) & shift_bool(x3, 2)
            c_p1 = shift_f(c, 1)
            c_p2 = shift_f(c, 2)
            yang_yin_yang = (yang_b & shift_bool(yin_b, 1) & shift_bool(yang_b, 2)
                             & (c_p1 < c) & (c_p1 < c_p2))
            yin_yang_yin = (yin_b & shift_bool(yang_b, 1) & shift_bool(yin_b, 2)
                            & (c_p1 > c) & (c_p1 > c_p2))

            # ---- 结构形态 ----
            c_prev = shift_f(c, 1)
            upper = np.full(m, np.nan)
            if m >= 60:
                pm60 = sliding_window_view(c, 60).max(axis=1)   # 行 j = c[j..j+59]
                upper[60:] = pm60[:m - 60] * 1.03
            upv = np.isfinite(upper) & np.isfinite(c_prev)
            box_breakout = (c > upper) & (c_prev <= upper) & upv
            upper_p1 = shift_f(upper, 1)
            box_hold2 = shift_bool(box_breakout, 1) & (c > upper_p1) & np.isfinite(upper_p1)
            gap_up_unfilled = np.isfinite(c_prev) & (o > c_prev * 1.02) & (l > c_prev)
            ma20 = np.full(m, np.nan)
            if m >= 20:
                ma20[19:] = sliding_window_view(c, 20).mean(axis=1)
            ma20_p1 = shift_f(ma20, 1)
            ma20_m5 = shift_f(ma20, 5)
            ma20_up_cross = (np.isfinite(ma20) & np.isfinite(ma20_p1) & np.isfinite(ma20_m5)
                             & (c > ma20) & (c_prev <= ma20_p1) & (ma20 >= ma20_m5))

            # ---- 量价交叉 6 类（vr5=vol/MA5(vol)） ----
            vr5 = np.full(m, np.nan)
            if m >= 5:
                ma5v = sliding_window_view(v, 5).mean(axis=1)
                vr5[4:] = v[4:] / ma5v
            # F4 天量专用口径（前5日不含当日）：主 vr5(含当日) 数学上界恒<5，阈值>5 不可达
            vr5_prev = np.full(m, np.nan)
            if m >= 6:
                ma5p = sliding_window_view(v, 5).mean(axis=1)   # 行 j = v[j..j+4]
                vr5_prev[5:] = v[5:] / ma5p[:m - 5]
            vp_up_expand = (ret1 > 0) & (vr5 > 1.5)
            vp_up_shrink = (ret1 > 0) & (vr5 < 0.7)
            vp_dn_expand = (ret1 < 0) & (vr5 > 1.5)
            vp_dn_shrink = (ret1 < 0) & (vr5 < 0.7)
            vp_flat_shrink = (ar1 < 0.005) & (vr5 < 0.7)
            vp_flat_expand = (ar1 < 0.005) & (vr5 > 1.5)

            # ---- 指标状态 6 类 ----
            dif, dea = macd_arrays(c)
            rsi = rsi_array(c)
            mdv = np.isfinite(dif) & np.isfinite(dea)
            dif_p1 = shift_f(dif, 1)
            dea_p1 = shift_f(dea, 1)
            macd_golden = mdv & shift_bool(mdv, 1) & (dif > dea) & (dif_p1 <= dea_p1)
            macd_death = mdv & shift_bool(mdv, 1) & (dif < dea) & (dif_p1 >= dea_p1)
            rsi_ob = rsi > 70
            rsi_os = rsi < 30
            kdj_k, kdj_d = kdj_arrays(h, l, c)
            kdv = np.isfinite(kdj_k) & np.isfinite(kdj_d)
            k_p1 = shift_f(kdj_k, 1)
            d_p1 = shift_f(kdj_d, 1)
            kdj_golden = kdv & shift_bool(kdv, 1) & (kdj_k > kdj_d) & (k_p1 <= d_p1)
            kdj_death = kdv & shift_bool(kdv, 1) & (kdj_k < kdj_d) & (k_p1 >= d_p1)

            # ---- 位置/换手环境标签 ----
            pos_low = pos_bucket == 0
            pos_mid = pos_bucket == 1
            pos_high = pos_bucket == 2
            t_low = turn_pct < 0.25
            t_mid = (turn_pct >= 0.25) & (turn_pct <= 0.75)
            t_high = (turn_pct > 0.75) & (turn_pct <= 0.95)
            t_ext = turn_pct > 0.95
            tb = np.full(m, -1, np.int8)
            fturn = np.isfinite(turn_pct)
            tb[fturn & (turn_pct < 0.25)] = 0
            tb[fturn & (turn_pct >= 0.25) & (turn_pct <= 0.75)] = 1
            tb[fturn & (turn_pct > 0.75) & (turn_pct <= 0.95)] = 2
            tb[fturn & (turn_pct > 0.95)] = 3

            # ---- 量价互斥六类（共振维度：横盘优先，涨>=0.5%/跌<=-0.5%） ----
            vc = np.zeros(m, np.int8)
            flatm = ar1 < 0.005
            upm = ret1 >= 0.005
            dnm = ret1 <= -0.005
            expm = vr5 > 1.5
            shrm = vr5 < 0.7
            vc[upm & shrm] = 2
            vc[upm & expm] = 1
            vc[dnm & shrm] = 4
            vc[dnm & expm] = 3
            vc[flatm & shrm] = 5
            vc[flatm & expm] = 6

        flags = [big_yang, mid_yang, small_yang, doji, big_yin, mid_yin, small_yin,
                 hammer, inv_hammer,
                 red_three, three_crows, yang_yin_yang, yin_yang_yin,
                 box_breakout, box_hold2, gap_up_unfilled, ma20_up_cross,
                 vp_up_expand, vp_up_shrink, vp_dn_expand, vp_dn_shrink,
                 vp_flat_shrink, vp_flat_expand,
                 macd_golden, macd_death, rsi_ob, rsi_os, kdj_golden, kdj_death,
                 pos_low, pos_mid, pos_high, t_low, t_mid, t_high, t_ext]
        assert len(flags) == NR
        P = np.stack(flags, axis=1)   # bool (m, 36)

        # ---- 累积 ----
        ACC['cnt_n'] += P.sum(axis=0)
        ACC['cnt_short_valid'] += P[sv].sum(axis=0)
        ACC['cnt_short_up'] += P[sv & su].sum(axis=0)
        ACC['cnt_short_dn'] += P[sv & sd_].sum(axis=0)
        ACC['cnt_mid_valid'] += P[mv].sum(axis=0)
        ACC['cnt_mid_up'] += P[mv & mu].sum(axis=0)
        ACC['cnt_mid_dn'] += P[mv & md_].sum(axis=0)
        ACC['cnt_tr_valid'] += P[tv].sum(axis=0)
        ACC['cnt_tr_up'] += P[tv & tu].sum(axis=0)
        ACC['cnt_tr_dn'] += P[tv & td_].sum(axis=0)
        if sv.any():
            X = P[sv].astype(np.float64)
            ACC['sum_mfe3'] += (X * mfe3[sv][:, None]).sum(axis=0)
            ACC['sum_mae3'] += (X * mae3[sv][:, None]).sum(axis=0)
        if mv.any():
            X = P[mv].astype(np.float64)
            ACC['sum_mfe10'] += (X * mfe10[mv][:, None]).sum(axis=0)
            ACC['sum_mae10'] += (X * mae10[mv][:, None]).sum(axis=0)
            ACC['sum_absmae10'] += (X * np.abs(mae10[mv])[:, None]).sum(axis=0)
        sel = mv & mu
        if sel.any():
            ACC['sum_mfe10_mu'] += (P[sel].astype(np.float64) * mfe10[sel][:, None]).sum(axis=0)
            ACC['cnt_mfe10_mu'] += P[sel].sum(axis=0)
        sel = mv & md_
        if sel.any():
            ACC['sum_absmae10_md'] += (P[sel].astype(np.float64)
                                       * np.abs(mae10[sel])[:, None]).sum(axis=0)
            ACC['cnt_absmae10_md'] += P[sel].sum(axis=0)

        for b in range(3):
            mk = pos_bucket == b
            if mk.any():
                ACC['pos_n'][b] += P[mk].sum(axis=0)
                ACC['pos_mu'][b] += P[mk & mu].sum(axis=0)
                ACC['pos_md'][b] += P[mk & md_].sum(axis=0)
        for b in range(3):
            mk = reg == b
            if mk.any():
                ACC['reg_n'][b] += P[mk].sum(axis=0)
                ACC['reg_mu'][b] += P[mk & mu].sum(axis=0)
                ACC['reg_md'][b] += P[mk & md_].sum(axis=0)
        for k in range(1, 7):
            mk = vc == k
            if mk.any():
                ACC['vc_n'][k - 1] += P[mk].sum(axis=0)
                ACC['vc_mu'][k - 1] += P[mk & mu].sum(axis=0)
                ACC['vc_md'][k - 1] += P[mk & md_].sum(axis=0)

        ACC['un_short_valid'] += int(sv.sum())
        ACC['un_short_up'] += int(su.sum())
        ACC['un_short_dn'] += int(sd_.sum())
        ACC['un_mid_valid'] += int(mv.sum())
        ACC['un_mid_up'] += int(mu.sum())
        ACC['un_mid_dn'] += int(md_.sum())
        ACC['un_tr_valid'] += int(tv.sum())
        ACC['un_tr_up'] += int(tu.sum())
        ACC['un_tr_dn'] += int(td_.sum())
        if sv.any():
            ACC['un_mfe3'] += float(mfe3[sv].sum())
            ACC['un_mae3'] += float(mae3[sv].sum())
        if mv.any():
            ACC['un_mfe10'] += float(mfe10[mv].sum())
            ACC['un_mae10'] += float(mae10[mv].sum())
            ACC['un_absmae10'] += float(np.abs(mae10[mv]).sum())
        fv = np.isfinite(f3)
        if fv.any():
            ACC['un_f3'] += float(f3[fv].sum())
            ACC['un_f3n'] += int(fv.sum())
        fv = np.isfinite(f10)
        if fv.any():
            ACC['un_f10'] += float(f10[fv].sum())
            ACC['un_f10n'] += int(fv.sum())
        fv = np.isfinite(f20)
        if fv.any():
            ACC['un_f20'] += float(f20[fv].sum())
            ACC['un_f20n'] += int(fv.sum())
        ACC['un_pos_rows'] += np.bincount(pos_bucket[pos_bucket >= 0], minlength=3)[:3]
        ACC['un_turn_rows'] += np.bincount(tb[tb >= 0], minlength=4)[:4]
        ACC['un_reg_rows'] += np.bincount(reg, minlength=3)[:3]
        ACC['un_vc_rows'] += np.bincount(vc, minlength=7)[1:7]
        ACC['pos_defined_rows'] += int((pos_bucket >= 0).sum())

        mk = box_breakout & (vr5 > 1.5)
        ACC['spec_f3'] += np.array([int(mk.sum()), int((mk & tv).sum()), int((mk & tu).sum())])
        mk = vr5_prev > 5
        ACC['spec_f4'] += np.array([int(mk.sum()), int((mk & mv).sum()), int((mk & md_).sum())])
        mk = (vr5 < 0.4) & (turn_pct < 0.25)
        ACC['spec_f5'] += np.array([int(mk.sum()), int((mk & mv).sum()), int((mk & mu).sum())])

        if ACC['stocks'] % 500 == 0:
            log('进度 %d 只 / %d 行' % (ACC['stocks'], ACC['rows']))

    return flush


# ---------------- 数值工具 ----------------

def prate(hit, valid):
    return round(float(hit) / valid, 6) if valid > 0 else None


def pmean(s, n):
    return round(float(s) / n, 6) if n > 0 else None


def fals_conclusion(diff_pp, n, higher_is_claim=True):
    if diff_pp is None:
        return '有效样本不足，无法得出确定性判断。'
    parts = []
    if diff_pp >= 1.0:
        parts.append('较全样本基线高 %+.2fpp' % diff_pp)
        parts.append('该口径下民间认知成立' if higher_is_claim else '该口径下民间认知不成立（反向）')
    elif diff_pp <= -1.0:
        parts.append('较全样本基线低 %+.2fpp' % diff_pp)
        parts.append('该口径下民间认知被证伪' if higher_is_claim else '该口径下民间认知反向成立')
    else:
        parts.append('与基线差 %+.2fpp（|差|<1，基本持平）' % diff_pp)
        parts.append('该口径下无优势，民间认知不成立')
    if n < MIN_N_NOTE:
        parts.append('样本参考性有限')
    return '，'.join(parts) + '。'


# ---------------- 统计装配 ----------------

def build_baselines(ACC):
    b = {
        'label_rates_unconditional': {
            'short_up': {'p': prate(ACC['un_short_up'], ACC['un_short_valid']),
                         'n_valid': int(ACC['un_short_valid'])},
            'short_dn': {'p': prate(ACC['un_short_dn'], ACC['un_short_valid']),
                         'n_valid': int(ACC['un_short_valid'])},
            'mid_up': {'p': prate(ACC['un_mid_up'], ACC['un_mid_valid']),
                       'n_valid': int(ACC['un_mid_valid'])},
            'mid_dn': {'p': prate(ACC['un_mid_dn'], ACC['un_mid_valid']),
                       'n_valid': int(ACC['un_mid_valid'])},
            'trend_up': {'p': prate(ACC['un_tr_up'], ACC['un_tr_valid']),
                         'n_valid': int(ACC['un_tr_valid'])},
            'trend_dn': {'p': prate(ACC['un_tr_dn'], ACC['un_tr_valid']),
                         'n_valid': int(ACC['un_tr_valid'])},
            'note': '分母=该标签窗口有效行数（MFE/MAE 路径窗口不足的行剔除）',
        },
        'window_equal_weight_returns': {
            'fwd3': {'mean': pmean(ACC['un_f3'], ACC['un_f3n']), 'n': int(ACC['un_f3n'])},
            'fwd10': {'mean': pmean(ACC['un_f10'], ACC['un_f10n']), 'n': int(ACC['un_f10n'])},
            'fwd20': {'mean': pmean(ACC['un_f20'], ACC['un_f20n']), 'n': int(ACC['un_f20n'])},
            'note': '全市场等权平均 close→close 收益（本地无中证全指，等权替代，见 meta 声明）',
        },
        'mfe_mae_unconditional': {
            'mfe3_mean': pmean(ACC['un_mfe3'], ACC['un_short_valid']),
            'mae3_mean': pmean(ACC['un_mae3'], ACC['un_short_valid']),
            'mfe10_mean': pmean(ACC['un_mfe10'], ACC['un_mid_valid']),
            'mae10_mean': pmean(ACC['un_mae10'], ACC['un_mid_valid']),
            'absmae10_mean': pmean(ACC['un_absmae10'], ACC['un_mid_valid']),
            'n_short_window': int(ACC['un_short_valid']),
            'n_mid_window': int(ACC['un_mid_valid']),
        },
    }
    return b


PMAP_CN = {k: cn for k, _, cn in PATTERNS}


def build_pattern_rows(ACC, base):
    rows = []
    base_mu = base['label_rates_unconditional']['mid_up']['p']
    base_md = base['label_rates_unconditional']['mid_dn']['p']
    for i, (key, grp, cn) in enumerate(ALL_ROWS):
        n = int(ACC['cnt_n'][i])
        sv = int(ACC['cnt_short_valid'][i])
        mvv = int(ACC['cnt_mid_valid'][i])
        tvv = int(ACC['cnt_tr_valid'][i])
        p_mu = prate(ACC['cnt_mid_up'][i], mvv)
        p_md = prate(ACC['cnt_mid_dn'][i], mvv)
        upl_u = (round((p_mu - base_mu) * 100, 4)
                 if (p_mu is not None and base_mu is not None) else None)
        upl_d = (round((p_md - base_md) * 100, 4)
                 if (p_md is not None and base_md is not None) else None)
        m1 = (ACC['sum_mfe10_mu'][i] / ACC['cnt_mfe10_mu'][i]) if ACC['cnt_mfe10_mu'][i] else None
        m2 = (ACC['sum_absmae10_md'][i] / ACC['cnt_absmae10_md'][i]) if ACC['cnt_absmae10_md'][i] else None
        pl_cond = round(m1 / m2, 4) if (m1 and m2) else None
        if 0 < n < MIN_N_NOTE:
            note = '参考性有限'
        elif n == 0:
            note = '无样本'
        else:
            note = ''
        rows.append({
            'pattern': key, 'group': grp, 'group_cn': GROUP_CN[grp], 'name_cn': cn, 'n': n,
            'p_short_up': prate(ACC['cnt_short_up'][i], sv),
            'p_short_dn': prate(ACC['cnt_short_dn'][i], sv),
            'p_mid_up': p_mu,
            'p_mid_dn': p_md,
            'p_trend_up': prate(ACC['cnt_tr_up'][i], tvv),
            'p_trend_dn': prate(ACC['cnt_tr_dn'][i], tvv),
            'mfe3_mean': pmean(ACC['sum_mfe3'][i], sv),
            'mae3_mean': pmean(ACC['sum_mae3'][i], sv),
            'mfe10_mean': pmean(ACC['sum_mfe10'][i], mvv),
            'mae10_mean': pmean(ACC['sum_mae10'][i], mvv),
            'absmae10_mean': pmean(ACC['sum_absmae10'][i], mvv),
            'pl_ratio_all': (round(float(ACC['sum_mfe10'][i]) / float(ACC['sum_absmae10'][i]), 4)
                             if ACC['sum_absmae10'][i] > 0 else None),
            'pl_ratio_cond': pl_cond,
            'uplift_mid_up_pp': upl_u,
            'uplift_mid_dn_pp': upl_d,
            'note': note,
        })
    return rows


def build_cross(ACC, rows, names, n_arr, mu_arr, md_arr):
    pmap = {r['pattern']: r for r in rows}
    out = []
    for i in range(N_PAT):
        key = PATTERNS[i][0]
        base_mu = pmap[key]['p_mid_up']
        base_md = pmap[key]['p_mid_dn']
        for b, bn in enumerate(names):
            n = int(n_arr[b][i])
            p_mu = prate(mu_arr[b][i], n)
            p_md = prate(md_arr[b][i], n)
            out.append({
                'pattern': key, 'bucket': bn, 'n': n,
                'p_mid_up': p_mu, 'p_mid_dn': p_md,
                'diff_mid_up_pp': (round((p_mu - base_mu) * 100, 4)
                                   if (p_mu is not None and base_mu is not None) else None),
                'diff_mid_dn_pp': (round((p_md - base_md) * 100, 4)
                                   if (p_md is not None and base_md is not None) else None),
            })
    return out


def build_resonance(ACC, rows):
    pmap = {r['pattern']: r for r in rows}
    order15 = sorted(range(N_PAT), key=lambda i: (-int(ACC['cnt_n'][i]), PATTERNS[i][0]))[:15]
    out = []
    for i in order15:
        key = PATTERNS[i][0]
        p_single = pmap[key]['p_mid_up']
        p_single_d = pmap[key]['p_mid_dn']
        for k in range(6):
            n = int(ACC['vc_n'][k][i])
            p_j = prate(ACC['vc_mu'][k][i], n)
            p_jd = prate(ACC['vc_md'][k][i], n)
            out.append({
                'pattern': key, 'vol_class': VC_NAMES[k], 'n_joint': n,
                'p_mid_up_joint': p_j, 'p_mid_dn_joint': p_jd,
                'p_mid_up_single': p_single, 'p_mid_dn_single': p_single_d,
                'diff_mid_up_pp': (round((p_j - p_single) * 100, 4)
                                   if (p_j is not None and p_single is not None) else None),
                'diff_mid_dn_pp': (round((p_jd - p_single_d) * 100, 4)
                                   if (p_jd is not None and p_single_d is not None) else None),
                'note': '参考性有限' if 0 < n < MIN_N_NOTE else ('无样本' if n == 0 else ''),
            })
    return out, order15


def build_falsification(ACC, base, rows):
    pmap = {r['pattern']: r for r in rows}
    b_mu = base['label_rates_unconditional']['mid_up']['p']
    b_md = base['label_rates_unconditional']['mid_dn']['p']
    b_tu = base['label_rates_unconditional']['trend_up']['p']

    def mk(fid, claim, metric, n, p, baseline, note=''):
        diff = (round((p - baseline) * 100, 4)
                if (p is not None and baseline is not None) else None)
        return {'id': fid, 'claim': claim, 'metric': metric, 'n': int(n), 'p': p,
                'baseline_unconditional': baseline, 'diff_pp': diff,
                'note': note,
                'conclusion': fals_conclusion(diff, int(n))}

    s3 = ACC['spec_f3']
    s4 = ACC['spec_f4']
    s5 = ACC['spec_f5']
    return [
        mk('F1', '民间认知：放量上涨（价涨量增）后续更容易大涨',
           'P(mid_up | 价涨量增)', pmap['vp_up_expand']['n'], pmap['vp_up_expand']['p_mid_up'], b_mu),
        mk('F2', '民间认知：缩量阴跌（价跌量缩）后续继续大跌',
           'P(mid_dn | 价跌量缩)', pmap['vp_dn_shrink']['n'], pmap['vp_dn_shrink']['p_mid_dn'], b_md),
        mk('F3', '民间认知：放量突破箱体后走出大趋势',
           'P(trend_up | 箱体突破 ∧ vr5>1.5)', int(s3[0]), prate(s3[2], s3[1]), b_tu),
        mk('F4', '民间认知：天量（当日成交量>5倍前5日均量）之后大跌',
           'P(mid_dn | vol/MA5(前5日,不含当日)>5)', int(s4[0]), prate(s4[2], s4[1]), b_md,
           note='主口径 vr5=vol/MA5(vol,含当日) 数学上界恒<5（vr5=5v/(v+S)，S=前4日量和），'
                '阈值 vr5>5 在主口径下不可达；本条按任务意图采用不含当日口径'
                '（等价：当日量>5倍前5日均量）'),
        mk('F5', '民间认知：地量见地价（vr5<0.4 且换手低分位）随后反弹',
           'P(mid_up | vr5<0.4 ∧ 换手分位<0.25)', int(s5[0]), prate(s5[2], s5[1]), b_mu),
    ]


def build_tops(rows):
    pat = rows[:N_PAT]
    cand_u = [r for r in pat if r['n'] >= MIN_N_TOP and r['p_mid_up'] is not None]
    cand_u.sort(key=lambda r: (-r['p_mid_up'], -r['n'], r['pattern']))
    cand_d = [r for r in pat if r['n'] >= MIN_N_TOP and r['p_mid_dn'] is not None]
    cand_d.sort(key=lambda r: (-r['p_mid_dn'], -r['n'], r['pattern']))
    return cand_u[:10], cand_d[:10]


def build_meta(ACC, dates, regime_d, mkt_rows):
    decl = [
        '基准替代声明：本地无中证全指，全部基线（标签无条件发生率、T+3/T+10/T+20 窗口收益）'
        '使用全市场等权平均替代（非市值加权指数口径），如实声明。',
        'MFE/MAE 声明：用后续 k 日盘中 high/low 路径（含盘中极端），窗口=T+1..T+k；'
        '窗口数据不足→NaN 剔除，各标签分母=窗口有效行数。',
        'trend_up/dn 口径声明：回撤/反弹的运行极值以 close[T] 为种子（含基准日），'
        '路径=close[T]..close[T+20]；回撤=任一时刻相对此前区间最高收盘的跌幅，'
        '反弹=相对此前区间最低收盘的涨幅。',
        '性质声明：本报告全部为描述性统计（事后视角、历史共现），非信号、非预测主张、'
        '不产生任何交易动作（零交易权）。',
        '宇宙声明：未做宇宙过滤——qfq 库全部有效行（close>0 AND volume>0），'
        '含科创/北交所/ST/退市股（%d 只）。' % ACC['stocks'],
        '换手率声明：daily_basic 仅 20200102 起，此前行换手率缺失→NaN；'
        '换手分位要求最近 250 个交易日（含当日）250 值全部有效。',
        'vr5 口径声明：主 vr5=vol/MA5(vol)（MA5 含当日，与 v2 一致）；该口径下 vr5=5v/(v+S)'
        '（S=前4日量和）数学上界恒<5，故 F4「天量」按任务意图改用 vol/MA5(前5日,不含当日)>5'
        '（等价当日量>5倍前5日均量）。',
        '样本重叠声明：MFE/MAE 前瞻窗口跨行重叠，统计存在自相关，pp 差不等于可交易超额；'
        '同一行可同时命中多个形态，各形态统计非独立样本。',
        '样本门槛：n<100 标「参考性有限」；Top10 双榜要求 n>=500；'
        '共振表展示要求联合样本 n>=%d。' % MIN_N_RESON,
        '大盘环境声明：ret_mkt=当日全部有效行 ret1 等权均值；trend_mkt=MA20(ret_mkt)'
        '（按交易日序列回看）；上涨(ret_mkt>0 且 MA20>0)/下跌(ret_mkt<0 且 MA20<0)/'
        '震荡(其余，含 MA20 未成形)；当日可知、无未来函数。',
    ]
    meta = {
        'report': 'TECHNICAL_PROFILE_V3_20261120',
        'script': 'analysis/technical_profile_v3_20261120.py',
        'purpose': 'v3 技术画像形态库统计——描述性统计（事后视角、历史共现），非信号非预测，零交易权',
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'data_sources': {
            'qfq_daily': QFQ_DB + ' stock_daily_meta_qfq (close>0 AND volume>0, ORDER BY code,date)',
            'turnover': FUND_DB + ' daily_basic (turnover_rate, 20200102 起, 每股 searchsorted 等值对齐)',
            'market_regime': '本地自算（universe_portrait_regime.db 无市场表）：'
                             '全市场截面当日等权 ret_mkt + MA20 → 上涨/下跌/震荡',
        },
        'sample': {
            'rows_valid': int(ACC['rows']),
            'stocks': int(ACC['stocks']),
            'n_trade_dates': int(len(dates)),
            'date_min': ACC['date_min'] if ACC['date_min'] is not None
            else (int(dates.min()) if len(dates) else None),
            'date_max': ACC['date_max'] if ACC['date_max'] is not None
            else (int(dates.max()) if len(dates) else None),
            'market_rows_with_prev_close': int(mkt_rows.sum()),
            'regime_day_counts': {'up': int((regime_d == 0).sum()),
                                  'down': int((regime_d == 1).sum()),
                                  'flat': int((regime_d == 2).sum())},
            'regime_row_counts': {'up': int(ACC['un_reg_rows'][0]),
                                  'down': int(ACC['un_reg_rows'][1]),
                                  'flat': int(ACC['un_reg_rows'][2])},
            'pos_defined_rows': int(ACC['pos_defined_rows']),
            'turn_defined_rows': int(ACC['un_turn_rows'].sum()),
            'volclass_row_counts': {VC_NAMES[k]: int(ACC['un_vc_rows'][k]) for k in range(6)},
            'bad_ohlc_dropped': int(ACC['bad_ohlc']),
            'date_regime_mismatch_rows': int(ACC['date_mismatch']),
            'stocks_without_turnover': int(ACC['no_turn_stocks']),
        },
        'declarations': decl,
        'formula_notes': {
            'pos250': '复用 v2 (feature_attr_v2_20261120.py)：pos250=(close-min(close,250))/'
                      '(max(close,250)-min(close,250))，窗口含当日、需满 250 根',
            'turn_pct': '当日 turn 在自身最近 250 个交易日（含当日、250 值全部有效）内的分位'
                        '=（窗口内<=当日值的个数）/250；daily_basic 2020+ 才有，缺失 NaN',
            'vr5': 'vr5 = vol / MA5(vol)（MA5 含当日）',
            'vr5_prev': 'F4 天量口径：vol / mean(vol[i-5..i-1])（前5日不含当日），需 m>=6',
            'kdj': 'KDJ 9,3,3 手算：RSV=(C-LLV9)/(HHV9-LLV9+eps)*100；K=(2K+RSV)/3；'
                   'D=(2D+K)/3；初值 K[0]=D[0]=首个 RSV',
            'box_upper': '上沿 = 前 60 日（不含当日）最高收盘 × 1.03；突破=当日收盘>上沿 且 '
                         '前一日收盘<=上沿；box_hold2=前一日为有效突破 且 当日收盘仍>其上沿',
            'regime': 'pass1 SQL: SELECT date,COUNT(*),AVG(rr) FROM (SELECT date, '
                      '1.0*close/LAG(close) OVER (PARTITION BY code ORDER BY date)-1.0 AS rr '
                      'FROM stock_daily_meta_qfq WHERE close>0 AND volume>0) '
                      'WHERE rr IS NOT NULL GROUP BY date',
        },
        'pl_ratio_definitions': {
            'pl_ratio_all': '形态命中行平均 MFE_10 / 平均|MAE_10|（全样本口径）',
            'pl_ratio_cond': '形态命中行中 mid_up 样本平均 MFE_10 / mid_dn 样本平均|MAE_10|'
                             '（任务书字面口径；Top10 双榜与控制台展示此口径）',
        },
        'versions': {
            'python': sys.version.split()[0],
            'numpy': np.__version__,
            'talib': (__import__('talib').__version__ if HAVE_TALIB else None),
            'sqlite': sqlite3.sqlite_version,
        },
    }
    return meta


# ---------------- 输出格式化 ----------------

def fmt_pct(x, nd=2):
    return ('%.*f%%' % (nd, x * 100)) if x is not None else '—'


def fmt_pp(x):
    return ('%+.2f' % x) if x is not None else '—'


def fmt_r(x, nd=2):
    return ('%.*f' % (nd, x)) if x is not None else '—'


def fmt_int(x):
    return '{:,}'.format(int(x)) if x is not None else '—'


def esc(s):
    """MD 表格单元格内 '|' 转义（形态名/口径串含 |c-o| 等会破坏表格列）。"""
    return str(s).replace('|', '\\|')


def build_md(meta, base, rows, cross_pos, cross_reg, res_rows, order15, fals,
             top_up, top_dn, overlap, only_up, only_dn):
    L = []
    A = L.append
    b_mu = base['label_rates_unconditional']['mid_up']['p']
    b_md = base['label_rates_unconditional']['mid_dn']['p']
    A('# 技术画像形态库统计报告 V3（2026-11-20）')
    A('')
    A('> **定位**：描述性统计（事后视角、历史共现）——非信号、非预测主张、零交易权。'
      '所有结论均标注样本量 n 与相对全样本无条件基线的差（pp）；样本 n<100 标「参考性有限」。')
    A('> **基准声明**：本地无中证全指，全部基线使用**全市场等权平均**替代（非市值加权指数口径）。')
    A('> **样本**：%s 只 / %s 行有效日线（close>0 且 volume>0），%s ~ %s，共 %s 个交易日。'
      % (fmt_int(meta['sample']['stocks']), fmt_int(meta['sample']['rows_valid']),
         meta['sample']['date_min'], meta['sample']['date_max'],
         fmt_int(meta['sample']['n_trade_dates'])))
    A('')
    A('## Top10 高胜率上涨形态（n≥%d，按 P(mid_up) 降序）' % MIN_N_TOP)
    A('')
    A('| # | 形态 | 组别 | n | P(mid_up) | 盈亏比(条件口径) | 较基线(pp) |')
    A('|---|---|---|---|---|---|---|')
    for i, r in enumerate(top_up, 1):
        A('| %d | %s | %s | %s | %s | %s | %s |'
          % (i, esc(r['name_cn']), r['group_cn'], fmt_int(r['n']), fmt_pct(r['p_mid_up']),
             fmt_r(r['pl_ratio_cond']), fmt_pp(r['uplift_mid_up_pp'])))
    A('')
    A('全样本无条件基线 P(mid_up)=%s（n=%s）。盈亏比(条件口径)=命中行中 mid_up 样本平均 MFE_10 '
      '/ mid_dn 样本平均|MAE_10|。' % (fmt_pct(b_mu),
                                       fmt_int(base['label_rates_unconditional']['mid_up']['n_valid'])))
    A('')
    A('## Top10 高风险下跌形态（n≥%d，按 P(mid_dn) 降序）' % MIN_N_TOP)
    A('')
    A('| # | 形态 | 组别 | n | P(mid_dn) | 盈亏比(条件口径) | 较基线(pp) |')
    A('|---|---|---|---|---|---|---|')
    for i, r in enumerate(top_dn, 1):
        A('| %d | %s | %s | %s | %s | %s | %s |'
          % (i, esc(r['name_cn']), r['group_cn'], fmt_int(r['n']), fmt_pct(r['p_mid_dn']),
             fmt_r(r['pl_ratio_cond']), fmt_pp(r['uplift_mid_dn_pp'])))
    A('')
    A('全样本无条件基线 P(mid_dn)=%s（n=%s）。' % (fmt_pct(b_md),
                                                 fmt_int(base['label_rates_unconditional']['mid_dn']['n_valid'])))
    A('')
    A('---')
    A('')
    A('## 一、形态量化定义手册（判定规则简表）')
    A('')
    A('**前瞻标签引擎**（窗口数据不足→NaN 剔除；MFE/MAE 用盘中 high/low 路径，含盘中极端）：')
    A('')
    A('- MFE_k = max(high[T+1..T+k])/close[T]−1；MAE_k = min(low[T+1..T+k])/close[T]−1')
    A('- short_up: MFE_3≥5%；short_dn: MAE_3≤−5%')
    A('- mid_up: MFE_10≥10%；mid_dn: MAE_10≤−10%')
    A('- trend_up: close[T+20]/close[T]−1≥15% 且路径（以 close[T] 为种子）最大回撤≤8%')
    A('- trend_dn: close[T+20]/close[T]−1≤−15% 且路径最大反弹≤8%')
    A('- 基线：各标签全市场无条件发生率（分母=窗口有效行）+ 全市场等权平均 fwd3/fwd10/fwd20')
    A('')
    A('**29 个离散形态（全部程序化判定，T 日 0/1）**：')
    A('')
    A('| key | 组别 | 判定规则 |')
    A('|---|---|---|')
    for key, grp, cn in PATTERNS:
        A('| %s | %s | %s |' % (key, GROUP_CN[grp], esc(cn)))
    A('')
    A('**交叉维度**：')
    A('')
    A('- 位置 pos250 三档：<0.3 低位 / 0.3~0.7 中位 / >0.7 高位'
      '（复用 v2 公式，窗口含当日、需满 250 根）')
    A('- 换手分位四档：<0.25 低 / 0.25~0.75 中 / 0.75~0.95 高 / >0.95 异常'
      '（自身最近 250 个交易日分位；daily_basic 2020+，缺失 NaN）')
    A('- 大盘环境三档：上涨(ret_mkt>0 且 MA20>0) / 下跌(ret_mkt<0 且 MA20<0) / 震荡(其余)；'
      'ret_mkt=当日全市场等权涨跌幅，MA20 为其 20 日均线（当日可知，无未来函数）')
    A('- 量价互斥六类（共振维度）：横盘优先(|ret1|<0.5%)，涨 ret1≥0.5% / 跌 ret1≤−0.5%，'
      '× vr5<0.7 缩量 / vr5>1.5 放量（vr5=vol/MA5(vol)，MA5 含当日，与 v2 一致，该口径数学上界<5）；'
      '注意量价 6 形态本身按任务书口径（ret1>0/<0/|ret1|<0.5%）判定，与共振互斥类在 0~0.5% 边界略有差异')
    A('- 天量口径说明：主 vr5（含当日）上界恒<5，证伪 F4 的天量阈值按任务意图采用 '
      'vol/MA5(前5日,不含当日)>5（等价当日量>5倍前5日均量）')
    A('')
    A('---')
    A('')
    A('## 二、单形态统计表（全量 %d 行；P=该形态命中行上各前瞻标签发生率）' % NR)
    A('')
    A('| 形态 | 组别 | n | P(短涨) | P(短跌) | P(中涨) | P(中跌) | P(趋涨) | P(趋跌) | '
      'MFE10均 | MAE10均 | 盈亏比(全) | 盈亏比(条件) | Δ中涨pp | Δ中跌pp | 备注 |')
    A('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for r in rows:
        A('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |'
          % (esc(r['name_cn']), r['group_cn'], fmt_int(r['n']),
             fmt_pct(r['p_short_up']), fmt_pct(r['p_short_dn']),
             fmt_pct(r['p_mid_up']), fmt_pct(r['p_mid_dn']),
             fmt_pct(r['p_trend_up']), fmt_pct(r['p_trend_dn']),
             fmt_pct(r['mfe10_mean']), fmt_pct(r['mae10_mean']),
             fmt_r(r['pl_ratio_all']), fmt_r(r['pl_ratio_cond']),
             fmt_pp(r['uplift_mid_up_pp']), fmt_pp(r['uplift_mid_dn_pp']),
             r['note'] or ''))
    A('')
    A('注：P(短涨)=P(short_up)，P(趋涨)=P(trend_up)；MFE10均/MAE10均=形态命中行平均'
      ' MFE_10/MAE_10（10 日窗口有效行）；盈亏比(全)=命中行平均 MFE_10/平均|MAE_10|；'
      'Δpp=相对全样本无条件基线的差。')
    A('')
    A('---')
    A('')
    A('## 三、高胜率组合库')
    A('')
    A('### 3.1 形态∧量价共振（Top15 大样本形态 × 6 量价类 = 90 组合；'
      '下表为联合样本 n≥%d 中 P(mid_up) 较单独形态提升最大的 Top20）' % MIN_N_RESON)
    A('')
    A('| 形态 | 量价类 | n(联合) | P(中涨)联合 | P(中涨)单独 | 提升(pp) | P(中跌)联合 | 提升(pp) |')
    A('|---|---|---|---|---|---|---|---|')
    res_sorted = [x for x in res_rows
                  if x['n_joint'] >= MIN_N_RESON and x['diff_mid_up_pp'] is not None]
    res_sorted.sort(key=lambda x: (-x['diff_mid_up_pp'], -x['n_joint'],
                                   x['pattern'], x['vol_class']))
    for x in res_sorted[:20]:
        A('| %s | %s | %s | %s | %s | %s | %s | %s |'
          % (esc(PMAP_CN[x['pattern']]), x['vol_class'], fmt_int(x['n_joint']),
             fmt_pct(x['p_mid_up_joint']), fmt_pct(x['p_mid_up_single']),
             fmt_pp(x['diff_mid_up_pp']), fmt_pct(x['p_mid_dn_joint']),
             fmt_pp(x['diff_mid_dn_pp'])))
    A('')
    A('注：量价形态 ∧ 自身对应量价类 为恒等命中（提升≈0）；Top15 形态按样本量 n 选取。')
    A('')
    A('### 3.2 位置(pos250)×形态：P(mid_up) 三档对比（Top15 大样本形态）')
    A('')
    A('| 形态 | n(总) | 低位 | 中位 | 高位 |')
    A('|---|---|---|---|---|')
    pos_by = {}
    for x in cross_pos:
        pos_by.setdefault(x['pattern'], []).append(x)
    nmap = {r['pattern']: r['n'] for r in rows}
    for i in order15:
        key = PATTERNS[i][0]
        cells = []
        for x in sorted(pos_by[key], key=lambda y: y['bucket']):
            cells.append('%s (n=%s)' % (fmt_pct(x['p_mid_up']), fmt_int(x['n'])))
        A('| %s | %s | %s |' % (esc(PMAP_CN[key]), fmt_int(nmap[key]), ' | '.join(cells)))
    A('')
    A('### 3.3 大盘环境×形态：P(mid_up) 三档对比（Top15 大样本形态）')
    A('')
    A('| 形态 | n(总) | 上涨市 | 下跌市 | 震荡市 |')
    A('|---|---|---|---|---|')
    reg_by = {}
    for x in cross_reg:
        reg_by.setdefault(x['pattern'], []).append(x)
    for i in order15:
        key = PATTERNS[i][0]
        cells = []
        for x in sorted(reg_by[key], key=lambda y: y['bucket']):
            cells.append('%s (n=%s)' % (fmt_pct(x['p_mid_up']), fmt_int(x['n'])))
        A('| %s | %s | %s |' % (esc(PMAP_CN[key]), fmt_int(nmap[key]), ' | '.join(cells)))
    A('')
    A('---')
    A('')
    A('## 四、归因总结（上涨/下跌共性 + 边界）')
    A('')
    grp_cnt_u = Counter(GROUP_CN[r['group']] for r in top_up)
    grp_cnt_d = Counter(GROUP_CN[r['group']] for r in top_dn)

    def grp_str(cnt):
        items = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
        return '、'.join('%s×%d' % (g, c) for g, c in items) if items else '无'

    A('**上涨端共性（Top10 高胜率上涨形态）**：')
    A('')
    A('- 组别分布：%s。' % grp_str(grp_cnt_u))
    upl = [r['uplift_mid_up_pp'] for r in top_up if r['uplift_mid_up_pp'] is not None]
    pus = [r['p_mid_up'] for r in top_up if r['p_mid_up'] is not None]
    if pus:
        A('- P(mid_up) 区间 %.2f%%~%.2f%%（全样本基线 %s），平均较基线提升 %+.2fpp。'
          % (min(pus) * 100, max(pus) * 100, fmt_pct(b_mu),
             (sum(upl) / len(upl)) if upl else 0.0))
    mf = [r['mfe10_mean'] for r in top_up if r['mfe10_mean'] is not None]
    ma = [r['absmae10_mean'] for r in top_up if r['absmae10_mean'] is not None]
    if mf and ma:
        A('- 命中行平均 MFE_10=%.2f%% / 平均|MAE_10|=%.2f%%（全样本 %s / %s）'
          % (sum(mf) / len(mf) * 100, sum(ma) / len(ma) * 100,
             fmt_pct(base['mfe_mae_unconditional']['mfe10_mean']),
             fmt_pct(base['mfe_mae_unconditional']['absmae10_mean'])))
    A('- 具体构成见开头 Top10 榜单与第二节统计表。')
    A('')
    A('**下跌端共性（Top10 高风险下跌形态）**：')
    A('')
    A('- 组别分布：%s。' % grp_str(grp_cnt_d))
    upl2 = [r['uplift_mid_dn_pp'] for r in top_dn if r['uplift_mid_dn_pp'] is not None]
    pds = [r['p_mid_dn'] for r in top_dn if r['p_mid_dn'] is not None]
    if pds:
        A('- P(mid_dn) 区间 %.2f%%~%.2f%%（全样本基线 %s），平均较基线提升 %+.2fpp。'
          % (min(pds) * 100, max(pds) * 100, fmt_pct(b_md),
             (sum(upl2) / len(upl2)) if upl2 else 0.0))
    A('- 具体构成见开头 Top10 榜单与第二节统计表。')
    A('')
    A('**双榜重叠洞察（重要）**：')
    A('')
    A('- %d/10 个形态同时进入上涨榜与下跌榜：%s。这些形态本质是「波动放大」标记——'
      'P(mid_up) 与 P(mid_dn) 同时显著高于基线，方向并不由形态本身决定；'
      '把它们当「看涨信号」而忽略同等的下跌风险是典型误读。'
      % (len(overlap), '、'.join(PMAP_CN[k] for k in overlap) or '无'))
    A('- 上涨榜独有（方向偏多更强）：%s。' % ('、'.join(PMAP_CN[k] for k in only_up) or '无'))
    A('- 下跌榜独有（方向偏空更强）：%s。' % ('、'.join(PMAP_CN[k] for k in only_dn) or '无'))
    A('')
    A('**民间认知证伪 5 条（数据 + 一句话结论）**：')
    A('')
    A('| # | 民间认知 | 口径 | n | 数值 | 基线 | 差(pp) | 结论 |')
    A('|---|---|---|---|---|---|---|---|')
    for f in fals:
        A('| %s | %s | %s | %s | %s | %s | %s | %s |'
          % (f['id'], esc(f['claim']), esc(f['metric']), fmt_int(f['n']), fmt_pct(f['p']),
             fmt_pct(f['baseline_unconditional']), fmt_pp(f['diff_pp']), f['conclusion']))
    A('')
    A('**边界（必读）**：')
    A('')
    A('- 事后统计共现：一行可同时命中多个形态，各形态统计非独立样本；'
      'MFE/MAE 前瞻窗口跨行重叠，存在自相关，pp 差不等于可交易超额。')
    A('- 未按行业/市值/流动性分层，未剔除涨跌停制度影响（一字板、T+1 无法成交等）。')
    A('- 基准为全市场等权（含科创/北交所/ST/退市股），非可投资宇宙口径。')
    A('- 位置/环境交叉为条件统计，分桶样本量小时波动大（表中均标注分桶 n）。')
    A('- 历史共现不构成未来预测；接入选股前必须预注册（见第五节）。')
    A('')
    A('---')
    A('')
    A('## 五、落地建议')
    A('')
    A('- 本报告全部为事后描述性统计，不得直接作为任何买入/卖出依据（零交易权）。')
    A('- 任何形态接入选股/信号前，必须按项目预注册规范（H-R6 先例）先行预注册：'
      '登记形态精确定义、阈值、样本窗口、验收指标与证伪条件，经影子验证与灰度后再评估；'
      '禁止从本报告事后挑选形态直接接入信号（多重检验偏差）。')
    A('- 建议流程：①从本报告选候选（n≥500 且基线差≥2pp）→ ②预注册登记 → '
      '③近端样本外复核（位置/环境分层稳定性）→ ④模拟盘小规模验证 → '
      '⑤按预注册验收指标决定去留。')
    A('- 后续可扩展：形态×市值/行业分层、涨跌停剔除敏感性、分年度稳定性'
      '（防单一年份行情驱动）。')
    A('')
    return '\n'.join(L) + '\n'


def print_console(top_up, top_dn, fals, res_rows):
    print('')
    print('=' * 100)
    print('Top10 高胜率上涨形态 (n>=%d, 按 P(mid_up) 降序):' % MIN_N_TOP)
    for i, r in enumerate(top_up, 1):
        print('  %2d. %-42s n=%-9s P(mid_up)=%s  盈亏比=%s  基线%spp'
              % (i, r['name_cn'], fmt_int(r['n']), fmt_pct(r['p_mid_up']),
                 fmt_r(r['pl_ratio_cond']), fmt_pp(r['uplift_mid_up_pp'])))
    print('Top10 高风险下跌形态 (n>=%d, 按 P(mid_dn) 降序):' % MIN_N_TOP)
    for i, r in enumerate(top_dn, 1):
        print('  %2d. %-42s n=%-9s P(mid_dn)=%s  盈亏比=%s  基线%spp'
              % (i, r['name_cn'], fmt_int(r['n']), fmt_pct(r['p_mid_dn']),
                 fmt_r(r['pl_ratio_cond']), fmt_pp(r['uplift_mid_dn_pp'])))
    print('')
    print('民间认知证伪 5 条 (数据 + 结论):')
    for f in fals:
        print('  %s n=%s: %s = %s vs 基线 %s (%spp) → %s'
              % (f['id'], fmt_int(f['n']), f['metric'], fmt_pct(f['p']),
                 fmt_pct(f['baseline_unconditional']), fmt_pp(f['diff_pp']), f['conclusion']))
    print('')
    print('共振提升 Top5 (联合样本 n≥%d, 按 P(mid_up) 提升):' % MIN_N_RESON)
    res_sorted = [x for x in res_rows
                  if x['n_joint'] >= MIN_N_RESON and x['diff_mid_up_pp'] is not None]
    res_sorted.sort(key=lambda x: (-x['diff_mid_up_pp'], -x['n_joint']))
    for x in res_sorted[:5]:
        print('  %s ∧ [%s]: n=%s, P(mid_up) %s → %s (%spp)'
              % (PMAP_CN[x['pattern']], x['vol_class'], fmt_int(x['n_joint']),
                 fmt_pct(x['p_mid_up_single']), fmt_pct(x['p_mid_up_joint']),
                 fmt_pp(x['diff_mid_up_pp'])))
    print('=' * 100)


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    log('==== v3 技术画像形态库统计（只读，零交易权，描述性统计） ====')
    log('pass1: 大盘环境自算（SQL LAG → 按日等权 ret_mkt → MA20 → regime）...')
    dates, regime_d, mkt_rows, ret_mkt, ma20_d = load_market_regime()
    log('pass1 完成: %d 个交易日 (%d~%d), regime 天数 up/down/flat=%d/%d/%d'
        % (len(dates), int(dates.min()), int(dates.max()),
           int((regime_d == 0).sum()), int((regime_d == 1).sum()), int((regime_d == 2).sum())))
    log('加载 daily_basic 换手率 (2020+) ...')
    turn_map = load_turn_map()
    log('换手率覆盖 %d 只' % len(turn_map))

    ACC = init_acc()
    flush = make_flush(ACC, dates, regime_d, turn_map)

    log('pass2: 流式读取 qfq 日线（ORDER BY code,date 走 PK 索引）...')
    con = ro_connect(QFQ_DB)
    cur = con.execute(
        'SELECT code,date,open,high,low,close,volume '
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
    log('pass2 完成: %d 只 / %d 行 (bad_ohlc=%d, date_mismatch=%d)'
        % (ACC['stocks'], ACC['rows'], ACC['bad_ohlc'], ACC['date_mismatch']))

    base = build_baselines(ACC)
    rows = build_pattern_rows(ACC, base)
    cross_pos = build_cross(ACC, rows, POS_NAMES, ACC['pos_n'], ACC['pos_mu'], ACC['pos_md'])
    cross_reg = build_cross(ACC, rows, REG_NAMES, ACC['reg_n'], ACC['reg_mu'], ACC['reg_md'])
    res_rows, order15 = build_resonance(ACC, rows)
    fals = build_falsification(ACC, base, rows)
    top_up, top_dn = build_tops(rows)
    up_keys = {r['pattern'] for r in top_up}
    dn_keys = {r['pattern'] for r in top_dn}
    overlap = sorted(up_keys & dn_keys)
    only_up = sorted(up_keys - dn_keys)
    only_dn = sorted(dn_keys - up_keys)
    meta = build_meta(ACC, dates, regime_d, mkt_rows)

    out = {
        'meta': meta,
        'baselines': base,
        'pattern_stats': rows,
        'cross_position': cross_pos,
        'cross_regime': cross_reg,
        'cross_resonance': res_rows,
        'falsification': fals,
        'top_lists': {
            'top10_up': [{'pattern': r['pattern'], 'name_cn': r['name_cn'], 'group_cn': r['group_cn'],
                          'n': r['n'], 'p_mid_up': r['p_mid_up'],
                          'pl_ratio_cond': r['pl_ratio_cond'],
                          'uplift_mid_up_pp': r['uplift_mid_up_pp']} for r in top_up],
            'top10_dn': [{'pattern': r['pattern'], 'name_cn': r['name_cn'], 'group_cn': r['group_cn'],
                          'n': r['n'], 'p_mid_dn': r['p_mid_dn'],
                          'pl_ratio_cond': r['pl_ratio_cond'],
                          'uplift_mid_dn_pp': r['uplift_mid_dn_pp']} for r in top_dn],
            'ranking_rule': 'n>=%d，按 P(mid_up)/P(mid_dn) 降序（仅 29 个形态标签，'
                            '不含位置/换手环境标签）' % MIN_N_TOP,
            'overlap_analysis': {
                'overlap_patterns': [{'pattern': k, 'name_cn': PMAP_CN[k]} for k in overlap],
                'up_only': [{'pattern': k, 'name_cn': PMAP_CN[k]} for k in only_up],
                'dn_only': [{'pattern': k, 'name_cn': PMAP_CN[k]} for k in only_dn],
                'note': '双榜重叠形态本质是波动放大标记（P(mid_up) 与 P(mid_dn) 同时高于基线），'
                        '非方向标记',
            },
        },
    }
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(OUT_JSON, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, sort_keys=True, indent=1)
    log('JSON 已写出: %s' % OUT_JSON)

    md = build_md(meta, base, rows, cross_pos, cross_reg, res_rows, order15, fals,
                  top_up, top_dn, overlap, only_up, only_dn)
    with open(OUT_MD, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(md)
    log('MD 已写出: %s' % OUT_MD)

    print_console(top_up, top_dn, fals, res_rows)
    log('完成。')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
