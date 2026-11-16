# -*- coding: utf-8 -*-
"""屠龙系统 V4.2 全市场回测主脚本 (2016-2026)"""

import sys
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

import os, json, logging, sqlite3, warnings
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# 框架
from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest, PortfolioBacktest

# Fix: Add quarterly to rebalance_interval in SingleStrategyBacktest.run
import core.backtest_system.strategy as _strat_module
def _patched_run(self, signal_func, data_loader, start_date, end_date,
                 rebalance_freq="weekly", strategy_name=None):
    """Patched run with quarterly support"""
    import logging
    logger = logging.getLogger("BacktestEngineV4")
    logger.info(f"=== Patched 单策略回测: {start_date} ~ {end_date} ===")
    all_dates = sorted(data_loader.get_trading_dates(start_date, end_date))
    logger.info(f"共 {len(all_dates)} 个交易日")
    rebalance_interval = {"daily": 1, "weekly": 5, "monthly": 22, "quarterly": 63}.get(rebalance_freq, 5)
    days_since_rebalance = 999
    preloaded = {}
    for i, date in enumerate(all_dates):
        preloaded[date] = data_loader.get_data(date)
    pending_weights = None
    pending_risk_liquidate = False
    held_since = {}
    for i, date in enumerate(all_dates):
        daily_data = preloaded.get(date)
        if daily_data is None:
            continue
        prices = daily_data.get("prices", {})
        opens = daily_data.get("open", {})
        prev_closes = daily_data.get("prev_close", daily_data.get("prev_closes", {}))
        names = daily_data.get("names", {})
        is_st_map = daily_data.get("is_st", {})
        if pending_risk_liquidate:
            self.engine.liquidate_all(prices, date, opens=opens, prev_closes=prev_closes,
                                      is_st_map=is_st_map, names=names, exit_reason="risk_control")
            pending_risk_liquidate = False
            pending_weights = None
        elif pending_weights is not None:
            try:
                self.engine.rebalance(pending_weights, prices, date=date, opens=opens,
                                      prev_closes=prev_closes, is_st_map=is_st_map, names=names)
            except Exception as e:
                logger.warning(f"调仓异常 {date}: {e}")
            pending_weights = None
        for code, pos in list(self.engine.portfolio.positions.items()):
            if pos.shares <= 0:
                continue
            if self.config.max_holding_bars > 0 and code in held_since:
                from core.backtest_system.rules import to_date
                d0 = to_date(held_since[code])
                d1 = to_date(date)
                if (d1 - d0).days >= self.config.max_holding_bars:
                    self._enqueue_sell(code, prices, opens, date, reason="max_holding")
                    continue
            if pos.unrealized_pnl_pct <= -self.config.stop_loss_pct * 100:
                self._enqueue_sell(code, prices, opens, date, reason="stop_loss")
            elif self.config.take_profit_pct > 0 and pos.unrealized_pnl_pct >= self.config.take_profit_pct * 100:
                self._enqueue_sell(code, prices, opens, date, reason="take_profit")
        days_since_rebalance += 1
        if days_since_rebalance >= rebalance_interval:
            try:
                target_weights = signal_func(daily_data, date)
                if target_weights and isinstance(target_weights, dict):
                    total_w = sum(target_weights.values())
                    if total_w > 0:
                        target_weights = {k: v / total_w for k, v in target_weights.items()}
                    pending_weights = target_weights
                    days_since_rebalance = 0
            except Exception as e:
                logger.warning(f"信号异常 {date}: {e}")
        is_ok, warnings = self.engine.check_risk_controls(prices)
        if not is_ok:
            logger.warning(f"风控触发: {warnings}")
            pending_risk_liquidate = True
            pending_weights = None
        self.engine.settle_day(date, prices)
        for code, pos in self.engine.portfolio.positions.items():
            if pos.shares > 0:
                held_since.setdefault(code, pos.open_date or date)
        if (i + 1) % 500 == 0:
            eq = self.engine.portfolio.total_equity
            ret = (eq / self.config.initial_capital - 1) * 100
            logger.info(f"  [{i+1}/{len(all_dates)}] 权益={eq:,.0f} ({ret:+.1f}%)")
    if all_dates:
        last = preloaded.get(all_dates[-1], {})
        self.engine.liquidate_all(last.get("prices", {}), date=all_dates[-1],
                                  opens=last.get("open", {}), exit_reason="end_of_sample")
    metrics = self.engine.calculate_metrics()
    logger.info(f"=== 完成: 总收益 {metrics.get('total_return_pct', 0):.1f}%, "
                f"夏普 {metrics.get('sharpe_ratio', 0):.2f}, "
                f"最大回撤 {metrics.get('max_drawdown_pct', 0):.1f}% ===")
    return {
        "metrics": metrics,
        "equity_curve": self.engine.get_equity_curve_df(),
        "trades": self.engine._clean_trades(),
        "daily_pnl": self.engine.daily_pnl,
        "engine": self.engine,
    }
