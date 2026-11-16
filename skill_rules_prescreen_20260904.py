#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""选股类 Skill 全量测试·幸存规则包无前视预筛（2026-09-04）

预注册：reports/PREREG_SKILL_SWEEP_20260904.md（跑前钉死，本脚本不得偏离）
骨架复用：classical_pattern_prescreen.py（宇宙/尺子/自检/判定）

5 个规则包（幸存/边界候选，定义见 prereg §2）：
  A trend_quality  GMMA+一目+ADX 趋势质量（源 stockanalysispro）
  B dip_pullback   强势股缩量回调企稳低吸（源 trend-dip-checker）
  C second_buy     放量上涨后缩量回调三确认再启动（源 a-share-second-buy-research）
  D factor_invest  均线多头+RSI+放量五条件（源 factor-investment，剔竞价维度）
  E macd_flow      MACD金叉+放量共振（源 stock-analysis-23 M010+M004 子集）

实现注记（prereg §2 C2 的落地偏差，跑前声明）：
  C2 「自 j 以来高点」以 21 日窗高点 max(high,21) 代理（j∈[t-20,t-1]），
  代理高点 ≥ 真实段高点 → 回调深度计算更深 → 更难满足 [-10%,0] 区间 = 偏保守方向。

尺子：信号日收盘 → t+1 开盘买（停牌/开盘涨停作废）；持有 N∈{5,10,20} 跌停拦卖重试≤10；
     名义 1 万整手；佣金 max(万2.5,5元)+卖印花税 0.05%。冷却 10 bar/包。
自检：SC1 历史切片重算断言 / SC3 手工对账3笔 / SC4 持有期 sanity / SC5 入场bar=t+1。
判定（主窗 20160101 起）：n≥500 且 mean>0 且 t≥3.0 → candidate，否则 falsified。

运行：python skill_rules_prescreen_20260904.py [--limit-codes N]
输出：_cache/skill_sweep_20260904.json + _cache/skill_sweep_20260904_evidence.log
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
OUT_JSON = Path(BASE) / "_cache" / "skill_sweep_20260904.json"
OUT_LOG = Path(BASE) / "_cache" / "skill_sweep_20260904_evidence.log"

WARM_START = "20150401"          # 暖机（GMMA60+云52+26 前移+量能60 ≈ 160 bar）
SIG_END = "20260904"
MAIN_START = "20160101"          # 主窗统计起点
RECENT5Y = "20210828"
HOLDS = (5, 10, 20)
NOTIONAL = 10_000.0
COMM, STAMP, MIN_COMM, LOT = 0.00025, 0.0005, 5.0, 100
COOLDOWN = 10
MIN_N = 500

sys.path.insert(0, BASE)
from tulong_buy_whitelist import board_ok, name_ok, norm6  # noqa: E402

PACKS = ("trend_quality", "dip_pullback", "second_buy", "factor_invest", "macd_flow")


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


def load_wide(uni: set[str]) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date",
        con, params=(WARM_START, SIG_END))
    con.close()
    df["code"] = df["code"].map(lambda c: norm6(str(c)))
    df = df[df["code"].isin(uni)].reset_index(drop=True)
    return df


# ---------------------------------------------------------------- 指标 helper（全部只用历史）

def _ma(s: pd.Series, w: int) -> pd.Series:
    return s.rolling(w, min_periods=w).mean()


def _rsi_wilder(c: pd.Series, w: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = up.ewm(alpha=1.0 / w, min_periods=w, adjust=False).mean()
    ad = dn.ewm(alpha=1.0 / w, min_periods=w, adjust=False).mean()
    rs = au / ad.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _macd(c: pd.Series):
    dif = c.ewm(span=12, min_periods=26, adjust=False).mean() - \
        c.ewm(span=26, min_periods=26, adjust=False).mean()
    dea = dif.ewm(span=9, min_periods=9, adjust=False).mean()
    return dif, dea


def _wilder_smooth(s: pd.Series, w: int) -> pd.Series:
    return s.ewm(alpha=1.0 / w, min_periods=w, adjust=False).mean()


def _adx_di(h: pd.Series, l: pd.Series, c: pd.Series, w: int = 14):
    up_m = h.diff()
    dn_m = -l.diff()
    plus_dm = pd.Series(np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0), index=h.index)
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = _wilder_smooth(tr, w)
    di_p = 100 * _wilder_smooth(plus_dm, w) / atr
    di_m = 100 * _wilder_smooth(minus_dm, w) / atr
    dx = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0.0, np.nan)
    adx = _wilder_smooth(dx.fillna(0.0), w)
    return adx, di_p, di_m


