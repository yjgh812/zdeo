#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_combo_hr6_runner_20261120.py — H-R6 特征组合规则·预注册验证 runner
================================================================================
PREREG: data/PREREG_PORTRAIT_COMBO_HR6_V1_20261120.json (指纹 dc1b6c0df6b26caf 跑前冻结)
假设: IS(2016-2022)挖掘的 1-2 条件特征组合规则, 在 OOS(2023-2026) 对 fwd20 超额
      (对同日全市场 fwd20 等权中位)有稳定正期望。
判据: 主判据 = Top10 中至少一条 OOS 超额均值>0 且 日度聚合 NW(20) t>=2.5
      且覆盖天数>=300 且方向与 IS 一致; BOUNDARY = 最优规则 t∈[2.0,2.5); FAIL=其余。
流程: 全量日线(>=20160101 有效行) 19 特征(v2 同式) + fwd20 + 日中位基准
      -> IS 按 4:1 降采样(idx%4==0) -> 139 条件矩阵(15 数值 x 9 + 4 布尔 x 1)
      -> 矩阵法(CᵀC / CᵀD / CᵀD2 / CᵀP / 逐日命中计数) 一次算全 9730 规则
      -> IS 筛选(n_ds>=5000 且超额均值>0, 粗 t 排序 Top10 冻结)
      -> OOS 全量行逐规则重建掩码(IS 冻结分桶边) -> 日度聚合 NW(20) -> verdict。
实现校验: 6 样本股全历史上 19 特征与 feature_attr_v2_20261120.compute_all
      逐元素对照(rtol=atol=1e-15, equal_nan), 不一致 exit 4(fail-closed)。
