#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alphalens_factor_diagnosis.py — 因子全维度诊断管道（Alphalens 驱动）

对任意因子做 Alphalens 标准诊断：
  - IC 序列（日度/月度）
  - 分位收益（long-short spread + 单调性）
  - IC 衰减
  - 分组收益
  - 换手率分析
  - 综合判定

内置因子（屠龙系统）:
  bp  = 1/pb
  ep  = 1/pe_ttm
  bp_ep = (bp + ep) / 2
  另支持 9 Anomaly因子 / 自定义

用法:
    python alphalens_factor_diagnosis.py bp          # 诊断 bp 因子
    python alphalens_factor_diagnosis.py ep          # 诊断 ep 因子
    python alphalens_factor_diagnosis.py bp_ep       # 诊断 bp+ep 等权
    python alphalens_factor_diagnosis.py all         # 全部内置因子
    python alphalens_factor_diagnosis.py all --anomaly  # 全部 + 9 Anomaly
    python alphalens_factor_diagnosis.py --save-plots  # 保存图表到 reports/
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import alphalens as al

warnings.filterwarnings("ignore", category=FutureWarning)

# ── 数据库路径 ──
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"

REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)

# ── 日期范围 ──
START_DATE = "20250801"
END_DATE = "20260731"


# ═══════════════════════════════════════════════════════
#  数据加载
# ═══════════════════════════════════════════════════════

def load_prices(freq: str = "daily") -> pd.DataFrame:
    """加载价格数据，返回 date × code 的 pivot table（Alphalens 格式）"""
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=30)
    try:
        df = pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta "
            "WHERE date BETWEEN ? AND ? AND close > 0 "
            "ORDER BY date, code",
            conn, params=[int(START_DATE), int(END_DATE)],
        )
    finally:
        conn.close()

    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    # pivot: index=date, columns=code, values=close
    prices = df.pivot_table(index="date", columns="code", values="close", aggfunc="last")
    prices = prices.sort_index()
    # 重置频率：A股日期间隔不规律（周末/假期），Alphalens 需要无 freq 的索引
    prices.index = pd.DatetimeIndex(prices.index)
    return prices


def load_factor_bp(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    """加载 bp = 1/pb 因子"""
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, pb FROM daily_basic "
            "WHERE trade_date BETWEEN ? AND ? AND pb > 0",
            conn, params=[START_DATE, END_DATE],
        )
    finally:
        conn.close()

    df["code"] = df["ts_code"].apply(lambda x: str(x).split(".")[0])
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df[df["date"].isin(date_slice)]
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    df["pb"] = pd.to_numeric(df["pb"], errors="coerce")
    df = df.dropna(subset=["pb"])
    df["factor"] = 1.0 / df["pb"]  # bp = 1/pb
    # MultiIndex: (date, code)
    factor_df = df.set_index(["date", "code"])["factor"].sort_index()
    factor_df = factor_df[factor_df.index.get_level_values("date").isin(date_slice)]
    return factor_df


def load_factor_ep(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    """加载 ep = 1/pe_ttm 因子"""
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, pe_ttm FROM daily_basic "
            "WHERE trade_date BETWEEN ? AND ? AND pe_ttm > 0",
            conn, params=[START_DATE, END_DATE],
        )
    finally:
        conn.close()

    df["code"] = df["ts_code"].apply(lambda x: str(x).split(".")[0])
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df[df["date"].isin(date_slice)]
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    df["pe_ttm"] = pd.to_numeric(df["pe_ttm"], errors="coerce")
    df = df.dropna(subset=["pe_ttm"])
    df["factor"] = 1.0 / df["pe_ttm"]  # ep = 1/pe_ttm
    factor_df = df.set_index(["date", "code"])["factor"].sort_index()
    factor_df = factor_df[factor_df.index.get_level_values("date").isin(date_slice)]
    return factor_df


