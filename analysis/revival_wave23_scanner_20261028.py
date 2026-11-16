# -*- coding: utf-8 -*-
"""revival_wave23_scanner_20261028.py — 全员复活模拟盘验证 Wave2 九项 + Wave3 八项 扫描器

合同: analysis/revival_paper_test_PREREG_20261027.md（统一合同不变: 每方法5只/100股/
委托价=信号日收盘价/预算帽7500/宇宙过滤/方法间去重/-8%止损/20日窗口）。
主人令 2026-10-28「W2 九项 + W3 八项共 17 项待建名单（需定义还原/模型重训），用真实数据」。

W2 九项（定义档案还原）:
  revival_amp_a       理想振幅A单因子（下跌日振幅均值-上涨日振幅均值,20日; 高值好 IC+0.060）
  revival_ampA_3f     bp+ep+振幅A 三因子组合（黑名单原版口径 = bp_ep_amp_monthly_v2 策略B）
  revival_amp_b       理想振幅B单因子（rel_amount切割振幅; 低值好 IC-0.069）
  revival_icir_sue    多因子ICIR合成含SUE（还原口径: PIT因子池+eps同比SUE, 24月ICIR加权）
  revival_anom118     118异象7因子（还原口径: 本地可算中国异象核心7因子等权）
  revival_chan        缠论二买（去前视 chanlun_signals_nolookahead 确认日口径）
  revival_bpep_weekly P2-C bp_ep周滚动重评（月频池语义, 周频重评分）
  revival_v52_weekly  v52_select 六层深跌启动 周批（周批次口径=当期快照）
  revival_hy_tail     P2-B 行业动量尾部过滤（RS rank>=25 剔除+顺延, PREREG 原规则）

W3 八项（模型族）:
  revival_dbd_gru     DBD-GRU 分钟双支路模型 T 日截面推理（权重 dbd_gru_minute_v1.pth 在档）
  revival_lstm        自研LSTM（deep_seq 重测配方重训: 5特征60窗/Top800/CPU小模型）
  revival_kronos_ft   Kronos微调 → 缺件（见 note; 微调需GPU长训, 官方管线已知泄漏bug）
  revival_rl          RL(A2C) → 缺件（ckpt在档但观测面板停更20260731）
  revival_v8_3dir     V8增强三方向 → 定义不可还原（缺件, 需主人指认）
  revival_v51_fusion  V5.1 full_fusion 管线实弹（select_stocks_daily.py --config full_fusion_v51）
  revival_p2          P2信号独立下单（stock_signals_p2 在档推理, 信号日=20260921 如实标注）
  revival_composite   综合信号 composite_score 独立下单（stock_signals 在产表 @T）

主人令 2026-10-29「60分钟确认层正式接入生产选股链」:
  出票前对每只候选跑 60 分钟确认层（analysis/timeframe_60min_20261029.py, minute_5聚合,
  MA5 vs MA10 趋势): 死叉=否决并顺延下一名候选补位; 金叉/中性/无数据=通过并在 pick 记录
  60min 字段。日线=方向层, 60分钟=确认层, 双周期共振入场。
"""
from __future__ import annotations
import json, sys, os, hashlib, statistics, subprocess, math
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
import sqlite3
import numpy as np

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
MAIN = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
META = r"D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db"
PIT_DB = os.path.join(ROOT, "factor_pit.db")
IND_DB = os.path.join(ROOT, "data", "industry_group_map_snapshot.db")
OUT = os.path.join(ROOT, "data", "revival_wave23_picks_20261028.json")  # 10/28冻结批次件(保留只读)
# 2026-11-09 补缺件令: 重跑出票按信号日命名落盘, 防覆盖冻结md5合同件; 冻结件消费方
# (batch_plan_gen/.executed/.cmd)均一次性已消费, 无活跃运行时读取方。
BUDGET_CAP = 7500.0
TOP_SUB = 15
STALE_MAX_DAYS = 5  # 信号时效窗(20261030主人令): 候选信号滞后>5交易日整源不出票——归档模型信号不得进入批次
LOOKBACK_ROWS = 790  # 3年分位/250日位置/120日id_vol/24月IC 所需
from stock_name_lookup import get_name  # 唯一名称接口


# ---------- 数据装载 ----------
def load_qfq():
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    dates = sorted({int(r[0]) for r in con.execute(
        "select distinct date from stock_daily_meta_qfq order by date desc limit ?", (LOOKBACK_ROWS,))})
    dmin = str(dates[0])
    series = defaultdict(lambda: defaultdict(list))
    for code, d, o, h, l, c, v, a in con.execute(
        "select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq "
        "where date>=? order by code,date", (dmin,)):
        s = series[code]
        s["date"].append(int(d)); s["open"].append(o); s["high"].append(h)
        s["low"].append(l); s["close"].append(c); s["vol"].append(v); s["amt"].append(a)
    con.close()
    for code, s in series.items():
        for k in s:
            s[k] = np.array(s[k], dtype=float)
    return series, dates


def base_universe(series, held_extra):
    held = set(held_extra)
    try:
        doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
        for p in doc["positions"]:
            c = str(p["code"]).lower()
            for pref in ("sh", "sz", "bj"):
                if c.startswith(pref):
                    c = c[len(pref):]
            held.add(c.zfill(6))
    except Exception:
        pass
    out = {}
    for code, s in series.items():
        n = len(s["close"])
        if n < 60 or code in held:
            continue
        if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
            continue
        cT = s["close"][-1]
        if not cT or cT > 30 or cT <= 0.5:
            continue
        name = get_name(code) or ""
        if "ST" in name.upper() or "退" in name:
            continue
        out[code] = s
    return out, held


# ---------- PIT 因子（dragon_quant.stock_factors_fundamental, 行级 pit_avail_date 闸） ----------
def pit_latest(T):
    """T日截面: 每票取 pit_avail_date<=T 的最新一期 ep/bp/sp/roe/ato/pm"""
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    rows = con.execute(
        "select code,ep,bp,sp,roe,ato,pm from stock_factors_fundamental "
        "where pit_avail_date>=? and pit_avail_date<=? order by code,date",
        (int(T) - 800, int(T))).fetchall()
    con.close()
    latest = {}
    for code, ep, bp, sp, roe, ato, pm in rows:
        latest[code] = (ep, bp, sp, roe, ato, pm)
    return latest


