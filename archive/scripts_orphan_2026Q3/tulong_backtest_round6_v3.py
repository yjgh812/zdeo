# -*- coding: utf-8 -*-
"""
屠龙 round6 三轮迭代复测（V3：单项独立变体）
============================================
吸取 V2 教训（联合效应无法归因），本次每变体只改一个参数：
  v3a  bp_ep_s2only : S2 止损预提 -6.5%（bp_ep 独立验证，无S1无M4）
  v3b  ml_s1_2pct   : S1 Gap保护阈值 3%->2%（ml 独立验证）
  v3c  bp_ep_m4b    : M4改版: 回撤>=25% 仅停止新买入不卖存量（bp_ep）
  v3d  ml_m4b       : M4改版: 回撤>=25% 仅停止新买入不卖存量（ml）
验收对照: 各自与 round6 v1 基线全指标对比（总收益/年化/MDD/夏普/胜率/大亏笔数）
"""
import sys, os, json, time, types, warnings, gc, logging
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

FACTOR_ENGINE_ROOT = r"D:\workbuddyspace\factor_engine"
if FACTOR_ENGINE_ROOT not in sys.path:
    sys.path.insert(0, FACTOR_ENGINE_ROOT)

from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest

# ---------- 复用主引擎数据层与信号函数 ----------
SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
with open(SRC, encoding="utf-8") as f:
    _src = f.read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns: dict = {}
exec(compile(_src[:_cut], SRC, "exec"), _ns)
TulongDataLoader = _ns["TulongDataLoader"]
bp_ep_signal = _ns["bp_ep_signal"]
ml_signal = _ns["ml_signal"]

for h in list(logging.getLogger().handlers):
    logging.getLogger().removeHandler(h)

OUTPUT_DIR = r"D:\zcodesj\reports\tulong_backtest_multi_round6_v3_20260819"
os.makedirs(OUTPUT_DIR, exist_ok=True)
START_DATE = "2016-01-01"
END_DATE = "2026-08-14"
INITIAL_CAPITAL = 100000
DATA_DIR = r"D:\workbuddyspace\factor_engine\data"
DB_PATH = os.path.join(DATA_DIR, "dragon_quant.db")
QFQ_PATH = os.path.join(DATA_DIR, "dragon_quant_qfq.db")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(OUTPUT_DIR, "v3_run.log"), encoding="utf-8")])
logger = logging.getLogger("Round6V3")


def make_cfg(stop_loss=0.08, **overrides):
    kv = dict(max_holding_bars=45, max_positions=50)
    kv.update(overrides)
    return BacktestConfig(
        initial_capital=INITIAL_CAPITAL, commission_rate=0.00025, stamp_tax_rate=0.0005,
        slippage_rate=0.0003, max_position_pct=0.20, max_drawdown_limit=10.0,
        max_single_loss_pct=0.08, stop_loss_pct=stop_loss, take_profit_pct=0.0,
        trailing_stop_pct=0.10, enable_t_plus_1=True,
        enable_price_limit=True, enable_st_filter=True, lot_size=100, universe_filter=True,
        min_commission=5.0, slippage_model="fixed", execution="next_open",
        exit_execution="same_close", **kv)


class V3Backtest(SingleStrategyBacktest):
    def __init__(self, config, gap_th=None, m4_mode=None, m4_th=0.25):
        super().__init__(config)
        self._gap_th = gap_th          # 跳空低开容忍阈值（如0.02），None=关闭
        self._m4_mode = m4_mode        # None | "stop_buy"
        self._m4_th = m4_th
        self._m4_stopped = False

        if gap_th:
            orig_rebalance = self.engine.rebalance

            def rebalance_gap(_self2, target_weights, prices, date=None,
                              opens=None, prev_closes=None, **kw):
                if opens and prev_closes:
                    flt = {}
                    for code, w in (target_weights or {}).items():
                        pc = prev_closes.get(code)
                        op = opens.get(code)
                        if pc and op and op < pc * (1 - gap_th):
                            continue
                        flt[code] = w
                    target_weights = flt or None
                if not target_weights:
                    return []
                return orig_rebalance(target_weights, prices, date=date, opens=opens,
                                      prev_closes=prev_closes, **kw)

            self.engine.rebalance = types.MethodType(rebalance_gap, self.engine)

    def _m4_check(self, prices, date):
        """M4改版: 回撤>=阈值 仅停止新买入（不卖存量），回撤收敛<=10% 恢复"""
        eq = self.engine.portfolio.total_equity
        peak = getattr(self.engine, "_peak_equity", eq)
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if self._m4_mode != "stop_buy":
            return
        if not self._m4_stopped and dd >= self._m4_th:
            self._m4_stopped = True
            logger.warning(f"[M4-stopbuy] 回撤 {dd*100:.1f}%≥{self._m4_th*100:.0f}% -> 暂停新买入, date={date}")
        elif self._m4_stopped and dd <= 0.10:
            self._m4_stopped = False
            logger.info(f"[M4-stopbuy] 回撤收敛至 {dd*100:.1f}%≤10% -> 恢复新买入, date={date}")

    def run(self, signal_func, data_loader, start_date="2016-01-01",
            end_date="2026-08-14", rebalance_freq="weekly", strategy_name=None):
        from core.backtest_system.rules import to_date
        logger.info(f"=== V3变体({strategy_name}): {start_date} ~ {end_date} | "
                    f"gap={self._gap_th} m4={self._m4_mode}({self._m4_th}) stop={self.config.stop_loss_pct} ===")
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
                if self._m4_stopped:
                    pass  # M4-stopbuy: 暂停生成新调仓目标（持仓风控照常）
                else:
                    try:
                        weights, _r = signal_func(data_loader, date)
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
            self._m4_check(prices, date)

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
        logger.info(f"=== V3完成: {strategy_name} 总收益{metrics.get('total_return_pct',0):.1f}% "
                    f"夏普{metrics.get('sharpe_ratio',0):.2f} MDD{metrics.get('max_drawdown_pct',0):.1f}% ===")
        return {"metrics": metrics, "equity_curve": self.engine.get_equity_curve_df(),
                "trades": self.engine._clean_trades()}