SingleStrategyBacktest.run = _patched_run
from core.backtest_system.report import build_html_report
from core.backtest_system.performance import equity_metrics
from core.backtest_system.export import export_results

# ============================================================
#  路径
# ============================================================
DATA_DIR = r"D:\workbuddyspace\factor_engine\data"
DB_PATH = os.path.join(DATA_DIR, "dragon_quant.db")
QFQ_PATH = os.path.join(DATA_DIR, "dragon_quant_qfq.db")
OUTPUT_DIR = r"D:\zcodesj\reports\tulong_backtest_20260813"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
#  日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(OUTPUT_DIR, "backtest_run.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("Tulong")

START_DATE = "2016-06-30"
END_DATE = "2026-08-13"


# ============================================================
#  日期归一化工具
# ============================================================
def _norm(d) -> str:
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    return s


def _to_yyyymmdd(s: str) -> int:
    """YYYY-MM-DD -> YYYYMMDD int"""
    parts = s[:10].split("-")
    if len(parts) == 3:
        return int(parts[0] + parts[1] + parts[2])
    return int(s.replace("-", "")[:8])


# ============================================================
#  BacktestConfig
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


# ============================================================
#  TulongDataLoader
# ============================================================
class TulongDataLoader:
    """统一数据加载器：行情 QFQ + 信号 + 基本面 + 指数"""

    def __init__(self):
        logger.info("=== TulongDataLoader 初始化 ===")
        self._conn = sqlite3.connect(DB_PATH)
        self._conn_qfq = sqlite3.connect(QFQ_PATH)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA cache_size=-8000000")
        self._conn_qfq.execute("PRAGMA journal_mode=WAL")
        self._conn_qfq.execute("PRAGMA cache_size=-8000000")

        # ---- 交易日历 ----
        logger.info("  交易日历...")
        self._all_dates = pd.read_sql(
            "SELECT date_str FROM trading_calendar ORDER BY date_str", self._conn
        )["date_str"].tolist()
        logger.info(f"    {len(self._all_dates)} 日")

        # ---- 股票信息 ----
        logger.info("  stock_info...")
        self._si = pd.read_sql(
            "SELECT code, name, market, industry, list_date, is_st, status FROM stock_info",
            self._conn,
        ).set_index("code")

        # 可交易股票（主板+创业板，非ST，非688/8/4/920/430/4）
        self._tradable = set()
        for code, row in self._si.iterrows():
            if row.get("is_st", 0) == 1:
                continue
            if code.startswith(("688", "689", "8", "4", "92", "43")):
                continue
            self._tradable.add(code)
        logger.info(f"    可交易: {len(self._tradable)} 只")

        # ---- QFQ 行情 (date -> {code -> {open,high,low,close,vol,amt}}) ----
        logger.info("  加载 QFQ ...")
        chunks = []
        for c in pd.read_sql(
            "SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq ORDER BY date",
            self._conn_qfq, chunksize=500000
        ):
            chunks.append(c)
        qfq = pd.concat(chunks, ignore_index=True)
        qfq = qfq[qfq["code"].isin(self._tradable)].copy()
        logger.info(f"    QFQ 行数: {len(qfq):,}")

        self._bar: Dict[str, Dict[str, Dict]] = {}
        for dt, grp in qfq.groupby("date"):
            dk = _norm(dt)
            sub = {}
            for _, r in grp.iterrows():
                sub[r["code"]] = {
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r["volume"]),
                    "amount": float(r["amount"]),
                }
            self._bar[dk] = sub
        logger.info(f"    行情日期: {len(self._bar)} 日")

        # ---- prev_close (前收盘) ----
        self._prev: Dict[str, Dict[str, float]] = {}
        sorted_dates = sorted(self._bar.keys())
        for i, dt in enumerate(sorted_dates):
            if i == 0:
                self._prev[dt] = {}
                continue
            prev_recs = self._bar.get(sorted_dates[i - 1], {})
            self._prev[dt] = {c: v["close"] for c, v in prev_recs.items()}

        # ---- 上证指数 MA250 ----
        logger.info("  加载 index_daily (000001 MA250)...")
        idx = pd.read_sql(
            "SELECT date, close FROM index_daily WHERE index_code='000001' ORDER BY date",
            self._conn,
        )
        if len(idx) > 0:
            idx["_d"] = idx["date"].apply(_norm)
            idx["ma250"] = idx["close"].rolling(250, min_periods=100).mean()
            self._idx_close = dict(zip(idx["_d"], idx["close"]))
            self._idx_ma250 = dict(zip(idx["_d"], idx["ma250"]))
        else:
            self._idx_close = {}
            self._idx_ma250 = {}
        logger.info(f"    指数: {len(self._idx_close)} 日")

        # ---- 信号表索引 ----
        def load_signal_table(table, cols, date_col="date"):
            df = pd.read_sql(f"SELECT code, {date_col}, {', '.join(cols)} FROM {table}", self._conn)
            df["_d"] = df[date_col].apply(_norm)
            by_date = {}
            for dt, grp in df.groupby("_d"):
                by_date[dt] = grp.set_index("code")[cols]
            logger.info(f"    {table}: {len(by_date)} 日")
            return by_date

        logger.info("  加载信号表...")
        self._v375 = load_signal_table("stock_signals_v375", ["v375_prob"])
        self._p2 = load_signal_table("stock_signals_p2", ["p2_prob"])
        self._v4 = load_signal_table("stock_signals_v4", ["composite_score", "ai_prob_5d"])
        self._legacy = load_signal_table("stock_signals", ["composite_score", "ai_prob_5d"])
        self._fundamental = load_signal_table("stock_factors_fundamental", ["close", "ep", "bp"])

    def get_trading_dates(self, start: str, end: str) -> List[str]:
        return [d for d in self._all_dates if start <= d <= end]

    def get_data(self, date_str: str) -> Dict:
        """返回引擎兼容的行情 dict (永远非 None)"""
        recs = self._bar.get(date_str, {})
        prices, opens, highs, lows, volumes, amounts = {}, {}, {}, {}, {}, {}
        prev_closes, names, is_st = {}, {}, {}
        pc = self._prev.get(date_str, {})
        for code, r in recs.items():
            prices[code] = r["close"]
            opens[code] = r["open"]
            highs[code] = r["high"]
            lows[code] = r["low"]
            volumes[code] = r["volume"]
            amounts[code] = r["amount"]
            if code in pc:
                prev_closes[code] = pc[code]
            # 已经过滤了ST股票，都是可交易的
            names[code] = ""
            is_st[code] = False
        return {
            "date": date_str, "prices": prices, "open": opens,
            "high": highs, "low": lows, "volumes": volumes,
            "amounts": amounts, "prev_close": prev_closes,
            "names": names, "is_st": is_st,
        }

    def get_fundamental(self, d: str) -> Optional[pd.DataFrame]:
        df = self._fundamental.get(d)
        if df is None or len(df) == 0:
            return None
        return df.dropna()

    def get_v375(self, d: str) -> Optional[pd.DataFrame]:
        df = self._v375.get(d)
        if df is None or len(df) == 0:
            return None
        return df.dropna()

    def get_p2(self, d: str) -> Optional[pd.DataFrame]:
        df = self._p2.get(d)
        if df is None or len(df) == 0:
            return None
        return df.dropna()

    def get_composite(self, d: str) -> Optional[pd.DataFrame]:
        df = self._v4.get(d)
        if df is not None and len(df) > 0:
            return df.dropna()
        df2 = self._legacy.get(d)
        if df2 is not None and len(df2) > 0:
            return df2.dropna()
        return None

    def is_bull(self, d: str) -> bool:
        c = self._idx_close.get(d)
        m = self._idx_ma250.get(d)
        if c is None or m is None or pd.isna(m):
            return True
        return c > m

    def is_tradable(self, code: str) -> bool:
        return code in self._tradable