def pack_signals(g: pd.DataFrame) -> dict[str, np.ndarray]:
    """5 包信号布尔序列（未冷却）。全部 rolling/ewm/shift 语义=只用截至 t 历史。"""
    o, h, l, c = g["open"], g["high"], g["low"], g["close"]
    v, amt = g["volume"], g["amount"]
    sig: dict[str, pd.Series] = {}

    # ---- A trend_quality
    amt20 = amt.rolling(20, min_periods=20).mean()
    adx, di_p, di_m = _adx_di(h, l, c)
    short_grp = pd.concat([_ma(c, w) for w in (3, 5, 8, 10, 12, 15)], axis=1).min(axis=1)
    long_grp = pd.concat([_ma(c, w) for w in (30, 35, 40, 45, 50, 60)], axis=1).max(axis=1)
    tenkan = (h.rolling(9, min_periods=9).max() + l.rolling(9, min_periods=9).min()) / 2
    kijun = (h.rolling(26, min_periods=26).max() + l.rolling(26, min_periods=26).min()) / 2
    span_b_src = (h.rolling(52, min_periods=52).max() + l.rolling(52, min_periods=52).min()) / 2
    cloud = pd.concat([((tenkan + kijun) / 2).shift(26), span_b_src.shift(26)], axis=1).max(axis=1)
    sig["trend_quality"] = ((amt20 >= 1.0e8) & (adx > 20) & (di_p > di_m)
                            & (short_grp > long_grp) & (c > cloud) & (tenkan > kijun))

    # ---- B dip_pullback
    ma5, ma10, ma20 = _ma(c, 5), _ma(c, 10), _ma(c, 20)
    ma20_up = ma20 > ma20.shift(5)
    aligned = (ma5 > ma10) & (ma10 > ma20)
    dev_ok = (c / ma20 - 1.0) <= 0.20
    h20 = h.rolling(20, min_periods=20).max()
    pull = c / h20 - 1.0
    vol5 = v.rolling(5, min_periods=5).mean()
    vol60 = v.rolling(60, min_periods=60).mean()
    shrink = vol5 <= vol60 * 0.60
    stable = c >= l.rolling(5, min_periods=5).min()
    sig["dip_pullback"] = (ma20_up & aligned & dev_ok
                           & (pull >= -0.15) & (pull <= -0.05) & shrink & stable)

    # ---- C second_buy
    ret1 = c / c.shift(1) - 1.0
    vol20 = v.rolling(20, min_periods=20).mean()
    surge = (ret1 >= 0.08) & (v > 1.5 * vol20) & (c > o)
    surge_recent = surge.rolling(20, min_periods=1).max().shift(1).astype(bool)  # j∈[t-20,t-1]
    h21 = h.rolling(21, min_periods=21).max()
    cb_pull = c / h21 - 1.0
    cb_shrink = v.rolling(5, min_periods=5).mean() <= v.rolling(20, min_periods=20).mean() * 0.75
    dif, dea = _macd(c)
    gold = (dif > dea) & (dif.shift(1) <= dea.shift(1))
    relaunch = (c > ma20) & gold & (v > 1.1 * v.shift(1))
    sig["second_buy"] = (surge_recent & (cb_pull >= -0.10) & (cb_pull <= 0.0)
                         & cb_shrink & relaunch)

    # ---- D factor_invest
    rsi = _rsi_wilder(c)
    sig["factor_invest"] = (aligned & (c > _ma(c, 60)) & (v > 1.5 * vol20)
                            & (c > ma5) & (rsi >= 40) & (rsi <= 75))

    # ---- E macd_flow
    sig["macd_flow"] = gold & (v > 1.5 * vol20)

    return {k: s.fillna(False).to_numpy() for k, s in sig.items()}


def cooldown(idx: np.ndarray, gap: int = COOLDOWN) -> list[int]:
    out: list[int] = []
    last = -10 ** 9
    for i in idx:
        if i - last >= gap:
            out.append(int(i))
            last = i
    return out


