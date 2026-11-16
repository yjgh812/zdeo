# -*- coding: utf-8 -*-
"""
round9 A 采纳项 PIT v2 口径重核 —— 数据清洗后冻结协议漂移检查 (2026-08-27)
====================================================
背景(原): round9 主跑/ v2_retest 的 bp_ep 系数基于 PIT v1 前视口径, 已由
      round9_pitv2_recheck_20260826 以 PIT v2 + MA250 重核为权威 (+199.28%)。
背景(本轮新增): 8/27 凌晨完成数据底座假价修复(META_QFQ_POLLUTION_ESCALATION
      §15-§18: 退市股全史清除/待确认组闭环/孤立尖刺清除, 备份均可逆)。
      按准则"历史研究读过 meta/qfq 者引用前须双跑复核", 在清洗后的库上以
      冻结协议(参数/区间/口径零改动)重算 A1/A2/A3, 与基线逐行对比定漂移。
本脚本与 round9_pitv2_recheck_20260826 唯一差异 = OUT 输出目录。
基线备份: reports/round9_pitv2_recheck_20260826_bak_pre_driftcheck/
"""
import sys, os, json, dataclasses, time, warnings
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

from core.backtest_system.strategy import SingleStrategyBacktest

SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
_src = open(SRC, encoding="utf-8").read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns = {}; exec(compile(_src[:_cut], SRC, "exec"), _ns)
m = type("M", (), _ns)  # 命名空间直取

OUT = r"D:\zcodesj\reports\round9_pitv2_driftcheck_20260827"
os.makedirs(OUT, exist_ok=True)

t0 = time.time()
loader = _ns["TulongDataLoader"](_ns["DB_PATH"], _ns["QFQ_PATH"])
print(f"[check] loader 加载完成 ({time.time()-t0:.0f}s); MA250 注入 {len(loader._idx_close)} 日")


def bp_ep_topn(start: int, end: int):
    """与 round9_v2_retest.bp_ep_topn 完全同源(仅换 PIT v2 数据)。"""
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
        weight = 0.40 / n
        weights, reasons = {}, {}
        for idx, row in top.iterrows():
            mult = 0.5 if (idx in vt or idx in rc) else 1.0
            weights[idx] = weight * mult
            reasons[idx] = f"bp+ep月度rank{start + 1}-{end} PITv2 (score={row['score']:.3f})"
        return weights, reasons
    return sig


def run_variant(name, sig_fn, freq, overrides):
    bt_cfg = dataclasses.replace(_ns["cfg"], **(overrides or {}))
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(dd, ds, _fn=sig_fn):
        weights, _ = _fn(loader, ds)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=_ns["START_DATE"], end_date=_ns["END_DATE"],
                    rebalance_freq=freq, strategy_name=name)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])
    ec = result.get("equity_curve", pd.DataFrame())
    wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
    n_big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
    line = (f"[{name}] 总收益={metrics.get('total_return_pct', 0):+.1f}% "
            f"夏普={metrics.get('sharpe_ratio', 0):.2f} 回撤={metrics.get('max_drawdown_pct', 0):.1f}% "
            f"胜率={wr:.1f}% 交易={len(trades)} 大亏(<=-5%)={n_big5}")
    print(line, flush=True)
    ec.to_csv(os.path.join(OUT, f"{name}_equity.csv"), index=False, encoding="utf-8-sig")
    # v2 增补(2026-08-27): 导出逐笔交易供受影响码扫描/年度汇总——equity 数学零改动
    import json as _jsonlib
    with open(os.path.join(OUT, f"{name}_trades.json"), "w", encoding="utf-8") as _f:
        _jsonlib.dump(trades, _f, ensure_ascii=False, default=str)
    return {"name": name, "equity_curve": ec, "line": line}


def combo_60_40(ec_a, ec_b, label):
    # 2026-08-28: 统一走共享层 combo_synthesizer(唯一实现, 内置权威基线回归校验锚)
    import combo_synthesizer
    init = _ns["INITIAL_CAPITAL"]
    df = combo_synthesizer.synth_combo_60_40(ec_a, ec_b, init, 0.60, 0.40)
    total = (float(df["equity"].iloc[-1]) / init - 1) * 100
    print(f"[{label}] 组合60/40 总收益={total:+.2f}% 终值={df['equity'].iloc[-1]:,.0f}", flush=True)
    df.to_csv(os.path.join(OUT, f"{label}_combo_equity.csv"), index=False, encoding="utf-8-sig")
    return total


if __name__ == "__main__":
    r_ml = run_variant("ml_hold20_pitv2", _ns["ml_signal"], "monthly", {"max_holding_bars": 20})
    r_bp = run_variant("bp_ep_top15_pitv2", bp_ep_topn(0, 15), "monthly", {"max_holding_bars": 0})
    combo_60_40(r_bp["equity_curve"], r_ml["equity_curve"], "bpep15_ml20_pitv2")
    print("A 采纳项 PIT v2 漂移检查完成 (对照基线: bp_ep_top15=+152.2% ml_hold20=+250.8% 组合=+199.28%)")
