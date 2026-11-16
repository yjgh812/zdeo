#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""经典图表形态 A股合规预筛（研究层否决器，2026-09-02）

预注册：reports/PREREG_CLASSICAL_PATTERNS_20260902.md（运行前钉死全部定义/参数/判定）
骨架复用：vbt_hotspot_prescreen_step_a.py（宇宙/数据源） + vbt_ashare_adapter.AshareRules（费用/涨跌停语义）

形态（11 变体）：
  摆动点族（L=R=10，确认点=摆动点+10，突破搜索 [确认点, 确认点+60]——无前视核心）：
    hsb   头肩底突破 | wbot  双底突破 | mtop 双顶破位（空头侧信息位）
  滚动窗口族（窗口只用于截至 t 的历史，同型态 10 bar 冷却去重）：
    atri  上升三角突破 | symu/symd 对称三角上/下 | boxu/boxd 箱体上/下
    flag  旗形突破 | cup   杯柄突破 | rbot  圆弧底突破

尺子：信号日收盘 → 次日开盘买入（停牌/开盘涨停=信号作废）；持有 N∈{5,10,20} bar 后
"尽快卖"（跌停拦卖次 bar 重试≤10）；单笔名义 1 万元整手；佣金 max(万2.5,5元)+卖印花税 0.05%。

自检（fail → exit 2 不产出）：SC1 无前视断言 / SC2 抽样证据 / SC3 手工对账3笔 /
SC4 持有期 sanity / SC5 入场bar=信号日+1。

⚠️ 宇宙口径注记(2026-10-23 审计, reports/AUDIT_EXTERNAL_TDX_PIPELINE_20261023.md §八):
   load_universe 的 ST 剔除用 industry_map_ths 当前名称快照(name_ok), 套用至全历史窗
   ——「终将 ST 的票」自健康历史段即被剔除, 方向=利好信号统计(抬高均值/胜率)。
   本预筛及其全部派生(roundbottom wfa 等)的 PASS/观察数字均为上界, 复核/接线裁决前
   须待历史 ST 时点源重建宇宙(见审计报告§七)。

运行：python classical_pattern_prescreen.py [--limit-codes N] [--skip-selfcheck-visual]
输出：_cache/classical_pattern_prescreen_results.json + _cache/classical_pattern_prescreen_evidence.log
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT_JSON = Path(BASE) / "_cache" / "classical_pattern_prescreen_results.json"
OUT_LOG = Path(BASE) / "_cache" / "classical_pattern_prescreen_evidence.log"

WARM_START = "20151001"          # 暖机（75 bar 最大需求 + 余量）
SIG_END = "20260827"              # 终点=8/27 收盘完整（与 8/28 档案同约定）
RECENT5Y = "20210828"             # 子集视图起点（与 hotspot 档案同窗）
HOLDS = (5, 10, 20)
NOTIONAL = 10_000.0               # 单笔名义本金（prereg §5）
COMM, STAMP, MIN_COMM, LOT = 0.00025, 0.0005, 5.0, 100
SW_L = SW_R = 10                  # 摆动点左右窗
BREAK_WIN = 60                    # 摆动族突破搜索窗
COOLDOWN = 10                     # 同型态信号冷却
MIN_N = 500                       # prereg 判定最小样本

sys.path.insert(0, BASE)
from tulong_buy_whitelist import board_ok, name_ok, norm6  # noqa: E402


def limit_pct(code: str) -> float:
    return 0.20 if code.startswith(("300", "301")) else 0.10


def load_universe() -> set[str]:
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    rows = con.execute("SELECT code, name FROM industry_map_ths").fetchall()
    con.close()
    uni = set()
    for code, name in rows:
        c6 = norm6(str(code))
        if board_ok(c6) and name and name_ok(str(name).strip()):
            uni.add(c6)
    return uni


ST_PANEL_DB = r"D:\zcodesj\data\st_history.db"
_ST_DAILY_MAP: dict[int, set[str]] | None = None