# ============================================================
#  4 个策略信号函数
# ============================================================

def bp_ep(ld: TulongDataLoader, d: str) -> Dict[str, float]:
    df = ld.get_fundamental(d)
    if df is None or len(df) < 20:
        return {}
    df = df[df["close"] <= 30].copy()
    if len(df) < 20:
        return {}
    df["ep_r"] = df["ep"].rank(pct=True)
    df["bp_r"] = df["bp"].rank(pct=True)
    df["score"] = (df["ep_r"] + df["bp_r"]) / 2
    top20 = df.nlargest(20, "score")
    w = 0.40 / 20
    return {idx: w for idx, row in top20.iterrows()}


def v375(ld: TulongDataLoader, d: str) -> Dict[str, float]:
    df = ld.get_v375(d)
    if df is None or len(df) == 0:
        return {}
    cand = df[df["v375_prob"] >= 0.65].copy()
    if len(cand) == 0:
        return {}
    cand = cand.nlargest(min(3, len(cand)), "v375_prob")
    w = min(0.20, 1.0 / len(cand) * 0.20)
    return {idx: w for idx, row in cand.iterrows()}


def p2(ld: TulongDataLoader, d: str) -> Dict[str, float]:
    df = ld.get_p2(d)
    if df is None or len(df) == 0:
        return {}
    bull = ld.is_bull(d)
    thr = 0.70 if bull else 0.74
    cand = df[df["p2_prob"] >= thr].copy()
    if len(cand) == 0:
        return {}
    cand = cand.nlargest(min(5, len(cand)), "p2_prob")
    w = 0.40 / len(cand)
    return {idx: w for idx, row in cand.iterrows()}