def ruler(code, o, h, l, c, v, sig_idx):
    n = len(c)
    t = sig_idx
    e = t + 1                                                  # SC5
    if e >= n:
        return {"status": "no_next_bar"}
    prev_c = c[t]
    lp = limit_pct(code)
    lu_price = round(prev_c * (1 + lp), 2)
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
        for _ in range(11):
            if b >= n:
                break
            prev = c[b - 1]
            ldp = round(prev * (1 - lp), 2)
            ok_vol = (not np.isnan(v[b])) and v[b] > 0
            if ok_vol and o[b] > ldp + 0.005:
                exit_px = float(o[b])
                sell_val = shares * exit_px
                sell_fee = max(COMM * sell_val, MIN_COMM) + STAMP * sell_val
                exits[N] = {"ret": (sell_val - sell_fee) / (buy_val + buy_fee) - 1.0,
                            "hold_bars": b - e,
                            "fee_drag": (buy_fee + sell_fee) / buy_val}
                sold = True
                break
            b += 1
        if not sold:
            exits[N] = None
    return {"status": "ok", "entry": entry, "shares": shares, "exits": exits}


# ---------------------------------------------------------------- SC1：历史切片重算断言

def sc1_recheck(g: pd.DataFrame, signals_by_pack: dict[str, list[int]], per_pack: int = 2) -> list[str]:
    """对每包抽信号，截取 [0..t] 历史独立重算条件，断言布尔一致（防前视/防实现漂移）。"""
    lines = []
    fails = 0
    for pack in PACKS:
        idxs = signals_by_pack.get(pack, [])
        if not idxs:
            continue
        for i in idxs[:per_pack]:
            sub = g.iloc[: i + 1]                       # 只含 0..t
            recomputed = pack_signals(sub)[pack]
            got = bool(recomputed[-1])
            if not got:
                fails += 1
                lines.append(f"SC1 FAIL {pack} idx={i} 历史切片重算=False（前视或漂移）")
            else:
                lines.append(f"SC1 PASS {pack} idx={i} 历史切片重算一致")
    return fails, lines


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-codes", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260904)
    a = ap.parse_args()

    t0 = time.time()
    uni = load_universe()
    print(f"[info] 宇宙: board_ok+name_ok 后 {len(uni)} 只")
    if a.limit_codes:
        uni = set(sorted(uni)[: a.limit_codes])
        print(f"[info] --limit-codes: 截取 {len(uni)} 只（冒烟）")

    df = load_wide(uni)
    print(f"[info] qfq 长表: {len(df)} 行，{df['code'].nunique()} 只，{time.time()-t0:.0f}s")

    signals = []
    sc1_src = {}
    for code, g in df.groupby("code", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 160:
            continue
        masks = pack_signals(g)
        for pack, mask in masks.items():
            idxs = cooldown(np.flatnonzero(mask))
            for i in idxs:
                signals.append((pack, code, i))
                sc1_src.setdefault(pack, []).append((code, i))

    sig_df = pd.DataFrame(signals, columns=["pack", "code", "sig_idx"])
    print(f"[info] 信号总数(冷却去重后): {len(sig_df)}，{time.time()-t0:.0f}s")
    if len(sig_df) == 0:
        print("[FAIL] 零信号，检查规则实现")
        return 2
    print(sig_df.groupby("pack").size().to_string())

    # ---- SC1 历史切片重算（每包前 2 个信号）
    code_groups = {str(code): g.reset_index(drop=True) for code, g in df.groupby("code", sort=True)}
    fails, ev_lines = 0, []
    for pack in PACKS:
        src = sc1_src.get(pack, [])
        for code, i in src[:2]:
            sub = code_groups[code].iloc[: i + 1]
            got = bool(pack_signals(sub)[pack][-1])
            tag = "PASS" if got else "FAIL"
            if not got:
                fails += 1
            ev_lines.append(f"SC1 {tag} {pack} {code} idx={i} 历史切片重算一致={got}")
    if fails:
        print(f"[FAIL] SC1 历史切片重算 {fails} 处不一致（前视或实现漂移）")
        OUT_LOG.parent.mkdir(parents=True, exist_ok=True)
        OUT_LOG.write_text("\n".join(ev_lines), encoding="utf-8")
        return 2
    print(f"[PASS] SC1 历史切片重算: {len(ev_lines)} 条全部一致")

    # ---- 执行尺子
    trades = []
    status_cnt: dict[str, int] = {}
    for pack, code, i in signals:
        g = code_groups[code]
        r = ruler(code, g["open"].to_numpy(float), g["high"].to_numpy(float),
                  g["low"].to_numpy(float), g["close"].to_numpy(float),
                  g["volume"].to_numpy(float), i)
        st = r["status"]
        status_cnt[st] = status_cnt.get(st, 0) + 1
        if st != "ok":
            continue
        d = str(g["date"].astype(str).iloc[i])
        trades.append({"pack": pack, "code": code, "date": d, **r})
    print(f"[info] 尺子完成: ok={status_cnt.get('ok',0)} 作废={status_cnt}，{time.time()-t0:.0f}s")

    # ---- SC4 持有期 sanity
    hb = [t["exits"][5]["hold_bars"] for t in trades if t["exits"].get(5)]
    if hb:
        m = float(np.mean(hb))
        if not (4.5 <= m <= 6.5):
            print(f"[FAIL] SC4 持有期 sanity: N=5 平均 {m:.2f} bar 越界")
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
            ev_lines.append(f"SC3 {t['pack']} {t['code']}@{t['date']} entry={entry}(bar{i}+1) "
                            f"exit={exit_px} manual={manual:.8f} ruler={got:.8f} {'PASS' if ok else 'FAIL'}")
            if not ok:
                print(f"[FAIL] SC3 手工对账不一致: {t}")
                return 2
        print("[PASS] SC3 手工对账: 3 笔分毫不差")

    # ---- SC2 抽样证据
    for pack in sorted(sig_df["pack"].unique()):
        sub = [t for t in trades if t["pack"] == pack]
        for t in rng.sample(sub, min(5, len(sub))):
            g = code_groups[t["code"]]
            i = int(g.index[g["date"].astype(str) == t["date"]][0])
            lo, hi = max(0, i - 7), min(len(g), i + 8)
            seg = g.iloc[lo:hi][["date", "open", "high", "low", "close"]].to_string(index=False)
            ev_lines.append(f"SC2 {pack} {t['code']}@{t['date']} (idx={i})\n{seg}\n")

    # ---- 汇总（主窗=20160101 起）
    def stats(rows):
        out = {}
        for pack in sorted({r["pack"] for r in rows}):
            for N in HOLDS:
                rets = np.array([r["exits"][N]["ret"] for r in rows
                                 if r["pack"] == pack and r["exits"].get(N)])
                if not len(rets):
                    continue
                mu, sd = float(rets.mean()), float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
                t_stat = mu / (sd / math.sqrt(len(rets))) if sd > 0 else 0.0
                out[f"{pack}|N{N}"] = {
                    "n": int(len(rets)), "mean": round(mu, 6),
                    "median": round(float(np.median(rets)), 6),
                    "win": round(float((rets > 0).mean()), 4),
                    "t": round(t_stat, 3),
                    "verdict": ("candidate" if (mu > 0 and len(rets) >= MIN_N and t_stat >= 3.0)
                                else "falsified"),
                }
        return out

    main_rows = [t for t in trades if t["date"] >= MAIN_START]
    recent_rows = [t for t in trades if t["date"] >= RECENT5Y]
    main_stats = stats(main_rows)
    recent_stats = stats(recent_rows)

    result = {
        "meta": {"window": f"{WARM_START}(warm)~{SIG_END}", "main_start": MAIN_START,
                 "recent5y_start": RECENT5Y, "universe": len(uni), "signals": len(sig_df),
                 "trades_ok": len(trades), "status_counts": status_cnt,
                 "notional": NOTIONAL, "seed": a.seed,
                 "prereg": "reports/PREREG_SKILL_SWEEP_20260904.md"},
        "main": main_stats,
        "recent5y": recent_stats,
        "signal_counts": {k: int(v) for k, v in sig_df.groupby("pack").size().items()},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_LOG.write_text("\n".join(ev_lines), encoding="utf-8")

    print("\n===== 主窗（2016-01~2026-09）每笔净收益 | 判定门禁 n≥500 & mean>0 & t≥3 =====")
    print(f"{'包|N':<20}{'n':>7}{'均值%':>9}{'中位%':>8}{'胜率%':>7}{'t':>8}{'判定':>10}")
    for k, s in main_stats.items():
        print(f"{k:<20}{s['n']:>7}{s['mean']*100:>9.3f}{s['median']*100:>8.2f}"
              f"{s['win']*100:>7.1f}{s['t']:>8.2f}{s['verdict']:>10}")
    print(f"\n[done] {OUT_JSON} | evidence: {OUT_LOG} | 总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