def _load_st_daily_map() -> dict[int, set[str]]:
    """ST 历史面板懒加载 → {YYYYMMDD: set(6位码)}（PD-049 m4-v2；缺库 fail-loud）。"""
    global _ST_DAILY_MAP
    if _ST_DAILY_MAP is None:
        con = sqlite3.connect(f"file:{ST_PANEL_DB}?mode=ro", uri=True, timeout=30)
        try:
            mp: dict[int, set[str]] = {}
            for code, d in con.execute(
                    "SELECT code, date FROM st_daily WHERE is_st=1"):
                mp.setdefault(int(d), set()).add(str(code)[-6:])
            _ST_DAILY_MAP = mp
        finally:
            con.close()
    return _ST_DAILY_MAP


def st_hit(code6: str, date_val) -> bool:
    """(code, 信号日) 是否处于 ST 状态（面板覆盖内）。

    单侧过滤语义：只剔除面板确证 ST 的 (code,date)，面板未覆盖的配对不猜
    （放行）——覆盖缺口由回填收敛，不产生新的误剔除（PD-049 m4-v2）。"""
    mp = _load_st_daily_map()
    d = int(str(date_val)[:10].replace("-", ""))
    return code6 in mp.get(d, ())


def load_wide(uni: set[str]) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date",
        con, params=(WARM_START, SIG_END))
    con.close()
    df["code"] = df["code"].map(lambda c: norm6(str(c)))
    df = df[df["code"].isin(uni)].reset_index(drop=True)
    return df


# ---------------------------------------------------------------- 摆动点族

def _swing_idx(arr: np.ndarray, kind: str) -> np.ndarray:
    """摆动点索引（center 窗含未来 bar——仅用于定义形态结构；可交易性由确认点后移保证）。"""
    w = SW_L + SW_R + 1
    s = pd.Series(arr)
    roll = (s.rolling(w, min_periods=w).min() if kind == "low"
            else s.rolling(w, min_periods=w).max()).shift(-SW_R)
    flag = (s == roll) & roll.notna()
    return np.flatnonzero(flag.to_numpy())


def _swing_signals(low, high, close, kind: str):
    """摆动族信号。返回 [(sig_idx, confirm_idx)]。kind: 'wbot'(双底)/'hsb'(头肩底)/'mtop'(双顶)。"""
    if kind == "mtop":
        pts, px = _swing_idx(high, "high"), high
    else:
        pts, px = _swing_idx(low, "low"), low
    out = []
    if kind == "hsb":
        rng = range(2, len(pts))
    else:
        rng = range(1, len(pts))
    for i in rng:
        if kind == "hsb":
            a, b, c = pts[i - 2], pts[i - 1], pts[i]          # 左肩/头/右肩
            pa, pb, pc = px[a], px[b], px[c]
            if not (pb < pa and pb < pc):                       # 头最低
                continue
            if abs(pa - pc) / max(pa, pc) > 0.03:               # 双肩对称
                continue
            neck = (high[a:b + 1].max() + high[b:c + 1].max()) / 2.0
            confirm = int(c) + SW_R
            thresh, up = neck * 1.03, True
        elif kind == "wbot":
            a, c = pts[i - 1], pts[i]                           # 两底
            pa, pc = px[a], px[c]
            if abs(pa - pc) / max(pa, pc) > 0.03:
                continue
            neck = high[a:c + 1].max()
            if neck < pa * 1.10:                                # 中间反弹≥10%
                continue
            confirm = int(c) + SW_R
            thresh, up = neck * 1.03, True
        else:                                                   # mtop
            a, c = pts[i - 1], pts[i]
            pa, pc = px[a], px[c]
            if abs(pa - pc) / max(pa, pc) > 0.03:
                continue
            neck = low[a:c + 1].min()
            if neck > pa * 0.90:                                # 中间回落≥10%
                continue
            confirm = int(c) + SW_R
            thresh, up = neck * 0.97, False
        hi = min(confirm + BREAK_WIN, len(close))
        for j in range(confirm, hi):
            if (close[j] > thresh) if up else (close[j] < thresh):
                out.append((j, confirm))
                break                                           # 每形态实例只取首次突破
    return out