def load_factor_bp_ep(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    """加载 bp+ep 等权因子"""
    bp = load_factor_bp(date_slice)
    ep = load_factor_ep(date_slice)
    # 合并（取交集）
    combined = pd.concat({"bp": bp, "ep": ep}, axis=1).dropna()
    factor = (combined["bp"] + combined["ep"]) / 2.0
    factor.name = "factor"
    return factor


def load_factor_rev20_improved(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    """改进反转因子: 20日复合"开盘半小时后→收盘"收益（负向）。

    剔除隔夜收益与开盘后半小时涨幅的反转信号（海通'海量177'口径）。
    2026-08-16 入库: IC-0.073/74个月/5层严格单调, 定位=rev20 替代品（更纯日内反转）。
    数据源: stock_minute_5(2020-01起中证800域) + qfq 日线 intraday。
    带本地缓存(_cache/rev20_improved_panel.pkl), 分钟库有新数据自动重建。
    """
    import numpy as np
    from pathlib import Path
    cache = Path(r"D:\zcodesj\_cache\rev20_improved_panel.pkl")

    def _build() -> pd.DataFrame:
        mcon = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=120)
        mint = pd.read_sql(
            "SELECT code, date, "
            " SUM(CASE WHEN time=1000 THEN close ELSE 0 END) * 1.0 / "
            " SUM(CASE WHEN time=935  THEN open  ELSE 0 END) - 1.0 AS open30_ret "
            "FROM stock_minute_5 GROUP BY code, date", mcon)
        mcon.close()
        mint["date"] = pd.to_datetime(mint["date"].astype(str), format="%Y%m%d")
        open30 = mint.set_index(["date", "code"])["open30_ret"].sort_index()
        # qfq 日线 intraday(清洗口径与 microstructure_factors_20260815 一致)
        qcon = sqlite3.connect(
            f"file:{QFQ_DB if 'QFQ_DB' in globals() else r'D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db'}?mode=ro",
            uri=True, timeout=120)
        daily = pd.read_sql(
            "SELECT code,date,open,close FROM stock_daily_meta_qfq", qcon)
        qcon.close()
        daily["date"] = pd.to_datetime(daily["date"].astype(str), format="%Y%m%d")
        daily = daily[daily["code"].str.match(r"^(60|00|30|68|92)")]
        d = daily.sort_values(["code", "date"]).set_index(["date", "code"])
        intraday = (d["close"] / d["open"] - 1.0).clip(-0.21, 0.21)
        impro = intraday - open30.reindex(intraday.index)
        log1p = pd.Series(np.where(impro.values > -1.0, np.log1p(impro.values), np.nan),
                          index=impro.index)
        panel = np.expm1(log1p.groupby("code").transform(
            lambda s: s.rolling(20, min_periods=10).sum()))
        panel = panel.rename("factor").dropna()
        panel.to_pickle(cache)
        return panel

    if cache.exists():
        panel = pd.read_pickle(cache)
        mcon = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=30)
        mx = mcon.execute("SELECT MAX(date) FROM stock_minute_5").fetchone()[0]
        mcon.close()
        cached_max = panel.index.get_level_values(0).max().strftime("%Y%m%d")
        if mx and cached_max >= mx:
            return panel[panel.index.get_level_values("date").isin(date_slice)]
        panel = _build()
    else:
        panel = _build()
    return panel[panel.index.get_level_values("date").isin(date_slice)]


