#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""选股类 Skill 补测批 · Part1 日线向量化包（2026-09-05）

预注册：reports/PREREG_SKILL_SWEEP2_20260905.md（跑前钉死）
尺子逐位复用 skill_rules_prescreen_20260904（买入尺子；brk_support/sixdim_excl 判定方向反转注明）
本脚本覆盖非缠论日线包（缠论抽样滚动引擎=Part2 独立脚本，分钟级=Part3）。

运行：python skill_sweep2_daily_20260905.py [--limit-codes N]
输出：_cache/skill_sweep2_daily_20260905.json
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
SW_DB = r"D:\workbuddyspace\databases\sw_industry.db"
PIT_DB = r"D:\workbuddyspace\factor_engine\data\factor_pit.db"
OUT_JSON = Path(BASE) / "_cache" / "skill_sweep2_daily_20260905.json"

sys.path.insert(0, BASE)
from skill_rules_prescreen_20260904 import (  # noqa: E402
    load_universe, ruler, cooldown,
)

WARM_START, SIG_END = "20150401", "20260904"
MAIN_START, RECENT5Y, DB_DENSE = "20160101", "20210828", "20240101"
HOLDS = (5, 10, 20)
LIMITED_PACKS = {  # 限窗包（daily_basic 密集窗 2024 起）
    "st_R00x", "st_R01x", "hotrank_tech", "s23_chip",
    "em_volbreak", "em_lowpe", "em_strong", "em_oversold",
}
PACKS = [
    "st_R00", "st_R00x", "st_R01", "st_R01x", "st_R02", "st_R03", "st_R04", "st_R06", "st_R07",
    "s23_chip", "s23_kpattern", "s23_qilong", "s23_ztnext",
    "hotrank_tech", "brk_support", "aistock_rsi", "macd_cross",
    "em_volbreak", "em_lowpe", "em_strong", "em_oversold",
]


def _ma(s, w):
    return s.rolling(w, min_periods=w).mean()


def _swing_idx(arr, kind):
    w = 21
    s = pd.Series(arr)
    roll = (s.rolling(w, min_periods=w).min() if kind == "low" else s.rolling(w, min_periods=w).max()).shift(-10)
    return np.flatnonzero(((s == roll) & roll.notna()).to_numpy())


