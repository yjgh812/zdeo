# -*- coding: utf-8 -*-
"""
屠龙系统 V4.2 模块化回测主脚本
=============================
5 个策略模型 2016-2026 全市场回测 + 报告生成

数据源:
  - dragon_quant.db (信号/因子/信息)
  - dragon_quant_qfq.db (前复权行情)

框架: factor_engine/core/backtest_system/ V4.2

Output: D:/zcodesj/reports/tulong_backtest_20260813/
"""

import sys
# ONLY add the factor_engine root (D:\workbuddyspace\factor_engine)
# Do NOT add D:\workbuddyspace to avoid conflicting with D:\workbuddyspace\core\
FACTOR_ENGINE_ROOT = r"D:\workbuddyspace\factor_engine"
if FACTOR_ENGINE_ROOT not in sys.path:
    sys.path.insert(0, FACTOR_ENGINE_ROOT)

import os
import json
import logging
import sqlite3
import warnings
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Tuple, Callable

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Framework imports
from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest, PortfolioBacktest
from core.backtest_system.report import build_html_report
from core.backtest_system.performance import (
    performance_report, equity_metrics, trade_metrics, format_report, yearly_breakdown
)
from core.backtest_system.export import export_results

# ============================================================
#  Paths
# ============================================================
DATA_DIR = r"D:\workbuddyspace\factor_engine\data"
DB_PATH = os.path.join(DATA_DIR, "dragon_quant.db")
QFQ_PATH = os.path.join(DATA_DIR, "dragon_quant_qfq.db")
OUTPUT_DIR = r"D:\zcodesj\reports\tulong_backtest_20260813"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
#  Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(OUTPUT_DIR, "backtest_run.log"),
            encoding="utf-8"
        ),
    ]
)
logger = logging.getLogger("TulongBacktest")

# ============================================================
#  BacktestConfig (屠龙标准 v1.0)
# ============================================================
cfg = BacktestConfig(
    initial_capital=100000,
    commission_rate=0.00025,
    stamp_tax_rate=0.0005,
    slippage_rate=0.0003,
    max_position_pct=0.20,
    max_positions=50,
    max_drawdown_limit=0.20,
    max_single_loss_pct=0.08,
    stop_loss_pct=0.08,
    take_profit_pct=0.0,
    trailing_stop_pct=0.10,
    max_holding_bars=0,
    enable_t_plus_1=True,
    enable_price_limit=True,
    enable_st_filter=True,
    lot_size=100,
    universe_filter=True,
    min_commission=5.0,
    slippage_model="fixed",
    execution="next_open",
    exit_execution="same_close",
)
cfg.max_positions = 50

START_DATE = "2016-06-30"
END_DATE = "2026-08-13"


# ============================================================
#  TulongDataLoader — 数据加载与信号查询
# ============================================================