VARIANTS = [
    # 串行单变体(降低内存峰值): A口径 bp_ep 60日(季度) 纯净基准
    ("bp_ep_60d_bench", bp_ep_signal, "quarterly", None, None, 0.25, 0.08, {"max_holding_bars": 0}),
]


def main():
    loader = TulongDataLoader(DB_PATH, QFQ_PATH)
    for attr in ["_v375_by_date", "_p2_by_date", "_composite_by_date", "_legacy_signals_by_date"]:
        if hasattr(loader, attr):
            delattr(loader, attr)
    gc.collect()

    results = {}
    for name, sig, freq, gap, m4, m4th, stop, overrides in VARIANTS:
        cfg = make_cfg(stop_loss=stop, **overrides)
        bt = V3Backtest(cfg, gap_th=gap, m4_mode=m4, m4_th=m4th)
        t0 = time.time()
        res = bt.run(sig, loader, START_DATE, END_DATE, freq, strategy_name=name)
        results[name] = res
        res["equity_curve"].to_csv(os.path.join(OUTPUT_DIR, f"{name}_equity.csv"), index=False, encoding="utf-8-sig")
        trades = pd.DataFrame(res["trades"])
        if len(trades):
            trades.to_csv(os.path.join(OUTPUT_DIR, f"{name}_trades.csv"), index=False, encoding="utf-8-sig")
        with open(os.path.join(OUTPUT_DIR, f"{name}_summary.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": {"strategy": name, "variant": f"gap={gap} m4={m4}({m4th}) stop={stop}",
                                "start": START_DATE, "end": END_DATE, "n_trades": len(trades),
                                "runtime_s": round(time.time() - t0, 1)},
                       "summary": res["metrics"]}, f, ensure_ascii=False, indent=1, default=str)
        logger.info(f"  -> {name} 导出完成 ({time.time()-t0:.0f}s)")

    # 汇总对比 V1 基线
    base_dir = Path(r"D:\zcodesj\reports\tulong_backtest_multi_round6_20260819")
    rows = []
    for name, *_ in VARIANTS:
        v1name = "bp_ep_core" if name.startswith("bp_ep") else "ml_signal"
        try:
            v1 = json.load(open(base_dir / f"{v1name}_summary.json", encoding="utf-8"))["summary"]
        except Exception:
            v1 = {}
        m = results[name]["metrics"]
        trades = pd.DataFrame(results[name]["trades"])
        big = int((trades["pnl_pct"] <= -10).sum()) if len(trades) and "pnl_pct" in trades.columns else None
        rows.append({
            "variant": name, "v1_ref": v1name,
            "v1_total_ret": v1.get("total_return_pct"), "v3_total_ret": round(m.get("total_return_pct", 0), 2),
            "v1_mdd": v1.get("max_drawdown_pct"), "v3_mdd": round(m.get("max_drawdown_pct", 0), 2),
            "v1_sharpe": v1.get("sharpe_ratio"), "v3_sharpe": round(m.get("sharpe_ratio", 0), 3),
            "v1_n_trades": v1.get("total_trades"), "v3_n_trades": m.get("total_trades"),
            "v3_big_loss_le10": big,
        })
    cmp = pd.DataFrame(rows)
    cmp.to_csv(os.path.join(OUTPUT_DIR, "v3_vs_v1_comparison.csv"), index=False, encoding="utf-8-sig")
    print("\n=== V3 vs V1 对比 ===\n", cmp.to_string(index=False))
    logger.info("三轮迭代复测完成")


if __name__ == "__main__":
    main()