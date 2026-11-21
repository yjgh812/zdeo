# -*- coding: utf-8 -*-
"""
portrait_attr_intraday_20261120.py
日内形态 → 次日收益 描述性归因（主人令 2026-11-20 的一部分）

定位：事后视角的描述性统计（分布刻画），**非信号、不构成"能涨能跌"的预测主张、零交易权**。
边界：只读三个数据源；只新建本脚本与报告 JSON；不碰 git、不改任何既有文件。

数据源（全部只读打开）:
  1) D:/zcodesj/data/minute_archive.db                 表 bars30 / bars60
       列 code,day,tmin,o,h,l,c,vol；day=YYYYMMDD 整数；PK(code,day,tmin) WITHOUT ROWID
       实测 tmin（开工前抽样 000001 验证）:
         bars30: 600,630,660,690 | 810,840,870,900   (8 根/日, tmin 为 bar 结束时刻)
         bars60: 630,690 | 840,900                    (4 根/日)
       → 上午分界 = tmin < 720（两粒度上午末根均为 690 = 11:30 收盘，下午首根 >= 810）
  2) D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db  表 stock_daily_meta_qfq
       仅取 close>0 AND volume>0 的有效行（qfq 复权价，除权日跳变天然一致）；
       次日收益 = 下一交易日 close / 当日 close - 1（窗口内 idx+1，searchsorted 定位）
  3) D:/zcodesj/data/universe_portrait_regime.db  表 regime_segments
       每股 (start_date,end_date,state) 区间数组；
       li = searchsorted(ss, day, 'right') - 1；命中条件 li>=0 且 day <= se[li]

日内形态特征（每个 (code,day) 一组，30min 与 60min 各算一遍，同一函数复用）:
  ret_am      = 上午末根 close / 当日首根 open - 1
  ret_pm      = 全天末根 close / 上午末根 close - 1
  ret_tail    = 全天末根 close / 倒数第二根 close - 1
  hi_t / lo_t = bar 内最高价 argmax / 最低价 argmin 的位置（首根出现，0~1）
  range_pct   = (bar 内最高 - 最低) / 当日首根 open
  vol_am_share / vol_tail_share = 上午成交量占比 / 最后一根成交量占比
  全天涨跌 chg_day = 末根 close / 首根 open - 1

形态分类（按序判定，先命中先停）:
  1 单边上行: ret_am >= +0.5% 且 ret_pm >= 0
  2 单边下行: ret_am <= -0.5% 且 ret_pm <= 0
  3 冲高回落: hi_t < 0.4 且 lo_t >= 0.6
  4 探底回升: lo_t < 0.4 且 hi_t >= 0.6
  5 宽幅震荡: |全天涨跌| < 1% 且 range_pct >= 3%
  6 其他/平稳: 其余

三张统计表:
  T1 (粒度,形态) → 次日收益 n / 均值% / 中位% / 上涨占比（无次日 → 结构化剔除，不编造）
  T2 (粒度,形态,状态 UP/DOWN/RANGE) → n 与次日均值%（状态分布不均可如实呈现）
  T3 同日 30min 形态与 60min 形态一致率（两粒度均有标签的日）

输出: D:/zcodesj/reports/FEATURE_ATTRIBUTION_INTRADAY_20261120.json + 控制台紧凑摘要
性能: bars30 约 718 万行 + bars60 约 359 万行，按主键序整扫 + 逐 (code,day) 小数组运算，全程打印进度。
"""

import json
import os
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np

SCRIPT_PATH = os.path.abspath(__file__)

DB_MINUTE_URI = "file:D:/zcodesj/data/minute_archive.db?mode=ro"
DB_DAILY_URI = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DB_REGIME_URI = "file:D:/zcodesj/data/universe_portrait_regime.db?mode=ro"
OUT_JSON = r"D:\zcodesj\reports\FEATURE_ATTRIBUTION_INTRADAY_20261120.json"

GRANS = ("30min", "60min")
TABLE_BY_GRAN = {"30min": "bars30", "60min": "bars60"}