class TulongDataLoader:
    """屠龙数据加载器：行情 + 信号 + 因子，用 DataFrameDataFeed 兼容 V4.2 引擎"""

    @staticmethod
    def _norm_date(d) -> str:
        """统一日期格式为 YYYY-MM-DD
        输入: int(20160630), str('20160630'), str('2016-06-30')
        输出: str('2016-06-30')
        """
        s = str(d).strip()
        if len(s) == 8 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
        return s

    def __init__(self, db_path: str, qfq_path: str):
        logger.info("=== 初始化 TulongDataLoader ===")
        self.db_path = db_path
        self.qfq_path = qfq_path

        # 连接
        self._conn = sqlite3.connect(db_path)
        self._conn_qfq = sqlite3.connect(qfq_path)
        self._conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF; PRAGMA cache_size=-8000000;")
        self._conn_qfq.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF; PRAGMA cache_size=-8000000;")

        # -- 交易日历 --
        logger.info("  加载 trading_calendar ...")
        self._trading_dates = pd.read_sql(
            "SELECT date_str FROM trading_calendar ORDER BY date_str", self._conn
        )["date_str"].tolist()
        self._trading_set = set(self._trading_dates)
        logger.info(f"  交易日: {len(self._trading_dates)} 个")

        # -- 股票信息 --
        logger.info("  加载 stock_info ...")
        self._stock_info = pd.read_sql(
            "SELECT code, name, market, industry, list_date, is_st, status FROM stock_info",
            self._conn
        )
        self._si_indexed = self._stock_info.set_index("code")

        # 可交易股票代码集合 (提前计算好)
        tradable = []
        for code, row in self._si_indexed.iterrows():
            prefix = code[:3]
            if prefix in ("688", "689"):
                continue
            if code.startswith(("8", "4", "92", "43")):
                continue
            if row.get("is_st", 0) == 1:
                continue
            tradable.append(code)
        self._tradable_set = set(tradable)
        logger.info(f"  可交易股票: {len(self._tradable_set)} 只")

        # -- QFQ 前复权行情 (按日期索引) --
        logger.info("  加载 stock_daily_meta_qfq ...")
        chunk_list = []
        for chunk in pd.read_sql(
            "SELECT code, date, open, high, low, close, volume, amount, change_pct "
            "FROM stock_daily_meta_qfq ORDER BY date",
            self._conn_qfq, chunksize=500000
        ):
            chunk_list.append(chunk)
        qfq_all = pd.concat(chunk_list, ignore_index=True)
        logger.info(f"  QFQ 总行数: {len(qfq_all):,}")

        # 仅保留可交易股票
        qfq_all = qfq_all[qfq_all["code"].isin(self._tradable_set)].copy()
        logger.info(f"  过滤后可交易 QFQ: {len(qfq_all):,}")

        # 按日期分组构建 dict (统一日期格式为 YYYY-MM-DD)
        logger.info("  构建日期索引 ...")
        self._bar_map: Dict[str, Dict[str, Dict]] = {}
        for dt, grp in qfq_all.groupby("date"):
            dt_norm = self._norm_date(dt)
            grp = grp.set_index("code")
            recs = {}
            for code, row in grp.iterrows():
                recs[code] = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "amount": float(row["amount"]),
                    "change_pct": float(row["change_pct"]) if pd.notna(row.get("change_pct")) else 0.0,
                }
            self._bar_map[dt_norm] = recs
        logger.info(f"  日期索引: {len(self._bar_map)} 个交易日")

        # 预计算 prev_close (使用归一化日期)
        logger.info("  计算 prev_close 映射 ...")
        all_dates_sorted = sorted(self._bar_map.keys())
        self._prev_close_map: Dict[str, Dict[str, float]] = {}
        for i, dt in enumerate(all_dates_sorted):
            if i == 0:
                self._prev_close_map[dt] = {}
                continue
            prev_dt = all_dates_sorted[i - 1]
            prev_recs = self._bar_map.get(prev_dt, {})
            pcm = {}
            for code, rec in prev_recs.items():
                pcm[code] = rec["close"]
            self._prev_close_map[dt] = pcm

        # -- 上证指数 MA250 (牛熊判定) --
        logger.info("  加载 index_daily (000001 MA250) ...")
        idx_df = pd.read_sql(
            "SELECT date, close FROM index_daily WHERE index_code='000001' ORDER BY date",
            self._conn
        )
        if len(idx_df) > 0:
            idx_df["_date_norm"] = idx_df["date"].apply(self._norm_date)
            idx_df["ma250"] = idx_df["close"].rolling(250, min_periods=100).mean()
            self._idx_close = dict(zip(idx_df["_date_norm"], idx_df["close"]))
            self._idx_ma250 = dict(zip(idx_df["_date_norm"], idx_df["ma250"]))
        else:
            self._idx_close = {}
            self._idx_ma250 = {}
        logger.info(f"  上证指数: {len(self._idx_close)} 日")

        # -- 信号表: 按日期建立索引 --
        logger.info("  加载信号表并建日期索引 (统一 YYYY-MM-DD) ...")

        def _index_signals(df, cols):
            df["date"] = df["date"].apply(self._norm_date)
            result = {}
            for dt, grp in df.groupby("date"):
                result[dt] = grp.set_index("code")[cols]
            return result

        # V375
        logger.info("    -> stock_signals_v375")
        v375 = pd.read_sql(
            "SELECT code, date, v375_prob FROM stock_signals_v375", self._conn
        )
        self._v375_by_date = _index_signals(v375, ["v375_prob"])

        # P2
        logger.info("    -> stock_signals_p2")
        p2 = pd.read_sql(
            "SELECT code, date, p2_prob FROM stock_signals_p2", self._conn
        )
        self._p2_by_date = _index_signals(p2, ["p2_prob"])

        # Composite (stock_signals_v4)
        logger.info("    -> stock_signals_v4 (composite)")
        composite = pd.read_sql(
            "SELECT code, date, composite_score, ai_prob_5d FROM stock_signals_v4", self._conn
        )
        self._composite_by_date = _index_signals(composite, ["composite_score", "ai_prob_5d"])

        # Fundamental factors
        logger.info("    -> stock_factors_fundamental")
        fundamental = pd.read_sql(
            "SELECT code, date, close, ep, bp FROM stock_factors_fundamental", self._conn
        )
        self._fundamental_by_date = _index_signals(fundamental, ["close", "ep", "bp"])

        # stock_signals (legacy composite, fallback)
        logger.info("    -> stock_signals (legacy composite)")
        legacy = pd.read_sql(
            "SELECT code, date, composite_score, ai_prob_5d FROM stock_signals", self._conn
        )
        self._legacy_signals_by_date = _index_signals(legacy, ["composite_score", "ai_prob_5d"])

        logger.info(f"  V375 日期: {len(self._v375_by_date)} | P2 日期: {len(self._p2_by_date)} "
                     f"| Composite 日期: {len(self._composite_by_date)}")

    # ------------------------------------------------------------------
    #  DataFeed 接口 (V4.2 引擎必需)
    # ------------------------------------------------------------------

    def get_trading_dates(self, start_date: str, end_date: str) -> List[str]:
        """交易日序列"""
        return [d for d in self._trading_dates if start_date <= d <= end_date]

    def get_data(self, date_str: str) -> Dict:
        """获取指定交易日全市场行情 (引擎用)。
        永远返回 dict（即使为空），避免引擎空指针。
        """
        recs = self._bar_map.get(date_str)

        prices = {}
        opens = {}
        highs = {}
        lows = {}
        volumes = {}
        amounts = {}
        prev_closes = {}
        names = {}
        is_st = {}

        if recs is not None and len(recs) > 0:
            pcm = self._prev_close_map.get(date_str, {})
            for code, r in recs.items():
                if code not in self._tradable_set:
                    continue
                prices[code] = r["close"]
                opens[code] = r["open"]
                highs[code] = r["high"]
                lows[code] = r["low"]
                volumes[code] = r["volume"]
                amounts[code] = r["amount"]
                if code in pcm:
                    prev_closes[code] = pcm[code]
                si = self._si_indexed.loc[code] if code in self._si_indexed.index else None
                if si is not None:
                    names[code] = str(si.get("name", ""))
                    is_st[code] = bool(si.get("is_st", 0))

        return {
            "date": date_str,
            "prices": prices,
            "open": opens,
            "high": highs,
            "low": lows,
            "volumes": volumes,
            "amounts": amounts,
            "prev_close": prev_closes,
            "names": names,
            "is_st": is_st,
        }

    # ------------------------------------------------------------------
    #  信号查询接口 (策略函数使用)
    # ------------------------------------------------------------------

    def get_fundamental_factors(self, date_str: str) -> Optional[pd.DataFrame]:
        """获取该日基本面因子 (ep, bp, close)"""
        df = self._fundamental_by_date.get(date_str)
        if df is None or len(df) == 0:
            return None
        result = df[["close", "ep", "bp"]].dropna().copy()
        if len(result) == 0:
            return None
        return result

    def get_v375_signals(self, date_str: str) -> Optional[pd.DataFrame]:
        """获取该日 V375 信号"""
        df = self._v375_by_date.get(date_str)
        if df is None or len(df) == 0:
            return None
        return df[["v375_prob"]].dropna().copy()

    def get_p2_signals(self, date_str: str) -> Optional[pd.DataFrame]:
        """获取该日 P2 信号"""
        df = self._p2_by_date.get(date_str)
        if df is None or len(df) == 0:
            return None
        return df[["p2_prob"]].dropna().copy()

    def get_composite_signals(self, date_str: str) -> Optional[pd.DataFrame]:
        """获取该日综合信号 (composite_score, ai_prob_5d)"""
        # 优先用 stock_signals_v4
        df = self._composite_by_date.get(date_str)
        if df is not None and len(df) > 0:
            return df[["composite_score", "ai_prob_5d"]].dropna().copy()
        # 回退到 stock_signals
        df2 = self._legacy_signals_by_date.get(date_str)
        if df2 is not None and len(df2) > 0:
            return df2[["composite_score", "ai_prob_5d"]].dropna().copy()
        return None

    def is_bull_market(self, date_str: str) -> bool:
        """用上证指数判断牛熊: close > MA250 为牛"""
        close = self._idx_close.get(date_str)
        ma250 = self._idx_ma250.get(date_str)
        if close is None or ma250 is None or pd.isna(ma250):
            return True  # 默认牛市
        return close > ma250

    def is_tradable(self, code: str) -> bool:
        return code in self._tradable_set