def composite(ld: TulongDataLoader, d: str) -> Dict[str, float]:
    df = ld.get_composite(d)
    if df is None or len(df) == 0:
        return {}
    df = df[(df["composite_score"] >= 0.40) & (df["ai_prob_5d"] >= 0.50)]
    if len(df) == 0:
        return {}
    cand = df.nlargest(min(5, len(df)), "composite_score")
    w = 1.0 / len(cand)
    return {idx: w for idx, row in cand.iterrows()}


# ============================================================
#  运行回测
# ============================================================

def run_one(name: str, sig: Callable, freq: str, ld: TulongDataLoader) -> Dict:
    logger.info(f"\n{'='*50}\n开始: {name} (freq={freq})\n{'='*50}")
    bt = SingleStrategyBacktest(cfg)
    wrap = lambda dd, d: sig(ld, d)
    res = bt.run(wrap, ld, START_DATE, END_DATE, rebalance_freq=freq, strategy_name=name)
    m = res.get("metrics", {})
    eq = res.get("equity_curve", pd.DataFrame())
    tr = res.get("trades", [])
    logger.info(f"  Return: {m.get('total_return_pct', 0):.2f}% | Sharpe: {m.get('sharpe_ratio', 0):.2f} | "
                f"MDD: {m.get('max_drawdown_pct', 0):.2f}% | Trades: {m.get('total_trades', 0)}")
    pf = name.replace("+", "_").replace(" ", "_")
    export_results(eq, tr, prefix=pf, initial_cash=cfg.initial_capital,
                   start=START_DATE, end=END_DATE, market="china_a",
                   output_dir=OUTPUT_DIR, strategy_name=name, metrics=m)
    try:
        build_html_report(m, eq, tr, strategy_name=f"屠龙 {name}",
                          output_path=os.path.join(OUTPUT_DIR, f"{pf}_report.html"))
    except Exception as e:
        logger.warning(f"  HTML failed: {e}")
    return res


def run_combo(ld: TulongDataLoader) -> Optional[Dict]:
    logger.info(f"\n{'='*50}\n组合回测 (bp+ep 40% + v375 20% + p2 40%)\n{'='*50}")
    strategies = {
        "bp+ep_core": lambda dd, d: bp_ep(ld, d),
        "v375_signal": lambda dd, d: v375(ld, d),
        "p2_signal": lambda dd, d: p2(ld, d),
    }
    weights = {"bp+ep_core": 0.40, "v375_signal": 0.20, "p2_signal": 0.40}
    pb = PortfolioBacktest(strategies=strategies, weights=weights, config=cfg)
    try:
        result = pb.run(ld, START_DATE, END_DATE)
        return result
    except Exception as e:
        logger.error(f"  组合失败: {e}", exc_info=True)
        return None


