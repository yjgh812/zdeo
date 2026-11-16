#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aurumq_alpha101_spotcheck_20260815.py — aurumq-rl 因子库抽检

从 D:\\zcodesj\\strategy_libs\\aurumq-rl\\src\\aurumq_rl\\factors\\alpha101\\ 源码
忠实移植 11 个代表性因子到本地数据 (qfq OHLCV + amount→vwap/adv20):
  alpha002/003/005/006/012/013/014/015/016/025/101 (覆盖 volume_price/momentum/mean_reversion 族)

口径: 月末信号截面因子值 vs 持有期收益(次bar→次exec), Spearman IC;
域: 60/00/30/68 非ST, 信号日raw close≤30元, qfq收益; 2018-01~2026-07
方向: 仓库注册 direction='reverse' (因子值与收益预期负相关), IC 按原值方向报告

用法: python D:/zcodesj/aurumq_alpha101_spotcheck_20260815.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")

PRICE_START, PRICE_END = "20160104", "20260814"
MAIN_END_TS = pd.Timestamp("2026-07-31")
SIG_FIRST = pd.Timestamp("2018-01-31")
STOCK_PREFIX = ("00", "30", "60", "68")
GHOST_DATES = {20181231, 20260627}
MIN_CROSS = 50

t0 = time.time()


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


# ═══════════════════════ 数据 ═══════════════════════

def load_data() -> dict:
    like = " OR ".join(f"code LIKE '{p}%'" for p in STOCK_PREFIX)
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
    try:
        df = pd.read_sql_query(
            f"SELECT code, date, open, high, low, close, volume, amount "
            f"FROM stock_daily_meta_qfq WHERE ({like}) AND date BETWEEN ? AND ? "
            f"AND close > 0 ORDER BY date, code",
            con, params=[PRICE_START, PRICE_END])
    finally:
        con.close()
    df["date"] = df["date"].astype(int)
    df = df[~df["date"].isin(GHOST_DATES)]
    df["code"] = df["code"].astype(str).str.zfill(6)
    out = {}
    for f in ("open", "high", "low", "close", "volume", "amount"):
        piv = df.pivot_table(index="date", columns="code", values=f, aggfunc="last").sort_index()
        piv.index = pd.to_datetime(piv.index.astype(str), format="%Y%m%d")
        out[f] = piv
    # ⚠️ qfq库 amount 列不可用(600898实测=未复权口径×100倍偏差/000858 2026-06起为0)
    # vwap 用 (high+low+close)/3 标准代理; adv20(美元成交额)=close×volume×100(qfq一致口径)
    out["vwap"] = (out["high"] + out["low"] + out["close"]) / 3.0
    out["returns"] = out["close"].pct_change()
    out["adv20"] = (out["close"] * out["volume"] * 100.0).rolling(20).mean()
    return out


def load_raw_close(dates: list) -> dict:
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=300)
    try:
        ph = ",".join("?" * len(dates))
        df = pd.read_sql_query(
            f"SELECT date, code, close FROM stock_factors_fundamental WHERE date IN ({ph})",
            con, params=[int(d) for d in dates])
    finally:
        con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return {pd.Timestamp(str(d)): g.set_index("code")["close"]
            for d, g in df.groupby("date")}


def load_st_codes() -> set:
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    try:
        info = pd.read_sql_query("SELECT code, name, is_st FROM stock_info", con)
    finally:
        con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    return set(info.loc[(info["is_st"].astype(str) == "1")
                        | info["name"].fillna("").str.contains("ST")
                        | info["name"].fillna("").str.contains("退"), "code"])


# ═══════════════════════ Alpha101 因子 (忠实移植) ═══════════════════════
# 约定: X=(date×code) DataFrame, CS=按日横截面rank(pct), TS=按股滚动(含当前行, min_periods=window)

def _cs_rank(m: pd.DataFrame) -> pd.DataFrame:
    return m.rank(axis=0, pct=True)


def _ts_corr(a: pd.DataFrame, b: pd.DataFrame, w: int) -> pd.DataFrame:
    return a.rolling(w, min_periods=w).corr(b)


