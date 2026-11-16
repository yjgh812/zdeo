# -*- coding: utf-8 -*-
"""M-3 trailing 移动止盈（ml 腿）净值级第二轮复测 (2026-09-03)

定位: 回测专项指令第二轮迭代复测(指令六)——M-3 为证伪过滤后唯一带正面证据的合法候选。
门禁链: 8/28 滚动窗 PREREG PASS + 9/2 tech-grid.v1 PASS(指纹 a41fbe252a3af5b7641a, 笔均口径)
本轮补齐: 同引擎同数据版本(2026-09-02)的净值级独立 A/B——
  A 臂 baseline = 正典 ml_signal(monthly, max_holding_bars=45, trailing=0), 兼作 9/2 跑复现锚
  B 臂 M-3     = 登记格 trailing_stop_pct=10% + max_holding_bars=20(与门禁格参数逐字段一致)
判据(事后核对, 不调参): ①A 臂复现 drift_check_20260902e_0902end ml 腿; ②B 臂总收益>A 臂;
  ③第二轮硬要求: B 臂 MDD ≤ A 臂; ④盈亏比/笔均增量如实记录。
只读研究脚本: 不改生产配置(v1.4 ml 权重=0 不动), 不写库。
输出: reports/tulong_ab_m3_trail_equity_20260903/ (两臂标准三件套 + compare.json)
"""
import functools
import json
import logging
import os
import sys

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

os.environ["TULONG_BT_OUT"] = r"D:\zcodesj\reports\tulong_ab_m3_trail_equity_20260903"

import tulong_backtest_full_report as base  # noqa: E402  (END_DATE=2026-09-02 已在位)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("m3_trail_equity_round2")

OUT_DIR = base.OUTPUT_DIR
os.makedirs(OUT_DIR, exist_ok=True)


