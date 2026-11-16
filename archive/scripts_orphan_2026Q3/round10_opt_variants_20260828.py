# -*- coding: utf-8 -*-
"""
round10 优化变体运行器 (2026-08-28, 预注册见 ROUND10_OPT_STUDY_PREREG)
====================================================
变体: P1 bp_ep TopN12 / P2 TopN18 / P3 L3-0.25x / A1_ml ATR(14)×2.5 止损(ml hold20)
纪律: 每个变体独立进程(引擎 preload 内存边界); 信号/引擎/loader 复用冻结协议;
      atr_stop_mult 为引擎原生支持(阈值=atr14/close*mult, 夹逼4-20%, 缺ATR回退固定-8%)。
用法: python round10_opt_variants_20260828.py P1
"""
import sys, os, json, dataclasses, time, warnings
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")
from core.backtest_system.strategy import SingleStrategyBacktest

SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
_src = open(SRC, encoding="utf-8").read()
_ns = {}
exec(compile(_src[:_src.index("# ============================================================\n#  Main Entry")], SRC, "exec"), _ns)

OUT = r"D:\zcodesj\reports\tulong_backtest_round10_full_20260827\variants"
os.makedirs(OUT, exist_ok=True)

VARIANTS = {
    "P1":    {"kind": "bp", "n": 12, "l3": 0.5,  "ovr": {"max_holding_bars": 0}},
    "P2":    {"kind": "bp", "n": 18, "l3": 0.5,  "ovr": {"max_holding_bars": 0}},
    "P3":    {"kind": "bp", "n": 15, "l3": 0.25, "ovr": {"max_holding_bars": 0}},
    "A1_ml": {"kind": "ml", "ovr": {"max_holding_bars": 20, "atr_stop_mult": 2.5,
                                    "stop_loss_pct": 0.08}},
    # §8 第二批: A2 = 新基线P3(L3-0.25x) + ATR(14)×2.5 止损(单变量: 仅止损规则)
    "A2":    {"kind": "bp", "n": 15, "l3": 0.25,
              "ovr": {"max_holding_bars": 0, "atr_stop_mult": 2.5, "stop_loss_pct": 0.08}},
}


def bp_ep_topn_l3(start, end, l3_mult):
    def sig(ld, date_str):
        df = ld.get_fundamental_factors(date_str)
        if df is None or len(df) < end:
            return {}, {}
        df = df[df["close"] <= 30].copy()
        if len(df) < end:
            return {}, {}
        df["ep_rank"] = df["ep"].rank(pct=True)
        df["bp_rank"] = df["bp"].rank(pct=True)
        df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2
        top = df.nlargest(end, "score").iloc[start:]
        fail = ld.get_failure_flags(date_str)
        vt, rc = fail.get("value_trap", set()), fail.get("rebound_chase", set())
        n = end - start
        w = 0.40 / n
        weights = {}
        for idx, row in top.iterrows():
            mult = l3_mult if (idx in vt or idx in rc) else 1.0
            weights[idx] = w * mult
        return weights, {}
    return sig


def run(variant):
    v = VARIANTS[variant]
    bt_cfg = dataclasses.replace(_ns["cfg"], **v["ovr"])
    bt = SingleStrategyBacktest(bt_cfg)
    if v["kind"] == "bp":
        os.environ["TULONG_LOADER_LIGHT"] = "bp"
        sig = bp_ep_topn_l3(0, v["n"], v["l3"])
    else:
        os.environ["TULONG_LOADER_LIGHT"] = "ml"
        sig = _ns["ml_signal"]
    loader = _ns["TulongDataLoader"](_ns["DB_PATH"], _ns["QFQ_PATH"])

    def wrapped(dd, ds, _fn=sig):
        w, _ = _fn(loader, ds)
        return {k: min(vv, 0.20) for k, vv in w.items()}

    t0 = time.time()
    result = bt.run(wrapped, loader, start_date=_ns["START_DATE"], end_date=_ns["END_DATE"],
                    rebalance_freq="monthly", strategy_name=variant)
    m = result.get("metrics", {})
    trades = result.get("trades", [])
    ec = result.get("equity_curve", pd.DataFrame())
    wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
    big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
    print(f"[{variant}] 总收益={m.get('total_return_pct', 0):+.2f}% "
          f"年化={m.get('annualized_return_pct', 0):.2f}% 夏普={m.get('sharpe_ratio', 0):.2f} "
          f"回撤={m.get('max_drawdown_pct', 0):.2f}% 胜率={wr:.1f}% 交易={len(trades)} "
          f"大亏(<=-5%)={big5} 耗时={time.time()-t0:.0f}s", flush=True)
    ec.to_csv(os.path.join(OUT, f"{variant}_equity.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(OUT, f"{variant}_summary.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    if trades:
        pd.DataFrame(trades).to_csv(os.path.join(OUT, f"{variant}_trades.csv"),
                                    index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    run(sys.argv[1])