# 上午/下午分界：由抽样确认（30min 上午末根 tmin=690，60min 上午末根 tmin=690）。
MORNING_CUTOFF = 720

# 日线只取该日期之后（分钟数据覆盖 2026-01-12 起，留缓冲）；"次日"=该窗口内排序数组的 idx+1。
DAILY_FROM = "20260101"

PATTERNS = ("单边上行", "单边下行", "冲高回落", "探底回升", "宽幅震荡", "其他/平稳")
STATES = ("UP", "DOWN", "RANGE")

TH_EDGE = 0.005        # 单边形态上午阈值 ±0.5%
TH_SMALL_CHG = 0.01    # 宽幅震荡：全天涨跌 < 1%
TH_WIDE_RANGE = 0.03   # 宽幅震荡：range_pct >= 3%


def classify(ret_am, ret_pm, hi_t, lo_t, chg_day, range_pct):
    """按序判定，先命中先停；返回形态名。"""
    if ret_am >= TH_EDGE and ret_pm >= 0.0:
        return "单边上行"
    if ret_am <= -TH_EDGE and ret_pm <= 0.0:
        return "单边下行"
    if hi_t < 0.4 and lo_t >= 0.6:
        return "冲高回落"
    if lo_t < 0.4 and hi_t >= 0.6:
        return "探底回升"
    if abs(chg_day) < TH_SMALL_CHG and range_pct >= TH_WIDE_RANGE:
        return "宽幅震荡"
    return "其他/平稳"


def load_daily():
    """只读加载 qfq 日线（date>=20260101, close>0 AND volume>0），每股返回升序 (dates, closes) 数组。

    说明：只取窗口内日期即可——"次日 = 窗口内排序数组的 idx+1"语义与全史一致
    （前一天不影响 +1 相对位置），且内存可控。
    """
    t0 = time.time()
    con = sqlite3.connect(DB_DAILY_URI, uri=True)
    bad_rows = 0
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>=? AND close>0 AND volume>0",
            (DAILY_FROM,),
        )
        raw = {}
        n_rows = 0
        for code, date, close in cur:
            try:
                d_i = int(date)
                c_f = float(close)
            except (TypeError, ValueError):
                bad_rows += 1
                continue
            n_rows += 1
            ent = raw.get(code)
            if ent is None:
                raw[code] = ([d_i], [c_f])
            else:
                ent[0].append(d_i)
                ent[1].append(c_f)
    finally:
        con.close()
    out = {}
    for code, (ds, cs) in raw.items():
        ds_a = np.asarray(ds, dtype=np.int64)
        order = np.argsort(ds_a, kind="stable")
        out[code] = (ds_a[order], np.asarray(cs, dtype=np.float64)[order])
    print(f"[加载] qfq 日线：{n_rows:,} 行 / {len(out):,} 只（坏行 {bad_rows}）  用时 {time.time()-t0:.1f}s", flush=True)
    return out, n_rows, bad_rows


def load_regime():
    """只读加载 regime_segments，每股返回 (ss, se, sst) 按 start_date 升序对齐的数组。"""
    t0 = time.time()
    con = sqlite3.connect(DB_REGIME_URI, uri=True)
    bad_rows = 0
    try:
        cur = con.cursor()
        cur.execute("SELECT code,start_date,end_date,state FROM regime_segments")
        raw = {}
        n_rows = 0
        for code, s, e, st in cur:
            try:
                s_i = int(s)
                e_i = int(e)
                st_s = str(st)
            except (TypeError, ValueError):
                bad_rows += 1
                continue
            n_rows += 1
            ent = raw.get(code)
            if ent is None:
                raw[code] = ([s_i], [e_i], [st_s])
            else:
                ent[0].append(s_i)
                ent[1].append(e_i)
                ent[2].append(st_s)
    finally:
        con.close()
    out = {}
    for code, (ss, se, sst) in raw.items():
        ss_a = np.asarray(ss, dtype=np.int64)
        order = np.argsort(ss_a, kind="stable")
        out[code] = (ss_a[order], np.asarray(se, dtype=np.int64)[order], np.asarray(sst, dtype=object)[order])
    print(f"[加载] 状态标签：{n_rows:,} 段 / {len(out):,} 只（坏行 {bad_rows}）  用时 {time.time()-t0:.1f}s", flush=True)
    return out, n_rows, bad_rows


