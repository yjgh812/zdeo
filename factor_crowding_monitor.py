#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
因子拥挤度监控 (屠龙系统 P2)
================================================================
方法论: 国盛证券《因子拥挤度研究全攻略》(2019) 三比率法 + 海通证券配对相关
        性指标的简化实现。对给定因子的月度多空持仓(因子值前20%做多/后20%做空):

  a) 换手率比率 = 多头组日均换手率 / 空头组日均换手率
     (数据: fundamental_data.db daily_basic.turnover_rate)
     —— 多头显著更"活跃"是拥挤的典型信号;
  b) 波动率比率 = 多头组等权日收益波动 / 空头组等权日收益波动
     (数据: 前复权主库 dragon_quant_qfq.change_pct)
     —— 多头组合波动放大往往伴随拥挤;
  c) 多头组内个股日收益两两相关均值(取样<=30只防组合爆炸, 海通"因子内
     相关性"的简化) —— 同向交易集中时组内相关性升高。

  三指标各自取滚动3年(36个月)分位, 任一 > 90% 分位 = 拥挤预警。

  P0-3 斜率维度 (2026-08-15): 三个指标各自额外计算"斜率"
      slope = 当前月滚动分位 - 6个月前滚动分位 (分位数列的一阶差分);
      当 slope > +0.15 (分位点) 或 斜率序列自身滚动36月分位 > 90% 时
      输出 SLOPE_WARN (拥挤正在快速形成)。原"水平>90%分位"预警逻辑不变。
      方法论依据: QIML 2026风险模型文 —— "拥挤正在快速形成的斜率比拥挤
      水平本身更值得盯" (水平反映存量拥挤, 斜率反映增量拥挤的形成速度)。

数据源:
  --db D:\\zcodesj\\factor_pv.db    表 factor_price_volume (date,code,factor,value)
                                    月度截面, 6个量价因子;
  --db D:\\zcodesj\\factor_gpoa.db  表 factor_monthly (code,date,gpoa,droe,composite)
                                    月度宽表;
  日收益: D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db
          stock_daily_meta_qfq.change_pct (前复权百分数);
  换手率: D:\\workbuddyspace\\databases\\fundamental_data.db daily_basic
          (ts_code 形如 000001.SZ, trade_date yyyymmdd 文本)。

注意:
  * factor_pv.db 可能被其他进程写入, 本工具只读打开 (mode=ro);
  * qfq 收益透视首次构建约 30-60 秒, 之后走本地缓存 _cache/qfq_chg_pivot_2019.pkl;
  * daily_basic 自 20200115 起, 之前月份换手率比率无值(不影响其余两指标)。

用法:
  python factor_crowding_monitor.py --db D:\\zcodesj\\factor_pv.db --factors illiq20,overnight20
  python factor_crowding_monitor.py --db D:\\zcodesj\\factor_gpoa.db --factors droe
  python factor_crowding_monitor.py --db ... --factors ... --csv out.csv
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
CACHE_PIVOT = Path(r"D:\zcodesj\_cache\qfq_chg_pivot_2019.pkl")

ROLL_MONTHS = 36      # 滚动3年
WARN_Q = 0.90         # 预警分位
GROUP_Q = 0.20        # 多空各取20%
SLOPE_MONTHS = 6      # 斜率回看月数(当前月分位 - N月前分位)
SLOPE_THRESH = 0.15   # 斜率预警阈值(分位点)
CORR_WIN = 60         # 相关性回看窗口(交易日)
CORR_SAMPLE = 30      # 组内相关性取样上限
MIN_SIDE = 50         # 单边最少持仓数


# ----------------------------------------------------------------------------
# 数据加载
# ----------------------------------------------------------------------------
def load_return_pivot() -> pd.DataFrame:
    """前复权日涨跌幅透视表 index=code, columns=date(int)。
    缓存缺失或落后(qfq 主库最新日之后)时从 qfq 主库重建。2026-08-27 加新鲜度自检:
    此前仅"存在即读"致缓存停在 8/14, 配对检查/情绪合成等 5 个消费者长期用旧数据。"""
    def _build() -> pd.DataFrame:
        print("[cache] 重建收益透视缓存(从 qfq 主库, 一次约30-60s)...")
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT code,date,change_pct FROM stock_daily_meta_qfq WHERE date>='20190101'")
        codes, dates, rets = [], [], []
        while True:
            rows = cur.fetchmany(500_000)
            if not rows:
                break
            for c, d, r in rows:
                if r is None:
                    continue
                codes.append(c)
                dates.append(int(d))
                rets.append(float(r))
        con.close()
        df = pd.DataFrame({"code": codes, "date": dates, "ret": rets})
        piv = df.pivot_table(index="code", columns="date", values="ret",
                             aggfunc="first").astype(np.float32).sort_index(axis=1)
        CACHE_PIVOT.parent.mkdir(parents=True, exist_ok=True)
        piv.to_pickle(CACHE_PIVOT)
        return piv

    if CACHE_PIVOT.exists():
        piv = pd.read_pickle(CACHE_PIVOT)
        try:
            con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
            qfq_max = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
            con.close()
            if int(piv.columns.max()) >= int(qfq_max):
                return piv
            print(f"[cache] 缓存落后(末列 {piv.columns.max()} < qfq {qfq_max}), 重建...")
        except Exception:
            return piv
    return _build()


def load_factor_cross(db: str, factors: list[str]) -> dict[str, pd.DataFrame]:
    """返回 {factor: DataFrame(date,code,value)}; 自动识别长表/宽表。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    out: dict[str, pd.DataFrame] = {}
    if "factor_price_volume" in tables:
        q = ("SELECT date,code,factor,value FROM factor_price_volume "
             "WHERE factor IN (%s)" % ",".join("?" * len(factors)))
        df = pd.read_sql_query(q, con, params=factors)
        con.close()
        for f in factors:
            sub = df[df["factor"] == f][["date", "code", "value"]].dropna()
            out[f] = sub
    elif "factor_monthly" in tables:
        cols_wanted = set(factors)
        have = {r[1] for r in con.execute("PRAGMA table_info(factor_monthly)").fetchall()}
        use = cols_wanted & have
        missing = cols_wanted - have
        if missing:
            print(f"[warn] factor_monthly 无列: {missing}")
        if use:
            df = pd.read_sql_query(
                "SELECT code,date,%s FROM factor_monthly" % ",".join(sorted(use)), con)
            for f in sorted(use):
                sub = df[["date", "code", f]].rename(columns={f: "value"}).dropna()
                sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y%m%d").astype(int)
                out[f] = sub
        con.close()
    else:
        con.close()
        raise RuntimeError(f"{db} 中未找到 factor_price_volume / factor_monthly 表")
    return out


class TurnoverCache:
    """按月缓存 daily_basic 换手率, 避免多因子重复查询。"""

    def __init__(self):
        self.con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
        self.cache: dict[tuple[int, int], pd.DataFrame] = {}

    def month(self, d0: int, d1: int) -> pd.DataFrame:
        key = (d0, d1)
        if key not in self.cache:
            df = pd.read_sql_query(
                "SELECT ts_code, trade_date, turnover_rate FROM daily_basic "
                "WHERE trade_date>=? AND trade_date<=? AND turnover_rate IS NOT NULL",
                self.con, params=(str(d0), str(d1)))
            df["code"] = df["ts_code"].str[:6]
            self.cache[key] = df[["code", "trade_date", "turnover_rate"]]
        return self.cache[key]

    def close(self):
        self.con.close()


def code_to_ts(code: str) -> str:
    if code.startswith(("60", "68", "90")):
        return code + ".SH"
    if code.startswith(("00", "30")):
        return code + ".SZ"
    return code + ".BJ"


# ----------------------------------------------------------------------------
# 三指标
# ----------------------------------------------------------------------------
def long_short_codes(cross: pd.DataFrame, piv_index: pd.Index) -> tuple[list[str], list[str]]:
    c = cross[cross["code"].isin(piv_index)].sort_values("value")
    n = len(c)
    if n < MIN_SIDE * 5:
        return [], []
    k = max(MIN_SIDE, int(n * GROUP_Q))
    return c["code"].iloc[-k:].tolist(), c["code"].iloc[:k].tolist()


def turnover_ratio(tc: TurnoverCache, d0: int, d1: int,
                   long_c: list[str], short_c: list[str]) -> tuple[float, int]:
    """返回 (比率, 当月daily_basic可用交易日数)。daily_basic 为稀疏快照,
    多数月份仅约1-20个交易日有换手率数据, 比率是当月可得样本的均值之比。"""
    df = tc.month(d0, d1)
    if df.empty:
        return np.nan, 0
    lm = df[df["code"].isin(long_c)]["turnover_rate"].mean()
    sm = df[df["code"].isin(short_c)]["turnover_rate"].mean()
    nd = df["trade_date"].nunique()
    return (lm / sm if sm and sm > 0 else np.nan), nd


def vol_ratio(piv: pd.DataFrame, cols: list[int],
              long_c: list[str], short_c: list[str]) -> float:
    if len(cols) < 10:
        return np.nan
    lr = piv.loc[long_c, cols].mean(axis=0, skipna=True)
    sr = piv.loc[short_c, cols].mean(axis=0, skipna=True)
    lv, sv = lr.std(ddof=1), sr.std(ddof=1)
    return lv / sv if sv and sv > 0 else np.nan


def pairwise_corr(piv: pd.DataFrame, d1: int,
                  long_c: list[str]) -> float:
    """多头组内日收益两两相关均值; 回看 CORR_WIN 个交易日(截至持仓期末 d1,
    用全局交易日历定位, 不受持仓月天数影响); 取样<=CORR_SAMPLE 防组合爆炸。"""
    pos = int(np.searchsorted(piv.columns.values, d1, side="right"))
    window = piv.columns.values[max(0, pos - CORR_WIN):pos]
    if len(window) < 20:
        return np.nan
    if len(long_c) > CORR_SAMPLE:
        step = len(long_c) / CORR_SAMPLE
        idx = sorted({long_c[int(i * step)] for i in range(CORR_SAMPLE)})
    else:
        idx = long_c
    r = piv.loc[idx, window]
    r = r.dropna(thresh=int(len(window) * 0.8))       # 样本太少的剔除
    if r.shape[0] < 10:
        return np.nan
    corr = r.T.corr()
    mask = np.triu(np.ones(corr.shape, dtype=bool), k=1)
    vals = corr.to_numpy()[mask]
    vals = vals[~np.isnan(vals)]
    return float(vals.mean()) if len(vals) else np.nan


def rolling_pct(series: pd.Series) -> pd.Series:
    """滚动 ROLL_MONTHS 期分位: 当期值在历史窗口中的位置(0~1)。
    min_periods=80% 窗口, 容忍换手率稀疏月导致的缺失。"""
    def _pct(x: np.ndarray) -> float:
        return float((x[-1] >= x).mean())
    return series.rolling(ROLL_MONTHS, min_periods=int(ROLL_MONTHS * 0.8)).apply(
        _pct, raw=True)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def analyze_factor(name: str, cross: pd.DataFrame, piv: pd.DataFrame,
                   tc: TurnoverCache) -> pd.DataFrame:
    snaps = np.sort(cross["date"].unique())
    rows = []
    for d0, d1 in zip(snaps[:-1], snaps[1:]):
        sub = cross[cross["date"] == d0]
        long_c, short_c = long_short_codes(sub, piv.index)
        if not long_c:
            continue
        cols = [c for c in piv.columns if d0 < c <= d1]
        if len(cols) < 10:
            continue
        tr, turn_days = turnover_ratio(tc, d0, d1, long_c, short_c)
        rows.append({
            # 标签=持仓期首个交易日的月份(月末日快照的下月/月初快照的当月)
            "month": pd.Timestamp(str(cols[0])).strftime("%Y-%m"),
            "date_snap": int(d0),
            "n_long": len(long_c),
            "turn_days": turn_days,
            "turnover_ratio": tr,
            "vol_ratio": vol_ratio(piv, cols, long_c, short_c),
            "pairwise_corr": pairwise_corr(piv, d1, long_c),
        })
    df = pd.DataFrame(rows).set_index("month")
    for col in ("turnover_ratio", "vol_ratio", "pairwise_corr"):
        df[f"pct_{col}"] = rolling_pct(df[col])
        # P0-3 斜率维度: 分位数列的一阶变化(当前月分位 - SLOPE_MONTHS 个月前分位)
        df[f"slope_{col}"] = df[f"pct_{col}"] - df[f"pct_{col}"].shift(SLOPE_MONTHS)
        # 斜率序列自身的滚动分位(斜率快速抬升本身也是信号)
        df[f"pct_slope_{col}"] = rolling_pct(df[f"slope_{col}"])
    return df


def print_card(name: str, df: pd.DataFrame, db: str) -> None:
    print("=" * 86)
    print(f"因子拥挤度结果卡: {name}  (db={Path(db).name}, 持仓月截至 {df.index[-1]})")
    print("=" * 86)
    if len(df) < ROLL_MONTHS:
        print(f"[warn] 仅 {len(df)} 个月历史, 滚动{ROLL_MONTHS}月分位不可用, 仅展示原始值")
    last = df.iloc[-1]
    flags = []
    slope_flags = []
    for col, label, interp in (
        ("turnover_ratio", "多空换手率比率", "多头交易活跃度/空头, >1 且高分位=拥挤"),
        ("vol_ratio", "多空波动率比率", "多头相对波动, 高分位=拥挤"),
        ("pairwise_corr", "多头组内两两相关", "组内同涨同跌, 绝对水平高或高分位=拥挤"),
    ):
        v, p = last[col], last.get(f"pct_{col}", np.nan)
        warn = (not np.isnan(p)) and p > WARN_Q
        flags.append(warn)
        sv = last.get(f"slope_{col}", np.nan)
        sp = last.get(f"pct_slope_{col}", np.nan)
        slope_warn = (not np.isnan(sv)) and (
            sv > SLOPE_THRESH or ((not np.isnan(sp)) and sp > WARN_Q))
        slope_flags.append(slope_warn)
        pv = f"{p:.0%}" if not np.isnan(p) else "n/a(<36月)"
        print(f"  {label:<12}: {v:6.3f}   滚动{ROLL_MONTHS}月分位: {pv:>6}   "
              f"{'!! 拥挤预警(>90%分位)' if warn else ''}")
        sv_s = f"{sv:+.3f}" if not np.isnan(sv) else "n/a"
        sp_s = f"{sp:.0%}" if not np.isnan(sp) else "n/a"
        print(f"               斜率(近{SLOPE_MONTHS}月分位变化): {sv_s:>6}  "
              f"斜率自身滚动分位: {sp_s:>6}  "
              f"{'!! SLOPE_WARN(拥挤快速形成: 斜率>%.2f 或 斜率>90%%分位)' % SLOPE_THRESH if slope_warn else ''}")
        print(f"               ({interp})")
    concl = ("【拥挤预警】三指标中 %d 项处于滚动3年90%%分位以上, 建议降仓/放缓加仓该因子敞口"
             % sum(flags)) if any(flags) else \
            ("【未触发预警】三指标均未超过滚动3年90%分位 (注意: 分位不可用时以水平值辅助判断)")
    print(f"  结论: {concl}")
    if any(slope_flags):
        print(f"  结论2: 【SLOPE_WARN】三指标中 %d 项分位斜率快速抬升(近%d月分位变化>%.2f 或"
              f"斜率处90%%分位以上) —— 拥挤正在快速形成, 比拥挤水平本身更值得盯 (QIML 2026)"
              % (sum(slope_flags), SLOPE_MONTHS, SLOPE_THRESH))
    if "turn_days" in df.columns:
        print(f"  数据说明: 换手率来自 daily_basic 稀疏快照(持仓月均 {df['turn_days'].mean():.0f} 个"
              f"交易日有数据, 0=整月缺失); 波动率/相关性基于前复权全量日线。")
    tail = df.tail(6)
    cols = [c for c in ("turnover_ratio", "vol_ratio", "pairwise_corr",
                        "pct_turnover_ratio", "pct_vol_ratio", "pct_pairwise_corr",
                        "slope_turnover_ratio", "slope_vol_ratio", "slope_pairwise_corr")
            if c in tail.columns]
    print("\n  近6个月明细 (含分位与斜率):")
    with pd.option_context("display.width", 120, "display.float_format",
                           lambda v: f"{v:.3f}"):
        print(tail[cols].to_string())
    print()


def main(argv=None) -> int:
    global ROLL_MONTHS, WARN_Q, GROUP_Q, SLOPE_MONTHS, SLOPE_THRESH
    ap = argparse.ArgumentParser(description="因子拥挤度监控(三比率+组内相关+斜率维度)")
    ap.add_argument("--db", required=True, help="factor_pv.db 或 factor_gpoa.db")
    ap.add_argument("--factors", required=True, help="因子名, 逗号分隔")
    ap.add_argument("--quantile", type=float, default=GROUP_Q, help="多空分组分位(默认0.2)")
    ap.add_argument("--warn-q", type=float, default=WARN_Q, help="预警分位(默认0.90)")
    ap.add_argument("--roll", type=int, default=ROLL_MONTHS, help="滚动月数(默认36)")
    ap.add_argument("--slope-months", type=int, default=SLOPE_MONTHS,
                    help="斜率回看月数: 当前月分位-N月前分位 (默认6, 不加参数即原行为+斜率列)")
    ap.add_argument("--slope-thresh", type=float, default=SLOPE_THRESH,
                    help="斜率预警阈值, 分位点 (默认0.15)")
    ap.add_argument("--csv", default="", help="保存全部月度指标 CSV")
    args = ap.parse_args(argv)

    ROLL_MONTHS, WARN_Q, GROUP_Q = args.roll, args.warn_q, args.quantile
    SLOPE_MONTHS, SLOPE_THRESH = args.slope_months, args.slope_thresh

    t0 = time.time()
    factors = [f.strip() for f in args.factors.split(",") if f.strip()]
    piv = load_return_pivot()
    print(f"[load] 收益透视 {piv.shape[0]} 股 x {piv.shape[1]} 日")
    cross_map = load_factor_cross(args.db, factors)
    if not cross_map:
        print("无可用因子数据", file=sys.stderr)
        return 2
    tc = TurnoverCache()
    all_out = []
    for name in factors:
        if name not in cross_map or cross_map[name].empty:
            print(f"[skip] {name}: 无数据")
            continue
        df = analyze_factor(name, cross_map[name], piv, tc)
        if df.empty:
            print(f"[skip] {name}: 有效持仓月不足")
            continue
        print_card(name, df, args.db)
        df.insert(0, "factor", name)
        all_out.append(df.reset_index())
    tc.close()
    if args.csv and all_out:
        out = pd.concat(all_out, ignore_index=True)
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"[save] {args.csv}")
    print(f"[done] 耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
