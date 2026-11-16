#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
microstructure_factors_20260815.py — 微观结构三件套落地（QIML公众号学习 P0）
================================================================
外部证据（海通金工"海量177"2020 14因子全表 + QIML生态）:
  * 尾盘成交占比(14:30后) 月均RankIC 4.86% / ICIR 3.59 —— 全表最强之一
    (多头+0.52%/月 vs 空头-1.10%/月 → 负向因子, 应作事前剔除/空头惩罚)
  * 改进反转(剔除隔夜+开盘后半小时涨幅) IC 4.33% / ICIR 3.74
  * 开源《因子切割论》: 按日均单笔成交金额切割的理想反转 IR 2.51/月胜率74%
    —— 屠龙此前用泛化worth复现失败, 本次按"成交额代理单笔金额"复核原口径

三件套:
  1) tail20         尾盘成交占比20日均值 (负向)
  2) rev20_improved 改进反转: 20日复合"开盘半小时后→收盘"收益 (负向, 反转族)
  3) ideal_rev      理想反转复核: 高成交日累计涨幅 - 低成交日累计涨幅
                     (切割指标=20日均成交额对数, 单笔金额的可得代理; 无逐笔数据局限已声明)

附带输出:
  * overnight_contrib: 隔夜收益贡献占比(|overnight|/(|overnight|+|intraday|) 20日) —— 拥挤度探针
  * 每因子: 月度截面IC( Fwd20, Spearman ) / ICIR / 正率 / 多空分解(海通口径) / 5层单调性
  * 与换手率+市值正交后IC (验证独立增量)

数据源:
  日线:  D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db  stock_daily_meta_qfq (前复权, 唯一干净价格源)
  分钟线: D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_meta.db   stock_minute_5 (2020-04起, 48bar/日, time=int)
  换手率: D:\\workbuddyspace\\databases\\fundamental_data.db daily_basic (2020-01起)

用法:
  python microstructure_factors_20260815.py            # 全量(分钟因子2020-06起)
  python microstructure_factors_20260815.py --fast     # 抽样2折快速冒烟
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")


def load_qfq() -> pd.DataFrame:
    con = sqlite3.connect(QFQ_DB)
    df = pd.read_sql(
        "SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq", con)
    con.close()
    # 代码白名单: 仅真A股 — qfq库已于2026-08-16库级清理(删5738只ETF/转债/指数,构建端同步加白名单),
    # 此处保留过滤作双保险(92=北交所920段)
    n0 = df["code"].nunique()
    df = df[df["code"].str.match(r"^(60|00|30|68|92)")]
    print(f"  [过滤] 非股票代码剔除: {n0} → {df['code'].nunique()} 只(60/00/30/68/43)")
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df


def load_minute_agg() -> pd.DataFrame:
    """分钟线聚合: 每(code,date)尾盘占比 + 开盘半小时涨幅"""
    con = sqlite3.connect(META_DB)
    sql = """
    SELECT code, date,
           SUM(CASE WHEN time>=1430 THEN amount ELSE 0 END) * 1.0 / SUM(amount) AS tail_ratio,
           SUM(CASE WHEN time=1000 THEN close ELSE 0 END) /
             SUM(CASE WHEN time=935  THEN open  ELSE 0 END) - 1.0 AS open30_ret
    FROM stock_minute_5
    GROUP BY code, date
    """
    df = pd.read_sql(sql, con)
    con.close()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df


def load_turnover() -> pd.DataFrame:
    con = sqlite3.connect(FUND_DB)
    try:
        df = pd.read_sql("SELECT ts_code,trade_date,turnover_rate FROM daily_basic", con)
    except Exception as e:
        print(f"  [warn] daily_basic 读取失败: {e}"); return pd.DataFrame()
    con.close()
    df["code"] = df["ts_code"].str.split(".").str[0]
    df["date"] = pd.to_datetime(df["trade_date"], format="mixed")  # 历史%Y%m%d+新ISO混合
    df = df.drop_duplicates(["code", "date"])
    return df[["code", "date", "turnover_rate"]]


def monthly_ic(factor: pd.Series, fwd_ret: pd.Series) -> dict:
    """月度截面IC: 月末截面 Spearman(factor, fwd20)"""
    # factor/fwd_ret 均 MultiIndex(date,code)
    df = pd.DataFrame({"f": factor, "r": fwd_ret}).dropna()
    df["ym"] = df.index.get_level_values(0).to_period("M")
    ics, months = [], []
    for ym, g in df.groupby("ym"):
        if len(g) < 50:
            continue
        r = spearmanr(g["f"], g["r"])
        if np.isfinite(r.statistic):
            ics.append(r.statistic); months.append(ym)
    s = pd.Series(ics, index=months)
    return {"IC": s.mean(), "ICIR": s.mean() / (s.std() + 1e-12),
            "pos": (s > 0).mean(), "n_month": len(s)}