def _ts_cov(a: pd.DataFrame, b: pd.DataFrame, w: int) -> pd.DataFrame:
    return a.rolling(w, min_periods=w).cov(b)


def _ts_rank(m: pd.DataFrame, w: int) -> pd.DataFrame:
    return m.rolling(w, min_periods=w).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1],
                                             raw=False)


def _ts_sum(m: pd.DataFrame, w: int) -> pd.DataFrame:
    return m.rolling(w, min_periods=w).sum()


def alpha002(o: dict) -> pd.DataFrame:
    dlogv = np.log(o["volume"].replace(0, np.nan)).diff(2)
    i = (o["close"] - o["open"]) / o["open"].replace(0, np.nan)
    return -1.0 * _ts_corr(_cs_rank(dlogv), _cs_rank(i), 6)


def alpha003(o: dict) -> pd.DataFrame:
    return -1.0 * _ts_corr(_cs_rank(o["open"]), _cs_rank(o["volume"]), 10)


def alpha005(o: dict) -> pd.DataFrame:
    vw10 = _ts_sum(o["vwap"], 10) / 10.0
    inner1, inner2 = o["open"] - vw10, o["close"] - o["vwap"]
    return _cs_rank(inner1) * (-1.0 * _cs_rank(inner2).abs())


def alpha006(o: dict) -> pd.DataFrame:
    return -1.0 * _ts_corr(o["open"], o["volume"], 10)


def alpha012(o: dict) -> pd.DataFrame:
    dvol = o["volume"].diff(1)
    dclose = o["close"].diff(1)
    return np.sign(dvol) * (-1.0 * dclose)


def alpha013(o: dict) -> pd.DataFrame:
    cov = _ts_cov(_cs_rank(o["close"]), _cs_rank(o["volume"]), 5)
    return -1.0 * _cs_rank(cov)


def alpha014(o: dict) -> pd.DataFrame:
    dret = o["returns"].diff(3)
    return (-1.0 * _cs_rank(dret)) * _ts_corr(o["open"], o["volume"], 10)


def alpha015(o: dict) -> pd.DataFrame:
    corr3 = _ts_corr(_cs_rank(o["high"]), _cs_rank(o["volume"]), 3)
    return -1.0 * _ts_sum(_cs_rank(corr3.fillna(0.0)), 3)


def alpha016(o: dict) -> pd.DataFrame:
    cov = _ts_cov(_cs_rank(o["high"]), _cs_rank(o["volume"]), 5)
    return -1.0 * _cs_rank(cov)


def alpha025(o: dict) -> pd.DataFrame:
    inner = (-1.0 * o["returns"]) * o["adv20"] * o["vwap"] * (o["high"] - o["close"])
    return _cs_rank(inner)


def alpha101(o: dict) -> pd.DataFrame:
    return (o["close"] - o["open"]) / ((o["high"] - o["low"]) + 0.001)


FACTORS = {
    "alpha002": (alpha002, "vol_change_rank×intraday_ret_rank corr6"),
    "alpha003": (alpha003, "open_rank×vol_rank corr10"),
    "alpha005": (alpha005, "open-vwap10 rank × -|rank(close-vwap)|"),
    "alpha006": (alpha006, "-corr(open,vol,10)"),
    "alpha012": (alpha012, "sign(Δvol)×(-Δclose)"),
    "alpha013": (alpha013, "-rank(cov(rank(close),rank(vol),5))"),
    "alpha014": (alpha014, "-rank(Δret3)×corr(open,vol,10)"),
    "alpha015": (alpha015, "-sum3(rank(corr(rank(high),rank(vol),3)))"),
    "alpha016": (alpha016, "-rank(cov(rank(high),rank(vol),5))"),
    "alpha025": (alpha025, "rank(-ret×adv20×vwap×(high-close))"),
    "alpha101": (alpha101, "(close-open)/(high-low+0.001)"),
}


# ═══════════════════════ 主流程 ═══════════════════════