def eps_sue(T):
    """SUE（还原口径）: PIT 季度eps, SUE=(eps_t-eps_{t-4})/std(最近8期eps差分)"""
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True)
    rows = con.execute(
        "select code,report_date,eps from stock_factors_fundamental_pit_v2 "
        "where pit_avail_date<=? order by code,report_date", (int(T),)).fetchall()
    con.close()
    hist = defaultdict(list)
    for code, rd, eps in rows:
        if eps is not None:
            hist[code].append(float(eps))
    sue = {}
    for code, es in hist.items():
        if len(es) >= 8:
            diffs = np.diff(es[-9:])
            sd = float(np.std(diffs)) if len(diffs) > 1 else 0.0
            if sd > 1e-9:
                sue[code] = (es[-1] - es[-5]) / sd
    return sue


# ---------- 振幅族（ideal_factor_suite / bp_ep_amp_monthly_v2 原口径） ----------
def amp_window(s, i):
    """20日窗: 收益率、振幅（优先表内amplitude列缺失时用(high-low)/close, qfq库无amplitude列→直接算）"""
    lo = max(0, i - 19)
    c = s["close"]; h = s["high"]; l = s["low"]
    closes = c[lo:i + 1]
    highs = h[lo:i + 1]; lows = l[lo:i + 1]
    if len(closes) < 20 or np.any(closes <= 0):
        return None, None, None
    rets = np.diff(closes) / closes[:-1]
    amps = (highs - lows) / np.maximum(closes, 1e-8)
    amps = amps[1:]  # 与rets对齐(当日振幅配当日收益)
    amts = s["amt"][lo:i + 1]
    return rets, amps, amts[1:]


def f_amp_a(s):
    rets, amps, _ = amp_window(s, len(s["close"]) - 1)
    if rets is None:
        return None
    up, down = rets > 0, rets <= 0
    if up.sum() == 0 or down.sum() == 0:
        return None
    return float(amps[down].mean() - amps[up].mean())  # 高值好(IC+0.060)


def f_amp_b(s):
    rets, amps, amts = amp_window(s, len(s["close"]) - 1)
    if rets is None:
        return None
    order = np.argsort(amts)
    half = len(order) // 2
    if half <= 0:
        return None
    return float(amps[order[half:]].mean() - amps[order[:half]].mean())  # 低值好(IC-0.069)


def f_amp_trad(s):
    rets, amps, _ = amp_window(s, len(s["close"]) - 1)
    if rets is None:
        return None
    return -float(amps.mean())  # 低波=高trad_amp(原因子取负,IC+0.080)


# ---------- 通用衍生因子 ----------
def idx_of(s, d):
    """per-code 日历索引(停牌缺失安全): s["date"]升序数组中查找d"""
    arr = s["date"]
    j = int(np.searchsorted(arr, d))
    if j < len(arr) and arr[j] == d:
        return j
    return None