def process_group(gran, code, day, tmins, o, h, l, c, v, ctx):
    """处理一个 (code, day) 的单日 bar 组（tmins 升序），计算特征、打形态标签并累计到各统计桶。

    空数据 / 结构不完整 → 计数后结构化跳过，不编造。
    """
    cnt = ctx["counters"]
    ctx["groups"][gran] += 1
    ctx["tmin_seen"][gran].update(tmins)

    if len(tmins) < 2:
        cnt[(gran, "skip_few_bars_不足两根")] += 1
        return
    try:
        fo = [float(x) for x in o]
        fh = [float(x) for x in h]
        fl = [float(x) for x in l]
        fc = [float(x) for x in c]
        fv = [float(x) if x is not None else 0.0 for x in v]
    except (TypeError, ValueError):
        cnt[(gran, "skip_bad_price_价格异常")] += 1
        return
    n = len(tmins)
    if min(fo) <= 0 or min(fh) <= 0 or min(fl) <= 0 or min(fc) <= 0:
        cnt[(gran, "skip_bad_price_价格异常")] += 1
        return

    am_last = -1
    for i, t in enumerate(tmins):
        if t < MORNING_CUTOFF:
            am_last = i
    if am_last < 0 or am_last == n - 1:
        # 缺上午或缺下午 bar（半日/异常结构），ret_pm 无法定义 → 跳过
        cnt[(gran, "skip_half_day_缺上午或下午")] += 1
        return

    day_open = fo[0]
    hi_i = fh.index(max(fh))   # 首个最高价 bar（与 numpy argmax 同语义：取首个最大）
    lo_i = fl.index(min(fl))   # 首个最低价 bar
    hi_t = hi_i / (n - 1)
    lo_t = lo_i / (n - 1)
    ret_am = fc[am_last] / day_open - 1.0
    ret_pm = fc[-1] / fc[am_last] - 1.0
    ret_tail = fc[-1] / fc[-2] - 1.0
    chg_day = fc[-1] / day_open - 1.0
    range_pct = (max(fh) - min(fl)) / day_open
    tv = sum(fv)
    if tv > 0:
        vol_am_share = sum(fv[: am_last + 1]) / tv
        vol_tail_share = fv[-1] / tv
    else:
        vol_am_share = 0.0
        vol_tail_share = 0.0
        cnt[(gran, "zero_vol_day_全日零量")] += 1

    pat = classify(ret_am, ret_pm, hi_t, lo_t, chg_day, range_pct)
    ctx["pattern_counts"][(gran, pat)] += 1
    fs = ctx["feat_sum"][(gran, pat)]
    fs[0] += ret_am
    fs[1] += ret_pm
    fs[2] += ret_tail
    fs[3] += range_pct
    fs[4] += vol_am_share
    fs[5] += vol_tail_share

    # T3：30min 扫描时存标签；60min 扫描时对齐比对（比对后 pop 释放内存）
    if gran == "30min":
        ctx["labels30"][(code, day)] = pat
    else:
        pat30 = ctx["labels30"].pop((code, day), None)
        if pat30 is None:
            cnt[(gran, "t3_only_60_仅60分标签")] += 1
        else:
            ctx["t3_matrix"][pat30][pat] += 1
            ctx["t3_both"] += 1
            if pat30 == pat:
                ctx["t3_same"] += 1

    # 次日收益（qfq 日线：当日 close 存在于有效行集合，且存在下一有效交易日）
    ret_next = None
    daily = ctx["daily"].get(code)
    if daily is None:
        cnt[(gran, "next_无日线代码")] += 1
    else:
        dates, closes = daily
        idx = int(np.searchsorted(dates, day))
        if idx >= len(dates) or int(dates[idx]) != day:
            cnt[(gran, "next_当日无有效日线行")] += 1
        elif idx + 1 >= len(dates):
            cnt[(gran, "next_无下一交易日")] += 1
        else:
            ret_next = float(closes[idx + 1] / closes[idx] - 1.0)
            cnt[(gran, "next_ok")] += 1
    if ret_next is not None:
        ctx["t1"][(gran, pat)].append(ret_next)

    # 状态标签（UP/DOWN/RANGE）
    reg = ctx["regime"].get(code)
    state = None
    if reg is None:
        cnt[(gran, "regime_无该代码")] += 1
    else:
        ss, se, sst = reg
        li = int(np.searchsorted(ss, day, side="right")) - 1
        if li >= 0 and day <= int(se[li]):
            st_s = str(sst[li])
            if st_s in STATES:
                state = st_s
            else:
                cnt[(gran, "regime_未知状态")] += 1
        else:
            cnt[(gran, "regime_当日无覆盖段")] += 1
    if state is not None and ret_next is not None:
        ctx["t2"][(gran, pat, state)].append(ret_next)