# ---------------------------------------------------------------- 滚动窗口族（全部 shift(1) 只用历史）

def _w(s: pd.Series, w: int, agg: str) -> pd.Series:
    r = s.rolling(w, min_periods=w)
    return (r.min() if agg == "min" else r.max() if agg == "max"
            else r.mean() if agg == "mean" else r.quantile(agg)).shift(1)


def rolling_signals(g: pd.DataFrame) -> dict[str, np.ndarray]:
    """返回 {pattern: bool ndarray}（未冷却去重）。"""
    o, h, l, c = g["open"], g["high"], g["low"], g["close"]
    n = len(g)
    sig: dict[str, pd.Series] = {}

    # 4 上升三角（W=30）
    h_max, h_min, h_mean = _w(h, 30, "max"), _w(h, 30, "min"), _w(h, 30, "mean")
    h_q90 = _w(h, 30, 0.90)
    l_1h, l_2h = _w(l, 15, "mean"), _w(l, 15, "mean").shift(15)
    sig["atri"] = (((h_max - h_min) / h_mean) < 0.04) & (l_1h > l_2h) & (c > h_q90 * 1.02)

    # 5/6 对称三角（三分窗）
    lt_h, ft_h = _w(h, 10, "max"), _w(h, 20, "max").shift(10)
    lt_l, ft_l = _w(l, 10, "min"), _w(l, 20, "min").shift(10)
    conv = (lt_h - lt_l) < (ft_h - ft_l) * 0.7
    tri = (ft_h > lt_h * 1.02) & (lt_l > ft_l * 1.02) & conv
    sig["symu"] = tri & (c > lt_h * 1.02)
    sig["symd"] = tri & (c < lt_l * 0.98)

    # 7/8 箱体（q95/q05 轨 + 自窗触轨代理，见报告"实现注记"）
    upper, lower = _w(h, 30, 0.95), _w(l, 30, 0.05)
    l_min = _w(l, 30, "min")
    touch_u = (h >= h_max * 0.97).rolling(30, min_periods=30).sum().shift(1) >= 2
    touch_d = (l <= l_min * 1.03).rolling(30, min_periods=30).sum().shift(1) >= 2
    box = (((h_max - upper) / upper) < 0.03) & (((lower - l_min) / lower) < 0.03)
    sig["boxu"] = box & touch_u & touch_d & (c > upper * 1.03)
    sig["boxd"] = box & touch_u & touch_d & (c < lower * 0.97)

    # 9 旗形（杆10+面10）
    pole_gain = c.shift(10) / c.shift(20) - 1.0
    f_min, f_high = _w(l, 10, "min"), _w(h, 10, "max")
    pullback = (c.shift(10) - f_min) / c.shift(10)
    f_low_rise = _w(l, 5, "min") > _w(l, 5, "min").shift(5)
    sig["flag"] = (pole_gain >= 0.15) & (pullback <= 0.5) & f_low_rise & (c > f_high * 1.01)

    # 10 杯柄（杯60+柄15）
    cup_lq = _w(c, 15, "max").shift(60)            # 杯左1/4高 [t-74,t-60]
    cup_rq = _w(c, 15, "max").shift(15)            # 杯右1/4高 [t-29,t-15]
    cup_mid = _w(l, 30, "min").shift(30)           # 杯中段低 [t-59,t-30]
    h_max15, h_min15 = _w(c, 15, "max"), _w(c, 15, "min")
    handle_amp = (h_max15 - h_min15) / h_max15
    pivot = pd.concat([cup_lq, cup_rq], axis=1).max(axis=1)
    sig["cup"] = (cup_mid <= pd.concat([cup_lq, cup_rq], axis=1).min(axis=1) * 0.88) \
        & (handle_amp <= 0.06) & (c > pivot * 1.01)

    # 11 圆弧底（三分最低点居中 + 单调占比 + 突破左半高）
    left_min = _w(l, 10, "min").shift(20)
    mid_min = _w(l, 10, "min").shift(10)
    right_min = _w(l, 10, "min")
    dec = (l.diff() < 0).astype(float)
    inc = (l.diff() > 0).astype(float)
    frac_dec = dec.rolling(15, min_periods=15).mean().shift(16)
    frac_inc = inc.rolling(15, min_periods=15).mean().shift(1)
    left_cmax = _w(c, 15, "max").shift(16)
    sig["rbot"] = (mid_min < left_min) & (mid_min < right_min) \
        & (frac_dec > 0.6) & (frac_inc > 0.6) & (c > left_cmax * 1.02)

    return {k: v.fillna(False).to_numpy() for k, v in sig.items()}