def main() -> int:
    log("[1/4] 加载数据...")
    o = load_data()
    prices = o["close"]
    st_codes = load_st_codes()
    cal = prices.index
    ym = cal.to_period("M")
    month_ends = sorted(pd.Series(cal, index=ym).groupby(level=0).max())
    month_ends = [d for d in month_ends if SIG_FIRST <= d <= MAIN_END_TS]
    rc_idx = load_raw_close([int(d.strftime("%Y%m%d")) for d in month_ends])
    execs = []
    for s in month_ends:
        pos = cal.searchsorted(s, side="right")
        execs.append(cal[pos] if pos < len(cal) and cal[pos] > s else None)

    # 持有期收益
    fr = {}
    for i, s in enumerate(month_ends):
        e = execs[i]
        end_e = execs[i + 1] if i + 1 < len(execs) else MAIN_END_TS
        if e is None:
            continue
        p0 = prices.loc[e]
        p1_raw = prices.loc[(prices.index > e) & (prices.index <= end_e)]
        p1 = p1_raw.iloc[-1] if len(p1_raw) else pd.Series(dtype=float)
        f = (p1 / p0).replace([np.inf, -np.inf], np.nan) - 1
        fr[s] = f.dropna()

    log("[2/4] 计算因子值 (全期日频)...")
    t1 = time.time()
    vals = {}
    for name, (fn, desc) in FACTORS.items():
        vals[name] = fn(o)
        log("      %s (%s) done %ds" % (name, desc, time.time() - t1))

    log("[3/4] 月度IC...")
    rows = []
    for name, (fn, desc) in FACTORS.items():
        v = vals[name]
        ics = {}
        for s in month_ends:
            rc = rc_idx.get(s)
            if rc is None or s not in fr:
                continue
            elig = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                            & rc.index.str[:2].isin(STOCK_PREFIX)]
            x = v.loc[s].reindex(elig).dropna()
            y = fr[s].reindex(x.index).dropna()
            common = x.index.intersection(y.index)
            if len(common) < MIN_CROSS:
                continue
            ic = stats.spearmanr(x.reindex(common), y.reindex(common))[0]
            if ic == ic:
                ics[s] = ic
        s_ic = pd.Series(ics).sort_index()
        if len(s_ic) == 0:
            rows.append((name, desc, np.nan, np.nan, np.nan, 0))
            continue
        icir = s_ic.mean() / s_ic.std() if s_ic.std() > 0 else 0.0
        rows.append((name, desc, s_ic.mean(), icir, (s_ic > 0).mean(), len(s_ic)))
        log("      %-10s IC%+.4f ICIR%+.2f 正率%.0f%% n=%d" % (
            name, s_ic.mean(), icir, (s_ic > 0).mean() * 100, len(s_ic)))

    log("[3.5/4] 最强因子组合回测 (alpha005/alpha015 vs 等权基准, 含0.1%成本)...")
    ret = prices.pct_change().clip(-0.21, 0.21)
    port_rows = []

    def sim(fn, topn=20):
        sig = {}
        for s in month_ends:
            rc = rc_idx.get(s)
            if rc is None:
                sig[s] = []
                continue
            elig = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                            & rc.index.str[:2].isin(STOCK_PREFIX)]
            x = fn(s).reindex(elig).dropna()
            if len(x) < MIN_CROSS:
                sig[s] = []
                continue
            sig[s] = list(x.nlargest(min(topn, len(x))).index)
        return sig

    def sim_engine(sig, end_ts=MAIN_END_TS):
        nav, navs, dts = 1.0, [], []
        prev_hold, turns = None, []
        for i, s in enumerate(month_ends):
            e = execs[i]
            end_e = execs[i + 1] if i + 1 < len(execs) else end_ts
            if e is None:
                continue
            hold = [c for c in sig[s] if c in prices.columns]
            period = cal[(cal > e) & (cal <= end_e)]
            if prev_hold is not None:
                turn = 1 - len(set(hold) & prev_hold) / max(len(set(hold)), 1)
                nav *= (1 - 0.001 * turn)
                turns.append(turn)
            prev_hold = set(hold)
            for d in period:
                if len(hold):
                    r = ret.loc[d].reindex(hold).fillna(0.0)
                    nav *= (1 + r.mean())
                navs.append(nav)
                dts.append(d)
        nav_s = pd.Series(navs, index=pd.DatetimeIndex(dts))
        return nav_s, (float(np.mean(turns)) if turns else 0.0)

    def perf(nav: pd.Series, turn: float = 0.0) -> dict:
        r = nav.pct_change().dropna()
        n_years = (nav.index[-1] - nav.index[0]).days / 365.25
        cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / max(n_years, 1e-9)) - 1
        vol = r.std() * np.sqrt(243)
        dd = (nav / nav.cummax() - 1).min()
        return {"cagr": cagr, "sharpe": cagr / vol if vol > 0 else 0,
                "mdd": dd, "turn": turn}

    for name in ("alpha015", "alpha006"):
        nav, turn = sim_engine(sim(vals[name].loc.__getitem__))
        st = perf(nav, turn)
        st["yearly"] = (1 + nav.pct_change().dropna()).groupby(
            nav.pct_change().dropna().index.year).prod() - 1
        port_rows.append((name, st))
        log("      %s Top20: CAGR%+.1f%% Sharpe%.2f MDD%.1f%% 换手%.0f%%" % (
            name, st["cagr"] * 100, st["sharpe"], st["mdd"] * 100, st["turn"] * 100))
        log("       分年: " + ", ".join("%d%+.0f%%" % (y, v * 100)
                                        for y, v in sorted(st["yearly"].items())))
    # 等权基准
    def _ew_picks():
        sig = {}
        for s in month_ends:
            rc = rc_idx.get(s)
            if rc is None:
                sig[s] = []
                continue
            sig[s] = [c for c in rc.index
                      if (rc[c] <= 30.0 and c not in st_codes and c[:2] in STOCK_PREFIX)]
        return sig
    nav_ew, _ = sim_engine(_ew_picks())
    st_ew = perf(nav_ew)
    port_rows.append(("EW_benchmark", st_ew))
    log("      EW基准: CAGR%+.1f%% Sharpe%.2f MDD%.1f%%" % (
        st_ew["cagr"] * 100, st_ew["sharpe"], st_ew["mdd"] * 100))

    log("[4/4] 写报告...")
    L = []
    L.append("# aurumq-rl Alpha101 因子抽检 — 2026-08-15\n")
    L.append("- 来源: D:\\zcodesj\\strategy_libs\\aurumq-rl\\ (polars源码逐式移植, 11因子)")
    L.append("- 口径: 月末信号截面 vs 持有期收益(次bar→次exec) Spearman IC; "
             "60/00/30/68 非ST; 信号日raw close≤30元; qfq; 2018-01~2026-07")
    L.append("- 方向: 仓库注册 direction='reverse'（因子值与未来收益负相关为预期方向）\n")
    L.append("| 因子 | 公式 | 月度IC | ICIR | 正率 | 月数 |")
    L.append("|---|---|---|---|---|---|")
    for name, desc, ic, icir, pos, n in rows:
        L.append("| %s | %s | %+.4f | %+.2f | %.0f%% | %d |" % (
            name, desc, ic, icir, pos * 100 if pos == pos else 0, n))
    L.append("\n## 组合回测 (最强因子, Top20, 含0.1%%单边成本, 月末信号次bar成交)\n")
    L.append("| 组 | CAGR | Sharpe | MDD | 月均换手 |")
    L.append("|---|---|---|---|---|")
    for name, st in port_rows:
        L.append("| %s | %+.1f%% | %.2f | %.1f%% | %.0f%% |" % (
            name, st["cagr"] * 100, st["sharpe"], st["mdd"] * 100, st["turn"] * 100))
    L.append("\n## 解读\n")
    L.append("（由主流程人工补写）")
    (REPORT_DIR / "AURUMQ_ALPHA101_SPOTCHECK_20260815.md").write_text(
        "\n".join(L), encoding="utf-8")
    log("完成 → reports/AURUMQ_ALPHA101_SPOTCHECK_20260815.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