def pct_rank(vals):
    """dict code->value -> dict code->pct_rank(0~1)"""
    ks = [k for k, v in vals.items() if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not ks:
        return {}
    vs = np.array([vals[k] for k in ks])
    order = np.argsort(vs, kind="stable")
    r = np.empty(len(vs))
    r[order] = np.arange(len(vs)) / max(len(vs) - 1, 1)
    return {k: float(r[i]) for i, k in enumerate(ks)}


def ret_n(s, n):
    c = s["close"]
    if len(c) <= n or not c[-n - 1]:
        return None
    return float(c[-1] / c[-n - 1] - 1)


def id_vol_120(s, mkt_ret):
    """120日市场模型残差波动率(年化)"""
    c = s["close"]
    if len(c) < 122:
        return None
    rets = np.diff(c[-121:]) / c[-121:-1]
    if np.any(rets == 0) and False:
        pass
    m = mkt_ret[-120:]
    if len(rets) != 120 or len(m) != 120:
        return None
    var_m = np.var(m)
    if var_m <= 0:
        return None
    beta = np.cov(rets, m)[0, 1] / var_m
    res = rets - beta * m
    return float(np.std(res) * (250 ** 0.5))


def amihud_20(s):
    c, a = s["close"], s["amt"]
    if len(c) < 22:
        return None
    rets = np.abs(np.diff(c[-21:]) / c[-21:-1])
    amts = a[-20:]
    if np.any(amts <= 0):
        return None
    return float(np.mean(rets / amts) * 1e9)


def mkt_ew_ret(series, codes, n=121):
    """全宇宙等权日收益序列(尾部n日)"""
    mats = []
    for code in codes:
        c = series[code]["close"]
        if len(c) >= n:
            mats.append(c[-n:])
    if not mats:
        return np.zeros(n - 1)
    M = np.vstack(mats)
    rets = np.diff(M, axis=1) / M[:, :-1]
    return rets.mean(axis=0)


# ---------- ICIR 合成（还原口径: Ledoit max-ICIR 非负权重, 24 月截面 IC） ----------
def icir_weights(series, uni, pit_by_me, sue_by_me, dates):
    """月度截面 rank-IC × 24月 → ICIR 权重"""
    factor_fns = {
        "ep": lambda f, su: f[0], "bp": lambda f, su: f[1],
        "roe": lambda f, su: f[3],
        "sue": lambda f, su: su,
    }
    date_idx = {d: i for i, d in enumerate(dates)}
    month_last = {}
    for d in dates:
        ym = d // 100
        month_last[ym] = d
    mes = sorted(month_last.values())[-25:-1]  # 24个月末(最后一个留作当前,不进训练)
    ics = defaultdict(list)
    for me in mes:
        fwd = {}
        for code in uni:
            s = series[code]
            i = idx_of(s, me)
            if i is None:
                continue
            c = s["close"]
            if len(c) > i + 21 and c[i] and c[i] > 0:
                fwd[code] = c[i + 21] / c[i] - 1
        if len(fwd) < 500:
            continue
        fr = pct_rank(fwd)
        pit_m = pit_by_me.get(me, {})
        sue_m = sue_by_me.get(me, {})
        for name, fn in factor_fns.items():
            vals = {}
            for code in uni:
                f = pit_m.get(code)
                if f is None:
                    continue
                v = fn(f, sue_m.get(code))
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    vals[code] = v
            if len(vals) < 300:
                continue
            rk = pct_rank(vals)
            pairs = [(rk[k], fr[k]) for k in rk if k in fr]
            if len(pairs) < 300:
                continue
            x = np.array([p[0] for p in pairs]); y = np.array([p[1] for p in pairs])
            ic = float(np.corrcoef(x, y)[0, 1])
            if not math.isnan(ic):
                ics[name].append(ic)
    w = {}
    for name, series_ic in ics.items():
        if len(series_ic) >= 12:
            icir = statistics.fmean(series_ic) / (statistics.pstdev(series_ic) + 1e-9)
            w[name] = max(icir, 0.0)
    return w, {k: len(v) for k, v in ics.items()}


def build_pit_by_me_fast(dates):
    month_last = {}
    for d in dates:
        month_last[d // 100] = d
    targets = sorted(set(list(month_last.values())[-25:-1]))
    by_me = {me: {} for me in targets}
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    rows = con.execute(
        "select code,pit_avail_date,ep,bp,sp,roe,ato,pm from stock_factors_fundamental "
        "where pit_avail_date>=? order by pit_avail_date,date",
        (min(dates),)).fetchall()
    con.close()
    tset = targets
    cur = {}
    ti = 0
    # 行按 pit_avail_date 升序: 到达某月末时快照当期 cur
    for code, avail, ep, bp, sp, roe, ato, pm in rows:
        while ti < len(tset) and int(avail) > int(tset[ti]):
            by_me[tset[ti]] = dict(cur)
            ti += 1
        cur[code] = (ep, bp, sp, roe, ato, pm)
    while ti < len(tset):
        by_me[tset[ti]] = dict(cur)
        ti += 1
    # 每月末快照中每票取最后写入值
    return by_me


def sue_by_me(dates):
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True)
    rows = con.execute(
        "select code,pit_avail_date,report_date,eps from stock_factors_fundamental_pit_v2 "
        "where eps is not null order by code,report_date").fetchall()
    con.close()
    month_last = {}
    for d in dates:
        month_last[d // 100] = d
    targets = sorted(set(list(month_last.values())[-25:-1]))
    hist = defaultdict(list)  # code -> [(report_date, eps)]
    for code, avail, rd, eps in rows:
        hist[code].append((int(rd), float(eps), int(avail)))
    out = {me: {} for me in targets}
    for code, h in hist.items():
        for me in targets:
            avail_ok = [x for x in h if x[2] <= int(me)]
            if len(avail_ok) >= 8:
                es = [x[1] for x in avail_ok[-9:]]
                diffs = np.diff(es)
                sd = float(np.std(diffs))
                if sd > 1e-9:
                    out[me][code] = (es[-1] - es[-5]) / sd
    return out


# ---------- 行业映射 ----------
def load_sw1():
    con = sqlite3.connect(f"file:{IND_DB}?mode=ro", uri=True)
    ind = dict(con.execute("select code,l1 from l1_member"))
    con.close()
    return ind


# ---------- W2 方法 ----------
def m_amp_a(series, uni, ctx):
    cands = []
    for code, s in uni.items():
        v = f_amp_a(s)
        if v is not None:
            cands.append((v, code))
    cands.sort(reverse=True)
    return [(c, f"振幅A={v:.4f}") for v, c in cands]


def m_ampA_3f(series, uni, ctx):
    pit = ctx["pit"]
    rows = []
    for code, (ep, bp, *_r) in pit.items():
        if code in uni and ep and bp and ep > 0 and bp > 0:
            rows.append((code, ep, bp))
    if len(rows) < 200:
        return []
    epr = pct_rank({c: e for c, e, b in rows})
    bpr = pct_rank({c: b for c, e, b in rows})
    combo = {c: (epr[c] + bpr[c]) / 2 for c, e, b in rows}
    prescreen = sorted(combo.items(), key=lambda x: -x[1])[:100]
    amps = []
    for code, _v in prescreen:
        a = f_amp_a(series[code])
        if a is not None:
            amps.append((code, a))
    ar = pct_rank({c: a for c, a in amps})
    out = []
    for code, _v in prescreen:
        if code in ar:
            out.append(((epr[code] + bpr[code] + ar[code]) / 3, code))
    out.sort(reverse=True)
    return [(c, f"三因子combo3={v:.3f}") for v, c in out]


def m_amp_b(series, uni, ctx):
    cands = []
    for code, s in uni.items():
        v = f_amp_b(s)
        if v is not None:
            cands.append((v, code))
    cands.sort()  # 低值好
    return [(c, f"振幅B={v:.4f}(低值优)") for v, c in cands]


def m_icir_sue(series, uni, ctx):
    pit, sue = ctx["pit"], ctx["sue_T"]
    w = ctx["icir_w"]
    comps = {}
    contrib = {k: 0.0 for k in w}
    pool = {c: (f, sue.get(c)) for c, f in pit.items()
            if c in uni and f[0] and f[1] and f[0] > 0 and f[1] > 0}
    if len(pool) < 300:
        return []
    fns = {"ep": lambda f, su: f[0], "bp": lambda f, su: f[1],
           "roe": lambda f, su: f[3], "sue": lambda f, su: su}
    for name, fn in fns.items():
        if w.get(name, 0) <= 0:
            continue
        vals = {c: fn(f, su) for c, (f, su) in pool.items()}
        vals = {c: v for c, v in vals.items() if v is not None}
        if len(vals) < 100:
            continue
        rk = pct_rank(vals)
        for c, r in rk.items():
            comps[c] = comps.get(c, 0.0) + w[name] * r
            contrib[name] += w[name]
    out = sorted(comps.items(), key=lambda x: -x[1])
    top_w = " ".join(f"{k}:{v:.2f}" for k, v in sorted(w.items(), key=lambda x: -x[1]) if v > 0)
    return [(c, f"ICIR合成={v:.3f}[{top_w}]") for c, v in out]


def m_anom118(series, uni, ctx):
    """七因子等权(还原口径v2): PIT可用列仅ep/bp/roe(sp/ato/pm/lev近14月停更99.7%空),
    第7支用qfq可算的20日均振幅(低波, 原因子方向IC+0.080取trad_amp口径)"""
    pit = ctx["pit"]
    pool = [c for c, f in pit.items() if c in uni and f[0] and f[1] and f[0] > 0 and f[1] > 0]
    if len(pool) < 300:
        return []
    idv = {}
    for c in pool:
        v = id_vol_120(series[c], ctx["mkt"])
        if v is not None:
            idv[c] = v
    amh = {}
    for c in pool:
        v = amihud_20(series[c])
        if v is not None:
            amh[c] = v
    r20 = {c: ret_n(series[c], 20) for c in pool}
    r20 = {c: v for c, v in r20.items() if v is not None}
    tamp = {}
    for c in pool:
        rets, amps, _a = amp_window(series[c], len(series[c]["close"]) - 1)
        if amps is not None:
            tamp[c] = -float(amps.mean())
    rk_idv = pct_rank(idv); rk_amh = pct_rank(amh); rk_r20 = pct_rank(r20); rk_tamp = pct_rank(tamp)
    ep = {c: pit[c][0] for c in pool}; bp = {c: pit[c][1] for c in pool}
    roe = {c: pit[c][3] for c in pool}
    rk_ep = pct_rank({c: v for c, v in ep.items() if v})
    rk_bp = pct_rank({c: v for c, v in bp.items() if v})
    rk_roe = pct_rank({c: v for c, v in roe.items() if v is not None})
    comps = defaultdict(float)
    ncov = defaultdict(int)
    for c in pool:
        tot = 0.0; n = 0
        if c in rk_idv: tot += (1 - rk_idv[c]); n += 1       # 低残差波(负向因子取反)
        if c in rk_amh: tot += rk_amh[c]; n += 1             # 高Amihud(非流动性溢价)
        if c in rk_r20: tot += (1 - rk_r20[c]); n += 1       # 短期反转
        if c in rk_ep: tot += rk_ep[c]; n += 1               # 价值ep
        if c in rk_bp: tot += rk_bp[c]; n += 1               # 价值bp
        if c in rk_roe: tot += rk_roe[c]; n += 1             # 盈利质量
        if c in rk_tamp: tot += rk_tamp[c]; n += 1           # 低波(20日均振幅取负)
        if n >= 6:
            comps[c] = tot / n
            ncov[c] = n
    out = sorted(((v, c) for c, v in comps.items()), reverse=True)
    return [(c, f"七因子等权={v:.3f}(n={ncov[c]}/7)") for v, c in out]


def m_chan(series, uni, ctx):
    # 2026-11-09 结构性零票修复: 二买信号在日期D需D+1/D+2两日确认(detect_local_lows
    # 含shift(-1/-2)), 故T=最新交易日当天永无second_buy=1——旧查询date=T恒空集,
    # 10/28批次picks=0的真因之一。改为取T以前最近有二买的信号日(正常=T-2), 并按
    # 交易日距离守时效(>3个交易日=信号陈旧整源不出票, 与STALE_MAX_DAYS=5主人心智
    # 同向取更严档, 因二买本就带2日确认滞后)。
    T = int(ctx["T"])
    dates = ctx["dates"]
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    sig_d = con.execute(
        "select max(date) from chanlun_signals_nolookahead where second_buy=1 and date<=?",
        (T,)).fetchone()[0]
    if sig_d is None:
        con.close()
        return [], {"error": "无二买信号日"}
    rows = con.execute(
        "select code,strength from chanlun_signals_nolookahead where date=? and second_buy=1",
        (int(sig_d),)).fetchall()
    con.close()
    # 交易日距离守时效
    try:
        lag = len(dates) - 1 - dates.index(sig_d)
    except ValueError:
        lag = 99
    if lag > 3:
        return [], {"stale_skip": True, "signal_date": int(sig_d), "lag_trading_days": lag}
    cands = [(float(st or 0), code) for code, st in rows if code in uni]
    cands.sort(reverse=True)
    return [(c, f"缠论二买(去前视)信号日{int(sig_d)}(确认滞后{lag}日) strength={st:.2f}")
            for st, c in cands]


def bpep_composite(series, uni, pit):
    rows = []
    for code, (ep, bp, *_r) in pit.items():
        if code in uni and ep and bp and ep > 0 and bp > 0:
            rows.append((code, ep, bp))
    if len(rows) < 100:
        return [], {}
    epr = pct_rank({c: e for c, e, b in rows})
    bpr = pct_rank({c: b for c, e, b in rows})
    combo = {c: (epr[c] + bpr[c]) / 2 for c, e, b in rows}
    return sorted(combo.items(), key=lambda x: -x[1]), combo


def m_bpep_weekly(series, uni, ctx):
    ranked, _ = bpep_composite(series, uni, ctx["pit"])
    ind = ctx["ind"]
    cap = defaultdict(int)
    picks = []
    for code, v in ranked:
        k = ind.get(code, "_none")
        if cap[k] >= 4:
            continue
        cap[k] += 1
        picks.append((code, f"P2C周滚动bp_ep={v:.3f}[{k}]"))
        if len(picks) >= TOP_SUB:
            break
    return picks


def m_hy_tail(series, uni, ctx):
    ranked, _ = bpep_composite(series, uni, ctx["pit"])
    ind = ctx["ind"]
    ind_ret = defaultdict(list)
    for code in uni:
        r = ret_n(series[code], 20)
        if r is not None and code in ind:
            ind_ret[ind[code]].append(r)
    all_r = []
    for code in uni:
        r = ret_n(series[code], 20)
        if r is not None:
            all_r.append(r)
    if not all_r:
        return []
    mkt = statistics.fmean(all_r)
    rs = {k: statistics.fmean(v) - mkt for k, v in ind_ret.items() if len(v) >= 3}
    if len(rs) < 10:
        return []
    order = sorted(rs.items(), key=lambda x: -x[1])
    tail = {k for i, (k, _v) in enumerate(order) if i >= 24}  # rank>=25(0基24)剔除
    picks = []
    for code, v in ranked:
        k = ind.get(code)
        if k is not None and k in tail:
            continue  # 行业尾部剔除(fail-open: 映射缺失不剔)
        picks.append((code, f"P2B bp_ep={v:.3f}[{k or '映射缺失'}]"))
        if len(picks) >= TOP_SUB:
            break
    return picks


def m_v52_weekly(series, uni, ctx):
    """v52 六层深跌启动（还原口径, 出处 reports/top5_v52_final_20260920.md 头注标准）"""
    n = {"p20": 0, "bull": 0, "pos250": 0, "p3y": 0, "seg": 0, "gap": 0, "vr": 0}
    cands = []
    for code, s in uni.items():
        c = s["close"]; v = s["vol"]
        nlen = len(c)
        if nlen < 260 or c[-1] > 20 or not c[-1]:
            continue
        n["p20"] += 1
        ma20 = c[-20:].mean(); ma60 = c[-60:].mean()
        if not ma20 > ma60:
            continue
        n["bull"] += 1
        c250 = c[-250:]
        mn, mx = c250.min(), c250.max()
        if mx <= mn:
            continue
        pos250 = (c[-1] - mn) / (mx - mn)
        if pos250 >= 0.5:
            continue
        n["pos250"] += 1
        c750 = c[-750:]
        p3y = float((c750 <= c[-1]).mean())
        if p3y >= 0.55:
            continue
        n["p3y"] += 1
        # 深跌>=25% + 段起(近40日内放量长阳) + 距段起-3%~+10%
        seg_k = None
        for k in range(nlen - 40, nlen):
            if k < 61:
                continue
            h60 = c[k - 60:k - 1].max()
            dd = c[k] / h60 - 1
            r1 = c[k] / c[k - 1] - 1
            vma = v[max(0, k - 20):k].mean()
            if dd <= -0.25 and r1 >= 0.05 and vma > 0 and v[k] >= 1.5 * vma:
                seg_k = k
        if seg_k is None:
            continue
        n["seg"] += 1
        seg_gap = c[-1] / c[seg_k] - 1
        if not (-0.03 <= seg_gap <= 0.10):
            continue
        n["gap"] += 1
        # 量价: 20日涨日量/跌日量>=1.1
        rets20 = np.diff(c[-21:]) / c[-21:-1]
        vols20 = v[-20:]
        up = rets20 > 0
        if up.sum() == 0 or (~up).sum() == 0:
            continue
        vr = vols20[up].mean() / vols20[~up].mean()
        if vr < 1.1:
            continue
        n["vr"] += 1
        cands.append((seg_gap, vr, code))
    cands.sort(key=lambda x: (-x[0], -x[1]))
    return [(c, f"深跌启动段起距={g*100:.1f}%量价={vr:.2f}pos250<50%") for g, vr, c in cands], \
        {"funnel": f"≤20元:{n['p20']}→多头:{n['bull']}→pos250<50%:{n['pos250']}→3年分位<55%:{n['p3y']}"
                   f"→段起:{n['seg']}→距段起-3~10%:{n['gap']}→量价≥1.1:{n['vr']}"}


# ---------- W3 方法 ----------
def m_dbd_gru(series, uni, ctx):
    try:
        import torch
        sys.path.insert(0, r"D:\workbuddyspace")
        from dbd_gru_cutter import DBDGRU, DEVICE
        MODEL_PATH = r"D:\workbuddyspace\factor_engine\models\dbd_gru_minute_v1.pth"
        ckpt = torch.load(MODEL_PATH, map_location=DEVICE, weights_only=False)
        cfg = ckpt.get("config", {})
        norm_key = "branch_high.norm.weight"
        input_size = ckpt["model_state"][norm_key].shape[0]
        model = DBDGRU(input_size=input_size, hidden_size=cfg.get("hidden_size", 64),
                       num_layers=cfg.get("num_layers", 2))
        model.load_state_dict(ckpt["model_state"])
        model = model.to(DEVICE).eval()
        con = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
        codes = list(uni.keys())
        preds = {}
        B = 512
        for bi in range(0, len(codes), B):
            batch = codes[bi:bi + B]
            seqs, keep = [], []
            for code in batch:
                rows = con.execute(
                    "select time,close,high,low,amount,volume from stock_minute_5 "
                    "where code=? and date=? and volume>0 order by time limit 48",
                    (code, int(ctx["T"]))).fetchall()
                if len(rows) < 20:
                    continue
                arr = np.array([[r[1], r[2], r[3], r[4], r[5]] for r in rows], dtype=float)
                closes, highs, lows = arr[:, 0], arr[:, 1], arr[:, 2]
                amounts, volumes = arr[:, 3], arr[:, 4]
                if np.any(closes <= 0):
                    continue
                rets = np.diff(closes, prepend=closes[0]) / max(closes[0], 1e-8)
                rets[0] = 0
                amp = (highs - lows) / np.maximum(closes, 1e-8)
                vol_ratio = volumes / (np.mean(volumes) + 1e-8)
                amt_norm = amounts / (np.mean(amounts) + 1e-8)
                mom5 = np.array([rets[i] - rets[max(0, i - 5)] for i in range(len(rets))])
                vol_z = (volumes - np.mean(volumes)) / (np.std(volumes) + 1e-8)
                hl = highs - lows
                price_pos = np.where(hl > 0, (closes - lows) / np.maximum(hl, 1e-8), 0.5)
                buy_pressure = np.sign(rets) * amt_norm
                feats = np.stack([rets, amp, vol_ratio, amt_norm, mom5, vol_z, price_pos, buy_pressure], axis=1)
                mu = feats.mean(axis=0); sd = feats.std(axis=0) + 1e-8
                feats = ((feats - mu) / sd).astype(np.float32)
                thr = np.median(amt_norm)
                mask = (amt_norm >= thr).astype(np.float32)[:, np.newaxis]
                seqs.append((feats * mask, feats * (1 - mask)))
                keep.append(code)
            if not seqs:
                continue
            L = min(len(s[0]) for s in seqs)
            if L < 20:
                continue
            sh = np.stack([s[0][:L] for s in seqs])
            sl = np.stack([s[1][:L] for s in seqs])
            with torch.no_grad():
                th = torch.from_numpy(sh).float().to(DEVICE)
                tl = torch.from_numpy(sl).float().to(DEVICE)
                _, reg_pred, _ = model(th, tl)
            pv = reg_pred.squeeze(-1).cpu().numpy()
            for code, p in zip(keep, pv):
                preds[code] = float(p)
        con.close()
        out = sorted(preds.items(), key=lambda x: -x[1])
        return [(c, f"DBD-GRU次日预测={v:.5f}") for c, v in out], {"n_scored": len(preds)}
    except Exception as e:
        return [], {"error": f"{type(e).__name__}: {e}"[:200]}


def m_lstm(series, uni, ctx, seed=20261029, pool_n=800):
    """池规模参数化(20261028 主人令「10个种子太少,能用500只股票训练吗」): pool_n可切500/800/全部"""
    """自研LSTM重训（deep_sequence_retest_20260816 配方还原: 5特征/60窗/rank标签/Top800）
    20261028 主人令「随机性=不稳定性,要稳定的」: seed参数化→多种子集成在调用方实现, 单种子调用仍确定。"""
    try:
        import torch
        import torch.nn as nn
        DEV = "cuda" if torch.cuda.is_available() else "cpu"
        # 20261028 主人令「修复、优化、迭代」(随机性消除): 固定种子使权重初始化/样本shuffle/dropout
        # 全部确定化——同数据+同种子=同模型=同名单, 逐字节可复现; GPU路径加cuDNN确定性开关。
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        date_idx = {d: i for i, d in enumerate(ctx["dates"])}
        T = ctx["T"]
        iT = date_idx[T]
        pool = sorted(uni.keys(), key=lambda c: -np.nanmean(series[c]["amt"][-20:]))[:pool_n]
        feats_T, keep_T = lstm_feats_matrix(series, pool, T)
        if feats_T is None:
            return [], {"error": "T日特征不足"}
        # 训练样本: 过去250个交易日每10日一个截面(按日历日取, 结算日=T前推20个交易日)
        sample_idx = [i for i in range(max(61, iT - 250), iT - 20, 10)]
        Xs, Ys = [], []
        for i in sample_idx:
            d_i = ctx["dates"][i]
            F, keep_i = lstm_feats_matrix(series, pool, d_i)
            if F is None:
                continue
            y = lstm_label_matrix(series, keep_i, d_i)
            Xs.append(F); Ys.append(y)
        if len(Xs) < 10:
            return [], {"error": f"训练截面不足({len(Xs)})"}
        X = np.concatenate(Xs, axis=0)  # [N,60,5]
        Y = np.concatenate(Ys, axis=0)
        class TinyLSTM(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(5, 64, num_layers=2, batch_first=True, dropout=0.1)
                self.head = nn.Linear(64, 1)
            def forward(self, x):
                o, _ = self.lstm(x)
                return self.head(o[:, -1]).squeeze(-1)
        model = TinyLSTM().to(DEV)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        lossf = nn.MSELoss()
        Xt = torch.from_numpy(X.astype(np.float32)).to(DEV)
        Yt = torch.from_numpy(Y.astype(np.float32)).to(DEV)
        model.train()
        for ep in range(3):
            perm = torch.randperm(len(Xt), device=DEV)
            for bi in range(0, len(Xt), 2048):
                idx = perm[bi:bi + 2048]
                pred = model(Xt[idx])
                loss = lossf(pred, Yt[idx])
                opt.zero_grad(); loss.backward(); opt.step()
        model.eval()
        with torch.no_grad():
            pred_T = model(torch.from_numpy(feats_T.astype(np.float32)).to(DEV)).cpu().numpy()
        preds = {code: float(p) for code, p in zip(keep_T, pred_T)}
        out = sorted(preds.items(), key=lambda x: -x[1])
        return [(c, f"LSTM重训fwd20rank={v:.4f}") for c, v in out], \
            {"n_train": int(len(X)), "n_stock": len(pool), "epochs": 3}
    except Exception as e:
        return [], {"error": f"{type(e).__name__}: {e}"[:200]}


def lstm_feats_matrix(series, pool, d_target):
    """d_target日截面 5 特征(逐日截面 rank 归一) → (F[N,60,5], keep列表); 与 deep_seq 5 维量价特征同族"""
    rows_r5, rows_r20, rows_v20, rows_a20, rows_amp20 = [], [], [], [], []
    keep = []
    for code in pool:
        s = series[code]
        i = idx_of(s, d_target)
        if i is None or i < 60:
            continue
        c = s["close"][:i + 1]; a = s["amt"][:i + 1]
        if np.any(c[-61:] <= 0):
            continue
        rets = np.diff(c[-61:]) / c[-61:-1]
        r5 = float(c[-1] / c[-6] - 1)
        r20 = float(c[-1] / c[-21] - 1)
        v20 = float(np.std(rets))
        a20 = float(np.nanmean(a[-20:]))
        amps = (s["high"][i - 20:i + 1] - s["low"][i - 20:i + 1]) / np.maximum(c[-21:], 1e-8)
        amp20 = float(np.nanmean(amps))
        keep.append(code)
        rows_r5.append(r5); rows_r20.append(r20); rows_v20.append(v20)
        rows_a20.append(a20); rows_amp20.append(amp20)
    if len(keep) < 100:
        return None, None
    cols = [np.array(x) for x in (rows_r5, rows_r20, rows_v20, rows_a20, rows_amp20)]
    F = np.zeros((len(keep), 60, 5), dtype=np.float32)
    for k in range(5):
        x = cols[k]
        order = np.argsort(x, kind="stable")
        r = np.empty(len(x)); r[order] = np.arange(len(x)) / max(len(x) - 1, 1)
        F[:, :, k] = r[:, np.newaxis]
    return F, keep


def lstm_label_matrix(series, keep, d_target):
    y = []
    for code in keep:
        s = series[code]
        i = idx_of(s, d_target)
        c = s["close"]
        if i is not None and len(c) > i + 20 and c[i] and c[i] > 0:
            y.append(c[i + 20] / c[i] - 1)
        else:
            y.append(0.0)
    y = np.array(y)
    order = np.argsort(y, kind="stable")
    r = np.empty(len(y)); r[order] = np.arange(len(y)) / max(len(y) - 1, 1)
    return r


def m_kronos_ft(series, uni, ctx):
    return [], {"blocked": "Kronos微调缺件: 仅hf_cache基座权重在档(零样本已三FAIL); "
                          "官方微调管线已知泄漏bug(LITERATURE_MATRIX_20260818 §1); "
                          "本轮无GPU长训预算; 已批准复活通道=实时前walk预注册(PREREG_KRONOS_FWALK, 起始20260930在役)未触发"}


def m_rl(series, uni, ctx):
    # 2026-11-09 补缺件收口: 面板生产线修复(fml_v2 build_panels全史重建83s级, 缺件真身=
    # 归档驱动硬编码旧pkl文件名)+A2C重训自验(analysis/rl_a2c_retrain_20261109.py,
    # 旧ckpt因qfq价格行漂移obs维度不可精确复原故按登记表解锁路径重训)。
    # 出票合同: analysis/rl_a2c_inference.py 产 data/rl_a2c_scores_{T}.json(动作权重榜,
    # 可交易掩码已滤), 本函数消费; 文件缺失/信号日不匹配=blocked如实标注。
    T = int(ctx["T"])
    f = os.path.join(ROOT, "data", f"rl_a2c_scores_{T}.json")
    if not os.path.exists(f):
        # 2026-11-09 补缺件定谳: 面板线修复+工具链齐备(rl_a2c_inference/retrain_20261109),
        # 但重训A2C=常数动作坍缩(饱和度探针11时点逐字相同, 见rl_a2c_retrain_result
        # verdict_override=INVALID_CONSTANT_POLICY)→不出票; 复活须新研究立项(奖励塑形/
        # 熵正则/观测降维)+主人批。
        return [], {"blocked": "RL不出票(2026-11-09定谳): 重训策略观测无关坍缩(常数动作, "
                               "11时点饱和度逐字相同), 伪票产物已删除; "
                               "复活须新立项+主人批"}
    d = json.load(open(f, encoding="utf-8"))
    if d.get("signal_date") != T:
        return [], {"blocked": f"RL推理信号日不匹配({d.get('signal_date')}≠{T})"}
    cands = [(r["weight"], r["code"]) for r in d.get("ranked", []) if r["code"] in uni]
    cands.sort(reverse=True)
    meta = {"ckpt": os.path.basename(str(d.get("ckpt", ""))),
            "n_tradeable": d.get("n_tradeable"), "obs_dim": d.get("obs_dim")}
    return [(c, f"RL-A2C动作权重 w={w:.4f} (可交易{d.get('n_tradeable')}只中排名)") for w, c in cands], meta


def m_v8_3dir(series, uni, ctx):
    return [], {"blocked": "V8增强三方向定义不可还原: 档案仅存结论「对现役组合无增量」"
                           "(PROJECT_BOUNDARY §6), 无三方向算法定义文件; 候选指认=9/4 stage1三臂"
                           "(RANKLOSS/REGIME-ADAPT/MAINLINE-CONSTRAINT)无定谳文件; "
                           "需主人指认三方向清单后方可建名单(反臆想: 无定义不出确定性名单)"}


def m_v51_fusion(series, uni, ctx):
    """V5.1 full_fusion 管线实弹（dragon_v4 SelectPipeline full_fusion_v51）"""
    script = r"D:\workbuddyspace\factor_engine\Scripts\select_stocks_daily.py"
    t_iso = f"{ctx['T'] // 10000}-{ctx['T'] // 100 % 100:02d}-{ctx['T'] % 100:02d}"
    try:
        r = subprocess.run(
            [sys.executable, script, "--date", t_iso, "--config", "full_fusion_v51"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=os.path.dirname(script), timeout=420)
        out = r.stdout
        # 输出前置日志行+JSON; raw_decode 支持JSON块后跟尾部日志
        js, _dec = None, json.JSONDecoder()
        for tag in ("[", "{"):
            start = 0
            while True:
                i = out.find(tag, start)
                if i < 0:
                    break
                try:
                    js, _ = _dec.raw_decode(out[i:])
                    break
                except Exception:
                    start = i + 1
            if js is not None:
                break
        if js is None:
            tail = (r.stdout[-400:] + "||" + r.stderr[-400:])
            return [], {"error": f"无JSON输出 rc={r.returncode}", "tail": tail[:600]}
        items = js if isinstance(js, list) else js.get("picks") or js.get("results") or []
        picks = []
        for it in items:
            code = str(it.get("code", "")).zfill(6)[-6:]
            score = it.get("score", it.get("fusion_score", it.get("prob", "")))
            if code in uni:
                picks.append((code, f"full_fusion_v51 score={score}"))
        raw_codes = [str(it.get("code", "")).zfill(6)[-6:] for it in items]
        return picks, {"raw_n": len(items), "rc": r.returncode, "raw_codes": raw_codes}
    except Exception as e:
        return [], {"error": f"{type(e).__name__}: {e}"[:300]}


def m_p2(series, uni, ctx):
    # 20261030 信号时效窗(主人"继续"令批准): 信号滞后>STALE_MAX_DAYS交易日→整源不出候选。
    # 背景: W23批次4只p2旧信号(滞后108日)混入, 冻结价=9/21收盘与现价偏+8.9%~+60.8%,
    # 仅靠R1b熔断兜底非稳态。"如实标注滞后"只解决诚信不解决风险, 归档信号正确行为=不出票。
    sig_d = 20260921  # stock_signals_p2 最后计算日
    # 2026-10-31 单位修正(立项②验收发现): 原实现 ctx["T"]-sig_d 为日期整数差(=109),
    # 而STALE_MAX_DAYS语义=交易日(主人令原文), 周界会误杀(如滞后4交易日的信号整数差6被拦)。
    # 改为 date_idx 索引差=真实交易日差。
    _date_idx = {d: i for i, d in enumerate(ctx["dates"])}
    _i_sig = _date_idx.get(sig_d)
    if _i_sig is None:
        return [], {"signal_date": sig_d, "error": "qfq无信号日20260921, p2整源不出候选"}
    stale = _date_idx[ctx["T"]] - _i_sig
    if stale > STALE_MAX_DAYS:
        print(f"  [m_p2] 信号时效窗拦截: 信号日{sig_d}滞后{stale}日>{STALE_MAX_DAYS} → p2整源不出候选"
              f"(模型推理未更新, 请先重跑stock_signals_p2)")
        return [], {"signal_date": sig_d, "stale_skip": True, "stale_days": stale}
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    rows = con.execute(
        "select code,p2_prob from stock_signals_p2 where date=? order by p2_prob desc limit 400",
        (sig_d,)).fetchall()
    con.close()
    date_idx = {d: i for i, d in enumerate(ctx["dates"])}
    i0 = date_idx.get(sig_d)
    if i0 is None:
        return [], {"error": "qfq无20260921"}
    picks = []
    for code, p in rows:
        code6 = str(code).zfill(6)[-6:]
        if code6 not in uni:
            continue
        c = series[code6]["close"]
        if len(c) > i0 and c[i0] and 0.5 < c[i0] <= 30:
            picks.append((code6, f"p2_prob={p:.4f}@{sig_d}(滞后{ctx['T']-sig_d}日)", float(c[i0])))
    return picks[:TOP_SUB], {"signal_date": sig_d}


def m_composite(series, uni, ctx):
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    rows = con.execute(
        "select code,composite_score from stock_signals where date=? order by composite_score desc limit 400",
        (int(ctx["T"]),)).fetchall()
    con.close()
    picks = [(str(code).zfill(6)[-6:], f"composite_score={v:.3f}@T")
             for code, v in rows if str(code).zfill(6)[-6:] in uni]
    return picks[:TOP_SUB], {}


# ---------- 主流程 ----------
def main():
    series, dates = load_qfq()
    T = dates[-1]
    print(f"信号日T={T} 装载{len(series)}只×{len(dates)}日")
    wave1_codes = set()
    try:
        w1 = json.load(open(os.path.join(ROOT, "data", "revival_picks_20261028.json"), encoding="utf-8"))
        for m in w1["methods"].values():
            for p in m["picks"]:
                wave1_codes.add(p["code"])
    except Exception:
        pass
    uni, held = base_universe(series, wave1_codes)
    print(f"宇宙={len(uni)}(剔持仓+Wave1名单{len(wave1_codes)}只)")

    # 60分钟确认层（主人令20261029接入; 缺件=显式警告降级, 不静默）
    try:
        import timeframe_60min_20261029 as tf60
        import sqlite3 as _sq
        con60 = _sq.connect(f"file:{tf60.META_DB}?mode=ro", uri=True)
        dates60 = [int(d) for d in dates[-5:]]
        n60 = con60.execute("select count(distinct date) from stock_minute_5 where date>=?",
                            (dates60[0],)).fetchone()[0]
        if n60 < 3:
            raise RuntimeError(f"minute_5近5日覆盖不足({n60}/5日)")
        print(f"60分钟确认层=ON(minute_5近5日覆盖{n60}日, 死叉否决/顺延补位)")
    except Exception as _e:
        tf60, con60, dates60 = None, None, None
        print(f"⚠️60分钟确认层不可用, 降级为仅日线: {type(_e).__name__}: {_e}")

    pit = pit_latest(T)
    sue_T = eps_sue(T)
    codes_u = list(uni.keys())
    mkt = mkt_ew_ret(series, codes_u)
    ind = load_sw1()

    print("构建24月末PIT快照…")
    pit_by_me = build_pit_by_me_fast(dates)
    print("构建SUE按月末…")
    sue_me = sue_by_me(dates)
    print("计算ICIR权重…")
    icir_w, ic_n = icir_weights(series, uni, pit_by_me, sue_me, dates)
    print(f"ICIR权重: { {k: round(v,3) for k,v in icir_w.items()} }")

    ctx = {"T": T, "dates": dates, "pit": pit, "sue_T": sue_T, "icir_w": icir_w,
           "mkt": mkt, "ind": ind}

    methods = [
        ("revival_amp_a", "W2", "archived_exact", "ideal_factor_suite.py/bp_ep_amp_monthly_v2.py 原口径; 方向IC+0.060(ideal_factor_suite_report.json)", m_amp_a),
        ("revival_ampA_3f", "W2", "archived_exact", "bp+ep+振幅A三因子组合=bp_ep_amp_monthly_v2.py 策略B(黑名单原版-1.3%口径)", m_ampA_3f),
        ("revival_amp_b", "W2", "archived_exact", "理想振幅B rel_amount切割振幅, 方向IC-0.069低值优", m_amp_b),
        ("revival_icir_sue", "W2", "reconstructed", "还原口径: PIT可用列ep/bp/roe+eps同比SUE, 24月ICIR非负加权(METHODOLOGY_ABSORPTION E2 maxicir工具族); sp/ato/pm/lev列近14月停更99.7%空(如实差异), 原定义细节档案未存", m_icir_sue),
        ("revival_anom118", "W2", "reconstructed", "还原口径v2: 本地全覆盖七因子(id_vol/Amihud/反转/ep/bp/roe/20日均振幅低波)等权; 原始7因子清单档案未存(文献=Hsu 2018 118异象), PIT sp/ato/pm/lev停更为替代动因(如实差异)", m_anom118),
        ("revival_chan", "W2", "archived_exact", "chanlun_signals_nolookahead 确认日无前视表(T=当日二买)", m_chan),
        ("revival_bpep_weekly", "W2", "archived_exact", "P2-C 周滚动重评(PREREG_P2C_WEEKLY_REBALANCE_20260829): bp_ep复合分Top15(行业≤4)", m_bpep_weekly),
        ("revival_v52_weekly", "W2", "archived_restored", "v52六层深跌启动周批(top5_v52_final_20260920 头注标准: 深跌25%+段起-3%~10%+多头+pos250<50%+3年分位<55%+量价1.1+≤20元)", m_v52_weekly),
        ("revival_hy_tail", "W2", "archived_exact", "P2-B 行业动量尾部过滤(PREREG_P2B_SECTOR_MOMENTUM_20260829 §二原规则: RS降序rank>=25剔除+顺延)", m_hy_tail),
        ("revival_dbd_gru", "W3", "model_inference", "DBD-GRU分钟双支路模型T日截面推理(dbd_gru_minute_v1.pth在档, 8特征48根5分钟K)", m_dbd_gru),
        ("revival_lstm", "W3", "retrained_recipe", "自研LSTM重训(deep_sequence_retest_20260816配方: 5特征60窗/Top800/fwd20rank标签/近年截面)", m_lstm),
        ("revival_kronos_ft", "W3", "blocked", "Kronos微调缺件(见note)", m_kronos_ft),
        ("revival_rl", "W3", "blocked", "RL缺件: ckpt在档但观测面板停更(见note)", m_rl),
        ("revival_v8_3dir", "W3", "definition_unrecoverable", "V8增强三方向定义不可还原(见note, 需主人指认)", m_v8_3dir),
        ("revival_v51_fusion", "W3", "model_inference", "V5.1 full_fusion管线实弹(select_stocks_daily.py --config full_fusion_v51)", m_v51_fusion),
        ("revival_p2", "W3", "model_output_archived", "P2信号独立下单(stock_signals_p2在档推理, 信号日20260921如实标注滞后)", m_p2),
        ("revival_composite", "W3", "model_output_archived", "综合信号composite_score独立下单(stock_signals在产表@T)", m_composite),
    ]

    used = set(held) | wave1_codes
    out = {"signal_date": T, "generated": __import__("datetime").datetime.now().isoformat(),
           "prereg": os.path.join(ROOT, "analysis", "revival_paper_test_PREREG_20261027.md"),
           "order": "W2九项+W3八项; 方法间去重起点=持仓∪Wave1名单49只", "methods": {}}
    for src, wave, restore, defsrc, fn in methods:
        try:
            r = fn(series, uni, ctx)
            if isinstance(r, tuple):
                cands, meta = r
            else:
                cands, meta = r, {}
        except Exception as e:
            import traceback as _tb
            cands, meta = [], {"error": f"{type(e).__name__}: {e}", "tb": _tb.format_exc()[-400:]}
        picks, cost, p60_reject = [], 0.0, 0
        p60_reject_list = []  # 20261031补: 死叉否决逐票落盘(影子A/B的60分钟专属臂数据源, 此前仅计数无法回溯)
        for item in cands[: max(TOP_SUB, 5)]:
            code, reason = item[0], item[1]
            price = float(item[2]) if len(item) > 2 else float(uni[code]["close"][-1])
            if code in used or len(picks) >= 5:
                continue
            if cost + price * 100 > BUDGET_CAP:
                continue
            tr = "层关"
            if tf60 is not None:
                st = tf60.get_60min_series(con60, code, dates60)
                tr = st.get("trend", "无数据")
                if tr == "死叉":
                    p60_reject += 1
                    p60_reject_list.append({"code": code, "name": get_name(code) or "?",
                                            "price": round(price, 2), "60min": tr})
                    continue
            picks.append({"code": code, "name": get_name(code) or "?", "price": round(price, 2),
                          "volume": 100, "source": src, "rule": reason, "60min": tr})
            cost += price * 100
        for p in picks:
            used.add(p["code"])
        out["methods"][src] = {
            "wave": wave, "def_restore": restore, "definition_src": defsrc,
            "picks": picks, "cost": round(cost, 2), "cand_total": len(cands),
            "short": len(picks) < 5, "p60_reject": p60_reject,
            "p60_reject_list": p60_reject_list, "meta": meta,
        }
        tag = f"[{wave}]"
        print(f"{tag} {src:<22} 候选{len(cands):>5} 入选{len(picks)} 60min否决{p60_reject} 成本{cost:>8.0f} "
              + ("⚠️不足5只" if len(picks) < 5 else "") + (f" meta={meta}" if meta else ""))

    # 2026-11-09: 按信号日落盘(不覆盖10/28冻结件)
    out_path = os.path.join(ROOT, "data", f"revival_wave23_picks_{T}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    md5 = hashlib.md5(open(out_path, "rb").read()).hexdigest()
    n_all = sum(len(m["picks"]) for m in out["methods"].values())
    cost_all = sum(m["cost"] for m in out["methods"].values())
    rej_all = sum(m.get("p60_reject", 0) for m in out["methods"].values())
    print(f"\n合计 {n_all} 只 / 预算 {cost_all:,.0f} 元 / 60分钟否决 {rej_all} 只 | md5={md5}")
    print(f"产物: {out_path}")
    if con60 is not None:
        con60.close()


if __name__ == "__main__":
    main()
