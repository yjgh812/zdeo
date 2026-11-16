# -*- coding: utf-8 -*-
"""
round9 R9-D1 持仓侧流动性止损复测 (2026-08-26)
================================================
M1(开仓侧危机标签)补全: 大亏根因 = 已持仓票在危机窗口被连续跌停套死
(引擎 enable_price_limit 跌停不可卖 -> 止损日被拒 -> 次日续触发 -> 深亏成交)。

D1 = 引擎新增 crisis_exit_check hook(step 3.5, 默认None零行为变化):
  - 极端日(深跌家数>=500): 全部持仓收盘提交卖出(reason=crisis_extreme)
  - 危机日(深跌家数>=200): 当日跌幅<=-5%的弱势持仓收盘卖出(reason=crisis_weak)
  逻辑: 危机信号早于个股-8%, 趁未连续跌停前离场; 跌停被拒次日续触发(同止损口径)。

变体(完整防御栈 = M1开仓侧 + D1持仓侧):
  - ml_hold20_defense  (对照 v2 无标签基线 +250.8% 大亏232)
  - bp_ep_top15_defense(对照 v2 无标签基线 +152.2% 大亏369)
验收: 大亏(<=-5%)笔数较无标签基线降 20% 且总收益不降。
数据: PIT v2 + MA250 修复后权威脚本口径(与 round9_pitv2_recheck 同源)。
"""
import sys, os, dataclasses, sqlite3, time, warnings
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

from core.backtest_system.strategy import SingleStrategyBacktest

SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
_src = open(SRC, encoding="utf-8").read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns = {}; exec(compile(_src[:_cut], SRC, "exec"), _ns)

OUT = r"D:\zcodesj\reports\round9_d1_retest_20260826"
os.makedirs(OUT, exist_ok=True)

CRISIS_N, EXTREME_N, WEAK_PCT = 200, 500, -5.0


def build_deep_drop_map():
    """qfq 库每日深跌家数(<=-9.5%)序列: date('YYYYMMDD') -> 家数 (M1 同源)。"""
    con = sqlite3.connect(_ns["QFQ_PATH"])
    q = pd.read_sql(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE date >= ? AND date <= ? ORDER BY date",
        con, params=("20160101", _ns["END_DATE"].replace("-", "")))
    con.close()
    q = q[q["close"] > 0]
    q["prev_close"] = q.groupby("code")["close"].shift(1)
    q["pct"] = (q["close"] / q["prev_close"] - 1) * 100
    deep = q[q["pct"] <= -9.5].groupby("date").size()
    return deep.to_dict()


def make_crisis_exit(deep_map):
    """D1 hook: 极端日全清, 危机日清弱势(当日<=WEAK_PCT)。"""
    def hook(date, positions, daily_data):
        n_deep = deep_map.get(str(date).replace("-", ""), 0)
        if n_deep < CRISIS_N:
            return {}
        if n_deep >= EXTREME_N:
            return {code: "crisis_extreme" for code, pos in list(positions.items()) if pos.shares > 0}
        prices = daily_data.get("prices", {})
        prev_closes = daily_data.get("prev_close", daily_data.get("prev_closes", {}))
        exits = {}
        for code, pos in list(positions.items()):
            if pos.shares <= 0:
                continue
            c, p0 = prices.get(code), prev_closes.get(code)
            if c and p0 and p0 > 0 and (c / p0 - 1) * 100 <= WEAK_PCT:
                exits[code] = "crisis_weak"
        return exits
    return hook


def bp_ep_crisis(loader, date_str, deep_map):
    """bp_ep Top15 + M1 开仓侧标签 (M1 同源)。"""
    df = loader.get_fundamental_factors(date_str)
    if df is None or len(df) < 15:
        return {}, {}
    df = df[df["close"] <= 30].copy()
    if len(df) < 15:
        return {}, {}
    df["ep_rank"] = df["ep"].rank(pct=True)
    df["bp_rank"] = df["bp"].rank(pct=True)
    df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2
    top = df.nlargest(15, "score")
    fail = loader.get_failure_flags(date_str)
    vt, rc = fail.get("value_trap", set()), fail.get("rebound_chase", set())
    n_deep = deep_map.get(date_str.replace("-", ""), 0)
    if n_deep >= EXTREME_N:
        return {}, {}
    mult_crisis = 0.5 if n_deep >= CRISIS_N else 1.0
    base = 0.40 / 15
    w, reasons = {}, {}
    for idx, row in top.iterrows():
        mult = mult_crisis * (0.5 if (idx in vt or idx in rc) else 1.0)
        w[idx] = base * mult
        reasons[idx] = f"bp+ep top15 (score={row['score']:.3f})"
    return w, reasons