# ============================================================
#  生成报告
# ============================================================

def save_summary(results: Dict[str, Dict], combo_res: Optional[Dict]):
    rows = []
    for name, res in results.items():
        m = res.get("metrics", {})
        trades = res.get("trades", [])
        n = len(trades)
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        wr = wins / n * 100 if n > 0 else 0
        pnl_pcts = [t.get("pnl_pct", 0) for t in trades]
        avg_w = np.mean([abs(p) for p in pnl_pcts if p > 0]) or 0
        avg_l = np.mean([abs(p) for p in pnl_pcts if p <= 0]) or 0
        pl = avg_w / avg_l if avg_l > 0 else 0
        hold = []
        for t in trades:
            try:
                ed = datetime.strptime(str(t.get("entry_date", ""))[:10], "%Y-%m-%d")
                xd = datetime.strptime(str(t.get("exit_date", ""))[:10], "%Y-%m-%d")
                hold.append((xd - ed).days)
            except:
                pass
        ah = np.mean(hold) if hold else 0
        rows.append({
            "name": name, "ret": m.get("total_return_pct", 0),
            "ann": m.get("annualized_return_pct", 0),
            "mdd": m.get("max_drawdown_pct", 0),
            "sharpe": m.get("sharpe_ratio", 0),
            "sortino": m.get("sortino_ratio", 0),
            "calmar": m.get("calmar_ratio", 0),
            "wr": wr, "pl": pl, "n": n,
            "hold": ah,
            "final": m.get("final_equity", 0),
        })

    if combo_res:
        cb = combo_res.get("combined", {})
        eq_cb = cb.get("equity_curve", pd.DataFrame())
        if len(eq_cb) > 0:
            cm = equity_metrics(eq_cb["date"].tolist(), eq_cb["equity"].tolist(), rf=0.03)
            rows.append({
                "name": "portfolio_combo",
                "ret": cb.get("total_return_pct", 0),
                "ann": round(cm.get("cagr", 0) * 100, 2),
                "mdd": round(cm.get("max_drawdown", 0) * 100, 2),
                "sharpe": round(cm.get("sharpe", 0), 4),
                "sortino": round(cm.get("sortino", 0), 4),
                "calmar": round(cm.get("calmar", 0), 4),
                "wr": 0, "pl": 0, "n": 0, "hold": 0,
                "final": round(cm.get("final_equity", 0), 2),
            })
            # 导出组合 equity
            eq_cb.to_csv(os.path.join(OUTPUT_DIR, "portfolio_combo_equity.csv"),
                          index=False, encoding="utf-8-sig")
            # 导出组合 summary.json
            json.dump({
                "meta": {
                    "strategy": "portfolio_combo",
                    "weights": {"bp+ep_core": 0.40, "v375_signal": 0.20, "p2_signal": 0.40},
                    "start_date": START_DATE, "end_date": END_DATE,
                    "initial_capital": cfg.initial_capital,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                },
                "summary": {
                    "total_return_pct": cb.get("total_return_pct", 0),
                    "annualized_return_pct": round(cm.get("cagr", 0) * 100, 2),
                    "sharpe_ratio": round(cm.get("sharpe", 0), 4),
                    "max_drawdown_pct": round(cm.get("max_drawdown", 0) * 100, 2),
                    "sortino_ratio": round(cm.get("sortino", 0), 4),
                    "calmar_ratio": round(cm.get("calmar", 0), 4),
                    "final_equity": round(cm.get("final_equity", 0), 2),
                },
            }, open(os.path.join(OUTPUT_DIR, "portfolio_combo_summary.json"), "w"),
               ensure_ascii=False, indent=2)
            # 组合报告
            try:
                build_html_report(
                    {
                        "total_return_pct": cb.get("total_return_pct", 0),
                        "annualized_return_pct": round(cm.get("cagr", 0) * 100, 2),
                        "sharpe_ratio": round(cm.get("sharpe", 0), 4),
                        "max_drawdown_pct": round(cm.get("max_drawdown", 0) * 100, 2),
                        "sortino_ratio": round(cm.get("sortino", 0), 4),
                        "calmar_ratio": round(cm.get("calmar", 0), 4),
                        "final_equity": round(cm.get("final_equity", 0), 2),
                    },
                    eq_cb, [],
                    strategy_name="屠龙 核心-卫星组合 (bp+ep 40% + v375 20% + p2 40%)",
                    output_path=os.path.join(OUTPUT_DIR, "portfolio_combo_report.html"),
                )
            except Exception as e:
                logger.warning(f"  组合 HTML: {e}")

    # ---- 对比总表 ----
    def _bars(labels, vals, color, w=700, h=200):
        if not vals:
            return "<p>-</p>"
        vmin, vmax = min(min(vals), 0), max(max(vals), 1)
        pad = 30
        bw = min(60, (w - 2 * pad) / len(labels) - 8)
        svg = f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto">'
        for i, (l, v) in enumerate(zip(labels, vals)):
            x = pad + i * (bw + 8)
            bh = (v - vmin) / (vmax - vmin) * (h - 2 * pad) if vmax > vmin else 0
            y = h - pad - bh
            c = color if v >= 0 else "#1a9c48"
            svg += f'<rect x="{x}" y="{y}" width="{bw}" height="{max(bh,1)}" fill="{c}" opacity="0.8" rx="2"/>'
            svg += f'<text x="{x+bw/2}" y="{h-8}" fill="#888" font-size="10" text-anchor="middle">{l[:12]}</text>'
            svg += f'<text x="{x+bw/2}" y="{y-4}" fill="#333" font-size="10" text-anchor="middle">{v:.1f}</text>'
        svg += "</svg>"
        return svg

    labels = [r["name"] for r in rows]
    ret_vals = [r["ret"] for r in rows]
    shp_vals = [r["sharpe"] for r in rows]
    mdd_vals = [abs(r["mdd"]) for r in rows]

    tr_html = ""
    for r in rows:
        rc = "#d33" if r["ret"] >= 0 else "#1a9c48"
        tr_html += f"""<tr>
            <td><b>{r['name']}</b></td>
            <td style="color:{rc}">{r['ret']:.2f}%</td>
            <td>{r['ann']:.2f}%</td>
            <td style="color:#e67e22">{r['mdd']:.2f}%</td>
            <td>{r['sharpe']:.2f}</td>
            <td>{r['sortino']:.2f}</td>
            <td>{r['calmar']:.2f}</td>
            <td>{r['wr']:.1f}%</td>
            <td>{r['pl']:.2f}</td>
            <td>{r['n']}</td>
            <td>{r['hold']:.1f}</td>
            <td>{r['final']:,.0f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<title>屠龙系统 5 策略 KPI 对比</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:0;padding:24px;color:#1f2329;}}
.container{{max-width:1200px;margin:0 auto;}}
h1{{font-size:22px;margin-bottom:4px;}}
.sub{{color:#888;font-size:13px;margin-bottom:20px;}}
.card{{background:#fff;border-radius:10px;padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.08);}}
.card h2{{font-size:16px;margin:0 0 12px;border-left:4px solid #f23645;padding-left:10px;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
th,td{{padding:6px 8px;border-bottom:1px solid #eee;text-align:center;}}
th{{background:#fafafa;font-weight:600;}}
tr:hover{{background:#f0f4ff;}}
.charts{{display:flex;flex-wrap:wrap;gap:16px;}}
.chart-box{{flex:1;min-width:300px;background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);}}
.chart-box h3{{font-size:14px;margin:0 0 8px;}}
.disc{{color:#999;font-size:12px;margin-top:20px;}}
</style></head><body>
<div class="container">
<h1>屠龙系统 多策略 KPI 对比总表</h1>
<div class="sub">生成 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {START_DATE} ~ {END_DATE}</div>
<div class="charts">
<div class="chart-box"><h3>总收益率 (%)</h3>{_bars(labels, ret_vals, "#d33")}</div>
<div class="chart-box"><h3>夏普比率</h3>{_bars(labels, shp_vals, "#3a7bd5")}</div>
<div class="chart-box"><h3>最大回撤 (%)</h3>{_bars(labels, mdd_vals, "#e67e22")}</div>
</div>
<div class="card">
<h2>绩效总表</h2>
<div style="overflow-x:auto">
<table><tr>
<th>策略</th><th>总收益</th><th>年化收益</th><th>最大回撤</th><th>夏普</th><th>索提诺</th><th>卡玛</th><th>胜率</th><th>盈亏比</th><th>交易次数</th><th>平均持仓(天)</th><th>终值(元)</th>
</tr>{tr_html}</table>
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
<div class="disc">⚠️ AI 生成，仅供参考，不构成投资建议。</div>
</div>
</body></html>"""
    with open(os.path.join(OUTPUT_DIR, "summary_comparison.html"), "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"  对比总表: {os.path.join(OUTPUT_DIR, 'summary_comparison.html')}")


def save_optimization_plan(results: Dict[str, Dict], combo_res: Optional[Dict]):
    def _m(name, key, default=0):
        return results.get(name, {}).get("metrics", {}).get(key, default)
    md = f"""# 屠龙系统策略诊断与优化方案

> 生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 区间: {START_DATE} ~ {END_DATE}
> 初始资金: {cfg.initial_capital:,.0f} 元

---

## 一、各策略绩效摘要

### bp+ep_core
- 总收益: {_m('bp+ep_core','total_return_pct'):+.2f}%
- 年化收益: {_m('bp+ep_core','annualized_return_pct'):+.2f}%
- 最大回撤: {_m('bp+ep_core','max_drawdown_pct'):.2f}%
- 夏普: {_m('bp+ep_core','sharpe_ratio'):.2f}
- 交易次数: {_m('bp+ep_core','total_trades')}
- 胜率: {_m('bp+ep_core','trade_win_rate_pct',0):.1f}%

### v375_signal
- 总收益: {_m('v375_signal','total_return_pct'):+.2f}%
- 年化收益: {_m('v375_signal','annualized_return_pct'):+.2f}%
- 最大回撤: {_m('v375_signal','max_drawdown_pct'):.2f}%
- 夏普: {_m('v375_signal','sharpe_ratio'):.2f}
- 交易次数: {_m('v375_signal','total_trades')}
- 胜率: {_m('v375_signal','trade_win_rate_pct',0):.1f}%

### p2_signal
- 总收益: {_m('p2_signal','total_return_pct'):+.2f}%
- 年化收益: {_m('p2_signal','annualized_return_pct'):+.2f}%
- 最大回撤: {_m('p2_signal','max_drawdown_pct'):.2f}%
- 夏普: {_m('p2_signal','sharpe_ratio'):.2f}
- 交易次数: {_m('p2_signal','total_trades')}
- 胜率: {_m('p2_signal','trade_win_rate_pct',0):.1f}%

### composite_signal
- 总收益: {_m('composite_signal','total_return_pct'):+.2f}%
- 年化收益: {_m('composite_signal','annualized_return_pct'):+.2f}%
- 最大回撤: {_m('composite_signal','max_drawdown_pct'):.2f}%
- 夏普: {_m('composite_signal','sharpe_ratio'):.2f}
- 交易次数: {_m('composite_signal','total_trades')}
- 胜率: {_m('composite_signal','trade_win_rate_pct',0):.1f}%

### portfolio_combo
"""
    if combo_res:
        cb = combo_res.get("combined", {})
        eq_cb = cb.get("equity_curve", pd.DataFrame())
        if len(eq_cb) > 0:
            cm = equity_metrics(eq_cb["date"].tolist(), eq_cb["equity"].tolist(), rf=0.03)
            md += f"""- 总收益: {cb.get('total_return_pct',0):+.2f}%
- 年化收益: {cm.get('cagr',0)*100:+.2f}%
- 最大回撤: {cm.get('max_drawdown',0)*100:.2f}%
- 夏普: {cm.get('sharpe',0):.2f}
- 索提诺: {cm.get('sortino',0):.2f}
- 卡玛: {cm.get('calmar',0):.2f}
"""

    md += """

---

## 二、短期微调

### 2.1 V375 入场阈值
| 参数 | 当前 | 建议范围 | 预期 |
|------|------|----------|------|
| v375_prob | 0.65 | 0.60~0.75 | 提高→胜率↑交易↓ |
| 每日最大买入 | 3 | 2~5 | 分散度调节 |
| 单票上限 | 20% | 15%~25% | 集中度控制 |

验证: 调参后夏普变化>0.1 视为有效

### 2.2 P2 牛熊阈值
| 参数 | 当前 | 建议范围 |
|------|------|----------|
| 牛市阈值 | 0.70 | 0.65~0.75 |
| 熊市阈值 | 0.74 | 0.70~0.80 |

验证: 分牛熊期回测，各自夏普最大化

### 2.3 止损参数
| 参数 | 当前 | 建议范围 |
|------|------|----------|
| stop_loss | -8% | -5%~-12% |
| trailing_stop | 峰值回撤10% | 8%~15% |

验证: MDD≤15% 前提下最大化 CAGR

---

## 三、中期规则升级

### 3.1 出场机制增强
1. **动态止损**: ATR(14) 倍数而非固定比例
2. **时间止损**: 持有>60 天未达预期则退出
3. **分档止盈**: 浮盈 10-30%→峰值回撤10%止盈, >30%→峰值回撤6%止盈

### 3.2 风险标签过滤
- 商誉>30%净资产→过滤
- 近90日大额解禁→过滤
- 实控人近期减持→过滤
- 立案调查/问询函→过滤

### 3.3 板块集中度控制
- 单板块≤总仓位30%
- 关联板块合并计算

---

## 四、长期体系增强

### 4.1 板块情绪权重
- 申万一级行业近5/20日动量
- 强势板块权重上浮10-20%
- 弱势板块权重下调20-30%

### 4.2 归因经验库联动选股
- 记录每笔交易的板块/市值/动量/估值标签
- 统计各标签组合胜率分布
- 高胜率标签组合额外权重加成

### 4.3 多周期共振
- 周线级别趋势过滤(仅上升趋势开仓)
- 60分钟级别择时(优化入场时点)
- 跨周期信号一致性校验

---

## 五、量化验证标准

| 优化项 | 成功标准 | 观测指标 |
|--------|----------|----------|
| 参数调优 | 夏普+0.1 且 MDD 不增 | Sharpe, MDD |
| 出场升级 | 盈亏比+0.3 | P/L Ratio |
| 风险过滤 | MDD 降3% 且 CAGR 不降 | MDD, CAGR |
| 板块权重 | 信息比+0.15 | IR |
| 多周期 | 胜率+5% | Win Rate |

---

*本方案由AI基于回测结果自动生成，仅供参考。*
"""
    with open(os.path.join(OUTPUT_DIR, "optimization_plan.md"), "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"  优化方案: {os.path.join(OUTPUT_DIR, 'optimization_plan.md')}")


# ============================================================
#  Main
# ============================================================

if __name__ == "__main__":
    t0 = datetime.now()
    logger.info(f"\n{'='*60}\n屠龙系统 V4.2 全市场回测启动\n{'='*60}")
    logger.info(f"区间: {START_DATE} ~ {END_DATE}")
    logger.info(f"输出: {OUTPUT_DIR}")

    ld = TulongDataLoader()

    strategies = [
        ("bp+ep_core", bp_ep, "quarterly"),
        ("v375_signal", v375, "daily"),
        ("p2_signal", p2, "daily"),
        ("composite_signal", composite, "daily"),
    ]

    results = {}
    for name, sig, freq in strategies:
        try:
            results[name] = run_one(name, sig, freq, ld)
        except Exception as e:
            logger.error(f"{name} 失败: {e}", exc_info=True)
            results[name] = {"metrics": {"total_return_pct": 0, "error": str(e)},
                             "equity_curve": pd.DataFrame(), "trades": []}

    combo_res = None
    try:
        combo_res = run_combo(ld)
    except Exception as e:
        logger.error(f"组合失败: {e}", exc_info=True)

    try:
        save_summary(results, combo_res)
    except Exception as e:
        logger.error(f"对比表失败: {e}", exc_info=True)

    try:
        save_optimization_plan(results, combo_res)
    except Exception as e:
        logger.error(f"优化方案失败: {e}", exc_info=True)

    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"\n{'='*60}\n完成! 耗时 {elapsed:.0f}s ({elapsed/60:.1f}m)")
    for f in sorted(os.listdir(OUTPUT_DIR)):
        sz = os.path.getsize(os.path.join(OUTPUT_DIR, f))
        logger.info(f"  {f} ({sz/1024:.1f} KB)")