def scan_table(gran, ctx):
    """整扫一张 bars 表：主键 (code,day,tmin) WITHOUT ROWID 自然序 = ORDER BY 序，无临时排序。"""
    tbl = TABLE_BY_GRAN[gran]
    con = sqlite3.connect(DB_MINUTE_URI, uri=True)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT code,day,tmin,o,h,l,c,vol FROM " + tbl + " ORDER BY code,day,tmin"
        )
        t0 = time.time()
        rows_seen = 0
        last_mark = 0
        cur_code = None
        cur_day = None
        bt = []
        bo = []
        bh = []
        bl = []
        bc = []
        bv = []
        while True:
            rows = cur.fetchmany(100000)
            if not rows:
                break
            rows_seen += len(rows)
            for code, day, tmin, o_, h_, l_, c_, v_ in rows:
                if code != cur_code or day != cur_day:
                    if cur_code is not None:
                        process_group(gran, cur_code, cur_day, bt, bo, bh, bl, bc, bv, ctx)
                    cur_code = code
                    cur_day = day
                    ctx["codes"].add(code)
                    d = int(day)
                    if d < ctx["day_min"]:
                        ctx["day_min"] = d
                    if d > ctx["day_max"]:
                        ctx["day_max"] = d
                    bt = []
                    bo = []
                    bh = []
                    bl = []
                    bc = []
                    bv = []
                bt.append(tmin)
                bo.append(o_)
                bh.append(h_)
                bl.append(l_)
                bc.append(c_)
                bv.append(v_)
            if rows_seen - last_mark >= 1000000:
                last_mark = rows_seen
                print(
                    "[%s] 已扫 %s 行 / %s 组  用时 %.1fs"
                    % (gran, format(rows_seen, ","), format(ctx["groups"][gran], ","), time.time() - t0),
                    flush=True,
                )
        if cur_code is not None:
            process_group(gran, cur_code, cur_day, bt, bo, bh, bl, bc, bv, ctx)
        ctx["rows"][gran] = rows_seen
        print(f"[{gran}] 扫描完成 {rows_seen:,} 行 / {ctx['groups'][gran]:,} 组  用时 {time.time()-t0:.1f}s", flush=True)
    finally:
        con.close()