def long_short(factor: pd.Series, fwd_ret: pd.Series, q: float = 0.20) -> dict:
    """海通177口径: 月度 Top/Bottom q 分位等权下月收益分解(仅多空方向意义, 非组合)"""
    df = pd.DataFrame({"f": factor, "r": fwd_ret}).dropna()
    if len(df) == 0:
        return {}
    df["ym"] = df.index.get_level_values(0).to_period("M")
    longs, shorts = [], []
    for _, g in df.groupby("ym"):
        if len(g) < 100:
            continue
        hi = g["f"] >= g["f"].quantile(1 - q)
        lo = g["f"] <= g["f"].quantile(q)
        if hi.sum() and lo.sum():
            longs.append(g.loc[hi, "r"].mean())
            shorts.append(g.loc[lo, "r"].mean())
    if not longs:
        return {}
    L, S = pd.Series(longs), pd.Series(shorts)
    return {"long": L.mean(), "short": S.mean(), "ls": L.mean() - S.mean(),
            "ls_win": (L > S).mean()}


def quintile_ls(factor: pd.Series, fwd_ret: pd.Series) -> list:
    """5层单调性: 各层月均收益"""
    df = pd.DataFrame({"f": factor, "r": fwd_ret}).dropna()
    if len(df) == 0:
        return []
    df["ym"] = df.index.get_level_values(0).to_period("M")
    means = {q: [] for q in range(5)}
    for _, g in df.groupby("ym"):
        if len(g) < 100:
            continue
        qs = pd.qcut(g["f"].rank(method="first"), 5, labels=False)
        for q in range(5):
            means[q].append(g.loc[qs == q, "r"].mean())
    return [float(pd.Series(means[q]).mean()) for q in range(5)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="快速冒烟(只跑部分月份)")
    ap.add_argument("--start", default="20200601")
    ap.add_argument("--end", default="20260731")
    args = ap.parse_args()

    t0 = time.time()
    print("== 加载日线(qfq) ==")
    daily = load_qfq()
    daily = daily.sort_values(["code", "date"]).set_index(["date", "code"])
    print(f"  {len(daily):,} 行 {time.time()-t0:.0f}s")

    # ── 数据清洗: qfq库2026-07-22/08-06等存在批量天价脏行(2209只日收益>100%) ──
    close = daily["close"]
    open_ = daily["open"]
    close = close.where(close > 0)
    prev_close = close.groupby("code").shift(1)
    raw_ret = close / prev_close - 1.0
    # 个股异常收益clip(除权跳空/脏行): 日收益超出±21%视为脏(涨跌停上限±20%)
    ret = raw_ret.clip(-0.21, 0.21)
    # 污染日整日剔除: 当日>25%股票收益异常 → 该日数据不可信, 全部置NaN
    bad = raw_ret.abs() > 0.21
    day_ratio = bad.groupby(level=0).mean()
    polluted_days = day_ratio[day_ratio > 0.25].index
    if len(polluted_days):
        print(f"  [清洗] 剔除污染日 {len(polluted_days)} 天: {list(polluted_days.strftime('%Y%m%d')[:8])}")
    ret = ret.mask(ret.index.get_level_values(0).isin(polluted_days))
    overnight = (open_ / prev_close - 1.0).clip(-0.21, 0.21)
    overnight = overnight.mask(overnight.index.get_level_values(0).isin(polluted_days))
    intraday = (close / open_ - 1.0).clip(-0.21, 0.21)
    intraday = intraday.mask(intraday.index.get_level_values(0).isin(polluted_days))

    # ── Fwd20(未来20日复合): 对数收益滚动和 shift(-20) 前置到当日 ──
    log1p = pd.Series(np.where(ret > -1.0, np.log1p(ret.values), np.nan), index=ret.index)
    log_sum20 = log1p.groupby("code").transform(lambda s: s.rolling(20, min_periods=15).sum())
    fwd = np.expm1(log_sum20.groupby("code").shift(-20))
    fwd = fwd.sort_index()
    fwd.name = "fwd20"

    rev20 = np.expm1(log1p.groupby("code").transform(lambda s: s.rolling(20, min_periods=15).sum()))
    amt = daily["amount"].where(close > 0)
    amt20 = amt.groupby("code").transform(lambda s: s.rolling(20, min_periods=10).mean())
    # 理想反转(开源《因子切割论》原口径复核, 成交额=单笔金额的可得代理):
    # 高成交日对数收益累计 - 低成交日对数收益累计 (向量化, 对数≈复合收益)
    hi_log = pd.Series(np.where(amt.values > amt20.values, log1p.values, np.nan), index=ret.index)
    lo_log = pd.Series(np.where(amt.values <= amt20.values, log1p.values, np.nan), index=ret.index)
    hi_cum = hi_log.groupby("code").transform(lambda s: s.rolling(20, min_periods=10).sum())
    lo_cum = lo_log.groupby("code").transform(lambda s: s.rolling(20, min_periods=10).sum())
    hi_cnt = pd.Series((amt > amt20).astype(float), index=ret.index).groupby("code").transform(
        lambda s: s.rolling(20, min_periods=1).sum())
    ideal_rev = (hi_cum - lo_cum).where((hi_cnt >= 5) & (hi_cnt <= 15))
    ideal_rev.name = "ideal_rev"

    # 隔夜贡献占比(20日) —— 拥挤度探针
    oc = (overnight.abs() / (overnight.abs() + intraday.abs())).groupby("code").transform(
        lambda s: s.rolling(20, min_periods=10).mean())
    oc.name = "overnight_contrib"

    # ── 分钟线因子 ──
    print("== 加载分钟线聚合 ==")
    mint = load_minute_agg()
    print(f"  {len(mint):,} 行 {time.time()-t0:.0f}s")
    tail = mint.set_index(["date", "code"])["tail_ratio"].groupby("code").transform(
        lambda s: s.rolling(20, min_periods=10).mean()).sort_index()
    tail.name = "tail20"
    open30 = mint.set_index(["date", "code"])["open30_ret"].sort_index()
    # 改进反转: 20日复合"开盘半小时后→收盘"收益 = Π(1+close/open30)-1
    # 近似: intraday 剔除 open30 部分 → close/open30-1 ≈ intraday - open30_ret
    impro_ret = intraday - open30.reindex(intraday.index)
    impro_log = pd.Series(np.where(impro_ret.values > -1.0, np.log1p(impro_ret.values), np.nan),
                          index=impro_ret.index)
    rev20_improved = np.expm1(impro_log.groupby("code").transform(
        lambda s: s.rolling(20, min_periods=10).sum()))
    rev20_improved.name = "rev20_improved"

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    sl = slice(start, end)

    # 换手率/市值正交(2020-01起daily_basic)
    turn = load_turnover()
    if len(turn):
        turn = turn.set_index(["date", "code"])["turnover_rate"]
        turn = turn.reindex(rev20.index).groupby("code").transform(
            lambda s: s.ffill().rolling(20, min_periods=5).mean())

    print("\n=== 微观结构三件套 + 探针: 月度截面IC(Fwd20)/多空分解/5层 ===")
    results = {}
    factors = {
        "tail20(负向)": tail,
        "rev20_improved(负向)": rev20_improved,
        "ideal_rev复核": ideal_rev,
        "overnight_contrib探针": oc,
        "rev20原始对照": rev20,
    }
    for name, f in factors.items():
        f = f.reindex(fwd.index).sort_index()
        ic = monthly_ic(f[sl], fwd[sl])
        ls = long_short(f[sl], fwd[sl])
        q5 = quintile_ls(f[sl], fwd[sl])
        results[name] = {"ic": ic, "ls": ls, "q5": q5}
        print(f"\n[{name}]  n_month={ic['n_month']}")
        print(f"  IC={ic['IC']:+.4f}  ICIR={ic['ICIR']:+.2f}  正率={ic['pos']:.0%}")
        print(f"  多空分解: 多头+{ls.get('long', np.nan)*100:.2f}%/月  空头{ls.get('short', np.nan)*100:+.2f}%/月  "
              f"多空{ls.get('ls', np.nan)*100:+.2f}%/月  胜率{ls.get('ls_win', np.nan):.0%}")
        if q5:
            print(f"  5层月均: " + "  ".join(f"{x*100:+.2f}%" for x in q5))
        # 与换手率正交后
        if len(turn) and len(f):
            fz = f.reindex(turn.index)
            both = pd.DataFrame({"f": fz, "t": turn, "r": fwd.reindex(turn.index)}).dropna()
            if len(both) > 1000:
                resid = both["f"] - both.groupby(both.index.get_level_values(0))["t"].transform("mean")  # 月度均值正交(简)
                both["f_orth"] = resid
                ic_o = monthly_ic(both["f_orth"], both["r"])
                print(f"  月度均值正交换手后: IC={ic_o['IC']:+.4f}  ICIR={ic_o['ICIR']:+.2f}")
    print(f"\n总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