def pack_signals(g: pd.DataFrame, db: pd.DataFrame | None,
                 ind_pct: pd.Series | None) -> dict[str, np.ndarray]:
    """db=该票 daily_basic 面板（限窗包用，可能为空）；ind_pct=行业日涨幅序列。"""
    o, h, l, c = g["open"], g["high"], g["low"], g["close"]
    v, amt = g["volume"], g["amount"]
    idx = g.index
    sig: dict[str, pd.Series] = {}

    ma5, ma10, ma20 = _ma(c, 5), _ma(c, 10), _ma(c, 20)
    vol5, vol20, vol60 = _ma(v, 5), _ma(v, 20), _ma(v, 60)
    ret1 = c / c.shift(1) - 1.0

    # ---- st_R00 放量新高（全史版: C2+C3+C4+C6; x=限窗版加 C5 换手≤50%）
    c2 = ((v > 2 * vol20).rolling(3, min_periods=1).max().astype(bool)
          | (vol5 > 1.5 * vol20.shift(2)))
    c3 = (c > ma5) & (c.shift(1) > ma5.shift(1)) & (c.shift(2) > ma5.shift(2))
    c4 = amt.rolling(3, min_periods=3).sum() > 2.4e9
    wk52 = h.rolling(252, min_periods=120).max().shift(1)
    c6 = h >= wk52
    sig["st_R00"] = (c2 & c3 & c4 & c6).fillna(False)
    if db is not None and len(db):
        c5 = np.nan_to_num((db["turnover_rate"] <= 50.0).to_numpy()).astype(bool)
        sig["st_R00x"] = (c2 & c3 & c4 & c6 & c5).fillna(False)

    # ---- st_R01 量能验证突破（全史: 量+破高+涨幅; x=加换手≥3%）
    h10 = h.rolling(10, min_periods=10).max().shift(1)
    base01 = ((v >= 1.5 * _ma(v, 5)) & (c > h10) & (ret1 >= 0.03) & (ret1 <= 0.08)).fillna(False)
    sig["st_R01"] = base01
    if db is not None and len(db):
        sig["st_R01x"] = base01 & pd.Series(
            np.nan_to_num((db["turnover_rate"] >= 3.0).to_numpy()).astype(bool), index=idx)

    # ---- st_R02 均线回踩
    ret30 = c / c.shift(30) - 1.0
    near_ma = ((c / ma20 - 1.0).abs() <= 0.02) | ((c / _ma(c, 30) - 1.0).abs() <= 0.02)
    sig["st_R02"] = ((ret30 > 0) & near_ma & (vol5 < 0.7 * vol20) & (c >= ma20)).fillna(False)

    # ---- st_R03 涨停次日 / s23_ztnext（同口径，分开统计）
    lp = 0.20 if str(g["code"].iloc[0]).startswith(("300", "301")) else 0.10
    zt = ret1 >= lp * 0.98
    one_word = (o / c.shift(1) - 1.0) >= lp * 0.98
    st_zt = (zt & ~one_word)
    sig["st_R03"] = st_zt.fillna(False)
    sig["s23_ztnext"] = zt.fillna(False)          # M09 不剔一字板（差异点）

    # ---- st_R04 强势回调
    ret10 = c / c.shift(10) - 1.0
    sig["st_R04"] = ((ret10 >= 0.15) & (ret1 <= -0.01) & (ret1 >= -0.03)
                     & (v < 0.7 * vol5) & (c > ma5)).fillna(False)

    # ---- st_R06 缩量蓄势
    amp5 = (h.rolling(5, min_periods=5).max() / l.rolling(5, min_periods=5).min() - 1.0)
    low26w = l.rolling(130, min_periods=130).min()
    sig["st_R06"] = ((amp5 <= 0.05) & (vol5 < 0.6 * vol60) & (c > ma20)
                     & (c / low26w - 1.0 <= 0.20)).fillna(False)

    # ---- st_R07 在主循环单独处理（需行业日强度映射，见 main）
    sig["st_R07"] = pd.Series(False, index=idx)

    # ---- s23_kpattern K线形态（锤子/看涨吞没/启明星任一）
    body = (c - o).abs()
    lower = pd.concat([c, o], axis=1).min(axis=1) - l
    upper = h - pd.concat([c, o], axis=1).max(axis=1)
    hammer = (lower >= 2 * body) & (upper <= body) & (body > 0) & (ret1.shift(1) < 0)
    prev_body_dn = (c.shift(1) < o.shift(1))
    engulf = prev_body_dn & (c > o) & (c >= o.shift(1)) & (o <= c.shift(1))
    star = (prev_body_dn & (ret1 < -0.01) & (body.shift(1) / c.shift(1) < 0.005)
            & (c > o) & (c > (o.shift(2) + c.shift(2)) / 2))
    sig["s23_kpattern"] = (hammer | engulf | star).fillna(False)

    # ---- s23_qilong M007 平台突破
    box20 = (h.rolling(20, min_periods=20).max() / l.rolling(20, min_periods=20).min() - 1.0) <= 0.15
    brk = (v > 2 * vol20) & (c >= h.rolling(20, min_periods=20).max())
    sig["s23_qilong"] = (box20.shift(1) & brk).fillna(False)

    # ---- hotrank_tech（技术面：多头+J<20金叉+量比，限窗 volume_ratio）
    low9, high9 = l.rolling(9, min_periods=9).min(), h.rolling(9, min_periods=9).max()
    rsv = ((c - low9) / (high9 - low9) * 100).fillna(50.0)
    k_ = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d_ = k_.ewm(alpha=1 / 3, adjust=False).mean()
    j_ = 3 * k_ - 2 * d_
    jcross = (j_ > j_.shift(1)) & (j_ < 20)
    aligned = (ma5 > ma10) & (ma10 > ma20)
    tech = (aligned & jcross).fillna(False)
    if db is not None and len(db):
        sig["hotrank_tech"] = (tech & pd.Series(
            np.nan_to_num((db["volume_ratio"] > 1.5).to_numpy()).astype(bool), index=idx)).fillna(False)
    else:
        sig["hotrank_tech"] = pd.Series(False, index=idx)

    # ---- brk_support 破位确认（t 破 20 日低，t+1 未收复 → 信号在 t+1；判定方向反转）
    low20 = l.rolling(20, min_periods=20).min().shift(1)
    broke = c < low20
    sig["brk_support"] = (broke.shift(1) & (c <= low20.shift(1))).fillna(False)

    # ---- aistock_rsi
    dd = c.diff()
    au = dd.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    ad = (-dd).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + au / ad.replace(0.0, np.nan))
    sig["aistock_rsi"] = (rsi < 30).fillna(False)

    # ---- macd_cross（金叉，无量条件）
    dif = c.ewm(span=12, min_periods=26, adjust=False).mean() - c.ewm(span=26, min_periods=26, adjust=False).mean()
    dea = dif.ewm(span=9, min_periods=9, adjust=False).mean()
    sig["macd_cross"] = ((dif > dea) & (dif.shift(1) <= dea.shift(1))).fillna(False)

    # ---- s23_chip 筹码（限窗：换手率衰减分布）
    if db is not None and len(db):
        arr_tr = (db["turnover_rate"] / 100.0).clip(upper=0.99).to_numpy(float)
        arr_px = (amt / v.replace(0, np.nan)).to_numpy(float)
        # 衰减累加分布（120bar 窗；换手 NaN 的窗口不产出=限窗语义）
        profit = np.full(len(idx), np.nan)
        width = np.full(len(idx), np.nan)
        c_np = c.to_numpy(float)
        n = len(idx)
        for t in range(119, n):
            seg_tr, seg_px = arr_tr[t - 119:t + 1], arr_px[t - 119:t + 1]
            if np.isnan(seg_tr).any() or np.isnan(seg_px).any():
                continue
            wts = (1 - seg_tr)
            decay = np.cumprod(wts[::-1])[::-1]  # 越新权重越大
            w = decay * seg_tr
            if w.sum() <= 0:
                continue
            mu = float((w * seg_px).sum() / w.sum())
            s2 = float((w * (seg_px - mu) ** 2).sum() / w.sum())
            profit[t] = float((w[seg_px <= c_np[t]].sum()) / w.sum())
            width[t] = float(np.sqrt(s2)) / mu
        sig["s23_chip"] = pd.Series((profit < 0.25) & (width < 0.20), index=idx).fillna(False)
    else:
        sig["s23_chip"] = pd.Series(False, index=idx)

    # ---- 东财 4 配方（限窗）
    if db is not None and len(db):
        def _num(col):
            return pd.to_numeric(pd.Series(db[col].to_numpy()), errors="coerce").to_numpy(float)
        pe_, pb_, to_, vr_ = _num("pe"), _num("pb"), _num("turnover_rate"), _num("volume_ratio")
        mvs = _num("total_mv") if "total_mv" in db else np.full(len(idx), np.nan)
        sig["em_volbreak"] = ((ret1 >= 0.03).to_numpy()
                              & (vr_ > 2) & (pe_ <= 50) & (pe_ > 0))
        sig["em_lowpe"] = ((pe_ <= 15) & (pe_ > 0) & (pb_ <= 1.5) & (pb_ > 0) & (mvs >= 500))
        sig["em_strong"] = ((ret1 >= 0.05).to_numpy() & (to_ >= 10) & (mvs <= 100) & (mvs > 0))
        sig["em_oversold"] = ((ret1 <= -0.05).to_numpy() & (to_ >= 2) & (pe_ <= 30) & (pe_ > 0))
        for k in ("em_volbreak", "em_lowpe", "em_strong", "em_oversold"):
            sig[k] = pd.Series(sig[k], index=idx).fillna(False)
    else:
        for k in ("em_volbreak", "em_lowpe", "em_strong", "em_oversold"):
            sig[k] = pd.Series(False, index=idx)

    return {k: s.fillna(False).to_numpy() for k, s in sig.items()}


