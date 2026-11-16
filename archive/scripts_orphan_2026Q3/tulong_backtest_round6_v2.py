# -*- coding: utf-8 -*-
"""
屠龙 round6 二轮迭代复测（变体 V2）
===================================
基线: round6 (2016-2026, 2579交易日, QFQ, 10万本金)
优化项:
  S1 ml/bp_ep 买入 Gap 保护: 跳空低开>3% 的标的跳过买入(当日开盘可观察, 无未来函数)
  S2 止损预提: 触发阈值 -8% -> -6.5% (对冲单日暴跌击穿与收盘成交滑点)
  M4 账户级回撤 20% 降半仓: 权益自峰值回撤>=20% 卖出一半仓位, 创新高恢复
对照: bp_ep_core + ml_signal 两生产模型, V2 vs V1(round6) 全指标对比
"""
import sys, os, json, logging, time, types, warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

FACTOR_ENGINE_ROOT = r"D:\workbuddyspace\factor_engine"
if FACTOR_ENGINE_ROOT not in sys.path:
    sys.path.insert(0, FACTOR_ENGINE_ROOT)

from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest
from core.backtest_system.performance import equity_metrics

# ============================================================
#  复用主引擎的数据层与信号函数（截取到 Main Entry 之前）
# ============================================================
SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
with open(SRC, encoding="utf-8") as f:
    _src = f.read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns: dict = {}
exec(compile(_src[:_cut], SRC, "exec"), _ns)
TulongDataLoader = _ns["TulongDataLoader"]
bp_ep_signal = _ns["bp_ep_signal"]
ml_signal = _ns["ml_signal"]
_cfg_base = _ns["cfg"]

# 清理 exec 带来的日志 handler，统一指向 v2 目录
import logging as _logging
for h in list(_logging.getLogger().handlers):
    _logging.getLogger().removeHandler(h)

OUTPUT_DIR = r"D:\zcodesj\reports\tulong_backtest_multi_round6_v2b_20260819"
os.makedirs(OUTPUT_DIR, exist_ok=True)
START_DATE = "2016-01-01"
END_DATE = "2026-08-14"
INITIAL_CAPITAL = 100000
DATA_DIR = r"D:\workbuddyspace\factor_engine\data"
DB_PATH = os.path.join(DATA_DIR, "dragon_quant.db")
QFQ_PATH = os.path.join(DATA_DIR, "dragon_quant_qfq.db")
DB_FUND = r"D:\zcodesj\fund.db"  # 若存在则用于行业（本次不依赖）

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(OUTPUT_DIR, "v2_run.log"), encoding="utf-8")],
)
logger = logging.getLogger("Round6V2")

# V2 配置: 继承基线 + S2 止损预提 6.5%
_cfg = _cfg_base
_cfg_v2s = dict(
    initial_capital=INITIAL_CAPITAL,
    commission_rate=0.00025, stamp_tax_rate=0.0005, slippage_rate=0.0003,
    max_position_pct=0.20, max_positions=50, max_drawdown_limit=10.0,
    max_single_loss_pct=0.08, stop_loss_pct=0.065,  # S2
    take_profit_pct=0.0, trailing_stop_pct=0.10, max_holding_bars=45,
    enable_t_plus_1=True, enable_price_limit=True, enable_st_filter=True,
    lot_size=100, universe_filter=True, min_commission=5.0,
    slippage_model="fixed", execution="next_open", exit_execution="same_close",
)


class V2Backtest(SingleStrategyBacktest):
    """S1+S2+M4 变体回测（复制框架 run() 并注入三项改造）"""

    def __init__(self, config: BacktestConfig = None):
        super().__init__(config)
        # S1: 实例级替换 engine.rebalance -> Gap 过滤版
        orig_rebalance = self.engine.rebalance

        def rebalance_gap(_self_engine, target_weights, prices, date=None,
                          opens=None, prev_closes=None, **kw):
            if opens and prev_closes:
                flt = {}
                for code, w in (target_weights or {}).items():
                    pc = prev_closes.get(code)
                    op = opens.get(code)
                    if pc and op and op < pc * 0.97:  # 跳空低开>3% 跳过买入
                        continue
                    flt[code] = w
                target_weights = flt or None
            if not target_weights:
                return []
            return orig_rebalance(target_weights, prices, date=date, opens=opens,
                                  prev_closes=prev_closes, **kw)

        self.engine.rebalance = types.MethodType(rebalance_gap, self.engine)
        self._halved = False

    def _m4_halve_check(self, prices, date):
        """M4: 账户自峰值回撤>=20% 卖出一半, 创新高恢复"""
        eq = self.engine.portfolio.total_equity
        peak = getattr(self.engine, "_peak_equity", eq)
        if peak <= 0:
            return
        dd = (peak - eq) / peak
        if not self._halved and dd >= 0.20:
            self._halved = True
            n = 0
            for code, pos in list(self.engine.portfolio.positions.items()):
                if pos.shares <= 0:
                    continue
                half = pos.shares // 2 // self.config.lot_size * self.config.lot_size
                if half > 0:
                    px = prices.get(code)
                    if px:
                        self.engine.submit_order(code, "sell", px, half, date=date,
                                                 prev_close=None,
                                                 stock_name=pos.stock_name,
                                                 exit_reason="m4_halve")
                        n += 1
            logger.warning(f"[M4] 账户回撤 {dd*100:.1f}%≥20% -> 减半持仓({n}只), date={date}")
        elif self._halved and dd < 0.08:  # V2b修复: 回撤自峰值收敛至<8% 即恢复满仓(原"创新高"过苛致长期低仓)
            self._halved = False
            logger.info(f"[M4] 回撤收敛至 {dd*100:.1f}%<8% 恢复满仓, date={date}")

    def run(self, signal_func, data_loader, start_date="2016-01-01",
            end_date="2026-08-14", rebalance_freq="weekly",
            strategy_name=None):
        from core.backtest_system.rules import to_date
        logger.info(f"=== V2变体回测({strategy_name}): {start_date} ~ {end_date} ===")
        all_dates = sorted(data_loader.get_trading_dates(start_date, end_date))
        rebalance_interval = {"daily": 1, "weekly": 5, "monthly": 22, "quarterly": 60}.get(rebalance_freq, 5)
        days_since_rebalance = 999
        preloaded = {d: data_loader.get_data(d) for d in all_dates}
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

            # 收盘后止损/止盈/最大持有期 (S2: 阈值 -6.5%)
            for code, pos in list(self.engine.portfolio.positions.items()):
                if pos.shares <= 0:
                    continue
                if self.config.max_holding_bars > 0 and code in held_since:
                    d0 = to_date(held_since[code]); d1 = to_date(date)
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
                    # 与主引擎 wrapped 一致: 信号函数签名 (loader, date_str) + M-1 单票<=20% 截断
                    weights, _reasons = signal_func(data_loader, date)
                    weights = {k: min(v, 0.20) for k, v in weights.items()}
                    tw = weights
                    if tw and isinstance(tw, dict):
                        total_w = sum(tw.values())
                        if total_w > 0:
                            tw = {k: v / total_w for k, v in tw.items()}
                        pending_weights = tw
                        days_since_rebalance = 0
                except Exception as e:
                    logger.warning(f"信号异常 {date}: {e}")

            is_ok, warnings_ = self.engine.check_risk_controls(prices)
            if not is_ok:
                pending_risk_liquidate = True
                pending_weights = None

            self.engine.settle_day(date, prices)
            self._m4_halve_check(prices, date)  # M4: 账户级回撤减半

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
        logger.info(f"=== V2完成: {strategy_name} 总收益{metrics.get('total_return_pct',0):.1f}% "
                    f"夏普{metrics.get('sharpe_ratio',0):.2f} MDD{metrics.get('max_drawdown_pct',0):.1f}% ===")
        return {"metrics": metrics, "equity_curve": self.engine.get_equity_curve_df(),
                "trades": self.engine._clean_trades(), "engine": self.engine}