# ============================================================
#  Strategy Signal Functions
#  所有函数签名: (data_loader: TulongDataLoader, date_str: str) -> Dict[code, weight]
#  由 lambda wrapper 适配为 (daily_data, date) 供引擎调用
# ============================================================

def bp_ep_signal(loader: TulongDataLoader, date_str: str) -> Dict[str, float]:
    """
    bp+ep 季度调仓:
    - 取 <= 30 元域内股票
    - 等权合成 ep_rank + bp_rank
    - Top20 等权, 总计 40% 核心仓位
    """
    df = loader.get_fundamental_factors(date_str)
    if df is None or len(df) < 20:
        return {}

    # 价格过滤
    df = df[df["close"] <= 30].copy()
    if len(df) < 20:
        return {}

    # 因子排序
    df["ep_rank"] = df["ep"].rank(pct=True)
    df["bp_rank"] = df["bp"].rank(pct=True)
    df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2

    top20 = df.nlargest(20, "score")
    weight = 0.40 / 20  # 40% 仓位 / 20 只
    return {idx: weight for idx, row in top20.iterrows()}


def v375_signal(loader: TulongDataLoader, date_str: str) -> Dict[str, float]:
    """
    V375 信号策略 (每日):
    - prob >= 0.65
    - 每日最多 3 只
    - 每只 <= 20%
    """
    df = loader.get_v375_signals(date_str)
    if df is None or len(df) == 0:
        return {}

    candidates = df[df["v375_prob"] >= 0.65].copy()
    if len(candidates) == 0:
        return {}

    candidates = candidates.nlargest(min(3, len(candidates)), "v375_prob")
    w = min(0.20, 1.0 / len(candidates) * 0.20)
    return {idx: w for idx, row in candidates.iterrows()}


def p2_signal(loader: TulongDataLoader, date_str: str) -> Dict[str, float]:
    """
    P2 集成信号策略:
    - 牛市阈值 >= 0.70, 熊市 >= 0.74
    - 最多 5 只, 机动仓位 40%
    """
    df = loader.get_p2_signals(date_str)
    if df is None or len(df) == 0:
        return {}

    bullish = loader.is_bull_market(date_str)
    threshold = 0.70 if bullish else 0.74

    candidates = df[df["p2_prob"] >= threshold].copy()
    if len(candidates) == 0:
        return {}

    candidates = candidates.nlargest(min(5, len(candidates)), "p2_prob")
    w = 0.40 / len(candidates)  # 机动仓位 40% 平分
    return {idx: w for idx, row in candidates.iterrows()}


def composite_signal(loader: TulongDataLoader, date_str: str) -> Dict[str, float]:
    """
    综合信号策略:
    - composite_score >= 0.70
    - ai_prob_5d >= 0.50
    - 最多 5 只, 等权 100%
    """
    df = loader.get_composite_signals(date_str)
    if df is None or len(df) == 0:
        return {}

    df = df[(df["composite_score"] >= 0.70) & (df["ai_prob_5d"] >= 0.50)]
    if len(df) == 0:
        return {}

    candidates = df.nlargest(min(5, len(df)), "composite_score")
    w = 1.0 / len(candidates)
    return {idx: w for idx, row in candidates.iterrows()}