def load_daily_basic(uni: set[str]) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    df = pd.read_sql_query(
        "SELECT ts_code,trade_date,turnover_rate,volume_ratio,pe,pb,total_share,float_share"
        " FROM daily_basic WHERE trade_date>=? ", con, params=(DB_DENSE,))
    con.close()
    df["code"] = df["ts_code"].astype(str).str[:6]
    df = df[df["code"].isin(uni)]
    df["date"] = df["trade_date"].astype(str)
    return df[["code", "date", "turnover_rate", "volume_ratio", "pe", "pb", "total_share"]]


def load_industry_pct(uni) -> tuple[dict[str, pd.Series], dict[str, set[str]]]:
    """行业日涨幅 + 行业成员（industry_kline_ths 90 个 THS 一级行业；成员映射用 ths 名对齐失败则全市场）。"""
    con = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True, timeout=60)
    ik = pd.read_sql_query(
        "SELECT industry_name,trade_date,close FROM industry_kline_ths WHERE trade_date>='2015-04-01'",
        con)
    con.close()
    ik["date"] = ik["trade_date"].astype(str).str.replace("-", "", regex=False)
    ik = ik.sort_values(["industry_name", "date"])
    ik["ind_ret"] = ik.groupby("industry_name")["close"].pct_change()
    # 每日行业涨幅排名（前10%）
    ik["rank_pct"] = ik.groupby("date")["ind_ret"].rank(pct=True)
    ik_strong = ik[ik["rank_pct"] >= 0.90][["date", "industry_name"]]
    strong_map = ik_strong.groupby("date")["industry_name"].apply(set).to_dict()
    # 个股→行业：无成分表，回退=全行业（st_R07 物化为：行业强日全市场滞涨股口径，声明见 prereg 修订）
    pct_by_name = {n: g.set_index("date")["ind_ret"] for n, g in ik.groupby("industry_name")}
    return strong_map, pct_by_name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-codes", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260905)
    a = ap.parse_args()
    t0 = time.time()

    uni = load_universe()
    if a.limit_codes:
        uni = set(sorted(uni)[: a.limit_codes])
    print(f"[info] 宇宙 {len(uni)} 只")

    from skill_rules_prescreen_20260904 import load_wide
    df = load_wide(uni)
    print(f"[info] qfq {len(df)} 行 {df['code'].nunique()} 只 {time.time()-t0:.0f}s")

    db_all = load_daily_basic(uni)
    # 市值补 close：用 qfq close 近似（亿= total_share(万股)*close/1e4）
    close_map = df[["code", "date", "close"]]
    db_all = db_all.merge(close_map, on=["code", "date"], how="left")
    db_all["total_mv"] = db_all["total_share"] * db_all["close"] / 1e4  # 亿元
    db_panel = {code: g.set_index("date") for code, g in db_all.groupby("code")}

    strong_map, _ = load_industry_pct(uni)
    print(f"[info] daily_basic {len(db_all)} 行; 强势行业日 {len(strong_map)} 天 {time.time()-t0:.0f}s")

    signals = []
    for code, g in df.groupby("code", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 160:
            continue
        g["code"] = code
        dbp = db_panel.get(code)
        if dbp is not None:
            dbp = dbp.reindex(g["date"].astype(str).to_numpy())  # 日期对齐到票行(位置一致)
        masks = pack_signals(g, dbp, None)
        dates = g["date"].astype(str).to_numpy()
        for pack, mask in masks.items():
            for i in cooldown(np.flatnonzero(mask)):
                signals.append({"pack": pack, "code": code, "date": dates[i], "sig_idx": i})
        # st_R07 单独：强势行业日 + 当日个股涨幅 < 0 且 close>MA20（滞涨代理：全市场无行业映射,声明）
        ma20 = g["close"].rolling(20, min_periods=20).mean()
        ret1 = g["close"] / g["close"].shift(1) - 1.0
        r07 = []
        for i in range(159, len(g)):
            d = str(g["date"].iloc[i])
            if strong_map.get(d) and ret1.iloc[i] < 0 and g["close"].iloc[i] > ma20.iloc[i]:
                r07.append(i)
        for i in cooldown(np.array(r07, dtype=int)):
            signals.append({"pack": "st_R07", "code": code, "date": str(g["date"].iloc[i]), "sig_idx": int(i)})

    sig_df = pd.DataFrame(signals)
    print(f"[info] 信号 {len(sig_df)}，{time.time()-t0:.0f}s")
    print(sig_df.groupby("pack").size().to_string())

    code_groups = {str(code): g.reset_index(drop=True) for code, g in df.groupby("code", sort=True)}
    trades, status_cnt = [], {}
    for row in signals:
        g = code_groups[row["code"]]
        r = ruler(row["code"], g["open"].to_numpy(float), g["high"].to_numpy(float),
                  g["low"].to_numpy(float), g["close"].to_numpy(float),
                  g["volume"].to_numpy(float), row["sig_idx"])
        status_cnt[r["status"]] = status_cnt.get(r["status"], 0) + 1
        if r["status"] == "ok":
            trades.append({"pack": row["pack"], "code": row["code"], "date": row["date"], **r})
    print(f"[info] 尺子 ok={len(trades)} {status_cnt} {time.time()-t0:.0f}s")

    hb = [t["exits"][5]["hold_bars"] for t in trades if t["exits"].get(5)]
    if hb and not (4.5 <= float(np.mean(hb)) <= 6.5):
        print(f"[FAIL] SC4 {np.mean(hb)}")
        return 2
    print(f"[PASS] SC4 hold={np.mean(hb):.2f}")

    rng = random.Random(a.seed)
    cand = [t for t in trades if all(t["exits"].get(N) for N in HOLDS)]
    sc3_ok = True
    for t in rng.sample(cand, min(3, len(cand))):
        g = code_groups[t["code"]]
        i = int(g.index[g["date"].astype(str) == t["date"]][0])
        e = i + 1
        entry = float(g["open"].iloc[e])
        shares = int(10000 / entry / 100) * 100
        bf = max(0.00025 * shares * entry, 5.0)
        b = e + 5
        xp = float(g["open"].iloc[b])
        sf = max(0.00025 * shares * xp, 5.0) + 0.0005 * shares * xp
        manual = (shares * xp - sf) / (shares * entry + bf) - 1.0
        if abs(manual - t["exits"][5]["ret"]) > 1e-12:
            sc3_ok = False
    print(f"[{'PASS' if sc3_ok else 'FAIL'}] SC3")

    def stats(rows, wstart, min_n):
        out = {}
        for pack in sorted({r["pack"] for r in rows}):
            for N in HOLDS:
                rets = np.array([r["exits"][N]["ret"] for r in rows
                                 if r["pack"] == pack and r["date"] >= wstart and r["exits"].get(N)])
                if not len(rets):
                    continue
                mu, sd = float(rets.mean()), float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
                t_stat = mu / (sd / math.sqrt(len(rets))) if sd > 0 else 0.0
                out[f"{pack}|N{N}"] = {"n": int(len(rets)), "mean": round(mu, 6),
                                       "win": round(float((rets > 0).mean()), 4),
                                       "t": round(t_stat, 3),
                                       "verdict": ("candidate" if (mu > 0 and len(rets) >= min_n and t_stat >= 3.0)
                                                   else "falsified")}
        return out

    main_stats, recent_stats = {}, {}
    for pack in PACKS:
        rows = [t for t in trades if t["pack"] == pack]
        if not rows:
            continue
        if pack in LIMITED_PACKS:
            main_stats.update(stats(rows, DB_DENSE, 300))
        else:
            main_stats.update(stats(rows, MAIN_START, 500))
        recent_stats.update(stats(rows, RECENT5Y, 1))

    result = {
        "meta": {"window": f"{WARM_START}~{SIG_END}", "universe": len(uni),
                 "signals": len(sig_df), "trades_ok": len(trades),
                 "prereg": "reports/PREREG_SKILL_SWEEP2_20260905.md",
                 "limited_packs": sorted(LIMITED_PACKS)},
        "main": main_stats, "recent5y": recent_stats,
        "signal_counts": {k: int(v) for k, v in sig_df.groupby("pack").size().items()},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n===== 主窗判定（brk_support 预期负=回避价值成立） =====")
    for k, s in main_stats.items():
        print(f"{k:<22}{s['n']:>8}{s['mean']*100:>9.3f}{s['win']*100:>7.1f}{s['t']:>8.2f}{s['verdict']:>10}")
    print(f"[done] {OUT_JSON} {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
