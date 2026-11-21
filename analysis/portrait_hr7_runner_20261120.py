#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_hr7_runner_20261120.py — H-R7 家族内形态精化·预注册验证 runner
================================================================================
PREREG: data/PREREG_PORTRAIT_HR7_V1_20261120.json (指纹 c5977a5f9038a535 跑前冻结)
设问: 在 H-R6 已 PASS 家族命中行内(pos60==b3 AND vola20==b0, 规则8), 叠加 v3 离散
      形态/量价/指标条件能否显著提升 fwd20 超额(精化方向, 服务 Top-N 化接线)。
流程: 全量日线(全历史有效行) -> 家族特征(H-R6 式, >=2016 截断序列, pos60/vola20)
      + 36 布尔标签(v3 式, 全历史递归) + fwd20 + 同日全市场 fwd20 中位基准 -> 超额
      -> IS(2016-2022) 4:1 降采样(idx%4==0 同 H-R6) -> 家族桶边(IS 五分位冻结,
      OOS 复用) -> 家族命中行内逐个叠加 32 条件(v3 形态池) -> Cohen's d + 日度聚合
      粗 t + IS-NW(20)t -> 筛选(nw_t>=2.0 且 d>0 且 n_ds>=3000 且 <=家族95%) -> 按 d
      Top10 冻结 -> OOS(2023-2026 全量行) 逐条叠加(行级均值/日度聚合 NW(20) t/命中天/
      方向) + 家族基准线 + 稳健域(pos60>=b3 AND vola20==b0)复核 -> verdict。
主判据: Top10 中至少一条 OOS 增量>0 且 叠加后 NW(20)t>=2.5 且 方向与 IS 一致 且
        命中天>=300; BOUNDARY=最优增量规则 t∈[2.0,2.5) 或 增量>0但t<2.5; FAIL=其余。
实现校验(fail-closed, 不一致 exit 4):
  C1 全宇宙全历史 36 标签计数 vs v3 报告(TECHNICAL_PROFILE_V3_20261120.json, n=cnt_n)
  C2 6 样本股(IS/OOS 各3) 全历史喂 v3 flush(make_flush/init_acc) 逐元素对照
     cnt_n/cnt_short_valid/cnt_mid_valid/cnt_tr_valid/pos_n/un_pos_rows
  C3 6 样本股 >=2016 截断切片 pos60/vola20 vs H-R6 compute_feats19(rtol=atol=1e-15)
  C4 IS 桶边(round 6dp) vs H-R6 已冻结发布边; 家族 IS 降采样 n == H-R6 规则8 n_ds=54375;
     存储行数/IS降采样数/OOS行数/基准天数 == H-R6 meta 锚点