def main():
    t_all = time.time()
    ctx = {
        "daily": None,
        "regime": None,
        "counters": defaultdict(int),
        "pattern_counts": defaultdict(int),
        "feat_sum": defaultdict(lambda: [0.0] * 6),
        "t1": defaultdict(list),
        "t2": defaultdict(list),
        "t3_matrix": defaultdict(lambda: defaultdict(int)),
        "t3_both": 0,
        "t3_same": 0,
        "labels30": {},
        "tmin_seen": {g: set() for g in GRANS},
        "groups": defaultdict(int),
        "rows": {},
        "codes": set(),
        "day_min": 99999999,
        "day_max": 0,
    }

    print("=== 日内形态→次日收益 描述性归因（事后视角，非信号，零交易权）===", flush=True)
    print(f"上午分界 tmin<{MORNING_CUTOFF}（实测：30min 上午末根 690=11:30，60min 同）", flush=True)
    ctx["daily"], n_daily_rows, daily_bad = load_daily()
    ctx["regime"], n_regime_rows, regime_bad = load_regime()

    for gran in GRANS:
        scan_table(gran, ctx)

    only_30 = len(ctx["labels30"])

    # ---------- 组装 T1 ----------
    t1_rows = []
    for gran in GRANS:
        for pat in PATTERNS:
            vals = ctx["t1"].get((gran, pat), [])
            pc = ctx["pattern_counts"].get((gran, pat), 0)
            fs = ctx["feat_sum"].get((gran, pat), [0.0] * 6)
            fn = pc if pc > 0 else 1
            t1_rows.append({
                "granularity": gran,
                "pattern": pat,
                "labeled_days": pc,
                "n": len(vals),
                "mean_pct": round(float(np.mean(vals)) * 100, 4) if vals else None,
                "median_pct": round(float(np.median(vals)) * 100, 4) if vals else None,
                "up_ratio": round(float(np.mean([v > 0 for v in vals])), 4) if vals else None,
                "feat_means": {
                    "ret_am_pct": round(fs[0] / fn * 100, 4),
                    "ret_pm_pct": round(fs[1] / fn * 100, 4),
                    "ret_tail_pct": round(fs[2] / fn * 100, 4),
                    "range_pct": round(fs[3] / fn * 100, 4),
                    "vol_am_share": round(fs[4] / fn, 4),
                    "vol_tail_share": round(fs[5] / fn, 4),
                },
            })

    # ---------- 组装 T2（36 组合全列出，n=0 如实标 0，不编造均值） ----------
    t2_rows = []
    for gran in GRANS:
        for pat in PATTERNS:
            for st in STATES:
                vals = ctx["t2"].get((gran, pat, st), [])
                t2_rows.append({
                    "granularity": gran,
                    "pattern": pat,
                    "state": st,
                    "n": len(vals),
                    "mean_pct": round(float(np.mean(vals)) * 100, 4) if vals else None,
                })

    # ---------- 组装 T3 ----------
    t3 = {
        "both_labeled_days": ctx["t3_both"],
        "same": ctx["t3_same"],
        "consistency_rate": round(ctx["t3_same"] / ctx["t3_both"], 4) if ctx["t3_both"] else None,
        "only_30min_groups": only_30,
        "only_60min_groups": ctx["counters"].get(("60min", "t3_only_60_仅60分标签"), 0),
        "matrix_30min_x_60min": {
            r: {c: int(ctx["t3_matrix"][r][c]) for c in PATTERNS} for r in PATTERNS
        },
    }

    # ---------- meta ----------
    meta = {
        "script": SCRIPT_PATH,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "描述性归因：30/60分钟日内形态→次日收益分布（事后视角，非信号、无预测主张、零交易权）",
        "data_sources": {
            "minute_archive": "D:/zcodesj/data/minute_archive.db (bars30/bars60, 只读)",
            "daily_qfq": "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db (stock_daily_meta_qfq, close>0 AND volume>0, 只读)",
            "regime": "D:/zcodesj/data/universe_portrait_regime.db (regime_segments, 只读)",
        },
        "row_counts": {
            "bars30": ctx["rows"].get("30min"),
            "bars60": ctx["rows"].get("60min"),
            "daily_loaded_rows": n_daily_rows,
            "daily_bad_rows": daily_bad,
            "regime_loaded_rows": n_regime_rows,
            "regime_bad_rows": regime_bad,
        },
        "scan": {
            "codes": len(ctx["codes"]),
            "day_min": ctx["day_min"],
            "day_max": ctx["day_max"],
            "groups_by_gran": {g: ctx["groups"][g] for g in GRANS},
        },
        "morning_cutoff_tmin": MORNING_CUTOFF,
        "tmin_observed": {g: sorted(ctx["tmin_seen"][g]) for g in GRANS},
        "thresholds": {
            "单边上午阈值": "±0.5%",
            "宽幅震荡": "全天涨跌<1% 且 range_pct>=3%",
            "hi_t_lo_t": "bar 最高/最低 argmax/argmin 首现位置(0~1)",
        },
        "pattern_rules": [
            "1 单边上行: ret_am>=+0.5% 且 ret_pm>=0",
            "2 单边下行: ret_am<=-0.5% 且 ret_pm<=0",
            "3 冲高回落: hi_t<0.4 且 lo_t>=0.6",
            "4 探底回升: lo_t<0.4 且 hi_t>=0.6",
            "5 宽幅震荡: |全天涨跌|<1% 且 range_pct>=3%",
            "6 其他/平稳: 其余（按序先命中先停）",
        ],
        "nextday_rule": "次日收益=下一交易日 qfq close/当日 close-1；日线仅取 date>=20260101 且 close>0 AND volume>0；无次日→结构化剔除",
        "notes": [
            "日线最新日期为 20261119，其'次日'（20261120）尚未入库 → 最新交易日的样本被剔除出 T1/T2（计数器 next_无下一交易日），T3 仍计入",
            "若次日为停牌（volume=0 被 close>0 AND volume>0 过滤剔除），'次日'顺延至下一有效交易日，收益跨停牌期，属描述性口径",
            "状态标签（regime_segments）2026 样本分布不均，T2 如实呈现各状态 n",
            "regime 读取未按 is_confirmed 过滤（按任务口径，全段参与）",
        ],
        "skip_counters": {f"{g}|{k}": v for (g, k), v in sorted(ctx["counters"].items())},
        "pattern_counts_all": {g: {p: int(ctx["pattern_counts"].get((g, p), 0)) for p in PATTERNS} for g in GRANS},
        "runtime_sec": round(time.time() - t_all, 1),
    }

    payload = {
        "meta": meta,
        "T1_pattern_to_nextday": t1_rows,
        "T2_pattern_by_regime": t2_rows,
        "T3_granularity_consistency": t3,
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # ---------- 控制台紧凑摘要 ----------
    print()
    print(f"扫描范围：{len(ctx['codes']):,} 只 / 日期 {ctx['day_min']}~{ctx['day_max']} / 组数 {dict((g, ctx['groups'][g]) for g in GRANS)}", flush=True)
    print()
    print("== T1 形态→次日收益（n=有次日样本；均值/中位为 %；上涨占比=次日>0）==")
    for gran in GRANS:
        for row in t1_rows:
            if row["granularity"] != gran:
                continue
            if row["n"] == 0:
                print(f"[{gran}] {row['pattern']:<5} n=0（标签 {row['labeled_days']:,} 天，全部无次日/异常，不编造）")
            else:
                print(
                    f"[{gran}] {row['pattern']:<5} n={row['n']:>8,} "
                    f"次日均值 {row['mean_pct']:+.3f}%  中位 {row['median_pct']:+.3f}%  上涨占比 {row['up_ratio']*100:.1f}%"
                )
    print()
    print("== T2 形态×状态 次日均值% (n) ==")
    for gran in GRANS:
        for pat in PATTERNS:
            parts = []
            for st in STATES:
                vals = ctx["t2"].get((gran, pat, st), [])
                if vals:
                    parts.append(f"{st}: {np.mean(vals)*100:+.2f}% ({len(vals):,})")
                else:
                    parts.append(f"{st}: 无样本")
            print(f"[{gran}] {pat:<5} " + "  |  ".join(parts))
    print()
    if t3["consistency_rate"] is not None:
        print(
            f"== T3 同日 30min/60min 形态一致率：两粒度均有标签 {t3['both_labeled_days']:,} 天，"
            f"一致 {t3['same']:,} 天 = {t3['consistency_rate']*100:.2f}%"
            f"（仅30min {only_30:,} 组 / 仅60min {t3['only_60min_groups']:,} 组）=="
        )
    else:
        print("== T3 无两粒度共同标签日（结构化空）==")
    print()
    print("跳过/异常计数（gran|原因: 次数）:")
    for k, v in sorted(meta["skip_counters"].items()):
        print(f"  {k}: {v:,}")
    print()
    print(f"JSON 已写出: {OUT_JSON}  总用时 {meta['runtime_sec']}s", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(2)