def ml_signal_crisis(loader, date_str, deep_map):
    """ml + M1 开仓侧标签 (M1 同源)。"""
    w, reasons = _ns["ml_signal"](loader, date_str)
    if not w:
        return w, reasons
    n_deep = deep_map.get(date_str.replace("-", ""), 0)
    if n_deep >= EXTREME_N:
        return {}, {}
    if n_deep >= CRISIS_N:
        w = {k: v * 0.5 for k, v in w.items()}
    return w, reasons


def run_variant(name, sig_fn, freq, overrides, loader, deep_map):
    hook = make_crisis_exit(deep_map)
    bt_cfg = dataclasses.replace(_ns["cfg"], crisis_exit_check=hook, **(overrides or {}))
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(dd, ds, _fn=sig_fn):
        weights, _ = _fn(loader, ds, deep_map)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=_ns["START_DATE"], end_date=_ns["END_DATE"],
                    rebalance_freq=freq, strategy_name=name)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])
    ec = result.get("equity_curve", pd.DataFrame())
    wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
    n_big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
    n_crisis_sell = sum(1 for t in trades if str(t.get("exit_reason", "")).startswith("crisis"))
    line = (f"[{name}] 总收益={metrics.get('total_return_pct', 0):+.1f}% "
            f"夏普={metrics.get('sharpe_ratio', 0):.2f} 回撤={metrics.get('max_drawdown_pct', 0):.1f}% "
            f"胜率={wr:.1f}% 交易={len(trades)} 大亏(<=-5%)={n_big5} 危机卖出={n_crisis_sell}")
    print(line, flush=True)
    ec.to_csv(os.path.join(OUT, f"{name}_equity.csv"), index=False, encoding="utf-8-sig")
    return {"name": name, "equity_curve": ec, "line": line, "n_big5": n_big5}


if __name__ == "__main__":
    t0 = time.time()
    deep_map = build_deep_drop_map()
    n_crisis = sum(1 for v in deep_map.values() if v >= CRISIS_N)
    n_extreme = sum(1 for v in deep_map.values() if v >= EXTREME_N)
    print(f"[深跌家数] 危机日(>=200): {n_crisis} | 极端日(>=500): {n_extreme}", flush=True)

    loader = _ns["TulongDataLoader"](_ns["DB_PATH"], _ns["QFQ_PATH"])
    print(f"[check] loader 完成 ({time.time()-t0:.0f}s)", flush=True)

    r_ml = run_variant("ml_hold20_defense", ml_signal_crisis, "monthly",
                       {"max_holding_bars": 20}, loader, deep_map)
    r_bp = run_variant("bp_ep_top15_defense", bp_ep_crisis, "monthly",
                       {"max_holding_bars": 0}, loader, deep_map)

    # 组合合成 (静态60/40, 与 pitv2 基线同法)
    def load_eq(p):
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")["equity"]
    bp, ml = load_eq(os.path.join(OUT, "bp_ep_top15_defense_equity.csv")), \
             load_eq(os.path.join(OUT, "ml_hold20_defense_equity.csv"))
    df = pd.concat([bp.rename("bp"), ml.rename("ml")], axis=1).dropna()
    r = (0.6 * df["bp"].pct_change() + 0.4 * df["ml"].pct_change()).fillna(0)
    port = (1 + r).cumprod()
    print(f"[combo_defense_6040] 总收益=+{(port.iloc[-1]-1)*100:.2f}% "
          f"回撤={(port/port.cummax()-1).min()*100:.1f}% (对照 pitv2 静态基线 +199.28%)", flush=True)
    port.to_frame("equity").assign(date=df.index).to_csv(
        os.path.join(OUT, "combo_defense_6040_equity.csv"), index=False, encoding="utf-8-sig")

    with open(os.path.join(OUT, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("R9-D1 持仓侧流动性止损复测 (完整防御栈=M1开仓+D1持仓)\n" + "=" * 60 + "\n")
        f.write(f"基线(PIT v2 无标签): ml +250.8% 大亏232 | bp +152.2% 大亏369 | 组合 +199.28%\n")
        f.write("M1参照(8/25 v1口径): ml +263.0% 大亏227 | bp +155.4% 大亏377\n")
        f.write(r_ml["line"] + "\n" + r_bp["line"] + "\n")
    print("R9-D1 复测完成")