class CachedLoader:
    """get_data 跨臂复用(只读缓存), 同 9/2 网格先例件"""

    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def main():
    from_disk = "--from-disk" in sys.argv
    if from_disk:
        # 从本目录已落盘产物重建 compare.json (生成物只读不手改: 改源后由生成命令再生)
        # 用于回测已完成后的判据修复/字段补齐, 不重跑回测 (引擎确定性, 产物即真值)
        import csv as _csv
        import collections

        def load_arm(name):
            sm = json.load(open(os.path.join(OUT_DIR, f"{name}_summary.json"), encoding="utf-8"))["summary"]
            with open(os.path.join(OUT_DIR, f"{name}_trades_full.csv"), encoding="utf-8-sig") as f:
                rows = list(_csv.DictReader(f))
            pct = [float(r["盈亏比例%"]) for r in rows]
            amt = [float(r["盈亏金额"]) for r in rows]
            wins_p = [p for p in pct if p > 0]; losses_p = [p for p in pct if p <= 0]
            wins_a = [a for a in amt if a > 0]; losses_a = [a for a in amt if a <= 0]
            return {
                "total_return_pct": sm.get("total_return_pct"),
                "annualized_return_pct": sm.get("annualized_return_pct"),
                "max_drawdown_pct": sm.get("max_drawdown_pct"),
                "sharpe_ratio": sm.get("sharpe_ratio"),
                "total_trades": sm.get("total_trades"),
                "trade_win_rate_pct": sm.get("trade_win_rate_pct"),
                "profit_loss_ratio": sm.get("profit_loss_ratio"),
                "per_trade_avg_pct": round(sum(pct) / len(pct), 4) if pct else None,
                "pct_pl_ratio": round((sum(wins_p) / len(wins_p)) / abs(sum(losses_p) / len(losses_p)), 4)
                                if wins_p and losses_p else None,
                "amt_pl_ratio": round((sum(wins_a) / len(wins_a)) / abs(sum(losses_a) / len(losses_a)), 4)
                                if wins_a and losses_a else None,
                "final_equity": sm.get("final_equity"),
            }

        arm_a = load_arm("ml_signal_baseline")
        arm_b = load_arm("ml_signal_m3_trail10_hold20")
    else:
        loader = CachedLoader(base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH))

        logger.info("=== A 臂: ml_signal 正典基线 (monthly, hold45, trailing=0) ===")
        arm_a = base.run_single_backtest(
            "ml_signal_baseline", base.ml_signal, loader, "monthly",
            {"max_holding_bars": 45},
        )

        logger.info("=== B 臂: ml_signal + M-3 登记格 (trailing=10%%, hold20) ===")
        arm_b = base.run_single_backtest(
            "ml_signal_m3_trail10_hold20", base.ml_signal, loader, "monthly",
            {"max_holding_bars": 20, "trailing_stop_pct": 0.10},
        )

    def core_from_result(r):
        m = r.get("metrics", {})
        tr = r.get("trades", [])
        pct = [t.get("pnl_pct", 0.0) for t in tr if t.get("pnl_pct") is not None]
        wins = [p for p in pct if p > 0]
        losses = [p for p in pct if p <= 0]
        pl_ratio = (sum(wins) / len(wins) / abs(sum(losses) / len(losses))) if wins and losses else None
        return {
            "total_return_pct": m.get("total_return_pct"),
            "annualized_return_pct": m.get("annualized_return_pct"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "sharpe_ratio": m.get("sharpe_ratio"),
            "total_trades": m.get("total_trades"),
            "trade_win_rate_pct": m.get("trade_win_rate_pct"),
            "profit_loss_ratio": m.get("profit_loss_ratio"),
            "per_trade_avg_pct": round(sum(pct) / len(pct), 4) if pct else None,
            "pct_pl_ratio": round(pl_ratio, 4) if pl_ratio else None,
            "final_equity": m.get("final_equity"),
        }

    core_a = arm_a if from_disk else core_from_result(arm_a)
    core_b = arm_b if from_disk else core_from_result(arm_b)

    compare = {
        "meta": {
            "date": "2026-09-03",
            "data_end": base.END_DATE,
            "purpose": "M-3 净值级第二轮复测(指令六); 门禁链=8/28 PREREG PASS + 9/2 techgrid PASS",
            "arm_a": "ml_signal monthly hold45 trailing0 (正典基线, 兼复现锚)",
            "arm_b": "ml_signal monthly hold20 trailing10% (登记格, 与门禁格逐字段一致)",
            "production_note": "只读研究, 生产 v1.4 (ml 权重=0) 不动",
            "rebuild": ("from_disk(--from-disk: 回测产物即真值, 仅再生判据)" if from_disk else "fresh_backtest"),
        },
        "arm_a_baseline": core_a,
        "arm_b_m3_trail": core_b,
        "checks": {},
    }
    a, b = compare["arm_a_baseline"], compare["arm_b_m3_trail"]
    # 注: max_drawdown_pct 为正数口径(33.56=回撤33.56%), "不劣化"= B <= A
    compare["checks"] = {
        "c1_return_gain": (b["total_return_pct"] - a["total_return_pct"]),
        "c2_mdd_better_or_equal": (b["max_drawdown_pct"] <= a["max_drawdown_pct"]),
        "c3_pl_ratio_gain": (None if (b["profit_loss_ratio"] is None or a["profit_loss_ratio"] is None)
                             else round(b["profit_loss_ratio"] - a["profit_loss_ratio"], 4)),
        "c3b_pct_pl_ratio_gain": (None if (b["pct_pl_ratio"] is None or a["pct_pl_ratio"] is None)
                                  else round(b["pct_pl_ratio"] - a["pct_pl_ratio"], 4)),
        "c4_per_trade_gain": (None if (b["per_trade_avg_pct"] is None or a["per_trade_avg_pct"] is None)
                              else round(b["per_trade_avg_pct"] - a["per_trade_avg_pct"], 4)),
    }
    with open(os.path.join(OUT_DIR, "compare.json"), "w", encoding="utf-8") as f:
        json.dump(compare, f, ensure_ascii=False, indent=1)
    logger.info("=== 双臂完成 → %s/compare.json ===", OUT_DIR)
    logger.info("A 基线: %s", json.dumps(compare["arm_a_baseline"], ensure_ascii=False))
    logger.info("B M-3 : %s", json.dumps(compare["arm_b_m3_trail"], ensure_ascii=False))
    logger.info("核对: %s", json.dumps(compare["checks"], ensure_ascii=False))


if __name__ == "__main__":
    main()