# ============================================================
#  Run Single Strategy Backtest
# ============================================================

def run_single_backtest(
    name: str,
    sig_func: Callable,
    loader: TulongDataLoader,
    freq: str,
) -> Dict:
    """包装信号函数适配引擎接口，执行回测并导出结果"""
    logger.info(f"\n{'='*60}")
    logger.info(f"开始回测: {name} (调仓频率={freq})")
    logger.info(f"{'='*60}")

    bt = SingleStrategyBacktest(cfg)

    # 包装: 信号函数用 data_loader 直接查 DB，忽略引擎传来的 daily_data
    wrapped = lambda daily_data, date_str: sig_func(loader, date_str)

    result = bt.run(
        wrapped,
        loader,
        start_date=START_DATE,
        end_date=END_DATE,
        rebalance_freq=freq,
        strategy_name=name,
    )

    metrics = result.get("metrics", {})
    equity_df = result.get("equity_curve", pd.DataFrame())
    trades = result.get("trades", [])

    logger.info(f"  总收益: {metrics.get('total_return_pct', 0):.2f}%")
    logger.info(f"  年化收益: {metrics.get('annualized_return_pct', 0):.2f}%")
    logger.info(f"  夏普: {metrics.get('sharpe_ratio', 0):.2f}")
    logger.info(f"  最大回撤: {metrics.get('max_drawdown_pct', 0):.2f}%")
    logger.info(f"  交易次数: {metrics.get('total_trades', 0)}")
    logger.info(f"  最终权益: {metrics.get('final_equity', 0):,.2f}")

    # 导出三件套
    prefix = name.replace("+", "_").replace(" ", "_")
    export_results(
        equity_curve=equity_df,
        trade_history=trades,
        prefix=prefix,
        initial_cash=cfg.initial_capital,
        start=START_DATE,
        end=END_DATE,
        market="china_a",
        output_dir=OUTPUT_DIR,
        strategy_name=name,
        metrics=metrics,
    )

    # HTML 报告
    try:
        html_path = os.path.join(OUTPUT_DIR, f"{prefix}_report.html")
        build_html_report(
            metrics=metrics,
            equity_df=equity_df,
            trades=trades,
            strategy_name=f"屠龙 {name}",
            output_path=html_path,
        )
        logger.info(f"  HTML: {html_path}")
    except Exception as e:
        logger.warning(f"  HTML 报告生成失败: {e}")

    return result


# ============================================================
#  Run Portfolio Backtest
# ============================================================

def run_portfolio_backtest(loader: TulongDataLoader, single_results: Dict) -> Optional[Dict]:
    """运行核心-卫星组合回测"""
    logger.info(f"\n{'='*60}")
    logger.info("组合回测: bp+ep(40%) + v375(20%) + p2(40%)")
    logger.info(f"{'='*60}")

    strategies = {
        "bp+ep_core": lambda daily_data, date_str: bp_ep_signal(loader, date_str),
        "v375_signal": lambda daily_data, date_str: v375_signal(loader, date_str),
        "p2_signal": lambda daily_data, date_str: p2_signal(loader, date_str),
    }
    weights = {"bp+ep_core": 0.40, "v375_signal": 0.20, "p2_signal": 0.40}

    pb = PortfolioBacktest(strategies=strategies, weights=weights, config=cfg)
    result_combo = pb.run(loader, start_date=START_DATE, end_date=END_DATE)

    combined = result_combo.get("combined", {})
    combo_ec = combined.get("equity_curve", pd.DataFrame())
    total_ret = combined.get("total_return_pct", 0)

    logger.info(f"  组合总收益: {total_ret:.2f}%")
    logger.info(f"  组合净值天数: {len(combo_ec)}")

    # 计算组合绩效
    if len(combo_ec) > 0:
        try:
            combo_metrics = equity_metrics(
                dates=combo_ec["date"].tolist(),
                equity=combo_ec["equity"].tolist(),
                rf=0.03,
            )
            logger.info(f"  组合年化: {combo_metrics.get('cagr', 0)*100:.2f}%")
            logger.info(f"  组合夏普: {combo_metrics.get('sharpe', 0):.2f}")
            logger.info(f"  组合最大回撤: {combo_metrics.get('max_drawdown', 0)*100:.2f}%")

            # 导出组合
            prefix = "portfolio_combo"

            # 净值 CSV
            combo_ec.to_csv(
                os.path.join(OUTPUT_DIR, f"{prefix}_equity.csv"),
                index=False, encoding="utf-8-sig"
            )

            # Summary JSON
            summary_data = {
                "meta": {
                    "strategy": "portfolio_combo",
                    "weights": weights,
                    "start_date": START_DATE,
                    "end_date": END_DATE,
                    "initial_capital": cfg.initial_capital,
                    "n_trading_days": len(combo_ec),
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                },
                "summary": {
                    "total_return_pct": total_ret,
                    "annualized_return_pct": round(combo_metrics.get("cagr", 0) * 100, 2),
                    "sharpe_ratio": round(combo_metrics.get("sharpe", 0), 4),
                    "max_drawdown_pct": round(combo_metrics.get("max_drawdown", 0) * 100, 2),
                    "sortino_ratio": round(combo_metrics.get("sortino", 0), 4),
                    "calmar_ratio": round(combo_metrics.get("calmar", 0), 4),
                    "final_equity": round(combo_metrics.get("final_equity", 0), 2),
                },
            }
            with open(os.path.join(OUTPUT_DIR, f"{prefix}_summary.json"), "w", encoding="utf-8") as f:
                json.dump(summary_data, f, ensure_ascii=False, indent=2)

            # 简易 HTML
            try:
                svg_line = _equity_svg(combo_ec["equity"].tolist())
                kpis = (
                    f"<div class='kpi'><div class='kpi-v'>{total_ret:.2f}%</div>"
                    f"<div class='kpi-l'>总收益</div></div>"
                    f"<div class='kpi'><div class='kpi-v'>{combo_metrics.get('cagr',0)*100:.2f}%</div>"
                    f"<div class='kpi-l'>年化收益</div></div>"
                    f"<div class='kpi'><div class='kpi-v'>{combo_metrics.get('max_drawdown',0)*100:.2f}%</div>"
                    f"<div class='kpi-l'>最大回撤</div></div>"
                    f"<div class='kpi'><div class='kpi-v'>{combo_metrics.get('sharpe',0):.2f}</div>"
                    f"<div class='kpi-l'>夏普</div></div>"
                )
                html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>屠龙 核心-卫星组合报告</title>