用法: python -I analysis/portrait_combo_hr6_runner_20261120.py [run2]
零交易权; 数据只读; 结果不自动接线。
双跑一致性: run1/run2 两份 JSON 剔除顶层 run_info(built_at/runtime) 后应逐字节一致。
"""
import os

# 单线程 BLAS: 保证矩阵乘数值确定性(run1/run2 逐字节一致的前提之一)
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import hashlib
import itertools
import json
import sqlite3
import sys
import time

import numpy as np
import talib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PREREG_PATH = r"D:\zcodesj\data\PREREG_PORTRAIT_COMBO_HR6_V1_20261120.json"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT1 = r"D:\zcodesj\reports\HR6_COMBO_RESULT_20261120.json"
OUT2 = OUT1.replace(".json", "_run2.json")

D_LO = 20160101                 # qfq 载入下限
IS_LO, IS_HI = 20160101, 20221231
OOS_LO, OOS_HI = 20230101, 20261119
HOLD = 20                       # fwd20
DS_MOD = 4                      # 4:1 降采样 idx%4==0
N_MIN_DS = 5000                 # IS 筛选: 降采样行 n 下限
NW_LAG = 20
T_PASS = 2.5
T_BOUND_LO = 2.0
DAYS_MIN = 300
TOP_K = 10

NUM15 = ["pos60", "turn_pctl60", "turn_z60", "bias20", "vol_z20", "ret5", "turn_rise",
         "pos250", "ret20", "vola20", "consec_vol_up", "hh60", "streak_up",
         "lower_shadow", "body_ratio"]
BOOL4 = ["ma_align_dn", "ll20", "vol_breakout20", "bull_engulf"]
FEATS19 = NUM15 + BOOL4
NFEAT = 19
FEAT_IDX = {n: j for j, n in enumerate(FEATS19)}

# 139 条件固定顺序: 数值特征 x (==b0..b4, >=b1..b4) = 9 条/特征; 布尔 x 1
CONDS = []
for _f in NUM15:
    for _b in range(5):
        CONDS.append(("eq", _f, _b))
    for _b in range(1, 5):
        CONDS.append(("ge", _f, _b))
for _f in BOOL4:
    CONDS.append(("true", _f, None))
K = len(CONDS)
assert K == 139, K

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
    """Newey-West HAC t 值(与 portrait_regime_hr1_runner.py 同式)。"""
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


# ---------------- 滚动原语(与 feature_attr_v2_20261120.py 完全一致) ----------------

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


def streaks_up_cvu(ret1, volsig, vol_ma_nan):
    """streak_up + consec_vol_up(与 v2 streaks_and_consec 的 up/cvu 分支逐语句同逻辑)。"""
    n = len(ret1)
    up = np.zeros(n)
    cvu = np.zeros(n)
    cu = pv = 0
    for i in range(n):
        r = ret1[i]
        if r > 0:
            cu += 1
        else:
            cu = 0
        if vol_ma_nan[i]:
            pv = 0
        elif volsig[i]:
            pv = pv + 1 if pv > 0 else 1
        else:
            pv = pv - 1 if pv < 0 else -1
        up[i] = cu
        cvu[i] = pv if pv > 0 else 0.0
    return up, cvu


def compute_feats19(o, h, l, c, v, turn):
    """19 特征(与 feature_attr_v2_20261120.compute_all 对应列同式复制)。"""
    n = len(c)
    pc = np.empty(n)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    f = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        ret1 = c / pc - 1.0
        hl = h - l + 1e-9
        f["body_ratio"] = np.abs(c - o) / hl
        f["lower_shadow"] = (np.minimum(o, c) - l) / hl

        ma5 = ma_cumsum(c, 5)
        ma10 = ma_cumsum(c, 10)
        ma20 = ma_cumsum(c, 20)
        ma60 = ma_cumsum(c, 60)
        rmin20 = roll_maxmin(c, 20, np.min)
        rmin60 = roll_maxmin(c, 60, np.min)
        rmax60 = roll_maxmin(c, 60, np.max)
        f["hh60"] = (c >= rmax60).astype(np.float64)
        f["pos60"] = (c - rmin60) / (rmax60 - rmin60)
        rmin250 = roll_maxmin(c, 250, np.min)
        rmax250 = roll_maxmin(c, 250, np.max)
        f["pos250"] = (c - rmin250) / (rmax250 - rmin250)
        f["ma_align_dn"] = ((ma5 < ma10) & (ma10 < ma20) & (ma20 < ma60)).astype(np.float64)
        f["bias20"] = c / ma20 - 1.0
        f["ll20"] = (c <= rmin20).astype(np.float64)

        def shift_fill(a, k):
            s = np.empty(n)
            s[:min(k, n)] = a[0]
            if k < n:
                s[k:] = a[:-k]
            return s

        f["ret5"] = c / shift_fill(c, 5) - 1.0
        f["ret20"] = c / shift_fill(c, 20) - 1.0
        f["vola20"] = roll_std_comb(ret1, 20)

        ma5v = ma_cumsum(v, 5)
        ma20v = ma_cumsum(v, 20)
        f["vol_z20"] = (v - ma20v) / roll_std_comb(v, 20)
        volsig = v > ma5v
        up, cvu = streaks_up_cvu(ret1, volsig, np.isnan(ma5v))
        f["streak_up"] = up
        f["consec_vol_up"] = cvu

        rmax20 = roll_maxmin(c, 20, np.max)
        prev_rmax20 = np.full(n, np.nan)
        if n > 1:
            prev_rmax20[1:] = rmax20[:-1]
        f["vol_breakout20"] = ((c > prev_rmax20) & (v > 1.5 * ma5v)).astype(np.float64)

        out = talib.CDLENGULFING(o, h, l, c)
        f["bull_engulf"] = (out > 0).astype(np.float64)

        tnan = np.isnan(turn)
        t5t = roll_mean_nan(turn, 5)
        t20t = roll_mean_nan(turn, 20)
        f["turn_rise"] = t5t / t20t - 1.0
        f["turn_pctl60"] = np.full(n, np.nan)
        if n >= 60:
            W = np.lib.stride_tricks.sliding_window_view(turn, 60)
            last = W[:, -1]
            wnan = np.isnan(W).any(axis=1)
            cnt = (W <= last[:, None]).sum(axis=1) / 60.0
            f["turn_pctl60"][59:] = np.where(wnan, np.nan, cnt)
        t60m = roll_mean_nan(turn, 60)
        t60s = roll_std_nan(turn, 60)
        f["turn_z60"] = (turn - t60m) / t60s
    return f


def align_turn(dint, td):
    """换手率按日 searchsorted 对齐(同日无值 -> NaN; 与 v2 同式)。"""
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
    """daily_basic 全量 -> {c6: (dates int64 升序, turnover f64)}(流式按 PK 序分组)。"""
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


def v2_equivalence_check(db_by_code):
    """6 样本股全历史: 本 runner 19 特征 vs feature_attr_v2_20261120.compute_all 逐元素对照。"""
    sys.path.insert(0, r"D:\zcodesj\analysis")
    import feature_attr_v2_20261120 as v2m
    samples = ["000001", "600519", "300750", "002230", "000333", "601888"]
    detail = {}
    all_ok = True
    con = ro_connect(QFQ_DB)
    for code in samples:
        rows = con.execute(
            "SELECT date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq "
            "WHERE code=? AND close>0 AND volume>0 ORDER BY date", (code,)).fetchall()
        if len(rows) < 300:
            detail[code] = "skipped(rows<300)"
            continue
        m = len(rows)
        dint = np.fromiter((safe_dint(r[0]) for r in rows), np.int64, m)
        o = np.fromiter(((r[1] if r[1] is not None else np.nan) for r in rows), np.float64, m)
        h = np.fromiter(((r[2] if r[2] is not None else np.nan) for r in rows), np.float64, m)
        l = np.fromiter(((r[3] if r[3] is not None else np.nan) for r in rows), np.float64, m)
        c = np.fromiter(((r[4] if r[4] is not None else np.nan) for r in rows), np.float64, m)
        v = np.fromiter(((r[5] if r[5] is not None else np.nan) for r in rows), np.float64, m)
        amt = np.fromiter(((r[6] if r[6] is not None else np.nan) for r in rows), np.float64, m)
        turn = align_turn(dint, db_by_code.get(code))
        f_mine = compute_feats19(o, h, l, c, v, turn)
        f_v2, talib_ok = v2m.compute_all(o, h, l, c, v, amt, turn)
        per = {}
        for name in FEATS19:
            a, b = f_mine[name], f_v2[name]
            ok = bool(np.allclose(a, b, rtol=1e-15, atol=1e-15, equal_nan=True))
            per[name] = ok
            if not ok:
                all_ok = False
        if not talib_ok:
            all_ok = False
        detail[code] = {"n_rows": int(m), "talib_ok": bool(talib_ok), "features_ok": per}
        log("v2等价校验 %s: n=%d %s" % (code, m, "全部一致" if all(per.values()) and talib_ok else "存在不一致"))
    con.close()
    return all_ok, detail


def bucket_range_text(kind, b):
    if kind == "eq":
        if b == 0:
            return "x<e20"
        if b == 4:
            return "x>=e80"
        return "e%d<=x<e%d" % (b * 20, (b + 1) * 20)
    return "x>=e%d" % (b * 20)


def main():
    fp = verify_prereg()

    # ---- 1. 换手率源 ----
    db_by_code = load_daily_basic()
    log("daily_basic 覆盖 %d 只(turnover_rate)" % len(db_by_code))

    # ---- 2. v2 实现等价校验(fail-closed) ----
    ok, detail = v2_equivalence_check(db_by_code)
    if not ok:
        print("[FAIL-CLOSED] 19特征与 v2 compute_all 不一致, 终止(exit 4)")
        print(json.dumps(detail, ensure_ascii=False, indent=1))
        sys.exit(4)
    log("v2等价校验通过: 6 样本股 x 19 特征逐元素一致")

    # ---- 3. 主载入: qfq >=20160101 有效行, 逐股 19 特征 + fwd20 ----
    con = ro_connect(QFQ_DB)
    total = con.execute(
        "SELECT COUNT(*) FROM stock_daily_meta_qfq "
        "WHERE close>0 AND volume>0 AND CAST(date AS INTEGER)>=%d" % D_LO).fetchone()[0]
    log("qfq 有效行(>=%d) = %d, 预分配全局数组" % (D_LO, total))
    g_date = np.empty(total, np.int32)
    g_code = np.empty(total, np.int32)
    g_feat = np.empty((total, NFEAT), np.float32)
    g_fwd = np.empty(total, np.float32)

    cur = con.execute(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE close>0 AND volume>0 AND CAST(date AS INTEGER)>=%d ORDER BY code,date" % D_LO)
    code_ids = {}
    cur_code = None
    buf = []
    pos = 0
    stats = {"stocks": 0, "rows_dropped_nonfinite_fwd": 0, "bad_ohlc_rows": 0,
             "stocks_too_short": 0}

    def flush(code_raw, rows):
        nonlocal pos
        m = len(rows)
        c6 = code6(code_raw)
        cid = code_ids.setdefault(c6, len(code_ids))
        dint = np.fromiter((safe_dint(r[1]) for r in rows), np.int64, m)
        o = np.fromiter(((r[2] if r[2] is not None else np.nan) for r in rows), np.float64, m)
        h = np.fromiter(((r[3] if r[3] is not None else np.nan) for r in rows), np.float64, m)
        l = np.fromiter(((r[4] if r[4] is not None else np.nan) for r in rows), np.float64, m)
        c = np.fromiter(((r[5] if r[5] is not None else np.nan) for r in rows), np.float64, m)
        v = np.fromiter(((r[6] if r[6] is not None else np.nan) for r in rows), np.float64, m)
        stats["bad_ohlc_rows"] += int(np.count_nonzero((h < l) | (h <= 0) | (l <= 0)))
        if m <= HOLD:
            stats["stocks_too_short"] += 1
            return
        turn = align_turn(dint, db_by_code.get(c6))
        f = compute_feats19(o, h, l, c, v, turn)
        nh = m - HOLD
        fwd = c[HOLD:] / c[:-HOLD] - 1.0
        kept = np.isfinite(fwd)
        k = int(np.count_nonzero(kept))
        if k == 0:
            stats["rows_dropped_nonfinite_fwd"] += nh
            return
        stats["rows_dropped_nonfinite_fwd"] += nh - k
        sl = slice(pos, pos + k)
        g_date[sl] = dint[:nh][kept]
        g_code[sl] = cid
        Fm = np.empty((nh, NFEAT), np.float32)
        for j, name in enumerate(FEATS19):
            Fm[:, j] = f[name][:nh]
        g_feat[sl] = Fm[kept]
        g_fwd[sl] = fwd[kept]
        pos += k
        stats["stocks"] += 1
        if stats["stocks"] % 1000 == 0:
            log("进度 %d 只 / %d 行" % (stats["stocks"], pos))

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
    feat_a = g_feat[:N]
    fwd_a = g_fwd[:N]
    log("装配完成: %d 行(fwd20 有效) / %d 只 / %d~%d, 非有限fwd剔除 %d 行, bad_ohlc %d 行"
        % (N, stats["stocks"], int(date_a.min()), int(date_a.max()),
           stats["rows_dropped_nonfinite_fwd"], stats["bad_ohlc_rows"]))

    # ---- 4. 全市场基准: 同日 fwd20 中位 -> 超额 ----
    ud, inv = np.unique(date_a, return_inverse=True)
    counts = np.bincount(inv, minlength=len(ud))
    o2 = np.argsort(inv, kind="stable")
    fs = fwd_a[o2].astype(np.float64)
    bnds = np.concatenate(([0], np.cumsum(counts)))
    med = np.empty(len(ud))
    for i in range(len(ud)):
        med[i] = np.median(fs[int(bnds[i]):int(bnds[i + 1])])
    del fs, o2
    g_exc = np.empty(N, np.float32)
    g_exc[:] = (fwd_a.astype(np.float64) - med[inv]).astype(np.float32)
    del inv
    exc_a = g_exc[:N]
    min_bench_n = int(counts.min())
    log("基准: %d 个交易日(等权中位, 最小日样本 %d 行)" % (len(ud), min_bench_n))

    # ---- 5. IS 降采样 + 分桶边 + 139 条件矩阵 ----
    gidx = np.arange(N, dtype=np.int64)
    in_is = (date_a >= IS_LO) & (date_a <= IS_HI)
    n_is_full = int(np.count_nonzero(in_is))
    ds_idx = np.flatnonzero(in_is & (gidx % DS_MOD == 0))
    n_ds = len(ds_idx)
    ds_dates = date_a[ds_idx]
    log("IS(%d-%d): 全量 %d 行 -> 降采样 %d 行 (idx%%%d==0)" % (IS_LO, IS_HI, n_is_full, n_ds, DS_MOD))

    edges = {}
    bkt_ds = {}
    for name in NUM15:
        vals = feat_a[ds_idx, FEAT_IDX[name]].astype(np.float64)
        fin = np.isfinite(vals)
        e = None
        if int(np.count_nonzero(fin)) >= 100:
            e = np.percentile(vals[fin], [20, 40, 60, 80])
        edges[name] = e
        b = np.full(n_ds, -1, np.int8)
        if e is not None:
            b[fin] = np.digitize(vals[fin], e).astype(np.int8)
        bkt_ds[name] = b
        del vals

    C = np.zeros((n_ds, K), dtype=bool)
    col = 0
    for name in NUM15:
        b = bkt_ds[name]
        for bb in range(5):
            C[:, col] = (b == bb)
            col += 1
        for bb in range(1, 5):
            C[:, col] = (b >= bb)
            col += 1
    for name in BOOL4:
        C[:, col] = (feat_a[ds_idx, FEAT_IDX[name]] == 1.0)
        col += 1
    assert col == K
    del bkt_ds

    # ---- 6. 逐日分组矩阵乘: 全 9730 规则的 n/超额和/平方和/正占比/覆盖天数 ----
    E = exc_a[ds_idx].astype(np.float64)
    E2 = E * E
    POS = (fwd_a[ds_idx] > 0).astype(np.float64)
    sorder = np.argsort(ds_dates, kind="stable")
    ds_dates_s = ds_dates[sorder]
    ud2, starts = np.unique(ds_dates_s, return_index=True)
    bnds2 = np.append(starts, n_ds)
    n_days_is = len(ud2)
    log("IS 降采样覆盖 %d 个交易日; 开始逐日矩阵统计(%d 规则)" % (n_days_is, K + K * (K - 1) // 2))

    ACC_CC = np.zeros((K, K))
    ACC_REST = np.zeros((K, 3 * K))
    DAYC = np.zeros((K, K), np.int16)
    for i in range(n_days_is):
        g = slice(int(bnds2[i]), int(bnds2[i + 1]))
        idx_g = sorder[g]
        Cc = C[idx_g].astype(np.float64)
        cc_d = Cc.T @ Cc
        DAYC += (cc_d > 0)
        ACC_CC += cc_d
        Dc = Cc * E[idx_g][:, None]
        D2c = Cc * E2[idx_g][:, None]
        Pc = Cc * POS[idx_g][:, None]
        Md = np.concatenate([Dc, D2c, Pc], axis=1)
        ACC_REST += Cc.T @ Md
    del C
    ACC_CD = ACC_REST[:, 0:K]
    ACC_CE2 = ACC_REST[:, K:2 * K]
    ACC_CP = ACC_REST[:, 2 * K:3 * K]
    del ACC_REST
    log("逐日矩阵统计完成")

    # ---- 7. 规则抽取 + IS 筛选 + Top10 冻结 ----
    pairs = list(itertools.combinations(range(K), 2))
    RI = np.array([i for i in range(K)] + [p[0] for p in pairs], np.int64)
    RJ = np.array([i for i in range(K)] + [p[1] for p in pairs], np.int64)
    NR = len(RI)
    assert NR == 139 + 139 * 138 // 2 == 9730, NR
    n_r = ACC_CC[RI, RJ]
    s_r = ACC_CD[RI, RJ]
    s2_r = ACC_CE2[RI, RJ]
    p_r = ACC_CP[RI, RJ]
    d_r = DAYC[RI, RJ].astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_r = s_r / n_r
        var_r = np.maximum(s2_r / n_r - mean_r * mean_r, 0.0)
        std_r = np.sqrt(var_r)
        t_r = np.where((d_r > 0) & (std_r > 0),
                       mean_r / (std_r / np.sqrt(d_r)), -np.inf)
    n_pass_nmin = int(np.count_nonzero(n_r >= N_MIN_DS))
    cand = (n_r >= N_MIN_DS) & (mean_r > 0) & (t_r > -np.inf)
    cand_ids = np.flatnonzero(cand)
    n_pass_screen = len(cand_ids)
    order = cand_ids[np.argsort(-t_r[cand_ids], kind="stable")]
    top = order[:min(TOP_K, len(order))]
    log("IS 筛选: 规则总数 %d; n>=%d 的 %d; 过筛(n且均值>0且t有限) %d; Top%d 冻结"
        % (NR, N_MIN_DS, n_pass_nmin, n_pass_screen, len(top)))

    def conds_of(rid):
        i, j = int(RI[rid]), int(RJ[rid])
        return [i] if i == j else [i, j]

    def cond_text(ci):
        kind, feat, b = CONDS[ci]
        if kind == "eq":
            return "%s==b%d" % (feat, b)
        if kind == "ge":
            return "%s>=b%d" % (feat, b)
        return "%s==1" % feat

    def rule_text(rid):
        return " AND ".join(cond_text(ci) for ci in conds_of(rid))

    def cond_json(ci):
        kind, feat, b = CONDS[ci]
        jj = {"feature": feat, "form": ("bool_true" if kind == "true" else kind)}
        if kind in ("eq", "ge"):
            jj["b"] = int(b)
            jj["bucket_range"] = bucket_range_text(kind, int(b))
            e = edges[feat]
            if e is not None:
                jj["edges"] = [round(float(x), 6) for x in e]
        return jj

    is_top_rows = []
    for rank, rid in enumerate(top, 1):
        rid = int(rid)
        is_top_rows.append({
            "rank": rank, "rule_id": rid, "rule_text": rule_text(rid),
            "conditions": [cond_json(ci) for ci in conds_of(rid)],
            "n_ds": int(n_r[rid]),
            "excess_mean": round(float(mean_r[rid]), 6),
            "excess_std_row": round(float(std_r[rid]), 6),
            "days_covered_ds": int(d_r[rid]),
            "rough_t": round(float(t_r[rid]), 4),
            "share_pos": round(float(p_r[rid] / n_r[rid]), 4),
        })

    # ---- 8. OOS 全量行: 逐规则重建掩码(IS 冻结分桶边) -> 日度聚合 NW(20) ----
    oos_idx = np.flatnonzero((date_a >= OOS_LO) & (date_a <= OOS_HI))
    n_oos = len(oos_idx)
    oos_days_all = int(len(np.unique(date_a[oos_idx])))
    bkt_oos = {}
    for name in NUM15:
        vals = feat_a[oos_idx, FEAT_IDX[name]].astype(np.float64)
        fin = np.isfinite(vals)
        b = np.full(n_oos, -1, np.int8)
        if edges[name] is not None:
            b[fin] = np.digitize(vals[fin], edges[name]).astype(np.int8)
        bkt_oos[name] = b
        del vals
    oos_dates = date_a[oos_idx]
    oos_exc = exc_a[oos_idx].astype(np.float64)
    oos_pos = fwd_a[oos_idx] > 0
    log("OOS(%d-%d): 全量 %d 行 / %d 个交易日" % (OOS_LO, OOS_HI, n_oos, oos_days_all))

    def oos_cond_mask(ci):
        kind, feat, b = CONDS[ci]
        if kind == "true":
            return feat_a[oos_idx, FEAT_IDX[feat]] == 1.0
        bb = bkt_oos[feat]
        return (bb == b) if kind == "eq" else (bb >= b)

    is_row_by_rank = {r["rank"]: r for r in is_top_rows}
    oos_rows = []
    for rank, rid in enumerate(top, 1):
        rid = int(rid)
        cis = conds_of(rid)
        mask = oos_cond_mask(cis[0])
        if len(cis) == 2:
            mask &= oos_cond_mask(cis[1])
        n = int(np.count_nonzero(mask))
        row_mean = float(oos_exc[mask].mean()) if n else float("nan")
        share_pos = float(oos_pos[mask].mean()) if n else float("nan")
        days = 0
        daily_mean = float("nan")
        t_nw = float("nan")
        first_hit = last_hit = None
        if n:
            d_hit = oos_dates[mask]
            e_hit = oos_exc[mask]
            udh, invh = np.unique(d_hit, return_inverse=True)
            sums = np.bincount(invh, weights=e_hit, minlength=len(udh))
            cnts = np.bincount(invh, minlength=len(udh))
            daily = sums / np.maximum(cnts, 1)
            days = int(len(udh))
            daily_mean = float(daily.mean())
            t_nw, _dm = nw_tstat(daily, NW_LAG)
            first_hit, last_hit = int(udh[0]), int(udh[-1])
        is_mean = is_row_by_rank[rank]["excess_mean"]
        dir_ok = bool(row_mean > 0 and is_mean > 0)
        oos_rows.append({
            "rank": rank, "rule_id": rid, "rule_text": rule_text(rid),
            "conditions": [cond_json(ci) for ci in cis],
            "n_rows": n,
            "excess_mean_row": round(row_mean, 6) if np.isfinite(row_mean) else None,
            "share_pos": round(share_pos, 4) if np.isfinite(share_pos) else None,
            "daily_mean": round(daily_mean, 6) if np.isfinite(daily_mean) else None,
            "nw_t": round(float(t_nw), 4) if np.isfinite(t_nw) else None,
            "days_covered": days,
            "direction_consistent": dir_ok,
            "is_excess_mean": is_mean,
            "is_rough_t": is_row_by_rank[rank]["rough_t"],
            "first_hit_date": first_hit, "last_hit_date": last_hit,
        })

    # ---- 9. verdict ----
    fin_ts = [r["nw_t"] for r in oos_rows if r["nw_t"] is not None]
    best_t = max(fin_ts) if fin_ts else None
    best_rule = None
    if fin_ts:
        best_rank = max(oos_rows, key=lambda r: (r["nw_t"] if r["nw_t"] is not None else -np.inf))["rank"]
        best_rule = next(r for r in oos_rows if r["rank"] == best_rank)["rule_text"]
    main_pass = any(
        r["excess_mean_row"] is not None and r["excess_mean_row"] > 0
        and r["nw_t"] is not None and r["nw_t"] >= T_PASS
        and r["days_covered"] >= DAYS_MIN and r["direction_consistent"]
        for r in oos_rows)
    if main_pass:
        verdict = "PASS"
    elif best_t is not None and T_BOUND_LO <= best_t < T_PASS:
        verdict = "BOUNDARY"
    else:
        verdict = "FAIL"
    passing = [r for r in oos_rows
               if r["excess_mean_row"] is not None and r["excess_mean_row"] > 0
               and r["nw_t"] is not None and r["nw_t"] >= T_PASS
               and r["days_covered"] >= DAYS_MIN and r["direction_consistent"]]

    # ---- 10. 落盘 + 控制台摘要 ----
    out = {
        "prereg_path": PREREG_PATH,
        "prereg_fp": fp,
        "script": "analysis/portrait_combo_hr6_runner_20261120.py",
        "verdict": verdict,
        "verdict_basis": {
            "main_gate_passed": bool(main_pass),
            "main_gate_rules": [r["rule_text"] for r in passing],
            "best_rule_text": best_rule,
            "best_nw_t": best_t,
            "rule": "PASS=主判据; BOUNDARY=最优规则t∈[2.0,2.5); FAIL=其余 (PREREG verdict_rule)",
        },
        "meta": {
            "purpose": "H-R6 特征组合规则预注册验证(零交易权, 只读, 不自动接线)",
            "params": {
                "D_LO": D_LO, "IS": "%d-%d" % (IS_LO, IS_HI), "OOS": "%d-%d" % (OOS_LO, OOS_HI),
                "HOLD": HOLD, "DS_MOD": DS_MOD, "N_MIN_DS": N_MIN_DS, "NW_LAG": NW_LAG,
                "T_PASS": T_PASS, "T_BOUND_LO": T_BOUND_LO, "DAYS_MIN": DAYS_MIN, "TOP_K": TOP_K,
            },
            "qfq_rows_ge2016_valid": int(total),
            "stocks": int(stats["stocks"]),
            "rows_fwd_valid": int(N),
            "rows_dropped_nonfinite_fwd": int(stats["rows_dropped_nonfinite_fwd"]),
            "bad_ohlc_rows": int(stats["bad_ohlc_rows"]),
            "stocks_too_short": int(stats["stocks_too_short"]),
            "date_min": int(date_a.min()), "date_max": int(date_a.max()),
            "benchmark_days": int(len(ud)),
            "benchmark_min_day_rows": min_bench_n,
            "IS_rows_full": n_is_full, "IS_rows_ds": int(n_ds), "IS_days_ds": int(n_days_is),
            "OOS_rows": int(n_oos), "OOS_days_all": oos_days_all,
            "n_conditions": K,
            "n_rules_total": int(NR),
            "n_rules_pass_nmin": n_pass_nmin,
            "n_rules_pass_screen": n_pass_screen,
            "feature_cols_order": FEATS19,
            "v2_impl_check": {"all_ok": True, "detail": detail},
            "notes": [
                "全局行序号=装配后 fwd20 有效行在载入顺序(逐股、股内日期升序)中的序号; 降采样取 idx%4==0",
                "分桶边=IS 降采样行内该特征有限值的 20/40/60/80 分位(np.percentile linear); NaN 行桶=-1 不命中任何该特征条件; OOS 复用 IS 冻结边",
                "turn 系特征(turn_pctl60/turn_z60/turn_rise)依赖 daily_basic(20200102 起), 2016-2019 为 NaN 不命中; 其 IS 分位边由 2020-2022 有限值计算",
                "基准=同日全市场(qfq 库全部有效行, 含退市/科创/北交) fwd20 等权中位; 超额= fwd20 - 基准",
                "IS 粗 t = 行级超额均值 / (行级总体标准差 ddof=0 / sqrt(IS覆盖天数))",
                "OOS 主判据的 'OOS超额均值>0' 取行级(全部命中行)均值, 与 HR1 verdict 口径一致; 日度均值另列披露",
                "方向一致定义: OOS 行级超额均值>0(IS 均值经筛选恒>0)",
                "OOS 行受 fwd20 可计算限制, 实际最晚命中日 <= 各股最后有效日前 20 个交易日",
                "实现一致性: 6 样本股全历史 19 特征与 feature_attr_v2_20261120.compute_all 逐元素对照(rtol=atol=1e-15, equal_nan), 不一致即 exit 4",
                "矩阵法: 单条件取对角线, 两条件取 CᵀC/CᵀD/CᵀD2/CᵀP 上三角; 覆盖天数=逐日分组命中矩阵(DAYC)按规则求和",
                "单线程 BLAS(OPENBLAS/OMP/MKL=1) 保证双跑确定性; run1/run2 剔除顶层 run_info 后须逐字节一致",
                "边界: 零交易权; PASS 不自动接线须主人批准另走变更程序; 多重检验补偿沿 E3 阈值 2.5",
            ],
        },
        "IS": {
            "rows_full": n_is_full, "rows_ds": int(n_ds), "days_ds": int(n_days_is),
            "n_rules_total": int(NR), "n_rules_pass_nmin": n_pass_nmin,
            "n_rules_pass_screen": n_pass_screen,
            "bucket_edges": {name: ([round(float(x), 6) for x in edges[name]] if edges[name] is not None else None)
                             for name in NUM15},
            "top10": is_top_rows,
        },
        "OOS": {
            "rows": int(n_oos), "days_all": oos_days_all,
            "rules": oos_rows,
            "main_gate": {"passed": bool(main_pass), "best_rule_text": best_rule, "best_nw_t": best_t},
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

    print("\n== H-R6 结果 (fingerprint %s) ==" % fp)
    print("IS Top10 (n_ds / 行级超额均值 / 粗t / 覆盖天 / 正占比):")
    for r in is_top_rows:
        print("  %2d. %-42s | n_ds=%7d mean=%+.6f t=%8.3f days=%4d pos=%.1f%%"
              % (r["rank"], r["rule_text"], r["n_ds"], r["excess_mean"],
                 r["rough_t"], r["days_covered_ds"], r["share_pos"] * 100))
    print("OOS (%d-%d 全量 %d 行):" % (OOS_LO, OOS_HI, n_oos))
    for r in oos_rows:
        print("  %2d. %-42s | n=%7d mean_row=%s daily=%s NW20t=%s days=%4d pos=%s dir=%s"
              % (r["rank"], r["rule_text"], r["n_rows"],
                 ("N/A" if r["excess_mean_row"] is None else "%+.6f" % r["excess_mean_row"]),
                 ("N/A" if r["daily_mean"] is None else "%+.6f" % r["daily_mean"]),
                 ("N/A" if r["nw_t"] is None else "%7.3f" % r["nw_t"]),
                 r["days_covered"],
                 ("N/A" if r["share_pos"] is None else "%.1f%%" % (r["share_pos"] * 100)),
                 "OK" if r["direction_consistent"] else "NO"))
    print("主判据: %s (最优规则 %s, 最优 NW20 t=%s)"
          % ("通过" if main_pass else "未通过", best_rule,
             ("N/A" if best_t is None else "%.3f" % best_t)))
    print("VERDICT: %s" % verdict)
    print("落盘: %s (%.1fs)" % (path, time.time() - T0))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] not in ("run2",):
        print("用法: python -I analysis/portrait_combo_hr6_runner_20261120.py [run2]")
        sys.exit(2)
    sys.exit(main())