def load_factor_turnover(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    """加载换手率因子（负值，低换手率=高分）"""
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, turnover_rate FROM daily_basic "
            "WHERE trade_date BETWEEN ? AND ? AND turnover_rate > 0",
            conn, params=[START_DATE, END_DATE],
        )
    finally:
        conn.close()

    df["code"] = df["ts_code"].apply(lambda x: str(x).split(".")[0])
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df[df["date"].isin(date_slice)]
    df["turnover_rate"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
    df = df.dropna(subset=["turnover_rate"])
    # 去重：防止同一标的同一天有重复记录导致 Alphalens pivot 失败
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    df["factor"] = -df["turnover_rate"]  # 低换手 = 高因子值
    factor_df = df.set_index(["date", "code"])["factor"].sort_index()
    factor_df = factor_df[factor_df.index.get_level_values("date").isin(date_slice)]
    return factor_df


def load_factor_volume_ratio(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    """加载量比因子"""
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, volume_ratio FROM daily_basic "
            "WHERE trade_date BETWEEN ? AND ? AND volume_ratio > 0",
            conn, params=[START_DATE, END_DATE],
        )
    finally:
        conn.close()

    df["code"] = df["ts_code"].apply(lambda x: str(x).split(".")[0])
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df[df["date"].isin(date_slice)]
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    df["volume_ratio"] = pd.to_numeric(df["volume_ratio"], errors="coerce")
    df = df.dropna(subset=["volume_ratio"])
    factor_df = df.set_index(["date", "code"])["volume_ratio"].sort_index()
    factor_df = factor_df[factor_df.index.get_level_values("date").isin(date_slice)]
    return factor_df


def load_factor_dv_ratio(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    """加载股息率因子"""
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, dv_ratio FROM daily_basic "
            "WHERE trade_date BETWEEN ? AND ? AND dv_ratio > 0",
            conn, params=[START_DATE, END_DATE],
        )
    finally:
        conn.close()

    df["code"] = df["ts_code"].apply(lambda x: str(x).split(".")[0])
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df[df["date"].isin(date_slice)]
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    df["dv_ratio"] = pd.to_numeric(df["dv_ratio"], errors="coerce")
    df = df.dropna(subset=["dv_ratio"])
    factor_df = df.set_index(["date", "code"])["dv_ratio"].sort_index()
    factor_df = factor_df[factor_df.index.get_level_values("date").isin(date_slice)]
    return factor_df


# ═══════════════════════════════════════════════════════
#  9 Anomaly 因子加载（from dragon_quant.db.finance_daily）
# ═══════════════════════════════════════════════════════

_FINANCE_CACHE: pd.DataFrame | None = None

def _load_finance_snapshots() -> pd.DataFrame:
    """加载全量财务快照数据（单次加载，所有因子共享）"""
    global _FINANCE_CACHE
    if _FINANCE_CACHE is not None:
        return _FINANCE_CACHE.copy()
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query("""
            SELECT code, report_date, total_assets, total_equity, revenue,
                   operating_profit, profit_before_tax, net_profit, operating_cashflow
            FROM finance_daily
            WHERE total_assets > 0 AND total_equity > 0
              AND report_date BETWEEN ? AND ?
            ORDER BY code, report_date
        """, conn, params=[int(START_DATE[:4] + '0101'), int(END_DATE)])
    finally:
        conn.close()

    df['code'] = df['code'].astype(str).str[:6]
    df['report_date'] = pd.to_datetime(df['report_date'].astype(str), format='%Y%m%d')
    _FINANCE_CACHE = df
    return df.copy()


def _forward_fill_factor(df_factor: pd.DataFrame, date_slice: pd.DatetimeIndex) -> pd.Series:
    """将(code, report_date)的因子值前向填充到所有交易日"""
    # Pivot: codes × report_dates
    pivot = df_factor.pivot_table(
        index='code', columns='report_date', values='factor', aggfunc='first'
    )
    # 前向填充（每个报告期的因子值一直用到下个报告期）
    pivot = pivot.ffill(axis=1)
    # 重索引到所有交易日（用最近的报告期数据）
    pivot = pivot.reindex(columns=date_slice, method='ffill')
    # 转回 Series
    result = pivot.stack()
    result.index.names = ['code', 'date']
    result = result.reorder_levels(['date', 'code']).sort_index()
    result.name = 'factor'
    return result


def load_factor_ag(date_slice: pd.DatetimeIndex) -> pd.Series:
    """Asset Growth: (total_assets / year_ago_total_assets) - 1"""
    df = _load_finance_snapshots()
    # 对每只股票，计算同比总资产增长率
    df = df.sort_values(['code', 'report_date'])
    df['total_assets_lag4'] = df.groupby('code')['total_assets'].shift(4)  # 4 quarters ago
    df = df.dropna(subset=['total_assets', 'total_assets_lag4'])
    df['factor'] = df['total_assets'] / df['total_assets_lag4'] - 1
    return _forward_fill_factor(df, date_slice)


def load_factor_droe(date_slice: pd.DatetimeIndex) -> pd.Series:
    """ΔROE: (net_profit/total_equity) - (year_ago_net_profit/year_ago_total_equity)"""
    df = _load_finance_snapshots()
    df = df.sort_values(['code', 'report_date'])
    df['roe'] = df['net_profit'] / df['total_equity']
    df['roe_lag4'] = df.groupby('code')['roe'].shift(4)
    df = df.dropna(subset=['roe', 'roe_lag4'])
    df['factor'] = df['roe'] - df['roe_lag4']
    return _forward_fill_factor(df, date_slice)


def load_factor_acc(date_slice: pd.DatetimeIndex) -> pd.Series:
    """Accruals: (net_profit - operating_cashflow) / total_assets"""
    df = _load_finance_snapshots()
    df['factor'] = (df['net_profit'] - df['operating_cashflow']) / df['total_assets']
    df = df.dropna(subset=['factor'])
    return _forward_fill_factor(df, date_slice)


def load_factor_sg(date_slice: pd.DatetimeIndex) -> pd.Series:
    """Sales Growth: (revenue / year_ago_revenue) - 1"""
    df = _load_finance_snapshots()
    df = df.sort_values(['code', 'report_date'])
    df['revenue_lag4'] = df.groupby('code')['revenue'].shift(4)
    df = df.dropna(subset=['revenue', 'revenue_lag4'])
    df['factor'] = df['revenue'] / df['revenue_lag4'] - 1
    return _forward_fill_factor(df, date_slice)


def load_factor_roa(date_slice: pd.DatetimeIndex) -> pd.Series:
    """ROA: net_profit / total_assets"""
    df = _load_finance_snapshots()
    df['factor'] = df['net_profit'] / df['total_assets']
    df = df.dropna(subset=['factor'])
    return _forward_fill_factor(df, date_slice)


def load_factor_pm(date_slice: pd.DatetimeIndex) -> pd.Series:
    """Profit Margin: operating_profit / revenue"""
    df = _load_finance_snapshots()
    df['factor'] = df['operating_profit'] / df['revenue'].replace(0, float('nan'))
    df = df.dropna(subset=['factor'])
    return _forward_fill_factor(df, date_slice)


def load_factor_ato(date_slice: pd.DatetimeIndex) -> pd.Series:
    """Asset Turnover: revenue / total_assets"""
    df = _load_finance_snapshots()
    df['factor'] = df['revenue'] / df['total_assets']
    df = df.dropna(subset=['factor'])
    return _forward_fill_factor(df, date_slice)


def load_factor_opa(date_slice: pd.DatetimeIndex) -> pd.Series:
    """OP/Assets: operating_profit / total_assets"""
    df = _load_finance_snapshots()
    df['factor'] = df['operating_profit'] / df['total_assets']
    df = df.dropna(subset=['factor'])
    return _forward_fill_factor(df, date_slice)


def load_factor_tbi(date_slice: pd.DatetimeIndex) -> pd.Series:
    """Tax-to-Book Income: profit_before_tax / net_profit"""
    df = _load_finance_snapshots()
    df['factor'] = df['profit_before_tax'] / df['net_profit'].replace(0, float('nan'))
    df = df.dropna(subset=['factor'])
    return _forward_fill_factor(df, date_slice)


def load_factor_bl(date_slice: pd.DatetimeIndex) -> pd.Series:
    """Book Leverage: total_assets / total_equity"""
    df = _load_finance_snapshots()
    df['factor'] = df['total_assets'] / df['total_equity']
    df = df.dropna(subset=['factor'])
    return _forward_fill_factor(df, date_slice)


# ═══════════════════════════════════════════════════════
#  Alphalens 诊断核心
# ═══════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════
#  频率修复（A 股非连续交易日兼容）
# ═══════════════════════════════════════════════════════

# ── 全局状态保存：绕过 Alphalens 频率验证（A 股非连续交易日兼容）──
_ORIGINAL_FREQ_VALIDATE = None


def _enter_alphalens_compat_mode():
    """绕过 pandas DatetimeIndex 频率验证（Alphalens 对 A 股非连续交易日不兼容）"""
    global _ORIGINAL_FREQ_VALIDATE
    if _ORIGINAL_FREQ_VALIDATE is None:
        from pandas.core.arrays import DatetimeArray
        cls = DatetimeArray
        _ORIGINAL_FREQ_VALIDATE = cls._validate_frequency
        
        def _patched(*args, **kwargs):
            pass  # 跳过频率验证
        cls._validate_frequency = _patched


def _exit_alphalens_compat_mode():
    """恢复原始频率验证"""
    global _ORIGINAL_FREQ_VALIDATE
    if _ORIGINAL_FREQ_VALIDATE is not None:
        from pandas.core.arrays import DatetimeArray
        cls = DatetimeArray
        cls._validate_frequency = _ORIGINAL_FREQ_VALIDATE
        _ORIGINAL_FREQ_VALIDATE = None


# ── AG因子库三强 loader (2026-08-16 二关筛选通过, 研究观察级) ──
def _load_ag_factor(factor_id: str, lib: str):
    """aurumq-rl因子 → stacked Series(带月度截面缓存)。IC口径=IC>0.04/ICIR>0.44/单调p<0.05/Sharpe>0.76。"""
    import sys as _sys, types as _types
    cache = Path(r"D:\zcodesj\_cache") / f"ag_fac_{factor_id}.pkl"
    if cache.exists():
        import pickle as _p
        return _p.load(open(cache, "rb"))
    _pkg = _types.ModuleType("aurumq_rl")
    _pkg.__path__ = ["D:/zcodesj/strategy_libs/aurumq-rl/src/aurumq_rl"]
    _sys.modules.setdefault("aurumq_rl", _pkg)
    _sys.path.insert(0, "D:/zcodesj/strategy_libs/aurumq-rl/src")
    if lib == "alpha101":
        import aurumq_rl.factors.alpha101 as _a
        entry = _a  # 触发注册
        from aurumq_rl.factors.registry import ALPHA101_REGISTRY as _R
    else:
        import aurumq_rl.factors.gtja191 as _g
        entry = _g
        from aurumq_rl.factors.registry import GTJA191_REGISTRY as _R
    import polars as _pl
    panel = _pl.read_parquet("D:/zcodesj/_cache/ag_panel_v2.parquet")
    s = _R[factor_id].impl(panel)
    df = panel.select(["stock_code", "trade_date"]).with_columns(
        s.alias("v")).to_pandas().dropna(subset=["v"])
    dt_col = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    ser = df.set_index([dt_col, "stock_code"])["v"]
    import pickle as _p
    _p.dump(ser, open(cache, "wb"))
    return ser


def load_factor_gtja_054(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    return _load_ag_factor("gtja_054", "gtja191")


def load_factor_gtja_154(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    return _load_ag_factor("gtja_154", "gtja191")


def load_factor_alpha026(date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    return _load_ag_factor("alpha026", "alpha101")


FACTOR_REGISTRY = {
    "bp": ("bp = 1/pb", load_factor_bp),
    "ep": ("ep = 1/pe_ttm", load_factor_ep),
    "bp_ep": ("(bp + ep) / 2 等权", load_factor_bp_ep),
    "turnover": ("-turnover_rate (低换手)", load_factor_turnover),
    "volume_ratio": ("volume_ratio 量比", load_factor_volume_ratio),
    "dv_ratio": ("dv_ratio 股息率", load_factor_dv_ratio),
    # ── 9 个 Anomaly 因子 ──
    "ag": ("Asset Growth 总资产增长率", load_factor_ag),
    "droe": ("ΔROE 净资产收益率变化", load_factor_droe),
    "acc": ("Accruals 应计利润", load_factor_acc),
    "sg": ("Sales Growth 营收增长率", load_factor_sg),
    "roa": ("ROA 总资产收益率", load_factor_roa),
    "pm": ("Profit Margin 利润率", load_factor_pm),
    "ato": ("Asset Turnover 总资产周转率", load_factor_ato),
    "opa": ("OP/Assets 营业利润/总资产", load_factor_opa),
    "tbi": ("Tax-to-Book Income 税会差异", load_factor_tbi),
    "bl": ("Book Leverage 账面杠杆", load_factor_bl),
    # ── 量价微观结构因子（2026-08-16 入库, 数据源=stock_minute_5中证800域+qfq）──
    "rev20_improved": ("改进反转: 20日复合'开盘半小时后→收盘'收益(负向, 剔除隔夜与开盘噪音; "
                      "IC-0.073/74个月/5层严格单调, rev20替代品; 与换手率信息重叠故非独立增量)",
                      load_factor_rev20_improved),
    # ── AG因子库三强 (2026-08-16 二关通过: IC/单调/组合Sharpe三关, 研究观察级) ──
    "gtja_054": ("GTJA#054 波动率类(vwap相关; IC+0.074/ICIR0.47/单调p0.037/"
                "Top20组合CAGR15.9%/Sharpe0.80; ⚠️低价量价风格暴露, 非纯alpha)",
                load_factor_gtja_054),
    "gtja_154": ("GTJA#154 (IC+0.046/ICIR0.44/单调p0.037/"
                "CAGR16.3%/Sharpe0.76/MDD-21%; ⚠️同上标注)",
                load_factor_gtja_154),
    "alpha026": ("Alpha#26 (IC+0.046/ICIR0.66/单调p0.000/"
                "CAGR14.1%/Sharpe0.72; ⚠️同上标注)",
                load_factor_alpha026),
}

PERIODS = (1, 5, 10, 20)  # forward return 周期
QUANTILES = 10  # 分10组


# ═══════════════════════════════════════════════════════
#  行业映射（行业中性化用）
# ═══════════════════════════════════════════════════════

_INDUSTRY_CACHE: pd.Series | None = None


def load_industry_mapping() -> pd.Series:
    """加载 code → industry 映射（dragon_quant.db.stock_info，Alphalens groupby 格式）"""
    global _INDUSTRY_CACHE
    if _INDUSTRY_CACHE is not None:
        return _INDUSTRY_CACHE
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=30)
    try:
        df = pd.read_sql_query(
            "SELECT code, industry FROM stock_info "
            "WHERE industry IS NOT NULL AND industry != ''",
            conn,
        )
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df = df.drop_duplicates(subset=["code"], keep="first")
    s = df.set_index("code")["industry"]
    _INDUSTRY_CACHE = s
    return s


def diagnose_factor(
    name: str,
    desc: str,
    factor_series: pd.Series,
    prices: pd.DataFrame,
    save_plots: bool = False,
    industry: pd.Series | None = None,
) -> dict:
    """用 Alphalens 对单个因子做全维度诊断"""
    print(f"\n{'=' * 70}")
    print(f"  📊 因子诊断: {name} — {desc}")
    print(f"  时间范围: {START_DATE} ~ {END_DATE}")
    print(f"{'=' * 70}\n")

    # ── Step 1: 清洗对齐 ──
    print("  [1/5] 数据对齐与清洗...")
    if industry is not None:
        n_ind = industry.nunique()
        # Alphalens 要求因子池内全部资产都有分组映射：
        # 过滤掉无行业映射的股票（688科创板/920北交所未收录在 stock_info 行业列）
        before = len(factor_series)
        covered = set(industry.index)
        factor_series = factor_series[
            factor_series.index.get_level_values(1).isin(covered)
        ]
        print(f"      行业中性化模式: {n_ind} 个行业分组, 因子池 {before}→{len(factor_series)} (剔除无映射股票)")
    # 进入 A 股兼容模式（跳过频率验证）
    _enter_alphalens_compat_mode()
    try:
        factor_data = al.utils.get_clean_factor_and_forward_returns(
            factor=factor_series,
            prices=prices,
            periods=PERIODS,
            quantiles=QUANTILES,
            bins=None,
            groupby=industry.to_dict() if industry is not None else None,
            groupby_labels=None,
            max_loss=0.5,
        )
    finally:
        _exit_alphalens_compat_mode()
    n_assets = factor_data.index.get_level_values("asset").nunique()
    n_dates = factor_data.index.get_level_values("date").nunique()
    n_total = len(factor_data)
    print(f"      股票数: {n_assets} | 交易日: {n_dates} | 样本数: {n_total}")
    print(f"      基准收益周期: {PERIODS}")

    # ── Step 2: IC 分析 ──
    print("  [2/5] IC（Information Coefficient）分析...")
    ic = al.performance.factor_information_coefficient(factor_data)
    ic_summary = al.performance.mean_information_coefficient(
        factor_data, by_group=False
    )

    # 计算 IC 标准差和正方向率（从原始 IC 序列计算）
    ic_raw = al.performance.factor_information_coefficient(factor_data)
    ic_stats = {}
    for col in ic_raw.columns:
        vals = ic_raw[col].dropna()
        if len(vals) > 0:
            ic_stats[col] = {
                "mean": vals.mean(),
                "std": vals.std(),
                "pos_rate": (vals > 0).mean(),
            }

    # 打印 IC 汇总
    print(f"\n  ── IC 汇总 ──")
    print(f"  {'周期':<8} {'Mean IC':>10} {'Std':>8} {'ICIR':>8} {'正方向率':>8}")
    print(f"  {'─' * 42}")
    for period_label, stats in ic_stats.items():
        mean_ic = stats["mean"]
        std_ic = stats["std"]
        icir = mean_ic / std_ic if std_ic > 0 else 0
        pos_rate = stats["pos_rate"]
        print(f"  {str(period_label):<8} {mean_ic:>+10.4f} {std_ic:>8.4f} {icir:>+8.3f} {pos_rate:>7.0%}")

    # IC 判定（取 abs mean 最大的周期）
    best_period_label = max(ic_stats, key=lambda k: abs(ic_stats[k]["mean"]))
    best_period_int = int(best_period_label.replace('D', ''))
    best_ic = ic_stats[best_period_label]["mean"]
    best_std = ic_stats[best_period_label]["std"]
    best_icir = best_ic / best_std if best_std > 0 else 0
    best_pos = ic_stats[best_period_label]["pos_rate"]

    ic_verdict = "❌ 无效"
    if abs(best_ic) > 0.03 and best_pos >= 0.70:
        ic_verdict = "✅ 有效"
    elif abs(best_ic) > 0.02:
        ic_verdict = "🟡 弱信号"
    elif abs(best_ic) > 0.015:
        ic_verdict = "🟠 微弱"

    ic_results = {
        "best_period": best_period_label,
        "mean_ic": round(best_ic, 4),
        "icir": round(best_icir, 3),
        "pos_rate": round(best_pos, 4),
        "verdict": ic_verdict,
    }

    # ── Step 2.5: 行业分组 IC（行业中性化诊断）──
    ic_by_group_results = {}
    if industry is not None:
        print("\n  [2.5/5] 行业分组 IC（行业中性化）...")
        try:
            ic_by_group = al.performance.mean_information_coefficient(factor_data, by_group=True)
            best_col_g = best_period_label if best_period_label in ic_by_group.columns else ic_by_group.columns[-1]
            icg = ic_by_group[best_col_g].sort_values(ascending=False)
            # IC 衰减比 = 行业内 IC / 全样本 IC
            n_pos_groups = int((icg > 0).sum())
            n_valid = int(icg.notna().sum())
            industry_stability = n_pos_groups / n_valid if n_valid else 0
            print(f"      行业数: {n_valid} | IC为正的行业: {n_pos_groups} ({industry_stability:.0%})")
            print(f"      行业内IC衰减比: {icg.mean() / best_ic:.2f}x (均值 {icg.mean():+.4f} / 全样本 {best_ic:+.4f})")
            print(f"\n  ── 行业分组 IC ({best_period_label}, 前8/后4) ──")
            top8 = icg.head(8)
            for ind, v in top8.items():
                print(f"    🟢 {str(ind)[:14]:<14s} IC={v:+.4f}")
            if len(icg) > 8:
                print(f"    {'...':>16s}")
                for ind, v in icg.tail(4).items():
                    print(f"    🔴 {str(ind)[:14]:<14s} IC={v:+.4f}")
            ic_by_group_results = {
                "n_groups": n_valid,
                "pos_rate_groups": round(industry_stability, 4),
                "ic_decay_ratio": round(float(icg.mean()) / best_ic, 3) if best_ic != 0 else None,
                "best_industries": {str(k): round(float(v), 4) for k, v in top8.items()},
                "worst_industries": {str(k): round(float(v), 4) for k, v in icg.tail(4).items()},
            }
        except Exception as e:
            print(f"      ⚠️ 行业分组IC失败: {e}")

        # 行业中性化后的分位收益（组内去均值）
        try:
            nr_by_q, _ = al.performance.mean_return_by_quantile(
                factor_data, by_date=False, by_group=False, group_adjust=True
            )
            if best_period_label in nr_by_q.columns:
                nq = nr_by_q[best_period_label]
                neut_ls = float(nq.iloc[-1] - nq.iloc[0])
                ic_by_group_results["neutralized_ls"] = round(neut_ls, 4)
        except Exception as e:
            print(f"      ⚠️ 行业中性化收益计算失败: {e}")

    # ── Step 3: 分位收益分析 ──
    print("\n  [3/5] 分位收益分析...")
    mean_ret_by_q, std_ret_by_q = al.performance.mean_return_by_quantile(
        factor_data, by_date=False, by_group=False
    )

    print(f"\n  ── 分位收益（{best_period_int}日周期 = {best_period_label}）──")
    print(f"  {'分位':<6} {'收益率%':>10}")
    print(f"  {'─' * 16}")
    cols = mean_ret_by_q.columns
    best_col = best_period_label
    q_series = mean_ret_by_q[best_col]
    for q in q_series.index:
        print(f"  Q{q:<4} {q_series[q] * 100:>+8.4f}%")

    # long-short spread
    long_short = q_series.iloc[-1] - q_series.iloc[0]
    monotonic = "是" if all(
        q_series.iloc[i] <= q_series.iloc[i + 1]
        for i in range(len(q_series) - 1)
    ) or all(
        q_series.iloc[i] >= q_series.iloc[i + 1]
        for i in range(len(q_series) - 1)
    ) else "否"
    print(f"\n  Long-Short Spread: {long_short * 100:+8.4f}%")
    print(f"  单调性: {monotonic}")
    print(f"  方向: {'做多高分位' if long_short > 0 else '做空高分位'}")
    if "neutralized_ls" in ic_by_group_results:
        neut_ls = ic_by_group_results["neutralized_ls"]
        print(f"  行业中性化 L-S: {neut_ls * 100:+8.4f}% (剔除行业效应后的纯alpha, 衰减 {(1 - neut_ls / long_short) * 100:.0f}%)" if long_short != 0 else "")

    q_verdict = "✅ 强" if abs(long_short) > 0.01 and monotonic == "是" else \
                "🟡 有信号" if abs(long_short) > 0.005 else "❌ 弱"

    q_results = {
        "long_short_spread": round(long_short, 4),
        "monotonic": monotonic,
        "verdict": q_verdict,
    }

    # ── Step 4: 换手率分析 ──
    print("\n  [4/5] 因子换手率分析...")
    autocorr = al.performance.factor_rank_autocorrelation(factor_data, period=1)
    if autocorr is not None and not autocorr.empty:
        mean_auto = float(autocorr.mean())
        turnover = 1 - mean_auto  # 1 - 自相关系数 = 换手率
        print(f"  因子自相关(1日): {mean_auto:.4f}")
        print(f"  因子换手率: {turnover:.4f} ({turnover * 100:.1f}%)")
        turnover_verdict = "✅ 稳定" if mean_auto > 0.8 else \
                          "🟡 中等" if mean_auto > 0.5 else "🔴 高换手"
    else:
        mean_auto = None
        turnover = None
        turnover_verdict = "N/A"

    turnover_results = {
        "autocorr_1d": round(mean_auto, 4) if mean_auto else None,
        "turnover_1d": round(turnover, 4) if turnover else None,
        "verdict": turnover_verdict,
    }

    # ── Step 5: 综合判定 ──
    print("\n  [5/5] 综合判定...")

    score = 0
    # IC 评分
    if abs(best_ic) > 0.03:
        score += 3
    elif abs(best_ic) > 0.02:
        score += 2
    elif abs(best_ic) > 0.015:
        score += 1

    if best_icir > 1.0:
        score += 2
    elif best_icir > 0.5:
        score += 1

    if best_pos > 0.80:
        score += 2
    elif best_pos > 0.70:
        score += 1

    # 分位收益评分
    if abs(long_short) > 0.01:
        score += 2
    elif abs(long_short) > 0.005:
        score += 1

    if monotonic == "是":
        score += 1

    # 换手率评分
    if mean_auto and mean_auto > 0.8:
        score += 1

    # 总分判定
    if score >= 8:
        final_verdict = "👑 S级 — 极优因子"
    elif score >= 6:
        final_verdict = "✅ A级 — 优秀因子"
    elif score >= 4:
        final_verdict = "🟡 B级 — 可用因子"
    elif score >= 2:
        final_verdict = "🟠 C级 — 弱因子（仅辅助）"
    else:
        final_verdict = "🔴 D级 — 无效因子"

    print(f"\n  综合评分: {score}/12")
    print(f"  最终判定: {final_verdict}")

    # ── 保存图表（可选）──
    if save_plots:
        _save_plots(name, ic, mean_ret_by_q)

    # ── 返回结果 ──
    return {
        "name": name,
        "description": desc,
        "samples": n_total,
        "assets": n_assets,
        "dates": n_dates,
        "ic": ic_results,
        "quantile": q_results,
        "turnover": turnover_results,
        "by_industry": ic_by_group_results,
        "score": score,
        "verdict": final_verdict,
    }


def _save_plots(name: str, ic: pd.DataFrame, q_ret: pd.DataFrame):
    """保存诊断图表"""
    print(f"\n  保存图表到 {REPORT_DIR}...")
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # IC 时间序列（返回值=axes数组，当前figure用plt.gcf()获取）
        al.plotting.plot_ic_ts(ic)
        fig = plt.gcf()
        fig.suptitle(f"IC Time Series — {name}")
        fig.savefig(REPORT_DIR / f"alphalens_{name}_ic_ts.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

        # 分位收益柱状图（返回值=单个Axes）
        al.plotting.plot_quantile_returns_bar(q_ret)
        fig = plt.gcf()
        fig.suptitle(f"Quantile Returns — {name}")
        fig.savefig(REPORT_DIR / f"alphalens_{name}_quantile_returns.png", dpi=150, bbox_inches="tight")
        plt.close(fig)

        print(f"  ✅ 图表已保存")
    except Exception as e:
        print(f"  ⚠️ 图表保存失败: {e}")


# ═══════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════

def run_diagnosis(
    factor_names: list[str],
    save_plots: bool = False,
    by_industry: bool = False,
) -> list[dict]:
    """对多个因子运行诊断"""
    print(f"\n{'#' * 70}")
    print(f"  Alphalens 因子全维度诊断")
    print(f"  数据源: fundamental_data.db / dragon_quant_meta.db")
    print(f"  诊断周期: {START_DATE} ~ {END_DATE} ({int(END_DATE) - int(START_DATE)} 天)")
    if by_industry:
        print(f"  模式: 行业中性化（dragon_quant.db stock_info 110行业）")
    print(f"{'#' * 70}")

    # 加载价格数据（所有因子共享）
    print("\n  📡 加载价格数据...")
    prices = load_prices()
    # 只保留有价格的日期
    date_slice = prices.index
    print(f"     交易日数: {len(date_slice)}")
    print(f"     股票数: {len(prices.columns)}")

    # 行业映射
    industry = None
    if by_industry:
        industry = load_industry_mapping()
        print(f"     行业映射: {len(industry)} 只股票 / {industry.nunique()} 个行业")

    results = []
    for name in factor_names:
        if name not in FACTOR_REGISTRY:
            print(f"\n  ⚠️ 未知因子: {name}，跳过")
            continue

        desc, loader = FACTOR_REGISTRY[name]
        print(f"\n  📡 加载因子: {name} ({desc})...")
        factor_series = loader(date_slice)
        print(f"     样本数: {len(factor_series)}")

        if len(factor_series) < 1000:
            print(f"     数据不足，跳过")
            continue

        result = diagnose_factor(name, desc, factor_series, prices, save_plots, industry=industry)
        results.append(result)

    return results


def print_summary(results: list[dict]):
    """打印综合对比表"""
    print(f"\n\n{'=' * 70}")
    print(f"  📋 因子诊断综合对比")
    print(f"{'=' * 70}")

    header = f"{'因子':<12} {'等级':<20} {'IC(最优)':>10} {'ICIR':>7} {'方向%':>7} {'L-S%':>8} {'单调':<6} {'换手':<8} {'评分':<5}"
    print(f"\n{header}")
    print(f"{'─' * 100}")

    for r in results:
        ic = r["ic"]
        q = r["quantile"]
        t = r["turnover"]
        print(
            f"{r['name']:<12} {r['verdict'].split('—')[0].strip():<20} "
            f"{ic['mean_ic']:>+10.4f} {ic['icir']:>+7.3f} "
            f"{ic['pos_rate']:>6.0%} "
            f"{q['long_short_spread'] * 100:>+7.4f}% "
            f"{'✅' if q['monotonic']=='是' else '❌':<6} "
            f"{t['verdict']:<8} {r['score']:<5}"
        )

    print(f"{'─' * 100}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Alphalens 因子全维度诊断管道",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
内置因子:
  bp / ep / bp_ep / turnover / volume_ratio / dv_ratio
  9 个 Anomaly 因子（需加 --anomaly）:
    ag(droe/acc/sg/roa/pm/ato/opa/tbi/bl

示例:
  python alphalens_factor_diagnosis.py bp
  python alphalens_factor_diagnosis.py all                         # 4 个基础因子
  python alphalens_factor_diagnosis.py all --anomaly               # 4 基础 + 10 个 Anomaly
  python alphalens_factor_diagnosis.py ag roa --anomaly            # 指定 Anomaly 因子
  python alphalens_factor_diagnosis.py all --save-plots
        """,
    )
    ap.add_argument(
        "factors",
        nargs="+",
        help="因子名 (bp/ep/bp_ep/turnover/sg/roa/.../all)",
    )
    ap.add_argument("--anomaly", action="store_true", help="加载 10 个 Anomaly 因子（需 dragon_quant.db）")
    ap.add_argument("--by-industry", action="store_true", help="行业中性化诊断（分组IC+组内去均值收益）")
    ap.add_argument("--save-plots", action="store_true", help="保存诊断图表")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()

    # 展开 "all"
    factor_names = []
    anomaly_factors = ["ag", "droe", "acc", "sg", "roa", "pm", "ato", "opa", "tbi", "bl"]
    for f in a.factors:
        if f == "all":
            factor_names.extend(["bp", "ep", "bp_ep", "turnover"])
            if a.anomaly:
                factor_names.extend(anomaly_factors)
        else:
            factor_names.append(f)
    # 去重保序
    seen = set()
    factor_names = [x for x in factor_names if x not in seen and not seen.add(x)]

    results = run_diagnosis(factor_names, save_plots=a.save_plots, by_industry=a.by_industry)

    if not results:
        print("\n⚠️ 无有效结果")
        return 1

    print_summary(results)

    if a.json:
        import json
        # 清理可序列化的结果
        clean = []
        for r in results:
            clean.append({
                k: v for k, v in r.items()
                if k != "verdict" or True
            })
        print(json.dumps(clean, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())