<style>
body {{ font-family:"Microsoft YaHei",sans-serif; background:#f5f6fa; padding:24px; color:#1f2329; }}
.container {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:22px; }}
.sub {{ color:#888; font-size:13px; margin-bottom:20px; }}
.kpis {{ display:flex; flex-wrap:wrap; gap:12px; margin:16px 0; }}
.kpi {{ background:#fff; border-radius:10px; padding:14px 20px; min-width:130px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.kpi-v {{ font-size:22px; font-weight:700; color:#f23645; }}
.kpi-l {{ font-size:12px; color:#888; margin-top:4px; }}
.card {{ background:#fff; border-radius:10px; padding:20px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.card h2 {{ font-size:16px; margin:0 0 12px; border-left:4px solid #f23645; padding-left:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ padding:8px 10px; border-bottom:1px solid #eee; text-align:left; }}
th {{ background:#fafafa; font-weight:600; }}
</style></head><body>
<div class="container">
<h1>屠龙 核心-卫星组合 (bp+ep 40% + v375 20% + p2 40%)</h1>
<div class="sub">生成 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {START_DATE} ~ {END_DATE}</div>
<div class="kpis">{kpis}</div>
<div class="card"><h2>组合净值曲线</h2>{svg_line}</div>
<div class="card"><h2>子策略权重</h2>
<table><tr><th>策略</th><th>权重</th></tr>
<tr><td>bp+ep 核心底仓</td><td>40%</td></tr>
<tr><td>V375 信号</td><td>20%</td></tr>
<tr><td>P2 集成信号</td><td>40%</td></tr>
</table></div>
</div></body></html>"""
                with open(os.path.join(OUTPUT_DIR, f"{prefix}_report.html"), "w", encoding="utf-8") as f:
                    f.write(html)
                logger.info(f"  HTML: {os.path.join(OUTPUT_DIR, f'{prefix}_report.html')}")
            except Exception as e:
                logger.warning(f"  组合 HTML 失败: {e}")

        except Exception as e:
            logger.warning(f"  组合绩效计算失败: {e}")

    return result_combo


def _equity_svg(equity: List[float], w: int = 900, h: int = 300) -> str:
    """生成权益曲线内联 SVG"""
    if len(equity) < 2:
        return "<p>数据不足</p>"
    arr = np.array(equity, dtype=float)
    y0, y1 = arr.min(), arr.max()
    pad = 20

    def sx(i):
        return pad + i / (len(arr) - 1) * (w - 2 * pad) if len(arr) > 1 else w / 2

    def sy(v):
        return h - pad - (v - y0) / (y1 - y0) * (h - 2 * pad) if y1 > y0 else h / 2

    pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(arr))
    return (
        f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto">'
        f'<polyline points="{pts}" fill="none" stroke="#f23645" stroke-width="2"/>'
        f'<text x="8" y="{h-8}" fill="#888" font-size="11">交易日</text>'
        f'<text x="8" y="14" fill="#888" font-size="11">权益</text>'
        f'</svg>'
    )


# ============================================================
#  Summary Comparison HTML
# ============================================================

def generate_summary_comparison(
    strategy_results: Dict[str, Dict],
    combo_result: Optional[Dict],
    loader: TulongDataLoader,
) -> None:
    """生成 5 策略 KPI 对比 HTML"""
    rows = []

    for name, res in strategy_results.items():
        m = res.get("metrics", {})
        trades = res.get("trades", [])

        # 交易统计
        n_trades = len(trades)
        win_trades = sum(1 for t in trades if t.get("pnl", 0) > 0) if trades else 0
        trade_win_rate = (win_trades / n_trades * 100) if n_trades > 0 else 0

        # 盈亏比 (基于每笔收益率)
        pnl_pcts = [t.get("pnl_pct", 0) for t in trades] if trades else []
        wins = [abs(p) for p in pnl_pcts if p > 0]
        losses = [abs(p) for p in pnl_pcts if p <= 0]
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # 平均持仓天数
        hold_days = []
        for t in trades:
            try:
                ed = datetime.strptime(str(t.get("entry_date", ""))[:10], "%Y-%m-%d")
                xd = datetime.strptime(str(t.get("exit_date", ""))[:10], "%Y-%m-%d")
                hold_days.append((xd - ed).days)
            except Exception:
                pass
        avg_hold = np.mean(hold_days) if hold_days else 0

        rows.append({
            "name": name,
            "total_return": m.get("total_return_pct", 0),
            "annual_return": m.get("annualized_return_pct", 0),
            "max_drawdown": m.get("max_drawdown_pct", 0),
            "sharpe": m.get("sharpe_ratio", 0),
            "sortino": m.get("sortino_ratio", 0),
            "calmar": m.get("calmar_ratio", 0),
            "win_rate": trade_win_rate,
            "pl_ratio": pl_ratio,
            "total_trades": n_trades,
            "avg_hold": avg_hold,
            "final_equity": m.get("final_equity", 0),
        })

    # 组合
    if combo_result is not None:
        combined = combo_result.get("combined", {})
        combo_ec = combined.get("equity_curve", pd.DataFrame())
        if len(combo_ec) > 0:
            try:
                cm = equity_metrics(
                    dates=combo_ec["date"].tolist(),
                    equity=combo_ec["equity"].tolist(),
                    rf=0.03,
                )
                rows.append({
                    "name": "portfolio_combo",
                    "total_return": combined.get("total_return_pct", 0),
                    "annual_return": round(cm.get("cagr", 0) * 100, 2),
                    "max_drawdown": round(cm.get("max_drawdown", 0) * 100, 2),
                    "sharpe": round(cm.get("sharpe", 0), 4),
                    "sortino": round(cm.get("sortino", 0), 4),
                    "calmar": round(cm.get("calmar", 0), 4),
                    "win_rate": 0,
                    "pl_ratio": 0,
                    "total_trades": 0,
                    "avg_hold": 0,
                    "final_equity": round(cm.get("final_equity", 0), 2),
                })
            except Exception:
                pass

    # 柱状图 SVG
    def _bar_svg(labels, values, color="#f23645", w=700, h=200):
        if not values:
            return "<p>无数据</p>"
        vmin, vmax = min(min(values), 0), max(max(values), 1)
        pad = 30
        bw = min(60, (w - 2 * pad) / len(labels) - 8)
        svg = f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto">'
        for i, (lbl, v) in enumerate(zip(labels, values)):
            x = pad + i * (bw + 8)
            bh = (v - vmin) / (vmax - vmin) * (h - 2 * pad) if vmax > vmin else 0
            y = h - pad - bh
            c = color if v >= 0 else "#1a9c48"
            svg += f'<rect x="{x}" y="{y}" width="{bw}" height="{max(bh,1)}" fill="{c}" opacity="0.8" rx="2"/>'
            svg += f'<text x="{x+bw/2}" y="{h-8}" fill="#888" font-size="10" text-anchor="middle">{lbl[:12]}</text>'
            svg += f'<text x="{x+bw/2}" y="{y-4}" fill="#333" font-size="10" text-anchor="middle">{v:.1f}</text>'
        svg += "</svg>"
        return svg

    labels = [r["name"] for r in rows]
    returns_vals = [r["total_return"] for r in rows]
    sharpes_vals = [r["sharpe"] for r in rows]
    mdd_vals = [abs(r["max_drawdown"]) for r in rows]

    svg_ret = _bar_svg(labels, returns_vals, "#d33")
    svg_shp = _bar_svg(labels, sharpes_vals, "#3a7bd5")
    svg_mdd = _bar_svg(labels, mdd_vals, "#e67e22")

    # 表格行
    table_rows = ""
    for r in rows:
        ret_color = "#d33" if r["total_return"] >= 0 else "#1a9c48"
        ann_color = "#d33" if r["annual_return"] >= 0 else "#1a9c48"
        table_rows += f"""<tr>
            <td><b>{r['name']}</b></td>
            <td style="color:{ret_color}">{r['total_return']:.2f}%</td>
            <td style="color:{ann_color}">{r['annual_return']:.2f}%</td>
            <td style="color:#e67e22">{r['max_drawdown']:.2f}%</td>
            <td>{r['sharpe']:.2f}</td>
            <td>{r['sortino']:.2f}</td>
            <td>{r['calmar']:.2f}</td>
            <td>{r['win_rate']:.1f}%</td>
            <td>{r['pl_ratio']:.2f}</td>
            <td>{r['total_trades']}</td>
            <td>{r['avg_hold']:.1f}</td>
            <td>{r['final_equity']:,.0f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>屠龙系统 5 策略 KPI 对比</title>
<style>
body {{ font-family:"Microsoft YaHei",sans-serif; background:#f5f6fa; margin:0; padding:24px; color:#1f2329; }}
.container {{ max-width:1200px; margin:0 auto; }}
h1 {{ font-size:22px; margin-bottom:4px; }}
.sub {{ color:#888; font-size:13px; margin-bottom:20px; }}
.card {{ background:#fff; border-radius:10px; padding:20px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.card h2 {{ font-size:16px; margin:0 0 12px; border-left:4px solid #f23645; padding-left:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th,td {{ padding:6px 8px; border-bottom:1px solid #eee; text-align:center; }}
th {{ background:#fafafa; font-weight:600; }}
tr:hover {{ background:#f0f4ff; }}
.charts {{ display:flex; flex-wrap:wrap; gap:16px; }}
.chart-box {{ flex:1; min-width:300px; background:#fff; border-radius:10px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.chart-box h3 {{ font-size:14px; margin:0 0 8px; }}
.disc {{ color:#999; font-size:12px; margin-top:20px; }}
</style>
</head>
<body>
<div class="container">
<h1>屠龙系统 多策略 KPI 对比总表</h1>
<div class="sub">生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 回测区间 {START_DATE} ~ {END_DATE} | 引擎 V4.2</div>

<div class="charts">
<div class="chart-box"><h3>总收益率 (%)</h3>{svg_ret}</div>
<div class="chart-box"><h3>夏普比率</h3>{svg_shp}</div>
<div class="chart-box"><h3>最大回撤 (%)</h3>{svg_mdd}</div>
</div>

<div class="card">
<h2>绩效总表</h2>
<div style="overflow-x:auto">
<table>
<tr>
<th>策略</th><th>总收益</th><th>年化收益</th><th>最大回撤</th><th>夏普</th><th>索提诺</th><th>卡玛</th>
<th>胜率</th><th>盈亏比</th><th>交易次数</th><th>平均持仓(天)</th><th>终值(元)</th>
</tr>
{table_rows}
</table>
</div>
</div>

<div class="card">
<h2>策略说明</h2>
<ul>
<li><b>bp+ep_core</b>: 基本面季度调仓，等权合成 ep+bp 排名，价格≤30元，Top20 等权(40%仓位)</li>
<li><b>v375_signal</b>: V375 概率≥0.65，每日最多3只，每只≤20%</li>
<li><b>p2_signal</b>: P2 概率阈值 0.70(牛)/0.74(熊)，最多5只(40%仓位)</li>
<li><b>composite_signal</b>: composite_score≥0.70 + ai_prob_5d≥0.50，最多5只等权</li>
<li><b>portfolio_combo</b>: 核心-卫星组合(bp+ep 40% + v375 20% + p2 40%)</li>
</ul>
</div>

<div class="disc">⚠️ 以上内容由AI基于回测引擎V4.2生成，仅供参考，不构成投资建议。</div>
</div>
</body>
</html>"""

    html_path = os.path.join(OUTPUT_DIR, "summary_comparison.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"对比总表: {html_path}")


# ============================================================
#  Optimization Plan (Markdown)
# ============================================================

def generate_optimization_plan(strategy_results: Dict, combo_result: Optional[Dict]) -> None:
    """基于回测结果生成优化方案 Markdown"""

    # 获取各策略指标
    def _get(k, name, default=0):
        r = strategy_results.get(name, {})
        return r.get("metrics", {}).get(k, default)

    md = f"""# 屠龙系统策略诊断与优化方案

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 回测区间: {START_DATE} ~ {END_DATE}
> 初始资金: {cfg.initial_capital:,.0f} 元

---

## 一、各策略绩效摘要

### bp+ep_core (基本面核心底仓)
- 总收益: {_get('total_return_pct', 'bp+ep_core'):+.2f}%
- 年化收益: {_get('annualized_return_pct', 'bp+ep_core'):+.2f}%
- 最大回撤: {_get('max_drawdown_pct', 'bp+ep_core'):.2f}%
- 夏普比率: {_get('sharpe_ratio', 'bp+ep_core'):.2f}
- 交易次数: {_get('total_trades', 'bp+ep_core')}
- 胜率: {_get('trade_win_rate_pct', 'bp+ep_core', 0):.1f}%

### v375_signal
- 总收益: {_get('total_return_pct', 'v375_signal'):+.2f}%
- 年化收益: {_get('annualized_return_pct', 'v375_signal'):+.2f}%
- 最大回撤: {_get('max_drawdown_pct', 'v375_signal'):.2f}%
- 夏普比率: {_get('sharpe_ratio', 'v375_signal'):.2f}
- 交易次数: {_get('total_trades', 'v375_signal')}
- 胜率: {_get('trade_win_rate_pct', 'v375_signal', 0):.1f}%

### p2_signal
- 总收益: {_get('total_return_pct', 'p2_signal'):+.2f}%
- 年化收益: {_get('annualized_return_pct', 'p2_signal'):+.2f}%
- 最大回撤: {_get('max_drawdown_pct', 'p2_signal'):.2f}%
- 夏普比率: {_get('sharpe_ratio', 'p2_signal'):.2f}
- 交易次数: {_get('total_trades', 'p2_signal')}
- 胜率: {_get('trade_win_rate_pct', 'p2_signal', 0):.1f}%

### composite_signal
- 总收益: {_get('total_return_pct', 'composite_signal'):+.2f}%
- 年化收益: {_get('annualized_return_pct', 'composite_signal'):+.2f}%
- 最大回撤: {_get('max_drawdown_pct', 'composite_signal'):.2f}%
- 夏普比率: {_get('sharpe_ratio', 'composite_signal'):.2f}
- 交易次数: {_get('total_trades', 'composite_signal')}
- 胜率: {_get('trade_win_rate_pct', 'composite_signal', 0):.1f}%

### portfolio_combo (核心-卫星组合)
"""
    if combo_result is not None:
        combined = combo_result.get("combined", {})
        combo_ec = combined.get("equity_curve", pd.DataFrame())
        if len(combo_ec) > 0:
            cm = equity_metrics(combo_ec["date"].tolist(), combo_ec["equity"].tolist(), rf=0.03)
            md += f"""- 总收益: {combined.get('total_return_pct', 0):+.2f}%
- 年化收益: {cm.get('cagr',0)*100:+.2f}%
- 最大回撤: {cm.get('max_drawdown',0)*100:.2f}%
- 夏普比率: {cm.get('sharpe',0):.2f}
- 索提诺比率: {cm.get('sortino',0):.2f}
- 卡玛比率: {cm.get('calmar',0):.2f}
"""
    else:
        md += """- 组合未生成或失败

"""

    md += """
---

## 二、短期微调

### 2.1 V375 信号入场阈值

| 参数 | 当前值 | 建议范围 | 预期效果 |
|------|--------|----------|----------|
| v375_prob 阈值 | 0.65 | 0.60 ~ 0.75 | 提高→减少交易提升胜率; 降低→增加机会 |
| 每日最大买入数 | 3 | 2 ~ 5 | 影响持仓分散度 |
| 单票上限 | 20% | 15% ~ 25% | 影响集中度风险 |

**验证标准**: 调参后夏普变化 > 0.1 视为有效

### 2.2 P2 信号牛熊阈值

| 参数 | 当前值 | 建议范围 |
|------|--------|----------|
| 牛市阈值 | 0.70 | 0.65 ~ 0.75 |
| 熊市阈值 | 0.74 | 0.70 ~ 0.80 |

**验证标准**: 分牛熊期回测，各自夏普最大化

### 2.3 止损参数

| 参数 | 当前值 | 建议范围 |
|------|--------|----------|
| stop_loss | -8% | -5% ~ -12% |
| trailing_stop | 峰值回撤 10% | 8% ~ 15% |

**验证标准**: 在最大回撤 ≤ 15% 前提下最大化 CAGR

---

## 三、中期规则升级

### 3.1 出场机制增强

1. **动态止损**: 基于 ATR(14) 倍数而非固定比例
2. **时间止损**: 持有超过 60 天仍未达预期则强制退出
3. **分档止盈**: 浮盈 10-30%→峰值回撤 10%止盈，>30%→峰值回撤 6%止盈

### 3.2 新增风险标签过滤

在信号生成层面加入:
- 商誉占比 > 30% 净资产 → 过滤
- 近 90 日有大额解禁 → 过滤
- 实控人近期减持 → 过滤
- 立案调查 / 问询函 → 过滤

### 3.3 板块集中度控制

- 单板块持仓 ≤ 总仓位 30%
- 关联板块合并计算控制

---

## 四、长期体系增强

### 4.1 板块情绪权重

将板块动量因子引入信号权重:
- 计算申万一级行业近 5/20 日动量
- 强势板块信号权重上浮 10-20%
- 弱势板块信号权重下调 20-30%

### 4.2 归因经验库联动选股

建立交易归因系统记录每笔交易:
- 板块 / 市值 / 动量 / 估值标签
- 统计各标签组合的胜率分布
- 高胜率标签组合获得额外权重加成

### 4.3 多周期共振

当前信号基于日线，可引入:
- 周线级别趋势过滤 (仅上升趋势开仓)
- 60 分钟级别择时 (优化入场时点)
- 跨周期信号一致性校验

---

## 五、量化验证标准

| 优化项 | 成功标准 | 观测指标 |
|--------|----------|----------|
| 参数调优 | 夏普 +0.1 且 MDD 不增 | Sharpe, MDD |
| 出场升级 | 盈亏比 +0.3 | P/L Ratio |
| 风险过滤 | MDD 降 3% 且 CAGR 不降 | MDD, CAGR |
| 板块权重 | 信息比 +0.15 | IR |
| 多周期 | 胜率 +5% | Win Rate |

---

*本方案由 AI 基于回测结果自动生成，仅供参考。*
"""

    md_path = os.path.join(OUTPUT_DIR, "optimization_plan.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"优化方案: {md_path}")


# ============================================================
#  Main Entry
# ============================================================

if __name__ == "__main__":
    t0 = datetime.now()
    logger.info(f"""
{'='*60}
屠龙系统 V4.2 模块化回测启动
{'='*60}
回测区间: {START_DATE} ~ {END_DATE}
输出目录: {OUTPUT_DIR}
数据源: dragon_quant.db + dragon_quant_qfq.db
{'='*60}
""")

    # 初始化数据加载器
    loader = TulongDataLoader(DB_PATH, QFQ_PATH)

    # 策略配置: (名称, 信号函数, 调仓频率)
    strategies = [
        ("bp+ep_core", bp_ep_signal, "quarterly"),
        ("v375_signal", v375_signal, "daily"),
        ("p2_signal", p2_signal, "daily"),
        ("composite_signal", composite_signal, "daily"),
    ]

    # 运行各策略
    strategy_results = {}
    for name, sig_func, freq in strategies:
        try:
            result = run_single_backtest(name, sig_func, loader, freq)
            strategy_results[name] = result
        except Exception as e:
            logger.error(f"策略 {name} 回测失败: {e}", exc_info=True)
            strategy_results[name] = {
                "metrics": {"total_return_pct": 0, "error": str(e)},
                "equity_curve": pd.DataFrame(),
                "trades": [],
            }

    # 运行组合
    combo_result = None
    try:
        combo_result = run_portfolio_backtest(loader, strategy_results)
    except Exception as e:
        logger.error(f"组合策略失败: {e}", exc_info=True)

    # 生成对比总表
    try:
        generate_summary_comparison(strategy_results, combo_result, loader)
    except Exception as e:
        logger.error(f"对比总表生成失败: {e}", exc_info=True)

    # 生成优化方案
    try:
        generate_optimization_plan(strategy_results, combo_result)
    except Exception as e:
        logger.error(f"优化方案生成失败: {e}", exc_info=True)

    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"""
{'='*60}
回测全部完成!
耗时: {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)
输出: {OUTPUT_DIR}
{'='*60}
""")

    # 列出输出文件
    logger.info("\n生成的文件:")
    for f in sorted(os.listdir(OUTPUT_DIR)):
        fpath = os.path.join(OUTPUT_DIR, f)
        sz = os.path.getsize(fpath)
        logger.info(f"  {f} ({sz/1024:.1f} KB)")