def cooldown(idx: np.ndarray, gap: int = COOLDOWN) -> list[int]:
    out: list[int] = []
    last = -10 ** 9
    for i in idx:
        if i - last >= gap:
            out.append(int(i))
            last = i
    return out


# ---------------------------------------------------------------- 尺子

def ruler(code, o, h, l, c, v, sig_idx, confirm_idx=None):
    """对单信号执行尺子。返回 dict 或 None（作废/未平仓）。"""
    n = len(c)
    t = sig_idx
    e = t + 1                                                  # SC5：入场bar=信号日+1
    if e >= n:
        return {"status": "no_next_bar"}
    prev_c = c[t]
    lp = limit_pct(code)
    lu_price = round(prev_c * (1 + lp), 2)
    ld_price = round(prev_c * (1 - lp), 2)
    if v[e] is None or np.isnan(v[e]) or v[e] <= 0:
        return {"status": "blocked_suspended"}
    if o[e] >= lu_price - 0.005:
        return {"status": "blocked_limit_up"}
    entry = float(o[e])
    shares = int(NOTIONAL / entry / LOT) * LOT
    if shares <= 0:
        return {"status": "unaffordable"}
    buy_val = shares * entry
    buy_fee = max(COMM * buy_val, MIN_COMM)

    exits = {}
    for N in HOLDS:
        b = e + N
        sold = False
        for _ in range(11):                                    # 跌停/停牌重试≤10
            if b >= n:
                break
            prev = c[b - 1]
            ldp = round(prev * (1 - lp), 2)
            ok_vol = (not np.isnan(v[b])) and v[b] > 0
            if ok_vol and o[b] > ldp + 0.005:
                exit_px = float(o[b])
                sell_val = shares * exit_px
                sell_fee = max(COMM * sell_val, MIN_COMM) + STAMP * sell_val
                exits[N] = {
                    "ret": (sell_val - sell_fee) / (buy_val + buy_fee) - 1.0,
                    "hold_bars": b - e,
                    "fee_drag": (buy_fee + sell_fee) / buy_val,
                }
                sold = True
                break
            b += 1
        if not sold:
            exits[N] = None
    return {"status": "ok", "entry": entry, "shares": shares,
            "entry_date": None, "exits": exits}


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-codes", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260902)
    ap.add_argument("--patterns", type=str, default="",
                    help="逗号分隔形态过滤（如 rbot），仅收集/统计目标形态")
    ap.add_argument("--dump-trades", type=str, default="",
                    help="逐笔成交明细导出路径（jsonl）")
    a = ap.parse_args()
    targets = set(filter(None, a.patterns.split(",")))

    t0 = time.time()
    uni = load_universe()
    print(f"[info] 宇宙: board_ok+name_ok 后 {len(uni)} 只")
    if a.limit_codes:
        uni = set(sorted(uni)[: a.limit_codes])
        print(f"[info] --limit-codes: 截取 {len(uni)} 只（冒烟）")

    df = load_wide(uni)
    print(f"[info] qfq 长表: {len(df)} 行，{df['code'].nunique()} 只，{time.time()-t0:.0f}s")

    signals = []          # (pattern, code, sig_idx, confirm_idx)
    sc1_pool = []         # SC1 断言素材
    ev_lines = []

    for code, g in df.groupby("code", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 120:
            continue
        o = g["open"].to_numpy(float)
        h = g["high"].to_numpy(float)
        l = g["low"].to_numpy(float)
        c = g["close"].to_numpy(float)
        v = g["volume"].to_numpy(float)
        dates = g["date"].astype(str).to_numpy()

        # 摆动族
        for kind, pname in (("hsb", "hsb"), ("wbot", "wbot"), ("mtop", "mtop")):
            if targets and pname not in targets:
                continue
            hits = _swing_signals(l, h, c, kind)
            idxs = cooldown(np.array([x[0] for x in hits], dtype=int)) if hits else []
            kept_conf = {x[0]: x[1] for x in hits}
            for i in idxs:
                if st_hit(str(code), dates[i]):  # m4-v2: 信号日 ST 时点过滤（单侧）
                    continue
                signals.append((pname, code, i, kept_conf[i]))
                sc1_pool.append((pname, code, i, kept_conf[i]))
        # 滚动族
        rs = rolling_signals(g)
        for pname, mask in rs.items():
            if targets and pname not in targets:
                continue
            for i in cooldown(np.flatnonzero(mask)):
                if st_hit(str(code), dates[i]):  # m4-v2: 信号日 ST 时点过滤（单侧）
                    continue
                signals.append((pname, code, i, None))

    sig_df = pd.DataFrame(signals, columns=["pattern", "code", "sig_idx", "confirm_idx"])
    print(f"[info] 信号总数(冷却去重后): {len(sig_df)}，{time.time()-t0:.0f}s")
    print(sig_df.groupby("pattern").size().to_string())

    # ---- SC1 无前视断言（摆动族：信号日≥确认点且≤确认点+60）
    bad = [x for x in sc1_pool if not (x[2] >= x[3] and x[2] <= x[3] + BREAK_WIN)]
    if bad:
        print(f"[FAIL] SC1 无前视断言 {len(bad)} 处违规")
        return 2
    print(f"[PASS] SC1 无前视断言: {len(sc1_pool)} 条摆动族信号全部 ≥确认点 且 ≤确认点+{BREAK_WIN}")

    # ---- 执行尺子
    code_groups = {str(code): g.reset_index(drop=True) for code, g in df.groupby("code", sort=True)}
    trades = []
    status_cnt: dict[str, int] = {}
    for row in signals:
        g = code_groups[row[1]]
        r = ruler(row[1], g["open"].to_numpy(float), g["high"].to_numpy(float),
                  g["low"].to_numpy(float), g["close"].to_numpy(float),
                  g["volume"].to_numpy(float), row[2], row[3])
        st = r["status"]
        status_cnt[st] = status_cnt.get(st, 0) + 1
        if st != "ok":
            continue
        d = str(g["date"].astype(str).iloc[row[2]])
        trades.append({"pattern": row[0], "code": row[1], "date": d, **r})
    print(f"[info] 尺子完成: ok={status_cnt.get('ok',0)} 作废={status_cnt}，{time.time()-t0:.0f}s")

    # ---- SC4 持有期 sanity + SC5 复核
    hb = [t["exits"][5]["hold_bars"] for t in trades if t["exits"].get(5)]
    if hb:
        m = float(np.mean(hb))
        if not (4.5 <= m <= 6.5):
            print(f"[FAIL] SC4 持有期 sanity: N=5 平均 {m:.2f} bar 越界 [4.5,6.5]")
            return 2
        print(f"[PASS] SC4 持有期 sanity: N=5 平均 {m:.2f} bar")

    # ---- SC3 手工对账 3 笔
    rng = random.Random(a.seed)
    cand = [t for t in trades if all(t["exits"].get(N) for N in HOLDS)]
    if len(cand) >= 3:
        for t in rng.sample(cand, 3):
            g = code_groups[t["code"]]
            i = int(g.index[g["date"].astype(str) == t["date"]][0])
            e = i + 1
            entry = float(g["open"].iloc[e])
            shares = int(NOTIONAL / entry / LOT) * LOT
            buy_fee = max(COMM * shares * entry, MIN_COMM)
            b = e + 5
            exit_px = float(g["open"].iloc[b])
            sell_fee = max(COMM * shares * exit_px, MIN_COMM) + STAMP * shares * exit_px
            manual = (shares * exit_px - sell_fee) / (shares * entry + buy_fee) - 1.0
            got = t["exits"][5]["ret"]
            ok = abs(manual - got) < 1e-12
            ev_lines.append(
                f"SC3 {t['pattern']} {t['code']}@{t['date']} entry={entry}(bar{i}+1) "
                f"exit={exit_px} manual={manual:.8f} ruler={got:.8f} {'PASS' if ok else 'FAIL'}")
            if not ok:
                print(f"[FAIL] SC3 手工对账不一致: {t}")
                return 2
        print("[PASS] SC3 手工对账: 3 笔分毫不差（详见 evidence.log）")

    # ---- SC2 抽样证据
    for pname in sorted(sig_df["pattern"].unique()):
        sub = [t for t in trades if t["pattern"] == pname]
        for t in rng.sample(sub, min(5, len(sub))):
            g = code_groups[t["code"]]
            i = int(g.index[g["date"].astype(str) == t["date"]][0])
            lo, hi = max(0, i - 7), min(len(g), i + 8)
            seg = g.iloc[lo:hi][["date", "open", "high", "low", "close"]].to_string(index=False)
            ev_lines.append(f"SC2 {pname} {t['code']}@{t['date']} (idx={i})\n{seg}\n")

    # ---- 汇总
    def stats(rows, key):
        out = {}
        for pname in sorted({r["pattern"] for r in rows}):
            for N in HOLDS:
                rets = np.array([r["exits"][N]["ret"] for r in rows
                                 if r["pattern"] == pname and r["exits"].get(N)])
                if not len(rets):
                    continue
                mu, sd = float(rets.mean()), float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
                fees = [r["exits"][N]["fee_drag"] for r in rows
                        if r["pattern"] == pname and r["exits"].get(N)]
                out[f"{pname}|N{N}"] = {
                    "n": int(len(rets)), "mean": round(mu, 6),
                    "median": round(float(np.median(rets)), 6),
                    "win": round(float((rets > 0).mean()), 4),
                    "t": round(mu / (sd / math.sqrt(len(rets))), 3) if sd > 0 else 0.0,
                    "fee_drag_avg": round(float(np.mean(fees)), 6),
                    "verdict": ("candidate" if (mu > 0 and len(rets) >= MIN_N
                                                and mu / (sd / math.sqrt(len(rets))) >= 3.0)
                                else "falsified"),
                }
        return out

    main_stats = stats(trades, "main")
    recent = [t for t in trades if t["date"] >= RECENT5Y]
    recent_stats = stats(recent, "recent5y")

    result = {
        "meta": {"window": f"{WARM_START}(warm)~{SIG_END}", "recent5y_start": RECENT5Y,
                 "universe": len(uni), "signals": len(sig_df), "trades_ok": len(trades),
                 "status_counts": status_cnt, "equivalent_N": f"11形态×3持有期={33}",
                 "notional": NOTIONAL, "seed": a.seed,
                 "prereg": "reports/PREREG_CLASSICAL_PATTERNS_20260902.md"},
        "main": main_stats,
        "recent5y": recent_stats,
        "signal_counts": {k: int(v) for k, v in sig_df.groupby("pattern").size().items()},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_LOG.write_text("\n".join(ev_lines), encoding="utf-8")

    if a.dump_trades:
        with open(a.dump_trades, "w", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        print(f"[info] 逐笔明细已导出: {a.dump_trades} ({len(trades)} 行)")

    print("\n===== 主窗（2016~2026-08）每笔净收益 | 判定以主窗为准 =====")
    print(f"{'形态|N':<12}{'n':>7}{'均值%':>9}{'中位%':>8}{'胜率%':>7}{'t':>8}{'判定':>10}")
    for k, s in main_stats.items():
        print(f"{k:<12}{s['n']:>7}{s['mean']*100:>9.3f}{s['median']*100:>8.2f}"
              f"{s['win']*100:>7.1f}{s['t']:>8.2f}{s['verdict']:>10}")
    print(f"\n[done] {OUT_JSON} | evidence: {OUT_LOG} | 总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