def main():
    import gc
    loader = TulongDataLoader(DB_PATH, QFQ_PATH)
    # 内存瘦身：bp_ep/ml 不需要 v375/p2/composite/legacy 信号表，立即释放
    for attr in ["_v375_by_date", "_p2_by_date", "_composite_by_date", "_legacy_signals_by_date"]:
        if hasattr(loader, attr):
            delattr(loader, attr)
    gc.collect()
    logger.info("已释放非必要信号表内存 (v375/p2/composite/legacy)")
    models = [
        ("bp_ep_core", bp_ep_signal, "quarterly", {"max_holding_bars": 0}),
        ("ml_signal", ml_signal, "monthly", {"max_holding_bars": 45}),
    ]
    results = {}
    for name, sig_func, freq, overrides in models:
        cfg_v2 = BacktestConfig(**{**_cfg_v2s, **overrides})
        bt = V2Backtest(cfg_v2)
        t0 = time.time()
        res = bt.run(sig_func, loader, START_DATE, END_DATE, freq, strategy_name=name)
        results[name] = res
        eq = res["equity_curve"]
        eq.to_csv(os.path.join(OUTPUT_DIR, f"{name}_equity.csv"), index=False, encoding="utf-8-sig")
        trades = pd.DataFrame(res["trades"])
        if len(trades):
            trades.to_csv(os.path.join(OUTPUT_DIR, f"{name}_trades.csv"), index=False, encoding="utf-8-sig")
        with open(os.path.join(OUTPUT_DIR, f"{name}_summary.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": {"strategy": name, "variant": "S1+S2+M4",
                                "start": START_DATE, "end": END_DATE,
                                "n_trades": len(trades), "runtime_s": round(time.time()-t0, 1)},
                       "summary": res["metrics"]}, f, ensure_ascii=False, indent=1, default=str)
        logger.info(f"  -> {name} 导出完成, {time.time()-t0:.0f}s")

    # 与 round6 v1 基线对比
    base_dir = Path(r"D:\zcodesj\reports\tulong_backtest_multi_round6_20260819")
    rows = []
    for name in models:
        n = name[0]
        try:
            v1 = json.load(open(base_dir / f"{n}_summary.json", encoding="utf-8"))["summary"]
        except Exception:
            v1 = {}
        v2m = results[n]["metrics"]
        rows.append({
            "model": n,
            "v1_total_ret": v1.get("total_return_pct"), "v2_total_ret": round(v2m.get("total_return_pct", 0), 2),
            "v1_cagr": v1.get("annualized_return_pct"), "v2_cagr": round(v2m.get("annualized_return_pct", 0), 2),
            "v1_mdd": v1.get("max_drawdown_pct"), "v2_mdd": round(v2m.get("max_drawdown_pct", 0), 2),
            "v1_sharpe": v1.get("sharpe_ratio"), "v2_sharpe": round(v2m.get("sharpe_ratio", 0), 3),
            "v1_win": v1.get("win_rate_pct"), "v2_win": round(v2m.get("win_rate_pct", 0), 2),
            "v1_n_trades": v1.get("total_trades"), "v2_n_trades": v2m.get("total_trades"),
        })
    cmp = pd.DataFrame(rows)
    cmp.to_csv(os.path.join(OUTPUT_DIR, "v2_vs_v1_comparison.csv"), index=False, encoding="utf-8-sig")
    print("\n=== V2 vs V1 对比 ===\n", cmp.to_string(index=False))
    logger.info("二轮复测完成")


if __name__ == "__main__":
    main()