用法: python -I analysis/portrait_hr7_runner_20261120.py [run2]   # run2=复跑产物
零交易权; 数据只读; 结果不自动接线。
双跑一致性: run1/run2 两份 JSON 剔除顶层 run_info(built_at/runtime/argv) 后应逐字节一致。
"""
import os

# 单线程 BLAS: 保证数值确定性(run1/run2 逐字节一致的前提之一; 必须在 import numpy 前)
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PREREG_PATH = r"D:\zcodesj\data\PREREG_PORTRAIT_HR7_V1_20261120.json"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
V3_REPORT = r"D:\zcodesj\reports\TECHNICAL_PROFILE_V3_20261120.json"
HR6_REPORT = r"D:\zcodesj\reports\HR6_COMBO_RESULT_20261120.json"
OUT1 = r"D:\zcodesj\reports\HR7_REFINE_RESULT_20261120.json"
OUT2 = OUT1.replace(".json", "_run2.json")

D_LO = 20160101                 # 存储行下限(家族特征截断口径, 同 H-R6); 形态轨道全历史
IS_LO, IS_HI = 20160101, 20221231
OOS_LO, OOS_HI = 20230101, 20261119
HOLD = 20                       # fwd20
DS_MOD = 4                      # 4:1 降采样 idx%4==0(同 H-R6)
N_MIN_DS = 3000                 # IS 筛选: 叠加后命中 n(降采样) 下限
OVERCAP = 0.95                  # 叠加后样本 <= 家族 95%(防同义)
T_SCREEN = 2.0                  # IS-NW(20) t 筛选阈
NW_LAG = 20
T_PASS = 2.5
T_BOUND_LO = 2.0
DAYS_MIN = 300
TOP_K = 10

# H-R6 锚点(实现一致性 fail-closed 对照, 来自 HR6_COMBO_RESULT_20261120.json)
HR6_ANCHOR = {
    "rows_fwd_valid": 11404790,
    "IS_rows_ds": 1612289,
    "OOS_rows": 4955406,
    "benchmark_days": 2622,
    "rule8_n_ds": 54375,
    "pos60_edges_6dp": [0.108414, 0.283654, 0.51, 0.769076],
    "vola20_edges_6dp": [0.015339, 0.020613, 0.026529, 0.034471],
}

SAMPLES = [  # (code, 归属窗) — IS 3 只 + OOS 3 只
    ("000001", "IS"), ("600519", "IS"), ("300750", "IS"),
    ("002230", "OOS"), ("000333", "OOS"), ("601888", "OOS"),
]

# 32 条件冻结池(prereg 枚举顺序; v3 ALL_ROWS 列号: 0-28 形态, 29 pos_low,
# 30 pos_mid, 31 pos_high, 32 turn_low, 33 turn_mid, 34 turn_high, 35 turn_extreme)
COND32 = [
    ("big_yang", "大阳"), ("mid_yang", "中阳"), ("small_yang", "小阳"),
    ("doji", "十字星"), ("big_yin", "大阴"), ("mid_yin", "中阴"), ("small_yin", "小阴"),
    ("hammer", "锤头"), ("inv_hammer", "倒锤头"),
    ("red_three", "红三兵"), ("three_crows", "三只乌鸦"),
    ("yang_yin_yang", "两阳夹一阴"), ("yin_yang_yin", "两阴夹一阳"),
    ("box_breakout", "箱体突破"), ("box_hold2", "突破站稳box_hold2"),
    ("gap_up_unfilled", "向上缺口未回补"), ("ma20_up_cross", "上穿MA20"),
    ("vp_up_expand", "价涨量增"), ("vp_up_shrink", "价涨量缩"),
    ("vp_dn_expand", "价跌量增"), ("vp_dn_shrink", "价跌量缩"),
    ("vp_flat_shrink", "横盘缩量"), ("vp_flat_expand", "横盘放量"),
    ("macd_golden", "MACD金叉"), ("macd_death", "MACD死叉"),
    ("rsi_overbought", "RSI超买"), ("rsi_oversold", "RSI超卖"),
    ("kdj_golden", "KDJ金叉"), ("kdj_death", "KDJ死叉"),
    ("pos_low", "低位置"), ("pos_high", "高位"), ("turn_extreme", "换手异常>95分位"),
]
V3_COL = {k: i for i, (k, _grp, _cn) in enumerate(
    [("big_yang", 0, 0), ("mid_yang", 0, 0), ("small_yang", 0, 0), ("doji", 0, 0),
     ("big_yin", 0, 0), ("mid_yin", 0, 0), ("small_yin", 0, 0), ("hammer", 0, 0),
     ("inv_hammer", 0, 0), ("red_three", 0, 0), ("three_crows", 0, 0),
     ("yang_yin_yang", 0, 0), ("yin_yang_yin", 0, 0), ("box_breakout", 0, 0),
     ("box_hold2", 0, 0), ("gap_up_unfilled", 0, 0), ("ma20_up_cross", 0, 0),
     ("vp_up_expand", 0, 0), ("vp_up_shrink", 0, 0), ("vp_dn_expand", 0, 0),
     ("vp_dn_shrink", 0, 0), ("vp_flat_shrink", 0, 0), ("vp_flat_expand", 0, 0),
     ("macd_golden", 0, 0), ("macd_death", 0, 0), ("rsi_overbought", 0, 0),
     ("rsi_oversold", 0, 0), ("kdj_golden", 0, 0), ("kdj_death", 0, 0),
     ("pos_low", 0, 0), ("pos_mid", 0, 0), ("pos_high", 0, 0),
     ("turn_low", 0, 0), ("turn_mid", 0, 0), ("turn_high", 0, 0),
     ("turn_extreme", 0, 0)])}
COND_COLS = np.array([V3_COL[k] for k, _cn in COND32], np.int64)
assert len(COND32) == 32 and COND_COLS.max() == 35

T0 = time.time()


def log(msg):
    print("[%8.1fs] %s" % (time.time() - T0, msg), flush=True)


def ro_connect(path):
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


def code6(x):
    s = str(x).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits[:6] if digits else s[:6]


def safe_dint(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        s2 = str(x).replace("-", "").replace("/", "").replace(" ", "")
        return int(s2[:8])


def verify_prereg() -> str:
    with open(PREREG_PATH, "rb") as fh:
        prereg = json.loads(fh.read().decode("utf-8"))
    fp_claim = prereg["fingerprint"]
    prereg["fingerprint"] = ""
    body = json.dumps(prereg, ensure_ascii=False, indent=1)
    fp_calc = hashlib.sha256(body.encode()).hexdigest()[:16]
    if fp_calc != fp_claim:
        print("[FAIL-CLOSED] PREREG指纹不符: calc=%s claim=%s" % (fp_calc, fp_claim))
        sys.exit(3)
    print("PREREG指纹校验通过: %s" % fp_claim, flush=True)
    return fp_claim


def nw_tstat(x, lag=NW_LAG):
    """Newey-West HAC t 值(与 portrait_regime_hr1_runner / H-R6 同式)。"""
    x = np.asarray(x, float)
    n = len(x)
    if n < lag + 5:
        return float("nan"), float("nan")
    m = float(x.mean())
    e = x - m
    g0 = float((e * e).sum() / n)
    s = g0
    for ll in range(1, min(lag, n - 1) + 1):
        gl = float((e[ll:] * e[:-ll]).sum() / n)
        s += 2.0 * (1.0 - ll / (lag + 1.0)) * gl
    se = (max(s, 1e-18) / n) ** 0.5
    return m / se if se > 0 else float("nan"), m


def daily_agg_t(dates_int, exc_vals):
    """日度聚合(命中日等权均值) -> (粗 t[ddof=1], NW(20) t, 命中天数, 日度均值)。"""
    ud, inv = np.unique(dates_int, return_inverse=True)
    nd = len(ud)
    sums = np.bincount(inv, weights=exc_vals, minlength=nd)
    cnts = np.bincount(inv, minlength=nd)
    daily = sums / np.maximum(cnts, 1)
    rough_t = float("nan")
    if nd >= 2:
        sd = float(daily.std(ddof=1))
        if sd > 0:
            rough_t = float(daily.mean()) / (sd / nd ** 0.5)
    t_nw, _ = nw_tstat(daily, NW_LAG)
    return rough_t, t_nw, nd, daily


# ---------------- 滚动原语(与 H-R6 compute_feats19 完全一致) ----------------

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


def family_feats_slice(c_s):
    """家族轨道(H-R6 式, >=2016 截断序列): pos60 / vola20。
    ret1 前收约定与 H-R6 一致: pc[0]=c[0](首行 ret1=0)。"""
    n = len(c_s)
    pc = np.empty(n)
    pc[0] = c_s[0]
    pc[1:] = c_s[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        ret1 = c_s / pc - 1.0
        rmin60 = roll_maxmin(c_s, 60, np.min)
        rmax60 = roll_maxmin(c_s, 60, np.max)
        pos60 = (c_s - rmin60) / (rmax60 - rmin60)
        vola20 = roll_std_comb(ret1, 20)
    return pos60, vola20


def align_turn(dint, td):
    """换手率按日 searchsorted 对齐(同日无值 -> NaN; 与 H-R6/v3 同式)。"""
    turn = np.full(len(dint), np.nan)
    if td is not None:
        td_d, td_v = td
        idx = np.searchsorted(td_d, dint)
        ok = idx < len(td_d)
        ok2 = ok.copy()
        ok2[ok] = td_d[idx[ok]] == dint[ok]
        turn[ok2] = td_v[idx[ok2]]
    return turn


def load_daily_basic():
    """daily_basic 全量 -> {c6: (dates int64 升序, turnover f64)}
    (H-R6 load_daily_basic 同式; 表实为 20200102 起, 与 v3 load_turn_map 内容一致)。"""
    con = ro_connect(FUND_DB)
    cur = con.execute(
        "SELECT ts_code, trade_date, turnover_rate FROM daily_basic ORDER BY ts_code, trade_date")
    out = {}
    cur_code = None
    buf = []

    def flush(code_raw, rows):
        c6 = code6(code_raw)
        m = len(rows)
        d = np.fromiter((safe_dint(r[1]) for r in rows), np.int64, m)
        v = np.fromiter(((r[2] if r[2] is not None else np.nan) for r in rows), np.float64, m)
        o = np.argsort(d, kind="stable")
        d, v = d[o], v[o]
        prev = out.get(c6)
        if prev is not None:
            d = np.concatenate([prev[0], d])
            v = np.concatenate([prev[1], v])
            o = np.argsort(d, kind="stable")
            d, v = d[o], v[o]
        out[c6] = (d, v)

    while True:
        rows = cur.fetchmany(200000)
        if not rows:
            break
        for row in rows:
            if row[0] != cur_code:
                if buf:
                    flush(cur_code, buf)
                buf = []
                cur_code = row[0]
            buf.append(row)
    if buf:
        flush(cur_code, buf)
    con.close()
    return out


# ---------------- 形态轨道(v3 式, 全历史; 判定式逐行复制 technical_profile_v3) ----------------

def compute_flags36(o, h, l, c, v, turn, v3m):
    """36 个布尔标签(v3 ALL_ROWS 顺序)。判定式逐行复制 v3 make_flush 内联段,
    指标路径直接调用 v3 模块自身 helper(macd_arrays/rsi_array/kdj_arrays/shift_*)。
    ret1 前收约定与 v3 一致: pc[0]=NaN(首行标签 False)。
    输入行集合=有效行(close>0 & volume>0); 全宇宙坏 OHLC 行已断言为 0,
    故与 v3 的坏行剔除分支无行为差异。"""
    m = len(c)
    EPS = v3m.EPS
    shift_bool = v3m.shift_bool
    shift_f = v3m.shift_f
    with np.errstate(divide="ignore", invalid="ignore"):
        # ret1（v3: 股内前收, 首行 NaN）
        pc = np.empty(m)
        pc[0] = np.nan
        pc[1:] = c[:-1]
        ret1 = c / pc - 1.0
        ar1 = np.abs(ret1)

        # pos250（v3/v2 公式, 窗口含当日, 需满 250 根）
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

        # 换手分位（最近 250 个交易日含当日、250 值全部有效）
        turn_pct = np.full(m, np.nan)
        if m >= 250:
            swt = sliding_window_view(turn, 250)
            okw = np.isfinite(swt).all(axis=1)
            if okw.any():
                cnt = (swt[okw] <= turn[249:][okw][:, None]).mean(axis=1)
                turn_pct[249:][okw] = cnt

        # MFE/MAE 前瞻路径(仅取其有效掩码供校验对照)
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
        f20 = np.full(m, np.nan)
        if m >= 21:
            rows21 = sliding_window_view(c, 21)
            f20[:m - 20] = rows21[:, -1] / rows21[:, 0] - 1.0
        sv = np.isfinite(mfe3)
        mv = np.isfinite(mfe10)
        tv = np.isfinite(f20)

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
            pm60 = sliding_window_view(c, 60).max(axis=1)
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

        # ---- 量价交叉 6 类（vr5=vol/MA5(vol), 含当日） ----
        vr5 = np.full(m, np.nan)
        if m >= 5:
            ma5v = sliding_window_view(v, 5).mean(axis=1)
            vr5[4:] = v[4:] / ma5v
        vp_up_expand = (ret1 > 0) & (vr5 > 1.5)
        vp_up_shrink = (ret1 > 0) & (vr5 < 0.7)
        vp_dn_expand = (ret1 < 0) & (vr5 > 1.5)
        vp_dn_shrink = (ret1 < 0) & (vr5 < 0.7)
        vp_flat_shrink = (ar1 < 0.005) & (vr5 < 0.7)
        vp_flat_expand = (ar1 < 0.005) & (vr5 > 1.5)

        # ---- 指标状态 6 类（直接调用 v3 模块 helper, 保证路径一致） ----
        dif, dea = v3m.macd_arrays(c)
        rsi = v3m.rsi_array(c)
        mdv = np.isfinite(dif) & np.isfinite(dea)
        dif_p1 = shift_f(dif, 1)
        dea_p1 = shift_f(dea, 1)
        macd_golden = mdv & shift_bool(mdv, 1) & (dif > dea) & (dif_p1 <= dea_p1)
        macd_death = mdv & shift_bool(mdv, 1) & (dif < dea) & (dif_p1 >= dea_p1)
        rsi_ob = rsi > 70
        rsi_os = rsi < 30
        kdj_k, kdj_d = v3m.kdj_arrays(h, l, c)
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

    flags = [big_yang, mid_yang, small_yang, doji, big_yin, mid_yin, small_yin,
             hammer, inv_hammer,
             red_three, three_crows, yang_yin_yang, yin_yang_yin,
             box_breakout, box_hold2, gap_up_unfilled, ma20_up_cross,
             vp_up_expand, vp_up_shrink, vp_dn_expand, vp_dn_shrink,
             vp_flat_shrink, vp_flat_expand,
             macd_golden, macd_death, rsi_ob, rsi_os, kdj_golden, kdj_death,
             pos_low, pos_mid, pos_high, t_low, t_mid, t_high, t_ext]
    P = np.stack(flags, axis=1)   # bool (m, 36), v3 ALL_ROWS 顺序
    return P, sv, mv, tv, pos_bucket


def load_stock_full(con, code):
    rows = con.execute(
        "SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE code=? AND close>0 AND volume>0 ORDER BY date", (code,)).fetchall()
    m = len(rows)
    dint = np.fromiter((safe_dint(r[0]) for r in rows), np.int64, m)
    o = np.fromiter(((r[1] if r[1] is not None else np.nan) for r in rows), np.float64, m)
    h = np.fromiter(((r[2] if r[2] is not None else np.nan) for r in rows), np.float64, m)
    l = np.fromiter(((r[3] if r[3] is not None else np.nan) for r in rows), np.float64, m)
    c = np.fromiter(((r[4] if r[4] is not None else np.nan) for r in rows), np.float64, m)
    v = np.fromiter(((r[5] if r[5] is not None else np.nan) for r in rows), np.float64, m)
    return dint, o, h, l, c, v


def impl_checks(turn_map, v3m, hr6):
    """C1-C3 预检(样本股), fail-closed exit 4。"""
    detail = []
    all_ok = True
    con = ro_connect(QFQ_DB)
    for code, window in SAMPLES:
        dint, o, h, l, c, v = load_stock_full(con, code)
        if len(c) < 300:
            detail.append({"code": code, "window": window, "ok": False, "why": "rows<300"})
            all_ok = False
            continue
        turn = align_turn(dint, turn_map.get(code))
        # C2: v3 flush 全历史对照
        acc = v3m.init_acc()
        flush_v3 = v3m.make_flush(acc, np.array([0], np.int64), np.array([0], np.int64), turn_map)
        rows7 = [(code, int(dint[i]), float(o[i]), float(h[i]), float(l[i]),
                  float(c[i]), float(v[i])) for i in range(len(c))]
        flush_v3(code, rows7)
        P, sv, mv, tv, pos_bucket = compute_flags36(o, h, l, c, v, turn, v3m)
        mine = {
            "cnt_n": P.sum(axis=0),
            "cnt_short_valid": P[sv].sum(axis=0),
            "cnt_mid_valid": P[mv].sum(axis=0),
            "cnt_tr_valid": P[tv].sum(axis=0),
            "pos_n": np.stack([P[pos_bucket == b].sum(axis=0) for b in range(3)]),
            "un_pos_rows": np.bincount(pos_bucket[pos_bucket >= 0], minlength=3)[:3],
        }
        ref = {
            "cnt_n": acc["cnt_n"],
            "cnt_short_valid": acc["cnt_short_valid"],
            "cnt_mid_valid": acc["cnt_mid_valid"],
            "cnt_tr_valid": acc["cnt_tr_valid"],
            "pos_n": acc["pos_n"],
            "un_pos_rows": acc["un_pos_rows"],
        }
        v3_ok = all(bool(np.array_equal(mine[k], ref[k])) for k in mine)
        # C3: H-R6 家族特征对照(>=2016 截断切片, H-R6 口径)
        i0 = int(np.searchsorted(dint, D_LO))
        o_s, h_s, l_s, c_s, v_s = o[i0:], h[i0:], l[i0:], c[i0:], v[i0:]
        turn_s = turn[i0:]
        f_hr6 = hr6.compute_feats19(o_s, h_s, l_s, c_s, v_s, turn_s)
        p60_mine, v20_mine = family_feats_slice(c_s)
        hr6_ok = (bool(np.allclose(p60_mine, f_hr6["pos60"], rtol=1e-15, atol=1e-15, equal_nan=True))
                  and bool(np.allclose(v20_mine, f_hr6["vola20"], rtol=1e-15, atol=1e-15, equal_nan=True)))
        detail.append({
            "code": code, "window": window, "n_rows_full": int(len(c)),
            "n_rows_ge2016": int(len(c_s)),
            "v3_counts_all_equal": v3_ok, "hr6_pos60_vola20_ok": hr6_ok,
            "v3_cnt_n_first3": [int(x) for x in acc["cnt_n"][:3]],
        })
        if not (v3_ok and hr6_ok):
            all_ok = False
        log("样本校验 %s(%s): v3计数%s H-R6特征%s (n_full=%d, n_ge2016=%d)"
            % (code, window, "一致" if v3_ok else "不一致", "一致" if hr6_ok else "不一致",
               len(c), len(c_s)))
    con.close()
    return all_ok, detail


def main():
    fp = verify_prereg()

    # ---- 参考模块导入(只读复用) ----
    sys.path.insert(0, r"D:\zcodesj\analysis")
    import portrait_combo_hr6_runner_20261120 as hr6
    import technical_profile_v3_20261120 as v3m

    # ---- 换手率源 ----
    turn_map = load_daily_basic()
    log("daily_basic 覆盖 %d 只(turnover_rate)" % len(turn_map))

    # ---- v3 报告 36 标签计数(报告级对照基准) ----
    v3rep = json.load(open(V3_REPORT, encoding="utf-8"))
    v3_n = np.array([int(r["n"]) for r in v3rep["pattern_stats"]], np.int64)
    assert len(v3_n) == 36
    log("v3 报告载入: 36 标签计数(全宇宙全历史, rows_valid=%d)"
        % v3rep["meta"]["sample"]["rows_valid"])

    # ---- C2/C3 样本校验(fail-closed) ----
    ok, sample_detail = impl_checks(turn_map, v3m, hr6)
    if not ok:
        print("[FAIL-CLOSED] 样本股 v3/H-R6 实现对照不一致, 终止(exit 4)")
        print(json.dumps(sample_detail, ensure_ascii=False, indent=1))
        sys.exit(4)
    log("样本校验通过: 6 样本股 v3 逐元素计数一致 + H-R6 pos60/vola20 逐元素一致")

    # ---- 主载入: 全历史有效行; 家族特征按 >=2016 截断序列(H-R6 口径) ----
    con = ro_connect(QFQ_DB)
    total = con.execute(
        "SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE close>0 AND volume>0").fetchone()[0]
    log("qfq 全历史有效行 = %d, 预分配全局数组" % total)
    g_date = np.empty(total, np.int32)
    g_code = np.empty(total, np.int32)
    g_fwd = np.empty(total, np.float32)
    g_pos = np.empty(total, np.float32)     # pos60(家族轨道, >=2016 截断序列)
    g_vol = np.empty(total, np.float32)     # vola20(家族轨道)
    g_pat = np.zeros((total, 32), np.bool_)  # 32 条件标签(形态轨道, 全历史)

    cur = con.execute(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE close>0 AND volume>0 ORDER BY code,date")
    code_ids = {}
    cur_code = None
    buf = []
    pos = 0
    stats = {"stocks": 0, "stocks_stored": 0, "bad_ohlc_rows": 0, "stocks_too_short": 0,
             "rows_dropped_nonfinite_fwd": 0}

    cnt_all = np.zeros(36, np.int64)

    def flush(code_raw, rows):
        nonlocal pos, cnt_all
        m = len(rows)
        c6 = code6(code_raw)
        cid = code_ids.setdefault(c6, len(code_ids))
        dint = np.fromiter((safe_dint(r[1]) for r in rows), np.int64, m)
        o = np.fromiter(((r[2] if r[2] is not None else np.nan) for r in rows), np.float64, m)
        h = np.fromiter(((r[3] if r[3] is not None else np.nan) for r in rows), np.float64, m)
        l = np.fromiter(((r[4] if r[4] is not None else np.nan) for r in rows), np.float64, m)
        c = np.fromiter(((r[5] if r[5] is not None else np.nan) for r in rows), np.float64, m)
        v = np.fromiter(((r[6] if r[6] is not None else np.nan) for r in rows), np.float64, m)
        stats["bad_ohlc_rows"] += int(np.count_nonzero(
            (o <= 0) | (h <= 0) | (l <= 0) | (h < l) | np.isnan(o) | np.isnan(h) | np.isnan(l)))
        stats["stocks"] += 1
        turn = align_turn(dint, turn_map.get(c6))
        # 形态轨道: 全历史(v3 口径, 指标递归含 2016 前历史)
        P, _sv, _mv, _tv, _pb = compute_flags36(o, h, l, c, v, turn, v3m)
        cnt_all += P.sum(axis=0)
        # 存储行: date>=2016 且 fwd20 可计算(与 H-R6 存储集合完全一致)
        i0 = int(np.searchsorted(dint, D_LO))
        if i0 >= m:
            return
        m_s = m - i0
        if m_s <= HOLD:
            stats["stocks_too_short"] += 1
            return
        p60_s, v20_s = family_feats_slice(c[i0:])
        nh = m_s - HOLD
        fwd_s = c[i0 + HOLD:] / c[i0:i0 + nh] - 1.0
        kept = np.isfinite(fwd_s)
        k = int(np.count_nonzero(kept))
        if k == 0:
            stats["rows_dropped_nonfinite_fwd"] += nh
            return
        stats["rows_dropped_nonfinite_fwd"] += nh - k
        sl = slice(pos, pos + k)
        g_date[sl] = dint[i0:i0 + nh][kept]
        g_code[sl] = cid
        g_fwd[sl] = fwd_s[kept]
        g_pos[sl] = p60_s[:nh][kept]
        g_vol[sl] = v20_s[:nh][kept]
        g_pat[sl] = P[i0:i0 + nh][kept][:, COND_COLS]
        pos += k
        stats["stocks_stored"] += 1
        if stats["stocks_stored"] % 1000 == 0:
            log("进度 %d 只 / %d 行" % (stats["stocks_stored"], pos))

    while True:
        rows = cur.fetchmany(50000)
        if not rows:
            break
        for row in rows:
            if row[0] != cur_code:
                if buf:
                    flush(cur_code, buf)
                buf = []
                cur_code = row[0]
            buf.append(row)
    if buf:
        flush(cur_code, buf)
    con.close()
    N = pos
    if N == 0:
        print("[FAIL-CLOSED] 无有效 fwd 行")
        sys.exit(5)
    date_a = g_date[:N]
    code_a = g_code[:N]
    fwd_a = g_fwd[:N]
    pos60_a = g_pos[:N]
    vola20_a = g_vol[:N]
    pat_a = g_pat[:N]
    log("装配完成: %d 行 / %d 只(存储) / %d~%d, too_short %d, bad_ohlc %d"
        % (N, stats["stocks_stored"], int(date_a.min()), int(date_a.max()),
           stats["stocks_too_short"], stats["bad_ohlc_rows"]))

    # ---- C1: 全宇宙 36 标签计数 vs v3 报告(fail-closed) ----
    c1_ok = bool(np.array_equal(cnt_all, v3_n))
    if stats["bad_ohlc_rows"] > 0:
        c1_ok = False
    if not c1_ok:
        print("[FAIL-CLOSED] C1 全宇宙标签计数与 v3 报告不一致(或存在坏 OHLC 行), exit 4")
        diffs = [(i, int(cnt_all[i]), int(v3_n[i])) for i in range(36) if cnt_all[i] != v3_n[i]]
        print("差异(列,mine,v3): %s" % diffs)
        sys.exit(4)
    log("C1 通过: 全宇宙 36 标签计数与 v3 报告逐元素一致")

    # ---- C4 锚点: 行数/降采样/基准 与 H-R6 完全一致(fail-closed) ----
    anchor_ok = True
    if N != HR6_ANCHOR["rows_fwd_valid"]:
        anchor_ok = False
        print("[FAIL-CLOSED] 存储行数 %d != H-R6 rows_fwd_valid %d"
              % (N, HR6_ANCHOR["rows_fwd_valid"]))

    # ---- 全市场基准: 同日 fwd20 中位 -> 超额(同 H-R6) ----
    ud, inv = np.unique(date_a, return_inverse=True)
    counts = np.bincount(inv, minlength=len(ud))
    o2 = np.argsort(inv, kind="stable")
    fs = fwd_a[o2].astype(np.float64)
    bnds = np.concatenate(([0], np.cumsum(counts)))
    med = np.empty(len(ud))
    for i in range(len(ud)):
        med[i] = np.median(fs[int(bnds[i]):int(bnds[i + 1])])
    del fs, o2
    exc_a = ((fwd_a.astype(np.float64) - med[inv]).astype(np.float32))
    del inv
    min_bench_n = int(counts.min())
    bench_days = int(len(ud))
    log("基准: %d 个交易日(等权中位, 最小日样本 %d 行)" % (bench_days, min_bench_n))
    if bench_days != HR6_ANCHOR["benchmark_days"]:
        anchor_ok = False
        print("[FAIL-CLOSED] 基准天数 %d != H-R6 %d" % (bench_days, HR6_ANCHOR["benchmark_days"]))

    # ---- IS 降采样 + 家族桶边(冻结, OOS 复用) ----
    gidx = np.arange(N, dtype=np.int64)
    in_is = (date_a >= IS_LO) & (date_a <= IS_HI)
    ds_idx = np.flatnonzero(in_is & (gidx % DS_MOD == 0))
    n_is_full = int(np.count_nonzero(in_is))
    if len(ds_idx) != HR6_ANCHOR["IS_rows_ds"]:
        anchor_ok = False
        print("[FAIL-CLOSED] IS 降采样行数 %d != H-R6 %d" % (len(ds_idx), HR6_ANCHOR["IS_rows_ds"]))
    oos_idx = np.flatnonzero((date_a >= OOS_LO) & (date_a <= OOS_HI))
    if len(oos_idx) != HR6_ANCHOR["OOS_rows"]:
        anchor_ok = False
        print("[FAIL-CLOSED] OOS 行数 %d != H-R6 %d" % (len(oos_idx), HR6_ANCHOR["OOS_rows"]))
    if not anchor_ok:
        sys.exit(4)
    log("IS(%d-%d): 全量 %d 行 -> 降采样 %d 行; OOS(%d-%d): 全量 %d 行"
        % (IS_LO, IS_HI, n_is_full, len(ds_idx), OOS_LO, OOS_HI, len(oos_idx)))

    edges = {}
    for name, arr in (("pos60", pos60_a), ("vola20", vola20_a)):
        vals = arr[ds_idx].astype(np.float64)
        fin = np.isfinite(vals)
        e = np.percentile(vals[fin], [20, 40, 60, 80])
        edges[name] = e
        del vals
    e_pos6 = [round(float(x), 6) for x in edges["pos60"]]
    e_vol6 = [round(float(x), 6) for x in edges["vola20"]]
    edges_ok = (e_pos6 == HR6_ANCHOR["pos60_edges_6dp"]
                and e_vol6 == HR6_ANCHOR["vola20_edges_6dp"])
    if not edges_ok:
        print("[FAIL-CLOSED] 桶边与 H-R6 发布边不一致: pos60=%s vola20=%s" % (e_pos6, e_vol6))
        sys.exit(4)
    log("桶边冻结: pos60=%s vola20=%s (与 H-R6 发布边一致)" % (e_pos6, e_vol6))

    # 两个特征的桶(NaN 行 = -1 不命中)
    bkt_pos = np.full(N, -1, np.int8)
    finp = np.isfinite(pos60_a)
    bkt_pos[finp] = np.digitize(pos60_a[finp].astype(np.float64), edges["pos60"]).astype(np.int8)
    bkt_vol = np.full(N, -1, np.int8)
    finv = np.isfinite(vola20_a)
    bkt_vol[finv] = np.digitize(vola20_a[finv].astype(np.float64), edges["vola20"]).astype(np.int8)

    family = (bkt_pos == 3) & (bkt_vol == 0)              # 主家族 = H-R6 规则8
    robust = (bkt_pos >= 3) & (bkt_vol == 0)              # 稳健域(宽家族) = H-R6 规则5
    fam_ds_idx = ds_idx[family[ds_idx]]
    n_fam_ds = int(len(fam_ds_idx))
    if n_fam_ds != HR6_ANCHOR["rule8_n_ds"]:
        print("[FAIL-CLOSED] 家族 IS 降采样 n=%d != H-R6 规则8 n_ds=%d" % (n_fam_ds, HR6_ANCHOR["rule8_n_ds"]))
        sys.exit(4)
    log("家族命中: IS 降采样 %d 行(== H-R6 规则8 n_ds)" % n_fam_ds)

    # C 补充: 样本股家族掩码逐元素对照(桶程序 + H-R6 特征 -> 家族)
    fam_check_detail = []
    fam_check_ok = True
    con = ro_connect(QFQ_DB)
    for code, window in SAMPLES:
        rows_where = np.flatnonzero(code_a == code_ids.get(code, -1))
        if len(rows_where) == 0:
            fam_check_ok = False
            fam_check_detail.append({"code": code, "ok": False, "why": "no_stored_rows"})
            continue
        r0, r1 = int(rows_where[0]), int(rows_where[-1])
        dint_s, o_s, h_s, l_s, c_s, v_s = load_stock_full(con, code)
        i0 = int(np.searchsorted(dint_s, D_LO))
        c_s = c_s[i0:]
        o_s, h_s, l_s, v_s = o_s[i0:], h_s[i0:], l_s[i0:], v_s[i0:]
        m_s = len(c_s)
        nh = m_s - HOLD
        turn_s = align_turn(dint_s[i0:], turn_map.get(code))
        f_hr6 = hr6.compute_feats19(o_s, h_s, l_s, c_s, v_s, turn_s)
        nh_eff = min(nh, r1 - r0 + 1)
        my_mask = family[r0:r0 + nh_eff]
        ref_mask = ((np.digitize(f_hr6["pos60"][:nh_eff].astype(np.float64),
                                 edges["pos60"]) == 3)
                    & (np.digitize(f_hr6["vola20"][:nh_eff].astype(np.float64),
                                   edges["vola20"]) == 0))
        ref_mask = ref_mask & np.isfinite(f_hr6["pos60"][:nh_eff]) & np.isfinite(f_hr6["vola20"][:nh_eff])
        eq = bool(np.array_equal(my_mask, ref_mask))
        fam_check_detail.append({
            "code": code, "window": window, "stored_rows": int(r1 - r0 + 1),
            "family_hits": int(my_mask.sum()), "mask_equal": eq})
        if not eq:
            fam_check_ok = False
        log("家族掩码校验 %s(%s): %s (存储 %d 行, 家族命中 %d)"
            % (code, window, "一致" if eq else "不一致", r1 - r0 + 1, int(my_mask.sum())))
    con.close()
    if not fam_check_ok:
        print("[FAIL-CLOSED] 样本股家族掩码与 H-R6 实现不一致, exit 4")
        print(json.dumps(fam_check_detail, ensure_ascii=False, indent=1))
        sys.exit(4)

    pos_flag = fwd_a > 0

    # ---- IS 挖掘: 家族命中行内逐条件叠加 ----
    P_fam = pat_a[fam_ds_idx]
    e_F = exc_a[fam_ds_idx].astype(np.float64)
    d_F = date_a[fam_ds_idx]
    pos_F = pos_flag[fam_ds_idx]
    nF = int(n_fam_ds)
    mF = float(e_F.mean())
    fam_is_daily_t, fam_is_nw_t, fam_is_days, _ = daily_agg_t(d_F, e_F)
    fam_is_stats = dict(
        n_ds=nF, excess_mean_row=round(mF, 6),
        share_pos=round(float(pos_F.mean()), 4),
        days_covered_ds=int(fam_is_days),
        rough_t_daily=round(fam_is_daily_t, 4),
        nw_t_daily=round(float(fam_is_nw_t), 4) if np.isfinite(fam_is_nw_t) else None,
        note="IS 窗口, 4:1 降采样行; 全量 IS 家族行数=%d" % int(np.count_nonzero(family[in_is])))

    is_rows = []
    n_screen_pass = 0
    for j, (key, cn) in enumerate(COND32):
        A = P_fam[:, j]
        nA = int(np.count_nonzero(A))
        row = {"rank": None, "cond_key": key, "cond_cn": cn, "v3_col": int(COND_COLS[j]),
               "n_ds": nA, "pct_of_family": round(nA / nF, 4) if nF else None}
        if nA == 0 or nF < 3:
            row.update({"d": None, "rough_t": None, "nw_t": None, "excess_mean_A": None,
                        "screen_pass": False})
            is_rows.append(row)
            continue
        eA = e_F[A]
        mA = float(eA.mean())
        s2A = float(eA.var(ddof=1)) if nA > 1 else float("nan")
        s2F = float(e_F.var(ddof=1))
        sp = (((nA - 1) * s2A + (nF - 1) * s2F) / (nA + nF - 2)) ** 0.5
        d = (mA - mF) / sp if (sp > 0 and np.isfinite(sp)) else float("nan")
        rough_t, t_nw, nd, _ = daily_agg_t(d_F[A], eA)
        screen = bool(np.isfinite(t_nw) and t_nw >= T_SCREEN and np.isfinite(d) and d > 0
                      and nA >= N_MIN_DS and nA <= OVERCAP * nF)
        if screen:
            n_screen_pass += 1
        row.update({
            "excess_mean_A": round(mA, 6), "excess_mean_family": round(mF, 6),
            "d": round(float(d), 4) if np.isfinite(d) else None,
            "rough_t": round(float(rough_t), 4) if np.isfinite(rough_t) else None,
            "nw_t": round(float(t_nw), 4) if np.isfinite(t_nw) else None,
            "days_covered_ds": int(nd),
            "share_pos_A": round(float(pos_F[A].mean()), 4),
            "screen_pass": screen})
        is_rows.append(row)

    cand = [r for r in is_rows if r["screen_pass"]]
    cand.sort(key=lambda r: -r["d"])
    top = cand[:TOP_K]
    for rk, r in enumerate(top, 1):
        r["rank"] = rk
    log("IS 筛选: 32 条件中过筛 %d 条(nw_t>=%.1f 且 d>0 且 n>=%d 且 <=家族%.0f%%); Top%d 冻结"
        % (n_screen_pass, T_SCREEN, N_MIN_DS, OVERCAP * 100, len(top)))

    # ---- OOS(全量行): 家族基准线 + Top10 逐条叠加 + 稳健域复核 ----
    fam_oos_idx = oos_idx[family[oos_idx]]
    rob_oos_idx = oos_idx[robust[oos_idx]]
    P_oos = pat_a[fam_oos_idx]
    e_oos = exc_a[fam_oos_idx].astype(np.float64)
    d_oos = date_a[fam_oos_idx]
    pos_oos = pos_flag[fam_oos_idx]
    nF_oos = int(len(fam_oos_idx))
    mF_oos = float(e_oos.mean())
    _, fam_oos_nw, fam_oos_days, _ = daily_agg_t(d_oos, e_oos)
    fam_oos_stats = dict(
        n_rows=nF_oos, excess_mean_row=round(mF_oos, 6),
        share_pos=round(float(pos_oos.mean()), 4),
        days_covered=int(fam_oos_days),
        nw_t_daily=round(float(fam_oos_nw), 4) if np.isfinite(fam_oos_nw) else None,
        first_hit_date=int(d_oos.min()), last_hit_date=int(d_oos.max()))

    P_rob = pat_a[rob_oos_idx]
    e_rob = exc_a[rob_oos_idx].astype(np.float64)
    d_rob = date_a[rob_oos_idx]
    nR_oos = int(len(rob_oos_idx))
    mR_oos = float(e_rob.mean())
    _, rob_oos_nw, rob_oos_days, _ = daily_agg_t(d_rob, e_rob)
    rob_oos_stats = dict(
        n_rows=nR_oos, excess_mean_row=round(mR_oos, 6),
        days_covered=int(rob_oos_days),
        nw_t_daily=round(float(rob_oos_nw), 4) if np.isfinite(rob_oos_nw) else None,
        note="稳健域= pos60>=b3 AND vola20==b0 (H-R6 规则5, 宽家族)")

    is_by_rank = {r["rank"]: r for r in top}
    oos_rows = []
    robust_rows = []
    for r_is in top:
        j = COND32.index((r_is["cond_key"], r_is["cond_cn"]))
        A = P_oos[:, j]
        nA = int(np.count_nonzero(A))
        eA = e_oos[A]
        mA = float(eA.mean()) if nA else float("nan")
        rough_t, t_nw, nd, _ = daily_agg_t(d_oos[A], eA) if nA else (float("nan"), float("nan"), 0, None)
        inc = (mA - mF_oos) if nA else float("nan")
        is_mean = is_by_rank[r_is["rank"]]["excess_mean_A"]
        dir_ok = bool(np.isfinite(mA) and mA > 0 and is_mean is not None and is_mean > 0)
        gate = bool(np.isfinite(inc) and inc > 0 and np.isfinite(t_nw) and t_nw >= T_PASS
                    and dir_ok and nd >= DAYS_MIN)
        oos_rows.append({
            "rank": r_is["rank"], "cond_key": r_is["cond_key"], "cond_cn": r_is["cond_cn"],
            "n_rows": nA, "excess_mean_row": round(mA, 6) if np.isfinite(mA) else None,
            "share_pos": round(float(pos_oos[A].mean()), 4) if nA else None,
            "increment_vs_family": round(float(inc), 6) if np.isfinite(inc) else None,
            "rough_t_daily": round(float(rough_t), 4) if np.isfinite(rough_t) else None,
            "nw_t": round(float(t_nw), 4) if np.isfinite(t_nw) else None,
            "days_covered": int(nd),
            "first_hit_date": int(d_oos[A].min()) if nA else None,
            "last_hit_date": int(d_oos[A].max()) if nA else None,
            "is_excess_mean": is_mean, "is_d": is_by_rank[r_is["rank"]]["d"],
            "direction_consistent": dir_ok, "main_gate_pass": gate})
        # 稳健域同口径复核
        Ar = P_rob[:, j]
        nAr = int(np.count_nonzero(Ar))
        eAr = e_rob[Ar]
        mAr = float(eAr.mean()) if nAr else float("nan")
        _, t_nwr, ndr, _ = daily_agg_t(d_rob[Ar], eAr) if nAr else (float("nan"), float("nan"), 0, None)
        incr = (mAr - mR_oos) if nAr else float("nan")
        dir_r = bool(np.isfinite(mAr) and mAr > 0 and is_mean is not None and is_mean > 0)
        robust_rows.append({
            "rank": r_is["rank"], "cond_key": r_is["cond_key"], "cond_cn": r_is["cond_cn"],
            "n_rows": nAr, "excess_mean_row": round(mAr, 6) if np.isfinite(mAr) else None,
            "increment_vs_robust_family": round(float(incr), 6) if np.isfinite(incr) else None,
            "nw_t": round(float(t_nwr), 4) if np.isfinite(t_nwr) else None,
            "days_covered": int(ndr), "direction_consistent_robust": dir_r})

    # ---- verdict(prereg verdict_rule 逐字实现) ----
    main_pass = any(r["main_gate_pass"] for r in oos_rows)
    incs = [r["increment_vs_family"] for r in oos_rows if r["increment_vs_family"] is not None]
    best_inc, best_rank, best_t = None, None, None
    if incs:
        bi = max(range(len(oos_rows)),
                 key=lambda i: (oos_rows[i]["increment_vs_family"]
                                if oos_rows[i]["increment_vs_family"] is not None else -np.inf))
        best_rank = oos_rows[bi]["rank"]
        best_inc = oos_rows[bi]["increment_vs_family"]
        best_t = oos_rows[bi]["nw_t"]
    if main_pass:
        verdict = "PASS"
    else:
        bt = best_t if best_t is not None else float("nan")
        if (np.isfinite(bt) and best_inc is not None and (
                (T_BOUND_LO <= bt < T_PASS) or (best_inc > 0 and bt < T_PASS))):
            verdict = "BOUNDARY"
        else:
            verdict = "FAIL"
    passing = [r["cond_cn"] for r in oos_rows if r["main_gate_pass"]]
    rob_dir_ok = [r["cond_cn"] for r in robust_rows if r["direction_consistent_robust"]]

    # ---- 落盘 + 控制台摘要 ----
    out = {
        "prereg_path": PREREG_PATH,
        "prereg_fp": fp,
        "script": "analysis/portrait_hr7_runner_20261120.py",
        "verdict": verdict,
        "verdict_basis": {
            "main_gate_passed": bool(main_pass),
            "main_gate_rules": passing,
            "best_increment_rule_rank": best_rank,
            "best_increment": best_inc,
            "best_nw_t": best_t,
            "rule": "PASS=主判据(>=1条 增量>0 且 叠加后NW(20)t>=2.5 且 方向与IS一致 且 命中天>=300); "
                    "BOUNDARY=最优增量规则 t∈[2.0,2.5) 或 增量>0但t<2.5; FAIL=其余 (PREREG verdict_rule)",
            "falsified_refs_note": "FAIL 时归档 falsified_rules(PORTRAIT-COMBO-HR7)",
        },
        "meta": {
            "purpose": "H-R7 家族内形态精化预注册验证(零交易权, 只读, 不自动接线)",
            "params": {
                "D_LO_storage": D_LO, "pattern_track": "全历史(v3口径)",
                "IS": "%d-%d" % (IS_LO, IS_HI), "OOS": "%d-%d" % (OOS_LO, OOS_HI),
                "HOLD": HOLD, "DS_MOD": DS_MOD, "N_MIN_DS": N_MIN_DS, "OVERCAP": OVERCAP,
                "T_SCREEN_IS_NW": T_SCREEN, "NW_LAG": NW_LAG, "T_PASS": T_PASS,
                "T_BOUND_LO": T_BOUND_LO, "DAYS_MIN": DAYS_MIN, "TOP_K": TOP_K,
                "family": "pos60==b3 AND vola20==b0 (H-R6规则8)",
                "robust_variant": "pos60>=b3 AND vola20==b0 (H-R6规则5)",
            },
            "qfq_full_valid_rows": int(total),
            "stocks_loaded": int(stats["stocks"]),
            "stocks_stored": int(stats["stocks_stored"]),
            "rows_fwd_valid": int(N),
            "rows_dropped_nonfinite_fwd": int(stats["rows_dropped_nonfinite_fwd"]),
            "bad_ohlc_rows": int(stats["bad_ohlc_rows"]),
            "stocks_too_short": int(stats["stocks_too_short"]),
            "date_min": int(date_a.min()), "date_max": int(date_a.max()),
            "benchmark_days": bench_days, "benchmark_min_day_rows": min_bench_n,
            "IS_rows_full": n_is_full, "IS_rows_ds": int(len(ds_idx)),
            "OOS_rows": int(len(oos_idx)),
            "bucket_edges": {"pos60": e_pos6, "vola20": e_vol6,
                             "note": "IS 降采样行内 20/40/60/80 分位(np.percentile linear), 冻结, OOS 复用"},
            "family": {"IS_ds_n": nF, "IS_full_rows": int(np.count_nonzero(family[in_is])),
                       "OOS_rows": nF_oos, "robust_OOS_rows": nR_oos},
            "condition_pool": [{"key": k, "cn": cn, "v3_col": int(COND_COLS[i])}
                               for i, (k, cn) in enumerate(COND32)],
            "condition_pool_count_note": "PREREG 文字标题写'31个', 冻结枚举实为 32 条(判据以 JSON 枚举文字为准), 如实披露",
            "cohens_d_def": "d=(mean(条件∧家族)-mean(家族))/s_pooled; s_pooled=sqrt(((nA-1)s2A+(nF-1)s2F)/(nA+nF-2)), ddof=1; 两组有重叠(家族含条件组), 按 PREREG 字面口径",
            "rough_t_def": "日度聚合(命中日等权均值)后普通 t=mean/(std(ddof=1)/sqrt(n_days)); 无自相关校正",
            "direction_def": "OOS 叠加后行级超额均值>0 且 IS 叠加后行级超额均值>0 (同 H-R6)",
            "impl_checks": {
                "C1_universe_v3_report": {"ok": c1_ok,
                                          "detail": "全宇宙全历史 36 标签计数 == v3 报告 pattern_stats n (rows_valid=%d)" % v3rep["meta"]["sample"]["rows_valid"]},
                "C2_sample_v3_flush": {"ok": True, "detail": sample_detail},
                "C3_sample_hr6_feats": {"ok": True, "detail": "含于 C2 每股 hr6_pos60_vola20_ok"},
                "C4_anchors": {"ok": True, "detail": {
                    "rows_fwd_valid": N, "IS_rows_ds": int(len(ds_idx)),
                    "OOS_rows": int(len(oos_idx)), "benchmark_days": bench_days,
                    "rule8_n_ds": nF, "edges_6dp_equal_hr6": True}},
                "family_mask_check": {"ok": fam_check_ok, "detail": fam_check_detail},
            },
            "notes": [
                "家族轨道=H-R6 口径: 逐股 >=2016 截断序列上算 pos60/vola20(前收 pc[0]=c[0]), 与 H-R6 存储集合/降采样索引完全一致(C4 锚点逐项相等)",
                "形态轨道=v3 口径: 逐股全历史序列上算 36 标签(MACD/RSI/KDJ 递归含 2016 前历史; ret1 首行 NaN); 存储行取交集(>=2016 且 fwd20 可计算)",
                "全宇宙坏 OHLC 行=0(跑前 SQL 实测), 故 H-R6(保留坏行)与 v3(剔除坏行)两套行约定在当前库上无行为差异; 载入中实测坏行=%d" % stats["bad_ohlc_rows"],
                "换手率=daily_basic(实为 20200102 起), H-R6 与 v3 载入口径内容一致; turn_extreme 需 250 日全部有效 -> 仅 2021+ 可命中",
                "基准=同日全市场(qfq 全部有效行, 含退市/科创/北交) fwd20 等权中位; 超额=fwd20-基准",
                "降采样=存储行全局序号 idx%%4==0 且日期在 IS 窗(同 H-R6), 与 H-R6 IS_rows_ds=%d 锚点一致" % HR6_ANCHOR["IS_rows_ds"],
                "IS 筛选: 叠加后日度聚合 NW(20) t>=%.1f 且 d>0 且 n(降采样)>=%d 且 n<=家族%.0f%%; 按 d 降序 Top%d 冻结" % (T_SCREEN, N_MIN_DS, OVERCAP * 100, TOP_K),
                "OOS 主判据的增量=叠加后行级超额均值-家族本身 OOS 行级超额均值(全量行, 同 H-R6 行级口径); NW t 为日度聚合 NW(20)",
                "多重检验补偿沿 E3 阈值 2.5; trial_budget 画像族如实=7",
                "边界: 零交易权; PASS 不自动接线须主人批准另走变更程序",
            ],
        },
        "IS": {
            "rows_full": n_is_full, "rows_ds": int(len(ds_idx)),
            "n_conditions": len(COND32), "n_screen_pass": n_screen_pass,
            "family_stats": fam_is_stats,
            "top10": top,
            "all_conditions": is_rows,
        },
        "OOS": {
            "rows": int(len(oos_idx)), "days_all": int(len(np.unique(date_a[oos_idx]))),
            "family_baseline": fam_oos_stats,
            "robust_baseline": rob_oos_stats,
            "rules": oos_rows,
            "robust_check": robust_rows,
            "robust_direction_consistent_rules": rob_dir_ok,
            "main_gate": {"passed": bool(main_pass), "rules": passing},
        },
        "run_info": {
            "built_at": time.strftime("%F %T"),
            "runtime_sec": round(time.time() - T0, 1),
            "argv": list(sys.argv[1:]),
        },
    }
    path = OUT2 if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print("\n== H-R7 结果 (fingerprint %s) ==" % fp)
    print("IS 家族基准: %s" % fam_is_stats)
    print("IS Top%d (条件 / d / IS-NW t / n_ds / 叠加后均值 / 粗t):" % len(top))
    for r in top:
        print("  %2d. %-14s(%s) | d=%s t=%s n=%d meanA=%s rough=%s"
              % (r["rank"], r["cond_key"], r["cond_cn"], r["d"], r["nw_t"],
                 r["n_ds"], r["excess_mean_A"], r["rough_t"]))
    print("OOS 家族基准线: %s" % fam_oos_stats)
    print("OOS 稳健域基准线: %s" % rob_oos_stats)
    print("OOS Top%d 全披露 (n / 叠加后均值 / 增量 / NW20t / 命中天 / 方向 / 主判据):" % len(oos_rows))
    for r in oos_rows:
        print("  %2d. %-14s | n=%6d mean=%s inc=%s t=%s days=%4d dir=%s gate=%s"
              % (r["rank"], r["cond_key"],
                 r["n_rows"],
                 ("N/A" if r["excess_mean_row"] is None else "%+.6f" % r["excess_mean_row"]),
                 ("N/A" if r["increment_vs_family"] is None else "%+.6f" % r["increment_vs_family"]),
                 ("N/A" if r["nw_t"] is None else "%7.3f" % r["nw_t"]),
                 r["days_covered"], "OK" if r["direction_consistent"] else "NO",
                 "PASS" if r["main_gate_pass"] else "-"))
    print("稳健域方向一致(叠加后>0): %s" % (rob_dir_ok if rob_dir_ok else "无"))
    print("主判据: %s" % ("通过" if main_pass else "未通过"))
    print("VERDICT: %s" % verdict)
    print("落盘: %s (%.1fs)" % (path, time.time() - T0))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] not in ("run2",):
        print("用法: python -I analysis/portrait_hr7_runner_20261120.py [run2]")
        sys.exit(2)
    sys.exit(main())
